"""Load non-secret, locally managed connection profiles from TOML."""

import re
import tomllib
from pathlib import Path

from pydantic import ValidationError

from .models import ConnectionProfile, ProfileType

PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class ProfileRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, profile_id: str) -> ConnectionProfile | None:
        if not PROFILE_ID_PATTERN.fullmatch(profile_id):
            return None
        if not self._path.is_file():
            return None
        try:
            raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            return None
        entry = profiles.get(profile_id)
        if not isinstance(entry, dict):
            return None
        try:
            return ConnectionProfile(profile_id=profile_id, **entry)
        except ValidationError:
            return None


def validate_profile_shape(profile: ConnectionProfile) -> str | None:
    if profile.type is ProfileType.DOCKER_CONTEXT and not profile.docker_context:
        return "Docker profiles must define docker_context."
    if profile.type is ProfileType.SSH_PROFILE and not profile.ssh_profile:
        return "SSH profiles must define ssh_profile."
    return None
