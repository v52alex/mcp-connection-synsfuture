"""Read-only Docker Compose operations scoped to an authorized profile."""

import json
import re
from pathlib import Path
from typing import Any

from .models import ComposeReadResult, ConnectionState
from .service import MCP_DOCUMENTATION_HINT, CommandRunner, ConnectionProfileService


class ComposeReadService:
    def __init__(self, runner: CommandRunner, connections: ConnectionProfileService):
        self._runner = runner
        self._connections = connections
        self._timeout = 60.0

    async def inspect(
        self, profile_id: str, project_path: str, compose_file: str | None, env_file: str | None
    ) -> ComposeReadResult:
        path = self._compose_path(project_path, compose_file)
        result = await self._run(
            profile_id, "inspect_compose_project", path, ["config", "--format", "json"], env_file
        )
        if not result.connected:
            return result
        services = sorted(result.records[0].get("services", {}).keys()) if result.records else []
        return result.model_copy(
            update={"services": services, "records": [{"services": services}]}
        )

    async def ps(
        self, profile_id: str, project_path: str, compose_file: str | None, env_file: str | None
    ) -> ComposeReadResult:
        path = self._compose_path(project_path, compose_file)
        return await self._run(profile_id, "compose_ps", path, ["ps", "--format", "json"], env_file)

    async def logs(
        self,
        profile_id: str,
        project_path: str,
        compose_file: str | None,
        tail: int,
        env_file: str | None,
    ) -> ComposeReadResult:
        if not 1 <= tail <= 500:
            raise ValueError("tail must be between 1 and 500")
        path = self._compose_path(project_path, compose_file)
        result = await self._run(
            profile_id, "compose_logs", path, ["logs", "--tail", str(tail)], env_file
        )
        return result.model_copy(
            update={"lines": [self._redact(line)[:2000] for line in result.lines[-tail:]]}
        )

    async def _run(
        self, profile_id: str, operation: str, path: Path, command: list[str], env_file: str | None
    ) -> ComposeReadResult:
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                operation,
                profile_id,
                path,
                False,
                f"El perfil no está listo: {connection.message}",
            )
        try:
            result = await self._runner.run(
                self._docker_command(connection.docker_context, path, command, env_file),
                self._timeout,
            )
        except TimeoutError:
            return self._result(
                operation,
                profile_id,
                path,
                True,
                "La operación Compose agotó el tiempo de espera.",
            )
        if result.returncode != 0:
            return self._result(
                operation,
                profile_id,
                path,
                True,
                "Compose no pudo completar la operación de lectura.",
            )
        records = self._records(result.stdout) if command[0] != "logs" else []
        lines = [] if records else (result.stdout + "\n" + result.stderr).strip().splitlines()
        return self._result(
            operation,
            profile_id,
            path,
            True,
            "Información Compose recuperada.",
            records,
            lines,
        )

    @staticmethod
    def _compose_path(project_path: str, compose_file: str | None) -> Path:
        project = Path(project_path).expanduser().resolve()
        if not project.is_dir():
            raise ValueError("project_path must be an existing directory")
        path = (project / compose_file).resolve() if compose_file else next(
            (
                project / name
                for name in ("compose.yaml", "compose.yml", "docker-compose.yml")
                if (project / name).is_file()
            ),
            None,
        )
        if path is None or not path.is_file() or project not in path.parents:
            raise ValueError("compose_file must be inside project_path")
        return path

    @staticmethod
    def _docker_command(
        context: str, path: Path, command: list[str], env_file: str | None
    ) -> list[str]:
        args = ["docker", "--context", context, "compose"]
        if env_file:
            env_path = Path(env_file).expanduser().resolve()
            if not env_path.is_file():
                raise ValueError("env_file must be an existing file")
            args.extend(["--env-file", str(env_path)])
        return [*args, "-f", str(path), *command]

    @staticmethod
    def _records(raw: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            return [
                {key: value for key, value in payload.items() if key not in {"Environment", "env"}}
            ]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _redact(line: str) -> str:
        return re.sub(
            r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)",
            r"\1\2[REDACTED]",
            line,
        )

    @staticmethod
    def _result(
        operation: str,
        profile_id: str,
        path: Path,
        connected: bool,
        message: str,
        records: list[dict[str, Any]] | None = None,
        lines: list[str] | None = None,
    ) -> ComposeReadResult:
        return ComposeReadResult(
            operation=operation,
            profile_id=profile_id,
            project_path=str(path.parent),
            connected=connected,
            records=records or [],
            lines=lines or [],
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
