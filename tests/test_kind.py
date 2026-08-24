from collections import deque
from pathlib import Path

import pytest

from mcp_connection_synsfuture.kind import KindService
from mcp_connection_synsfuture.models import ConnectionState, ConnectionValidationResult
from mcp_connection_synsfuture.process import CommandResult
from mcp_connection_synsfuture.profiles import ProfileRepository


class StubRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = deque(results)
        self.calls: list[list[str]] = []

    async def run(self, args: list[str], timeout_seconds: float) -> CommandResult:
        self.calls.append(args)
        return self.results.popleft()


class FakeConnections:
    async def connect(self, profile_id: str) -> ConnectionValidationResult:
        return ConnectionValidationResult(
            profile_id=profile_id,
            state=ConnectionState.READY,
            connected=True,
            message="ready",
        )


def profiles(tmp_path: Path) -> ProfileRepository:
    path = tmp_path / "profiles.toml"
    path.write_text(
        """[profiles.docker-remote1]
type = "docker-context"
docker_context = "windows-docker"
ssh_profile = "windows-docker"
capabilities = ["read"]
""",
        encoding="utf-8",
    )
    return ProfileRepository(path)


@pytest.mark.asyncio
async def test_lists_and_recommends_reachable_microservices(tmp_path: Path) -> None:
    runner = StubRunner(
        [
            CommandResult(0, "other\nmicroservices\n", ""),
            CommandResult(0, "node\n", ""),
            CommandResult(0, "node\n", ""),
        ]
    )
    service = KindService(runner, profiles(tmp_path), FakeConnections())

    result = await service.list_clusters("docker-remote1")

    assert result.connected is True
    assert result.recommended_cluster == "microservices"
    assert [item.name for item in result.clusters] == ["other", "microservices"]
    assert result.clusters[1].selected is True
    assert runner.calls[0][-3:] == ["kind", "get", "clusters"]


@pytest.mark.asyncio
async def test_inspects_nodes_and_namespace(tmp_path: Path) -> None:
    nodes = (
        '{"items":[{"status":{"conditions":[{"type":"Ready","status":"True"}]}},'
        '{"status":{"conditions":[{"type":"Ready","status":"False"}]}}]}'
    )
    runner = StubRunner(
        [
            CommandResult(0, "microservices\n", ""),
            CommandResult(0, "node\n", ""),
            CommandResult(0, nodes, ""),
            CommandResult(0, "namespace/microservices\n", ""),
        ]
    )
    service = KindService(runner, profiles(tmp_path), FakeConnections())

    result = await service.inspect_cluster("docker-remote1", "microservices")

    assert result.connected is True
    assert result.node_count == 2
    assert result.nodes_ready == 1
    assert result.namespace_exists is True


@pytest.mark.asyncio
async def test_load_image_is_dry_run_by_default(tmp_path: Path) -> None:
    runner = StubRunner(
        [
            CommandResult(0, "microservices\n", ""),
            CommandResult(0, "node\n", ""),
            CommandResult(0, "{}", ""),
        ]
    )
    service = KindService(runner, profiles(tmp_path), FakeConnections())

    result = await service.load_image("docker-remote1", "microservices", "eureka-server:1.0")

    assert result.state == "planned"
    assert result.executed is False
    assert "LOAD_IMAGES_TO_KIND_ON_WINDOWS_DOCKER" in (result.recommended_action or "")


@pytest.mark.asyncio
async def test_checks_docker_through_authorized_context(tmp_path: Path) -> None:
    runner = StubRunner(
        [
            CommandResult(0, "kind v0.24.0", ""),
            CommandResult(0, "Client Version: v1.30", ""),
            CommandResult(0, "Docker info", ""),
        ]
    )
    service = KindService(runner, profiles(tmp_path), FakeConnections())

    result = await service.check_prerequisites("docker-remote1")

    assert result.kind_available is True
    assert result.kubectl_available is True
    assert result.docker_available is True
    assert runner.calls[-1] == ["docker", "--context", "windows-docker", "info"]


@pytest.mark.asyncio
async def test_create_cluster_is_dry_run_by_default(tmp_path: Path) -> None:
    service = KindService(StubRunner([]), profiles(tmp_path), FakeConnections())

    result = await service.create_cluster("docker-remote1", "microservices")

    assert result.state == "planned"
    assert result.executed is False
    assert result.command_preview[-4:] == ["--name", "microservices", "--wait", "5m"]
    assert "CREATE_KIND_CLUSTER_ON_DOCKER_REMOTE" in (result.recommended_action or "")


@pytest.mark.asyncio
async def test_create_cluster_requires_confirmation(tmp_path: Path) -> None:
    service = KindService(StubRunner([]), profiles(tmp_path), FakeConnections())

    result = await service.create_cluster(
        "docker-remote1", "microservices", dry_run=False, confirmation="wrong"
    )

    assert result.state == "confirmation_required"
    assert result.executed is False


@pytest.mark.asyncio
async def test_rejects_unsafe_cluster_name(tmp_path: Path) -> None:
    service = KindService(StubRunner([]), profiles(tmp_path), FakeConnections())

    result = await service.create_cluster("docker-remote1", "microservices;rm")

    assert result.state == "validation_failed"
    assert result.executed is False


@pytest.mark.asyncio
async def test_rejects_unsafe_image_reference(tmp_path: Path) -> None:
    service = KindService(StubRunner([]), profiles(tmp_path), FakeConnections())

    result = await service.load_image("docker-remote1", "microservices", "image;rm -rf /")

    assert result.state == "validation_failed"
    assert result.executed is False
