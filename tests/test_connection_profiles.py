import sys
from pathlib import Path

import pytest

from mcp_connection_synsfuture.models import ConnectionState
from mcp_connection_synsfuture.process import CommandResult
from mcp_connection_synsfuture.profiles import ProfileRepository
from mcp_connection_synsfuture.service import ConnectionProfileService


class StubRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    async def run(self, args: list[str], timeout_seconds: float) -> CommandResult:
        self.calls.append(args)
        return self.results.pop(0)


def profile_file(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "profiles.toml"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_rejects_unknown_profile_without_running_docker(tmp_path: Path) -> None:
    runner = StubRunner([])
    service = ConnectionProfileService(runner, ProfileRepository(profile_file(tmp_path, "")))

    result = await service.connect("docker-remote1")

    assert result.state is ConnectionState.PROFILE_NOT_FOUND
    assert runner.calls == []


@pytest.mark.asyncio
async def test_validates_ssh_docker_context_and_returns_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
    runner = StubRunner(
        [
            CommandResult(
                0,
                '[{"Endpoints":{"docker":{"Host":"ssh://docker-remote1"}}}]',
                "",
            ),
            CommandResult(0, "256 SHA256:example profile-key (ED25519)", ""),
            CommandResult(0, '{"Client":{"Version":"29"}}', ""),
        ]
    )
    service = ConnectionProfileService(
        runner,
        ProfileRepository(
            profile_file(
                tmp_path,
                """[profiles.docker-remote1]\n"""
                """type = \"docker-context\"\n"""
                """docker_context = \"docker-remote1\"\n"""
                """ssh_profile = \"docker-remote1\"\n"""
                """capabilities = [\"read\"]\n""",
            )
        ),
    )

    result = await service.connect("docker-remote1")

    assert result.state is ConnectionState.READY
    assert result.connected is True
    assert result.transport == "ssh"
    assert runner.calls[1] == ["ssh-add", "-l"]
    assert runner.calls[2][:4] == ["docker", "--context", "docker-remote1", "version"]


@pytest.mark.asyncio
async def test_rejects_non_ssh_context(tmp_path: Path) -> None:
    runner = StubRunner(
        [CommandResult(0, '[{"Endpoints":{"docker":{"Host":"unix:///var/run/docker.sock"}}}]', "")]
    )
    service = ConnectionProfileService(
        runner,
        ProfileRepository(
            profile_file(
                tmp_path,
                """[profiles.local]\n"""
                """type = \"docker-context\"\n"""
                """docker_context = \"local\"\n""",
            )
        ),
    )

    result = await service.connect("local")

    assert result.state is ConnectionState.UNSUPPORTED_TRANSPORT
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_reports_missing_ssh_agent_before_remote_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    runner = StubRunner(
        [
            CommandResult(
                0,
                '[{"Endpoints":{"docker":{"Host":"ssh://docker-remote1"}}}]',
                "",
            )
        ]
    )
    service = ConnectionProfileService(
        runner,
        ProfileRepository(
            profile_file(
                tmp_path,
                """[profiles.docker-remote1]\n"""
                """type = \"docker-context\"\n"""
                """docker_context = \"docker-remote1\"\n"""
                """ssh_profile = \"docker-remote1\"\n""",
            )
        ),
    )

    result = await service.connect("docker-remote1")

    assert result.state is ConnectionState.SSH_AGENT_UNAVAILABLE
    assert result.connected is False
    assert "agente SSH" in result.message
    assert runner.calls == [["docker", "context", "inspect", "docker-remote1"]]


def test_registers_profile_metadata_without_overwriting_existing_file(tmp_path: Path) -> None:
    path = profile_file(tmp_path, "")
    service = ConnectionProfileService(StubRunner([]), ProfileRepository(path))

    result = service.register("vps", "vps", "vps", ["read"])
    duplicate = service.register("vps", "vps", "vps", ["read"])

    assert result.state is ConnectionState.PROFILE_REGISTERED
    assert result.profiles_file == str(path)
    assert '[profiles.vps]' in path.read_text(encoding="utf-8")
    assert duplicate.state is ConnectionState.PROFILE_EXISTS


def test_removes_only_requested_profile_metadata(tmp_path: Path) -> None:
    path = profile_file(
        tmp_path,
        """[profiles.vps]\n"""
        """type = \"docker-context\"\n"""
        """docker_context = \"vps\"\n\n"""
        """[profiles.docker-remote1]\n"""
        """type = \"docker-context\"\n"""
        """docker_context = \"docker-remote1\"\n""",
    )
    service = ConnectionProfileService(StubRunner([]), ProfileRepository(path))

    result = service.remove("vps")
    content = path.read_text(encoding="utf-8")

    assert result.state is ConnectionState.PROFILE_REMOVED
    assert "profiles.vps" not in content
    assert "profiles.docker-remote1" in content
    assert result.recommended_action is not None
    assert "Docker context" in result.recommended_action


def test_lists_sanitized_registered_profiles(tmp_path: Path) -> None:
    path = profile_file(
        tmp_path,
        """[profiles.vps]\n"""
        """type = \"docker-context\"\n"""
        """docker_context = \"vps\"\n"""
        """ssh_profile = \"vps\"\n"""
        """enabled = true\n"""
        """capabilities = [\"read\"]\n""",
    )
    service = ConnectionProfileService(StubRunner([]), ProfileRepository(path))

    result = service.list_profiles()

    assert len(result.profiles) == 1
    assert result.profiles[0].profile_id == "vps"
    assert result.profiles[0].ssh_profile == "vps"
    assert result.profiles_file == str(path)
