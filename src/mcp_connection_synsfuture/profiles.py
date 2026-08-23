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

    @property
    def path(self) -> Path:
        return self._path

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

    def list_profiles(self) -> tuple[ConnectionProfile, ...]:
        if not self._path.is_file():
            return ()
        try:
            raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return ()
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            return ()
        result: list[ConnectionProfile] = []
        for profile_id, entry in profiles.items():
            if isinstance(profile_id, str) and isinstance(entry, dict):
                try:
                    result.append(ConnectionProfile(profile_id=profile_id, **entry))
                except ValidationError:
                    continue
        return tuple(result)

    def register(self, profile: ConnectionProfile) -> str | None:
        """Append a new profile without overwriting existing configuration."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                raw = tomllib.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as error:
                return f"The profiles file is not valid TOML: {error}"
            profiles = raw.get("profiles")
            if profiles is not None and not isinstance(profiles, dict):
                return "The profiles section must be a TOML table."
            if isinstance(profiles, dict) and profile.profile_id in profiles:
                return "profile_exists"
        try:
            with self._path.open("a", encoding="utf-8") as file:
                if self._path.stat().st_size > 0:
                    file.write("\n")
                file.write(self._profile_toml(profile))
        except OSError as error:
            return str(error)
        return None

    def remove(self, profile_id: str) -> str | None:
        """Remove one local profile section without touching remote resources."""

        if not PROFILE_ID_PATTERN.fullmatch(profile_id) or not self._path.is_file():
            return "profile_not_found"
        try:
            content = self._path.read_text(encoding="utf-8")
            tomllib.loads(content)
        except (OSError, tomllib.TOMLDecodeError) as error:
            return f"The profiles file is not valid TOML: {error}"

        section_pattern = re.compile(r"^\[profiles\.([a-z0-9][a-z0-9-]{0,62})\]\s*$")
        header_pattern = re.compile(r"^\[[^\]]+\]\s*$")
        lines = content.splitlines(keepends=True)
        output: list[str] = []
        removing = False
        removed = False
        for line in lines:
            header = line.strip("\r\n")
            match = section_pattern.match(header)
            if match:
                removing = match.group(1) == profile_id
                removed = removed or removing
            elif header_pattern.match(header):
                removing = False
            if not removing:
                output.append(line)
        if not removed:
            return "profile_not_found"
        try:
            self._path.write_text("".join(output).rstrip() + "\n", encoding="utf-8")
        except OSError as error:
            return str(error)
        return None

    @staticmethod
    def _profile_toml(profile: ConnectionProfile) -> str:
        capabilities = ", ".join(f'"{item}"' for item in profile.capabilities)
        lines = [
            f"[profiles.{profile.profile_id}]",
            f'type = "{profile.type.value}"',
            *([f'docker_context = "{profile.docker_context}"'] if profile.docker_context else []),
        ]
        if profile.ssh_profile:
            lines.append(f'ssh_profile = "{profile.ssh_profile}"')
        lines.extend(
            [f"enabled = {str(profile.enabled).lower()}", f"capabilities = [{capabilities}]"]
        )
        return "\n".join(lines) + "\n"


def validate_profile_shape(profile: ConnectionProfile) -> str | None:
    if profile.type is ProfileType.DOCKER_CONTEXT and not profile.docker_context:
        return "Docker profiles must define docker_context."
    if profile.type is ProfileType.SSH_PROFILE and not profile.ssh_profile:
        return "SSH profiles must define ssh_profile."
    return None
