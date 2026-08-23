"""Controlled Docker container mutations scoped to an authorized profile."""

import json
import re

from .docker_read import IMAGE_PATTERN, NAME_PATTERN
from .models import ConnectionState, DockerMutationResult
from .service import MCP_DOCUMENTATION_HINT, CommandRunner, ConnectionProfileService

MANAGED_LABEL = "com.synsfuture.mcp.managed"
SENSITIVE_KEYS = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "PRIVATE", "API_KEY")
ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class DockerWriteService:
    def __init__(self, runner: CommandRunner, connections: ConnectionProfileService):
        self._runner = runner
        self._connections = connections
        self._timeout = 60.0

    async def create(
        self,
        profile_id: str,
        image_reference: str,
        container_name: str,
        environment: dict[str, str] | None,
        dry_run: bool,
        confirmation: str | None,
    ) -> DockerMutationResult:
        self._validate_image(image_reference)
        self._validate_name(container_name)
        safe_environment = self._validate_environment(environment or {})
        command = [
            "container",
            "run",
            "-d",
            "--label",
            f"{MANAGED_LABEL}=true",
            "--name",
            container_name,
        ]
        preview = ["docker", "--context", "<profile-context>", *command]
        for key in safe_environment:
            command.extend(["--env", f"{key}={safe_environment[key]}"])
            preview.extend(["--env", f"{key}=[REDACTED]"])
        command.append(image_reference)
        preview.append(image_reference)
        if dry_run or confirmation != "CONFIRM_CREATE":
            return self._result(
                "create_container",
                profile_id,
                "planned",
                False,
                container_name,
                preview,
                "Creación planificada. Usa confirmation='CONFIRM_CREATE' y "
                "dry_run=false para ejecutar.",
            )
        return await self._execute(
            profile_id, "create_container", container_name, command, "created", preview
        )

    async def lifecycle(
        self, profile_id: str, operation: str, container_name: str, confirmation: str | None
    ) -> DockerMutationResult:
        self._validate_name(container_name)
        expected = f"CONFIRM_{operation.upper()}"
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                operation,
                profile_id,
                "connection_failed",
                False,
                container_name,
                [],
                f"El perfil no está listo: {connection.message}",
            )
        inspect = await self._runner.run(
            [
                "docker",
                "--context",
                connection.docker_context,
                "container",
                "inspect",
                container_name,
                "--format",
                "{{json .Config.Labels}}",
            ],
            self._timeout,
        )
        if inspect.returncode != 0:
            return self._result(
                operation,
                profile_id,
                "not_found",
                False,
                container_name,
                [],
                "El contenedor no existe.",
            )
        if not self._is_managed(inspect.stdout):
            return self._result(
                operation,
                profile_id,
                "validation_failed",
                False,
                container_name,
                [],
                "El contenedor no está gestionado por este MCP; no se modificó.",
            )
        command = ["container", operation, container_name]
        preview = ["docker", "--context", "<profile-context>", *command]
        if confirmation != expected:
            return self._result(
                operation,
                profile_id,
                "planned",
                False,
                container_name,
                preview,
                f"Operación planificada. Usa confirmation='{expected}' para ejecutar.",
            )
        states = {"start": "started", "stop": "stopped", "restart": "restarted", "rm": "removed"}
        return await self._execute(
            profile_id, operation, container_name, command, states.get(operation, operation)
        )

    async def _execute(
        self,
        profile_id: str,
        operation: str,
        target: str,
        command: list[str],
        state: str,
        preview: list[str] | None = None,
    ) -> DockerMutationResult:
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                operation, profile_id, "connection_failed", False, target, [], connection.message
            )
        try:
            result = await self._runner.run(
                ["docker", "--context", connection.docker_context, *command], self._timeout
            )
        except TimeoutError:
            return self._result(
                operation,
                profile_id,
                "operation_failed",
                False,
                target,
                preview or command,
                "La operación agotó el tiempo de espera.",
            )
        if result.returncode != 0:
            return self._result(
                operation,
                profile_id,
                "operation_failed",
                False,
                target,
                preview or command,
                "Docker rechazó la operación.",
            )
        return self._result(
            operation,
            profile_id,
            state,
            True,
            target,
            preview or command,
            "Operación ejecutada correctamente.",
        )

    @staticmethod
    def _is_managed(raw: str) -> bool:
        try:
            labels = json.loads(raw)
        except ValueError:
            return False
        return isinstance(labels, dict) and labels.get(MANAGED_LABEL) == "true"

    @staticmethod
    def _validate_name(name: str) -> None:
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError("container_name has an invalid format")

    @staticmethod
    def _validate_image(image: str) -> None:
        if not IMAGE_PATTERN.fullmatch(image):
            raise ValueError("image_reference has an invalid format")

    @staticmethod
    def _validate_environment(environment: dict[str, str]) -> dict[str, str]:
        for key in environment:
            if not ENV_KEY_PATTERN.fullmatch(key) or any(
                marker in key for marker in SENSITIVE_KEYS
            ):
                raise ValueError(f"environment key is not allowed: {key}")
        return environment

    @staticmethod
    def _result(
        operation: str,
        profile_id: str,
        state: str,
        executed: bool,
        target: str,
        preview: list[str],
        message: str,
    ) -> DockerMutationResult:
        return DockerMutationResult(
            operation=operation,
            profile_id=profile_id,
            state=state,
            executed=executed,
            target=target,
            command_preview=preview,
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
