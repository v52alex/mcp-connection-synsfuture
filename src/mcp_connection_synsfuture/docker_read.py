"""Read-only Docker operations resolved through an authorized profile."""

import json
import re
from typing import Any

from .models import ConnectionState, DockerReadResult
from .service import MCP_DOCUMENTATION_HINT, CommandRunner, ConnectionProfileService

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
IMAGE_PATTERN = re.compile(
    r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[0-9]+)?/)*"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?$"
)


class DockerReadService:
    def __init__(self, runner: CommandRunner, connections: ConnectionProfileService):
        self._runner = runner
        self._connections = connections
        self._timeout = 30.0

    async def list_images(self, profile_id: str) -> DockerReadResult:
        return await self._json_lines(
            profile_id, "list_images", ["image", "ls", "--format", "{{json .}}"]
        )

    async def inspect_image(self, profile_id: str, image_reference: str) -> DockerReadResult:
        if not IMAGE_PATTERN.fullmatch(image_reference):
            raise ValueError("image_reference has an invalid format")
        result = await self._run(profile_id, "inspect_image", ["image", "inspect", image_reference])
        if not result.connected or not result.records:
            return result
        return result.model_copy(update={"records": [self._sanitize_image(result.records[0])]})

    async def list_containers(self, profile_id: str) -> DockerReadResult:
        return await self._json_lines(
            profile_id, "list_containers", ["container", "ls", "--all", "--format", "{{json .}}"]
        )

    async def inspect_container(self, profile_id: str, container_name: str) -> DockerReadResult:
        self._validate_name(container_name)
        result = await self._run(
            profile_id, "inspect_container", ["container", "inspect", container_name]
        )
        if not result.connected or not result.records:
            return result
        return result.model_copy(update={"records": [self._sanitize_container(result.records[0])]})

    async def logs(self, profile_id: str, container_name: str, tail: int = 100) -> DockerReadResult:
        self._validate_name(container_name)
        if not 1 <= tail <= 500:
            raise ValueError("tail must be between 1 and 500")
        result = await self._run(
            profile_id, "container_logs", ["container", "logs", "--tail", str(tail), container_name]
        )
        if not result.connected:
            return result
        if not result.lines:
            return result
        lines = [self._redact(line)[:2000] for line in result.lines[-tail:]]
        return result.model_copy(update={"lines": lines})

    async def _json_lines(
        self, profile_id: str, operation: str, command: list[str]
    ) -> DockerReadResult:
        result = await self._run(profile_id, operation, command)
        if not result.connected:
            return result
        return result

    async def _run(self, profile_id: str, operation: str, command: list[str]) -> DockerReadResult:
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                operation,
                profile_id,
                False,
                f"El perfil no está listo: {connection.message}",
            )
        try:
            result = await self._runner.run(
                ["docker", "--context", connection.docker_context, *command], self._timeout
            )
        except TimeoutError:
            return self._result(
                operation, profile_id, True, "La operación Docker agotó el tiempo de espera."
            )
        if result.returncode != 0:
            return self._result(
                operation,
                profile_id,
                True,
                "Docker no pudo completar la operación de lectura.",
            )
        if operation in {"list_images", "list_containers", "inspect_image", "inspect_container"}:
            records = self._parse_json_records(result.stdout)
            return self._result(
                operation,
                profile_id,
                True,
                f"Se obtuvieron {len(records)} registro(s).",
                records,
            )
        return self._result(
            operation,
            profile_id,
            True,
            "Logs recuperados como datos no confiables.",
            lines=(result.stdout + "\n" + result.stderr).strip().splitlines(),
        )

    @staticmethod
    def _parse_json_records(raw: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
            elif isinstance(payload, list):
                records.extend(item for item in payload if isinstance(item, dict))
        return records

    @staticmethod
    def _sanitize_image(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: payload.get(key)
            for key in ("Id", "RepoTags", "RepoDigests", "Created", "Size", "Architecture", "Os")
            if key in payload
        }

    @staticmethod
    def _sanitize_container(payload: dict[str, Any]) -> dict[str, Any]:
        state = payload.get("State")
        config = payload.get("Config")
        return {
            "Id": payload.get("Id"),
            "Name": payload.get("Name"),
            "Created": payload.get("Created"),
            "State": {key: state.get(key) for key in ("Status", "Running", "ExitCode")}
            if isinstance(state, dict)
            else None,
            "Image": config.get("Image") if isinstance(config, dict) else None,
        }

    @staticmethod
    def _redact(line: str) -> str:
        return re.sub(
            r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)",
            r"\1\2[REDACTED]",
            line,
        )

    @staticmethod
    def _validate_name(name: str) -> None:
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError("container_name has an invalid format")

    @staticmethod
    def _result(
        operation: str,
        profile_id: str,
        connected: bool,
        message: str,
        records: list[dict[str, Any]] | None = None,
        lines: list[str] | None = None,
    ) -> DockerReadResult:
        return DockerReadResult(
            operation=operation,
            profile_id=profile_id,
            connected=connected,
            records=records or [],
            lines=lines or [],
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
