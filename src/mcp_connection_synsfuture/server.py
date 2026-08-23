"""MCP server exposing explicit connection-profile validation."""

import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .models import ConnectionState, ConnectionValidationResult
from .process import ProcessRunner
from .profiles import ProfileRepository
from .service import ConnectionProfileService, default_profile_path

mcp = MCPServer(
    "mcp-connection-synsfuture",
    instructions=(
        "Validate only preconfigured connection profiles. The MCP never creates or "
        "modifies contexts, SSH aliases, keys, or remote infrastructure. If the user "
        "mentions only this MCP without naming a tool, explain that no tool was "
        "selected and show this example: "
        "mcp-connection-synsfuture.connect_connection_profile(profile_id='docker-remote1'). "
        "The available tool is connect_connection_profile and it requires an explicit "
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


def create_service() -> ConnectionProfileService:
    configured_path = os.getenv("MCP_CONNECTION_PROFILES_FILE")
    path = Path(configured_path).expanduser() if configured_path else default_profile_path()
    return ConnectionProfileService(ProcessRunner(), ProfileRepository(path))


@mcp.tool(name="connect_connection_profile", annotations=READ_ONLY_EXTERNAL)
async def connect_connection_profile(profile_id: str | None = None) -> ConnectionValidationResult:
    """Validate one preconfigured profile; profile_id is its connection-context name."""

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
                "Indica un profile_id explícito. Para más información, consulta "
                "README.md y docs/PROFILE_SETUP.md del proyecto."
            ),
            documentation_hint="Consulta README.md y docs/PROFILE_SETUP.md.",
        )
    return await create_service().connect(profile_id)


def run_server() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
