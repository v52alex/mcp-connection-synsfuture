"""Contracts for connection profiles and validation results."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProfileType(StrEnum):
    DOCKER_CONTEXT = "docker-context"
    SSH_PROFILE = "ssh-profile"


class ConnectionState(StrEnum):
    PROFILE_REQUIRED = "profile_required"
    PROFILE_REGISTERED = "profile_registered"
    PROFILE_REMOVED = "profile_removed"
    PROFILE_EXISTS = "profile_exists"
    PROFILE_NOT_FOUND = "profile_not_found"
    PROFILE_DISABLED = "profile_disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    CONTEXT_NOT_FOUND = "context_not_found"
    UNSUPPORTED_TRANSPORT = "unsupported_transport"
    SSH_AGENT_UNAVAILABLE = "ssh_agent_unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    READY = "ready"


class ConnectionProfile(BaseModel):
    """Non-secret mapping between a user-facing profile and a remote target."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
    type: ProfileType
    docker_context: str | None = None
    ssh_profile: str | None = None
    enabled: bool = True
    capabilities: tuple[str, ...] = ()


class ConnectionValidationResult(BaseModel):
    """Safe, structured result returned by the MCP."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str | None
    state: ConnectionState
    connected: bool = False
    profile_type: ProfileType | None = None
    docker_context: str | None = None
    transport: str | None = None
    capabilities: tuple[str, ...] = ()
    message: str
    recommended_action: str | None = None
    documentation_hint: str | None = None
    profiles_file: str | None = None
    profile_example: str | None = None


class ConnectionProfileSummary(BaseModel):
    """Sanitized profile metadata safe to expose through the MCP."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    profile_type: ProfileType
    docker_context: str | None = None
    ssh_profile: str | None = None
    enabled: bool
    capabilities: tuple[str, ...] = ()


class ConnectionProfileListResult(BaseModel):
    """Result of listing locally authorized profiles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profiles: tuple[ConnectionProfileSummary, ...]
    profiles_file: str
    message: str
    documentation_hint: str


class DockerReadResult(BaseModel):
    """Sanitized read-only Docker operation result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str
    profile_id: str
    connected: bool
    records: list[dict[str, Any]] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    message: str
    documentation_hint: str


class DockerMutationResult(BaseModel):
    """Controlled Docker mutation result with an explicit execution signal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str
    profile_id: str
    state: str
    executed: bool
    target: str
    command_preview: list[str] = Field(default_factory=list)
    message: str
    documentation_hint: str


class ComposeReadResult(BaseModel):
    """Sanitized read-only Docker Compose result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str
    profile_id: str
    project_path: str
    connected: bool
    services: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    message: str
    documentation_hint: str


class ComposeMutationResult(BaseModel):
    """Controlled Docker Compose mutation or deployment plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str
    profile_id: str
    project_path: str
    state: str
    executed: bool
    command_preview: list[str] = Field(default_factory=list)
    message: str
    documentation_hint: str


class DockerProjectInspectionResult(BaseModel):
    """Safe local Docker project inspection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_path: str
    dockerfile_path: str | None
    dockerignore_path: str | None
    build_ready: bool
    warnings: list[str] = Field(default_factory=list)
    documentation_hint: str


class DockerBuildResult(BaseModel):
    """Controlled Docker image build plan or result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    state: str
    executed: bool
    image_reference: str
    project_path: str
    command_preview: list[str] = Field(default_factory=list)
    message: str
    documentation_hint: str


class AuditEventListResult(BaseModel):
    """Sanitized local audit event listing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    events: list[dict[str, Any]] = Field(default_factory=list)
    message: str
    documentation_hint: str


class KindClusterSummary(BaseModel):
    """Sanitized metadata for a remotely discovered Kind cluster."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    context: str
    reachable: bool
    selected: bool = False


class KindClusterListResult(BaseModel):
    """Clusters visible through one authorized connection profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    ssh_profile: str | None = None
    connected: bool
    clusters: list[KindClusterSummary] = Field(default_factory=list)
    recommended_cluster: str | None = None
    message: str
    recommended_action: str | None = None
    documentation_hint: str = "Más información: consulta la documentación del MCP."


class KindClusterInspectResult(BaseModel):
    """Read-only node and namespace information for a selected cluster."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    cluster: KindClusterSummary | None = None
    connected: bool
    namespace: str
    node_count: int | None = Field(default=None, ge=0)
    nodes_ready: int | None = Field(default=None, ge=0)
    namespace_exists: bool = False
    message: str
    recommended_action: str | None = None
    documentation_hint: str = "Más información: consulta la documentación del MCP."


class KindImageLoadResult(BaseModel):
    """Dry-run or execution result for loading an image into Kind."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    cluster_name: str
    image_reference: str
    state: str
    executed: bool
    command_preview: list[str] = Field(default_factory=list)
    message: str
    recommended_action: str | None = None
    documentation_hint: str = "Más información: consulta la documentación del MCP."


class KindPrerequisitesResult(BaseModel):
    """Availability checks for the fixed Kind remote toolchain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str
    ssh_profile: str | None = None
    connected: bool
    kind_available: bool = False
    kubectl_available: bool = False
    docker_available: bool = False
    message: str
    recommended_action: str | None = None
    documentation_hint: str = "Más información: consulta la documentación del MCP."
