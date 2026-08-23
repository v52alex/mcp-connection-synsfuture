"""Validation service for preconfigured Docker connection profiles."""

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .models import (
    ConnectionProfile,
    ConnectionProfileListResult,
    ConnectionProfileSummary,
    ConnectionState,
    ConnectionValidationResult,
    ProfileType,
)
from .process import CommandResult
from .profiles import ProfileRepository, validate_profile_shape

MCP_DOCUMENTATION_HINT = "Más información: consulta la documentación del MCP."


class CommandRunner(Protocol):
    async def run(self, args: list[str], timeout_seconds: float) -> CommandResult: ...


class ConnectionProfileService:
    def __init__(self, runner: CommandRunner, profiles: ProfileRepository, timeout: float = 15.0):
        self._runner = runner
        self._profiles = profiles
        self._timeout = timeout

    @property
    def profiles_path(self) -> Path:
        return self._profiles.path

    @property
    def display_profiles_path(self) -> str:
        return display_profiles_path()

    async def connect(self, profile_id: str) -> ConnectionValidationResult:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return self._result(
                profile_id,
                ConnectionState.PROFILE_NOT_FOUND,
                f"El perfil no existe o no es válido. Archivo autorizado: {self._profiles.path}.",
                "Proporciona profile_id, docker_context y ssh_profile usando "
                "register_connection_profile; el MCP lo registrará automáticamente.",
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

    def register(
        self,
        profile_id: str,
        docker_context: str,
        ssh_profile: str | None,
        capabilities: list[str],
    ) -> ConnectionValidationResult:
        """Register safe local metadata; never stores credentials or keys."""

        try:
            profile = ConnectionProfile(
                profile_id=profile_id,
                type=ProfileType.DOCKER_CONTEXT,
                docker_context=docker_context,
                ssh_profile=ssh_profile or docker_context,
                enabled=True,
                capabilities=tuple(capabilities),
            )
        except ValidationError as error:
            message = error.errors()[0].get("msg", "configuración inválida")
            return self._result(
                profile_id,
                ConnectionState.INVALID_CONFIGURATION,
                f"El perfil no es válido: {message}.",
                "Corrige el identificador y los datos del Docker context.",
            )
        registration_error = self._profiles.register(profile)
        if registration_error == "profile_exists":
            return self._result(
                profile_id,
                ConnectionState.PROFILE_EXISTS,
                "El perfil ya existe y no fue sobrescrito.",
                "Usa otro profile_id o edita la configuración local de forma explícita.",
                profile,
            )
        if registration_error:
            return self._result(
                profile_id,
                ConnectionState.INVALID_CONFIGURATION,
                "No se pudo registrar el perfil local.",
                "Corrige los permisos o el formato del archivo de perfiles.",
                profile,
            )
        return self._result(
            profile_id,
            ConnectionState.PROFILE_REGISTERED,
            "Perfil registrado localmente. El MCP todavía debe validar el contexto y la conexión.",
            self._setup_action(profile),
            profile,
            profiles_file=self.display_profiles_path,
            profile_example=self._profile_example(profile),
        )

    def remove(self, profile_id: str) -> ConnectionValidationResult:
        """Remove only local profile metadata; never delete connection resources."""

        error = self._profiles.remove(profile_id)
        if error == "profile_not_found":
            return self._result(
                profile_id,
                ConnectionState.PROFILE_NOT_FOUND,
                f"El perfil no existe en {self._profiles.path}.",
                "Verifica el profile_id o regístralo con register_connection_profile.",
            )
        if error:
            return self._result(
                profile_id,
                ConnectionState.INVALID_CONFIGURATION,
                "No se pudo eliminar el perfil local porque el archivo no es válido "
                "o no se puede escribir.",
                "Corrige los permisos o el formato TOML del archivo de perfiles.",
            )
        return self._result(
            profile_id,
            ConnectionState.PROFILE_REMOVED,
            "Perfil eliminado del archivo local de perfiles.",
            "El Docker context, el alias SSH, las claves y los recursos remotos "
            "no fueron eliminados.",
        )

    def list_profiles(self) -> ConnectionProfileListResult:
        profiles = tuple(
            ConnectionProfileSummary(
                profile_id=profile.profile_id,
                profile_type=profile.type,
                docker_context=profile.docker_context,
                ssh_profile=profile.ssh_profile,
                enabled=profile.enabled,
                capabilities=profile.capabilities,
            )
            for profile in self._profiles.list_profiles()
        )
        message = (
            "No hay perfiles registrados en el archivo local."
            if not profiles
            else f"Se encontraron {len(profiles)} perfiles registrados."
        )
        return ConnectionProfileListResult(
            profiles=profiles,
            profiles_file=self.display_profiles_path,
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

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

    def _result(
        self,
        profile_id: str,
        state: ConnectionState,
        message: str,
        action: str | None,
        profile: ConnectionProfile | None = None,
        transport: str | None = None,
        connected: bool = False,
        profiles_file: str | None = None,
        profile_example: str | None = None,
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
            documentation_hint=MCP_DOCUMENTATION_HINT,
            profiles_file=profiles_file or self.display_profiles_path,
            profile_example=profile_example,
        )

    @staticmethod
    def _profile_example(profile: ConnectionProfile) -> str:
        capabilities = ", ".join(f'"{item}"' for item in profile.capabilities)
        return (
            f"[profiles.{profile.profile_id}]\n"
            'type = "docker-context"\n'
            f'docker_context = "{profile.docker_context}"\n'
            f'ssh_profile = "{profile.ssh_profile}"\n'
            "enabled = true\n"
            f"capabilities = [{capabilities}]"
        )

    @staticmethod
    def _setup_action(profile: ConnectionProfile) -> str:
        system = platform.system()
        if system == "Windows":
            commands = (
                'PowerShell: ssh "{alias}"',
                'PowerShell: docker context create "{context}" --docker "host=ssh://{alias}"',
            )
        else:
            commands = (
                'ssh "{alias}"',
                'docker context create "{context}" --docker "host=ssh://{alias}"',
            )
        command_text = " && ".join(
            command.format(alias=profile.ssh_profile, context=profile.docker_context)
            for command in commands
        )
        return (
            f"Plataforma detectada: {system or 'desconocida'}. Configura el alias SSH "
            f"'{profile.ssh_profile}' y ejecuta: {command_text}. Después vuelve a "
            "ejecutar connect_connection_profile. Consulta docs/PROFILE_SETUP.md."
        )


def default_profile_path() -> Path:
    config_root = os.environ.get("XDG_CONFIG_HOME")
    if config_root:
        return Path(config_root) / "mcp-connection-synsfuture" / "profiles.toml"
    return Path.home() / ".config" / "mcp-connection-synsfuture" / "profiles.toml"


def display_profiles_path() -> str:
    """Return a platform-generic path without exposing the local username."""

    if os.name == "nt":
        return r"%USERPROFILE%\.config\mcp-connection-synsfuture\profiles.toml"
    return "$HOME/.config/mcp-connection-synsfuture/profiles.toml"
