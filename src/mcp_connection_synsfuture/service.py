"""Validation service for preconfigured Docker connection profiles."""

import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from .models import ConnectionProfile, ConnectionState, ConnectionValidationResult, ProfileType
from .process import CommandResult
from .profiles import ProfileRepository, validate_profile_shape


class CommandRunner(Protocol):
    async def run(self, args: list[str], timeout_seconds: float) -> CommandResult: ...


class ConnectionProfileService:
    def __init__(self, runner: CommandRunner, profiles: ProfileRepository, timeout: float = 15.0):
        self._runner = runner
        self._profiles = profiles
        self._timeout = timeout

    async def connect(self, profile_id: str) -> ConnectionValidationResult:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return self._result(
                profile_id,
                ConnectionState.PROFILE_NOT_FOUND,
                "The requested connection profile does not exist or is invalid.",
                "Create the profile locally and add it to the authorized profile file.",
            )
        if not profile.enabled:
            return self._result(
                profile_id,
                ConnectionState.PROFILE_DISABLED,
                "The requested connection profile is disabled.",
                "Enable the profile explicitly in the local profile file.",
                profile,
            )
        shape_error = validate_profile_shape(profile)
        if shape_error:
            return self._result(
                profile_id,
                ConnectionState.INVALID_CONFIGURATION,
                shape_error,
                "Correct the local profile configuration.",
                profile,
            )
        if profile.type is not ProfileType.DOCKER_CONTEXT:
            return self._result(
                profile_id,
                ConnectionState.UNSUPPORTED_TRANSPORT,
                "SSH profiles are not implemented in this increment.",
                "Use a docker-context profile while SSH profile support is being built.",
                profile,
            )
        return await self._connect_docker(profile)

    async def _connect_docker(self, profile: ConnectionProfile) -> ConnectionValidationResult:
        assert profile.docker_context is not None
        context = profile.docker_context
        try:
            inspect = await self._runner.run(
                ["docker", "context", "inspect", context], self._timeout
            )
        except FileNotFoundError:
            return self._result(
                profile.profile_id,
                ConnectionState.UNAVAILABLE,
                "Docker CLI is not available.",
                "Install Docker CLI and retry.",
                profile,
            )
        except TimeoutError:
            return self._result(
                profile.profile_id,
                ConnectionState.TIMEOUT,
                "Docker context inspection timed out.",
                "Verify the local Docker CLI and retry.",
                profile,
            )
        if inspect.returncode != 0:
            return self._result(
                profile.profile_id,
                ConnectionState.CONTEXT_NOT_FOUND,
                "The configured Docker context does not exist or cannot be inspected.",
                "Create or repair the context outside the MCP, then retry.",
                profile,
            )
        transport = self._transport(inspect.stdout)
        if transport != "ssh":
            return self._result(
                profile.profile_id,
                ConnectionState.UNSUPPORTED_TRANSPORT,
                "The Docker context does not use the approved SSH transport.",
                "Configure the context with an SSH endpoint and retry.",
                profile,
            )
        agent_result = await self._validate_ssh_agent(profile)
        if agent_result is not None:
            return agent_result
        try:
            version = await self._runner.run(
                ["docker", "--context", context, "version", "--format", "{{json .}}"],
                self._timeout,
            )
        except TimeoutError:
            return self._result(
                profile.profile_id,
                ConnectionState.TIMEOUT,
                "Remote Docker version validation timed out.",
                "Verify SSH connectivity and the remote Docker Engine.",
                profile,
                transport,
            )
        if version.returncode != 0:
            state = (
                ConnectionState.AUTHENTICATION_FAILED
                if self._is_authentication_error(version.stderr)
                else ConnectionState.UNAVAILABLE
            )
            return self._result(
                profile.profile_id,
                state,
                "The configured Docker context could not reach its remote Engine.",
                "Verify the SSH profile, keys, endpoint and remote Docker Engine.",
                profile,
                transport,
            )
        return self._result(
            profile.profile_id,
            ConnectionState.READY,
            "The connection profile is ready.",
            None,
            profile,
            transport,
            connected=True,
        )

    async def _validate_ssh_agent(
        self, profile: ConnectionProfile
    ) -> ConnectionValidationResult | None:
        """Validate the local SSH agent without exposing keys or secrets."""

        await self._discover_ssh_agent()
        if not os.environ.get("SSH_AUTH_SOCK"):
            return self._result(
                profile.profile_id,
                ConnectionState.SSH_AGENT_UNAVAILABLE,
                "No hay un agente SSH disponible en la sesión del MCP.",
                "Inicia el agente SSH, carga la clave del perfil y reinicia la sesión de Codex. "
                "Ejecuta ssh-add -l para comprobarlo localmente.",
                profile,
                "ssh",
            )
        try:
            identities = await self._runner.run(["ssh-add", "-l"], self._timeout)
        except FileNotFoundError:
            return self._result(
                profile.profile_id,
                ConnectionState.SSH_AGENT_UNAVAILABLE,
                "El comando ssh-add no está disponible en el equipo local.",
                "Instala OpenSSH y vuelve a iniciar la sesión del MCP.",
                profile,
                "ssh",
            )
        except TimeoutError:
            return self._result(
                profile.profile_id,
                ConnectionState.SSH_AGENT_UNAVAILABLE,
                "La validación del agente SSH agotó el tiempo de espera.",
                "Comprueba SSH_AUTH_SOCK y reinicia el agente SSH.",
                profile,
                "ssh",
            )
        if identities.returncode != 0 or "The agent has no identities" in identities.stderr:
            return self._result(
                profile.profile_id,
                ConnectionState.SSH_AGENT_UNAVAILABLE,
                "El agente SSH está disponible, pero no tiene ninguna llave cargada.",
                "Carga la clave privada del perfil con ssh-add y reinicia la sesión de Codex. "
                "No envíes la clave ni la passphrase al MCP.",
                profile,
                "ssh",
            )
        return None

    async def _discover_ssh_agent(self) -> None:
        """Populate SSH_AUTH_SOCK from standard platform mechanisms when possible."""

        if os.environ.get("SSH_AUTH_SOCK"):
            return

        if sys.platform == "darwin":
            try:
                result = await self._runner.run(
                    ["launchctl", "getenv", "SSH_AUTH_SOCK"], self._timeout
                )
                socket_path = result.stdout.strip()
                if result.returncode == 0 and socket_path:
                    os.environ["SSH_AUTH_SOCK"] = socket_path
                    return
            except (FileNotFoundError, TimeoutError):
                return

        if os.name == "nt":
            # OpenSSH for Windows exposes its agent through this named pipe.
            os.environ["SSH_AUTH_SOCK"] = r"\\.\pipe\openssh-ssh-agent"
            return

        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        candidates = (
            Path(runtime_dir) / "ssh-agent.socket" if runtime_dir else None,
            Path(runtime_dir) / "keyring" / "ssh" if runtime_dir else None,
        )
        for candidate in candidates:
            if candidate is not None and candidate.exists():
                os.environ["SSH_AUTH_SOCK"] = str(candidate)
                return

    @staticmethod
    def _transport(output: str) -> str | None:
        try:
            payload: Any = json.loads(output)
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            endpoint = payload.get("Endpoints", {}).get("docker", {})
            host = endpoint.get("Host", "")
            return "ssh" if isinstance(host, str) and host.startswith("ssh://") else None
        except (json.JSONDecodeError, AttributeError, TypeError, IndexError):
            return None

    @staticmethod
    def _is_authentication_error(stderr: str) -> bool:
        markers = ("permission denied", "publickey", "authentication failed")
        return any(marker in stderr.lower() for marker in markers)

    @staticmethod
    def _result(
        profile_id: str,
        state: ConnectionState,
        message: str,
        action: str | None,
        profile: ConnectionProfile | None = None,
        transport: str | None = None,
        connected: bool = False,
    ) -> ConnectionValidationResult:
        return ConnectionValidationResult(
            profile_id=profile_id,
            state=state,
            connected=connected,
            profile_type=profile.type if profile else None,
            docker_context=profile.docker_context if profile else None,
            transport=transport,
            capabilities=profile.capabilities if profile else (),
            message=message,
            recommended_action=action,
        )


def default_profile_path() -> Path:
    return Path(__file__).resolve().parents[2] / "profiles.toml"
