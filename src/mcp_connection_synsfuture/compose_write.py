"""Controlled Docker Compose mutations scoped to an authorized profile."""

from pathlib import Path

from .compose_read import ComposeReadService
from .models import ComposeMutationResult, ConnectionState
from .service import MCP_DOCUMENTATION_HINT, CommandRunner, ConnectionProfileService


class ComposeWriteService:
    def __init__(self, runner: CommandRunner, connections: ConnectionProfileService):
        self._runner = runner
        self._connections = connections
        self._timeout = 900.0

    async def operation(
        self,
        profile_id: str,
        operation: str,
        project_path: str,
        compose_file: str | None,
        confirmation: str | None,
        dry_run: bool = False,
    ) -> ComposeMutationResult:
        path = ComposeReadService._compose_path(project_path, compose_file)
        commands = {
            "up": ["up", "-d"],
            "stop": ["stop"],
            "restart": ["restart"],
            "down": ["down", "--volumes=false"],
        }
        if operation not in commands:
            raise ValueError("unsupported compose operation")
        command = ["compose", "-f", str(path), *commands[operation]]
        confirmation_token = f"CONFIRM_COMPOSE_{operation.upper()}"
        preview = ["docker", "--context", "<profile-context>", *command]
        if dry_run or confirmation != confirmation_token:
            return self._result(
                operation,
                profile_id,
                path,
                "planned",
                False,
                preview,
                f"Operación planificada. Usa confirmation='{confirmation_token}' para ejecutar.",
            )
        return await self._execute(profile_id, operation, path, command, preview)

    async def audit(
        self, profile_id: str, project_path: str, compose_file: str | None
    ) -> ComposeMutationResult:
        path = ComposeReadService._compose_path(project_path, compose_file)
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                "audit", profile_id, path, "connection_failed", False, [], connection.message
            )
        command = ["compose", "-f", str(path), "config", "--quiet"]
        result = await self._runner.run(
            ["docker", "--context", connection.docker_context, *command], self._timeout
        )
        state = "ready" if result.returncode == 0 else "validation_failed"
        message = (
            "La configuración Compose es válida."
            if result.returncode == 0
            else "La configuración Compose no es válida."
        )
        preview = ["docker", "--context", "<profile-context>", *command]
        return self._result("audit", profile_id, path, state, False, preview, message)

    async def _execute(
        self, profile_id: str, operation: str, path: Path, command: list[str], preview: list[str]
    ) -> ComposeMutationResult:
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                operation, profile_id, path, "connection_failed", False, preview, connection.message
            )
        try:
            result = await self._runner.run(
                ["docker", "--context", connection.docker_context, *command], self._timeout
            )
        except TimeoutError:
            return self._result(
                operation,
                profile_id,
                path,
                "operation_failed",
                False,
                preview,
                "La operación Compose agotó el tiempo de espera.",
            )
        if result.returncode != 0:
            return self._result(
                operation,
                profile_id,
                path,
                "operation_failed",
                False,
                preview,
                "Compose rechazó la operación.",
            )
        return self._result(
            operation,
            profile_id,
            path,
            operation,
            True,
            preview,
            "Operación Compose ejecutada correctamente.",
        )

    @staticmethod
    def _result(
        operation: str,
        profile_id: str,
        path: Path,
        state: str,
        executed: bool,
        preview: list[str],
        message: str,
    ) -> ComposeMutationResult:
        return ComposeMutationResult(
            operation=operation,
            profile_id=profile_id,
            project_path=str(path.parent),
            state=state,
            executed=executed,
            command_preview=preview,
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
