"""MCP server exposing explicit connection-profile validation."""

import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .models import ConnectionProfileListResult, ConnectionState, ConnectionValidationResult
from .process import ProcessRunner
from .profiles import ProfileRepository
from .service import ConnectionProfileService, default_profile_path

mcp = MCPServer(
    "mcp-connection-synsfuture",
    instructions=(
    "Manage local connection-profile metadata and validate preconfigured contexts. "
    "The MCP never creates SSH keys, modifies SSH aliases, or changes remote infrastructure. "
        "mentions only this MCP without naming a tool, explain that no tool was "
        "selected and show this example: "
        "mcp-connection-synsfuture.connect_connection_profile(profile_id='docker-remote1'). "
        "Available tools are connect_connection_profile, register_connection_profile, "
        "remove_connection_profile, and list_connection_profiles. "
        "profile_id. Never infer profile_id from previous messages or select a profile "
        "automatically; ask the user for the profile identifier when it is absent from "
        "the latest request. For real Codex requests, invoke the MCP tool directly and "
        "do not run client.py, docker CLI, or shell commands as a substitute; client.py "
        "is only for local development tests."
    ),
)

READ_ONLY_EXTERNAL = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

LOCAL_PROFILE_WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)


def create_service() -> ConnectionProfileService:
    configured_path = os.getenv("MCP_CONNECTION_PROFILES_FILE")
    path = Path(configured_path).expanduser() if configured_path else default_profile_path()
    return ConnectionProfileService(ProcessRunner(), ProfileRepository(path))


@mcp.tool(name="connect_connection_profile", annotations=READ_ONLY_EXTERNAL)
async def connect_connection_profile(profile_id: str | None = None) -> ConnectionValidationResult:
    """Validate one preconfigured profile; profile_id is its connection-context name."""

    service = create_service()
    if not profile_id:
        return ConnectionValidationResult(
            profile_id=None,
            state=ConnectionState.PROFILE_REQUIRED,
            connected=False,
            message=(
                "Falta el parámetro profile_id. Es el identificador del perfil de conexión "
                "preconfigurado que representa el Docker context o perfil SSH que deseas "
                "validar. Ejemplo: profile_id=\"docker-remote1\"."
            ),
            recommended_action=(
                "Indica un profile_id explícito o usa register_connection_profile para "
                "registrarlo. Para más información, consulta README.md y "
                "docs/PROFILE_SETUP.md del proyecto."
            ),
            documentation_hint="Consulta README.md y docs/PROFILE_SETUP.md.",
            profiles_file=service.display_profiles_path,
        )
    return await service.connect(profile_id)


@mcp.tool(name="register_connection_profile", annotations=LOCAL_PROFILE_WRITE)
async def register_connection_profile(
    profile_id: str,
    docker_context: str,
    ssh_profile: str | None = None,
    capabilities: list[str] | None = None,
) -> ConnectionValidationResult:
    """Register safe Docker context metadata in the local authorized profile file."""

    return create_service().register(
        profile_id=profile_id,
        docker_context=docker_context,
        ssh_profile=ssh_profile,
        capabilities=capabilities or ["read"],
    )


@mcp.tool(name="remove_connection_profile", annotations=LOCAL_PROFILE_WRITE)
async def remove_connection_profile(profile_id: str) -> ConnectionValidationResult:
    """Remove one local profile entry without deleting SSH or Docker resources."""

    return create_service().remove(profile_id)


@mcp.tool(name="list_connection_profiles", annotations=READ_ONLY_EXTERNAL)
async def list_connection_profiles() -> ConnectionProfileListResult:
    """List sanitized metadata for locally authorized connection profiles."""

    return create_service().list_profiles()


def run_server() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
