"""MCP server exposing explicit connection-profile validation."""

import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .docker_read import DockerReadService
from .docker_write import DockerWriteService
from .models import (
    ConnectionProfileListResult,
    ConnectionState,
    ConnectionValidationResult,
    DockerMutationResult,
    DockerReadResult,
    ProfileType,
)
from .process import ProcessRunner
from .profiles import ProfileRepository
from .service import MCP_DOCUMENTATION_HINT, ConnectionProfileService, default_profile_path

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

REMOTE_MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

REMOTE_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


def create_service() -> ConnectionProfileService:
    configured_path = os.getenv("MCP_CONNECTION_PROFILES_FILE")
    path = Path(configured_path).expanduser() if configured_path else default_profile_path()
    return ConnectionProfileService(ProcessRunner(), ProfileRepository(path))


def create_docker_read_service() -> DockerReadService:
    return DockerReadService(ProcessRunner(), create_service())


def create_docker_write_service() -> DockerWriteService:
    return DockerWriteService(ProcessRunner(), create_service())


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
            documentation_hint=MCP_DOCUMENTATION_HINT,
            profiles_file=service.display_profiles_path,
        )
    return await service.connect(profile_id)


@mcp.tool(name="register_connection_profile", annotations=LOCAL_PROFILE_WRITE)
async def register_connection_profile(
    profile_id: str,
    docker_context: str | None = None,
    ssh_profile: str | None = None,
    profile_type: str = "docker-context",
    capabilities: list[str] | None = None,
) -> ConnectionValidationResult:
    """Register safe Docker or generic SSH profile metadata locally."""

    try:
        selected_type = ProfileType(profile_type)
    except ValueError:
        return ConnectionValidationResult(
            profile_id=profile_id,
            state=ConnectionState.INVALID_CONFIGURATION,
            message=f"Tipo de perfil no soportado: {profile_type}.",
            recommended_action="Usa profile_type='docker-context' o profile_type='ssh-profile'.",
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

    return create_service().register(
        profile_id=profile_id,
        docker_context=docker_context,
        ssh_profile=ssh_profile,
        profile_type=selected_type,
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


@mcp.tool(name="list_images_docker", annotations=READ_ONLY_EXTERNAL)
async def list_images_docker(profile_id: str) -> DockerReadResult:
    """List sanitized images through an authorized Docker context."""

    return await create_docker_read_service().list_images(profile_id)


@mcp.tool(name="inspect_image_docker", annotations=READ_ONLY_EXTERNAL)
async def inspect_image_docker(profile_id: str, image_reference: str) -> DockerReadResult:
    """Inspect an image without returning environment variables or history."""

    return await create_docker_read_service().inspect_image(profile_id, image_reference)


@mcp.tool(name="list_containers_docker", annotations=READ_ONLY_EXTERNAL)
async def list_containers_docker(profile_id: str) -> DockerReadResult:
    """List sanitized containers through an authorized Docker context."""

    return await create_docker_read_service().list_containers(profile_id)


@mcp.tool(name="inspect_container_docker", annotations=READ_ONLY_EXTERNAL)
async def inspect_container_docker(profile_id: str, container_name: str) -> DockerReadResult:
    """Inspect safe container state without environment variables or mounts."""

    return await create_docker_read_service().inspect_container(profile_id, container_name)


@mcp.tool(name="container_logs_docker", annotations=READ_ONLY_EXTERNAL)
async def container_logs_docker(
    profile_id: str, container_name: str, tail: int = 100
) -> DockerReadResult:
    """Return bounded, redacted logs as untrusted data."""

    return await create_docker_read_service().logs(profile_id, container_name, tail)


@mcp.tool(name="create_container_docker", annotations=REMOTE_MUTATION)
async def create_container_docker(
    profile_id: str,
    image_reference: str,
    container_name: str,
    environment: dict[str, str] | None = None,
    dry_run: bool = True,
    confirmation: str | None = None,
) -> DockerMutationResult:
    """Plan or create a managed container after explicit confirmation."""

    return await create_docker_write_service().create(
        profile_id, image_reference, container_name, environment, dry_run, confirmation
    )


@mcp.tool(name="start_container_docker", annotations=REMOTE_MUTATION)
async def start_container_docker(
    profile_id: str, container_name: str, confirmation: str | None = None
) -> DockerMutationResult:
    """Start only an MCP-managed container after explicit confirmation."""

    return await create_docker_write_service().lifecycle(
        profile_id, "start", container_name, confirmation
    )


@mcp.tool(name="stop_container_docker", annotations=REMOTE_MUTATION)
async def stop_container_docker(
    profile_id: str, container_name: str, confirmation: str | None = None
) -> DockerMutationResult:
    """Stop only an MCP-managed container after explicit confirmation."""

    return await create_docker_write_service().lifecycle(
        profile_id, "stop", container_name, confirmation
    )


@mcp.tool(name="restart_container_docker", annotations=REMOTE_MUTATION)
async def restart_container_docker(
    profile_id: str, container_name: str, confirmation: str | None = None
) -> DockerMutationResult:
    """Restart only an MCP-managed container after explicit confirmation."""

    return await create_docker_write_service().lifecycle(
        profile_id, "restart", container_name, confirmation
    )


@mcp.tool(name="remove_container_docker", annotations=REMOTE_DESTRUCTIVE)
async def remove_container_docker(
    profile_id: str, container_name: str, confirmation: str | None = None
) -> DockerMutationResult:
    """Remove only an MCP-managed container after explicit confirmation."""

    return await create_docker_write_service().lifecycle(
        profile_id, "rm", container_name, confirmation
    )


def run_server() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
