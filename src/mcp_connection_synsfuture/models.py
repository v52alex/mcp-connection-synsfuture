"""Contracts for connection profiles and validation results."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ProfileType(StrEnum):
    DOCKER_CONTEXT = "docker-context"
    SSH_PROFILE = "ssh-profile"


class ConnectionState(StrEnum):
    PROFILE_REQUIRED = "profile_required"
    PROFILE_REGISTERED = "profile_registered"
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
