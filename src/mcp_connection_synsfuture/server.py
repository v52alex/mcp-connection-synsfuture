"""MCP server exposing explicit connection-profile validation."""

import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .compose_read import ComposeReadService
from .compose_write import ComposeWriteService
from .docker_build import DockerBuildService
from .docker_pull import DockerPullService
from .docker_read import DockerReadService
from .docker_write import DockerWriteService
from .kind import KindService
from .models import (
    AuditEventListResult,
    ComposeMutationResult,
    ComposeReadResult,
    ConnectionProfileListResult,
    ConnectionState,
    ConnectionValidationResult,
    DockerBuildResult,
    DockerMutationResult,
    DockerProjectInspectionResult,
    DockerReadResult,
    KindClusterCreateResult,
    KindClusterInspectResult,
    KindClusterListResult,
    KindImageLoadResult,
    KindNamespaceEnsureResult,
    KindPrerequisitesResult,
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
        "is only for local development tests. Kind/Kubernetes operations always begin "
        "with list_kind_clusters and never select a cluster implicitly. For a requested "
        "cluster creation, if list_kind_clusters already reports the requested cluster "
        "as reachable, stop and return that result; do not call create_kind_cluster. "
        "Call create_kind_cluster only when the requested cluster is absent."
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


def create_docker_pull_service() -> DockerPullService:
    return DockerPullService(ProcessRunner(), create_service())


def create_compose_read_service() -> ComposeReadService:
    return ComposeReadService(ProcessRunner(), create_service())


def create_compose_write_service() -> ComposeWriteService:
    return ComposeWriteService(ProcessRunner(), create_service())


def create_docker_build_service() -> DockerBuildService:
    return DockerBuildService(ProcessRunner(), create_service())


def create_kind_service() -> KindService:
    connections = create_service()
    return KindService(
        ProcessRunner(),
        ProfileRepository(connections.profiles_path),
        connections,
    )


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


@mcp.tool(name="check_connection_docker", annotations=READ_ONLY_EXTERNAL)
async def check_connection_docker(profile_id: str) -> ConnectionValidationResult:
    """Compatibility alias for profile-scoped Docker connection validation."""

    return await create_service().connect(profile_id)


@mcp.tool(name="enable_connection_docker", annotations=READ_ONLY_EXTERNAL)
async def enable_connection_docker(profile_id: str) -> ConnectionValidationResult:
    """Compatibility alias; connection preparation is handled by the profile flow."""

    return await create_service().connect(profile_id)


@mcp.tool(name="connect_vps_codex", annotations=READ_ONLY_EXTERNAL)
async def connect_vps_codex(profile_id: str) -> ConnectionValidationResult:
    """Compatibility alias for a profile-scoped generic SSH/VPS connection."""

    return await create_service().connect(profile_id)


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


@mcp.tool(name="list_kind_clusters", annotations=READ_ONLY_EXTERNAL)
async def list_kind_clusters(profile_id: str) -> KindClusterListResult:
    """List remote Kind clusters and recommend an accessible cluster."""

    return await create_kind_service().list_clusters(profile_id)


@mcp.tool(name="create_kind_cluster", annotations=REMOTE_MUTATION)
async def create_kind_cluster(
    profile_id: str,
    cluster_name: str,
    dry_run: bool = True,
    confirmation: str | None = None,
) -> KindClusterCreateResult:
    """Plan or create a remote Kind cluster after explicit confirmation.

    Callers must list clusters first and must not invoke this tool when the
    requested cluster is already present and reachable.
    """

    return await create_kind_service().create_cluster(
        profile_id, cluster_name, dry_run, confirmation
    )


@mcp.tool(name="check_kind_prerequisites", annotations=READ_ONLY_EXTERNAL)
async def check_kind_prerequisites(profile_id: str) -> KindPrerequisitesResult:
    """Check kind, kubectl and Docker availability on the remote host."""

    return await create_kind_service().check_prerequisites(profile_id)


@mcp.tool(name="inspect_kind_cluster", annotations=READ_ONLY_EXTERNAL)
async def inspect_kind_cluster(
    profile_id: str, cluster_name: str, namespace: str | None = None
) -> KindClusterInspectResult:
    """Inspect nodes and namespace for a cluster returned by list_kind_clusters."""

    return await create_kind_service().inspect_cluster(profile_id, cluster_name, namespace)


@mcp.tool(name="ensure_kind_namespace", annotations=REMOTE_MUTATION)
async def ensure_kind_namespace(
    profile_id: str,
    cluster_name: str,
    namespace: str,
    dry_run: bool = True,
    confirmation: str | None = None,
) -> KindNamespaceEnsureResult:
    """Plan or create a namespace on an existing Kind cluster."""

    return await create_kind_service().ensure_namespace(
        profile_id, cluster_name, namespace, dry_run, confirmation
    )


@mcp.tool(name="load_images_to_kind", annotations=REMOTE_MUTATION)
async def load_images_to_kind(
    profile_id: str,
    cluster_name: str,
    image_reference: str,
    dry_run: bool = True,
    confirmation: str | None = None,
) -> KindImageLoadResult:
    """Plan or load a remote Docker image into a selected Kind cluster."""

    return await create_kind_service().load_image(
        profile_id, cluster_name, image_reference, dry_run, confirmation
    )


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


@mcp.tool(name="pull_image_docker", annotations=REMOTE_MUTATION)
async def pull_image_docker(
    profile_id: str,
    image_reference: str,
    dry_run: bool = True,
    confirmation: str | None = None,
) -> DockerMutationResult:
    """Plan or pull a remote Docker image after explicit confirmation."""

    return await create_docker_pull_service().pull(
        profile_id, image_reference, dry_run, confirmation
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


@mcp.tool(name="inspect_compose_project_docker", annotations=READ_ONLY_EXTERNAL)
async def inspect_compose_project_docker(
    profile_id: str, project_path: str, compose_file: str | None = None, env_file: str | None = None
) -> ComposeReadResult:
    """Inspect Compose service names without returning secret values."""

    return await create_compose_read_service().inspect(
        profile_id, project_path, compose_file, env_file
    )


@mcp.tool(name="compose_ps_docker", annotations=READ_ONLY_EXTERNAL)
async def compose_ps_docker(
    profile_id: str, project_path: str, compose_file: str | None = None, env_file: str | None = None
) -> ComposeReadResult:
    """List Compose runtime metadata through an authorized profile."""

    return await create_compose_read_service().ps(profile_id, project_path, compose_file, env_file)


@mcp.tool(name="compose_logs_docker", annotations=READ_ONLY_EXTERNAL)
async def compose_logs_docker(
    profile_id: str, project_path: str, compose_file: str | None = None, tail: int = 100,
    env_file: str | None = None
) -> ComposeReadResult:
    """Return bounded and redacted Compose logs as untrusted data."""

    return await create_compose_read_service().logs(
        profile_id, project_path, compose_file, tail, env_file
    )


@mcp.tool(name="compose_up_docker", annotations=REMOTE_MUTATION)
async def compose_up_docker(
    profile_id: str,
    project_path: str,
    compose_file: str | None = None,
    env_file: str | None = None,
    dry_run: bool = True,
    confirmation: str | None = None,
) -> ComposeMutationResult:
    """Plan or start a Compose project after explicit confirmation."""

    return await create_compose_write_service().operation(
        profile_id, "up", project_path, compose_file, env_file, confirmation, dry_run
    )


@mcp.tool(name="compose_stop_docker", annotations=REMOTE_MUTATION)
async def compose_stop_docker(
    profile_id: str,
    project_path: str,
    compose_file: str | None = None,
    env_file: str | None = None,
    confirmation: str | None = None,
) -> ComposeMutationResult:
    """Stop a Compose project after explicit confirmation."""

    return await create_compose_write_service().operation(
        profile_id, "stop", project_path, compose_file, env_file, confirmation
    )


@mcp.tool(name="compose_restart_docker", annotations=REMOTE_MUTATION)
async def compose_restart_docker(
    profile_id: str,
    project_path: str,
    compose_file: str | None = None,
    env_file: str | None = None,
    confirmation: str | None = None,
) -> ComposeMutationResult:
    """Restart a Compose project after explicit confirmation."""

    return await create_compose_write_service().operation(
        profile_id, "restart", project_path, compose_file, env_file, confirmation
    )


@mcp.tool(name="compose_down_docker", annotations=REMOTE_DESTRUCTIVE)
async def compose_down_docker(
    profile_id: str,
    project_path: str,
    compose_file: str | None = None,
    env_file: str | None = None,
    confirmation: str | None = None,
) -> ComposeMutationResult:
    """Remove Compose containers and networks while preserving volumes."""

    return await create_compose_write_service().operation(
        profile_id, "down", project_path, compose_file, env_file, confirmation
    )


@mcp.tool(name="audit_compose_project_docker", annotations=READ_ONLY_EXTERNAL)
async def audit_compose_project_docker(
    profile_id: str, project_path: str, compose_file: str | None = None, env_file: str | None = None
) -> ComposeMutationResult:
    """Validate Compose configuration without mutation."""

    return await create_compose_write_service().audit(
        profile_id, project_path, compose_file, env_file
    )


@mcp.tool(name="plan_compose_deployment_docker", annotations=READ_ONLY_EXTERNAL)
async def plan_compose_deployment_docker(
    profile_id: str, project_path: str, compose_file: str | None = None, env_file: str | None = None
) -> ComposeMutationResult:
    """Return a non-mutating Compose deployment plan."""

    return await create_compose_write_service().operation(
        profile_id,
        "up",
        project_path,
        compose_file, env_file,
        confirmation=None,
        dry_run=True,
    )


@mcp.tool(name="deploy_compose_project_docker", annotations=REMOTE_MUTATION)
async def deploy_compose_project_docker(
    profile_id: str,
    project_path: str,
    compose_file: str | None = None,
    env_file: str | None = None,
    dry_run: bool = True,
    confirmation: str | None = None,
    health_wait_seconds: int | None = None,
) -> ComposeMutationResult:
    """Plan or deploy Compose after explicit confirmation."""

    del health_wait_seconds
    return await create_compose_write_service().operation(
        profile_id, "up", project_path, compose_file, env_file, confirmation, dry_run
    )


@mcp.tool(name="remove_compose_project_docker", annotations=REMOTE_DESTRUCTIVE)
async def remove_compose_project_docker(
    profile_id: str,
    project_path: str,
    compose_file: str | None = None,
    env_file: str | None = None,
    confirmation: str | None = None,
) -> ComposeMutationResult:
    """Remove a Compose project after explicit confirmation."""

    return await create_compose_write_service().operation(
        profile_id, "down", project_path, compose_file, env_file, confirmation
    )


@mcp.tool(name="inspect_docker_project_docker", annotations=READ_ONLY_EXTERNAL)
def inspect_docker_project_docker(
    project_path: str, dockerfile: str = "Dockerfile"
) -> DockerProjectInspectionResult:
    """Inspect a local Docker project without remote mutation."""

    return create_docker_build_service().inspect_project(project_path, dockerfile)


@mcp.tool(name="build_image_docker", annotations=REMOTE_MUTATION)
async def build_image_docker(
    profile_id: str,
    project_path: str,
    image_name: str,
    tag: str = "latest",
    dockerfile: str = "Dockerfile",
    dry_run: bool = True,
    confirmation: str | None = None,
) -> DockerBuildResult:
    """Plan or build an image after explicit confirmation."""

    return await create_docker_build_service().build(
        profile_id, project_path, image_name, tag, dockerfile, dry_run, confirmation
    )


@mcp.tool(name="list_audit_events_docker", annotations=READ_ONLY_EXTERNAL)
def list_audit_events_docker(limit: int = 20) -> AuditEventListResult:
    """List sanitized local audit events when an audit store is configured."""

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return AuditEventListResult(
        events=[],
        message="No hay eventos de auditoría persistidos todavía.",
        documentation_hint=MCP_DOCUMENTATION_HINT,
    )


def run_server() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
