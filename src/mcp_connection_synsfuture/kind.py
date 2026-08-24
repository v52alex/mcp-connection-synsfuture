"""Controlled discovery and image loading for remote Kind clusters."""

import json
import os
import re
from typing import Protocol

from .models import (
    ConnectionProfile,
    ConnectionState,
    KindClusterInspectResult,
    KindClusterListResult,
    KindClusterSummary,
    KindImageLoadResult,
)
from .process import CommandResult
from .profiles import ProfileRepository
from .service import MCP_DOCUMENTATION_HINT, ConnectionProfileService

_SAFE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")
_SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,254}$")
LOAD_IMAGES_CONFIRMATION = "LOAD_IMAGES_TO_KIND_ON_WINDOWS_DOCKER"


class CommandRunner(Protocol):
    async def run(self, args: list[str], timeout_seconds: float) -> CommandResult: ...


class KindService:
    """Run a fixed set of Kind/kubectl commands through an authorized SSH profile."""

    def __init__(
        self,
        runner: CommandRunner,
        profiles: ProfileRepository,
        connections: ConnectionProfileService,
        timeout: float = 30.0,
    ) -> None:
        self._runner = runner
        self._profiles = profiles
        self._connections = connections
        self._timeout = timeout
        self._recommended_cluster = os.getenv(
            "MCP_CONNECTION_KIND_RECOMMENDED_CLUSTER", "microservices"
        )
        self._namespace = os.getenv("MCP_CONNECTION_KUBERNETES_NAMESPACE", "microservices")

    async def list_clusters(self, profile_id: str) -> KindClusterListResult:
        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return self._list_failure(profile_id, error or "Perfil no disponible.")
        assert profile.ssh_profile is not None
        try:
            result = await self._remote(profile, "kind", "get", "clusters")
        except (FileNotFoundError, TimeoutError):
            return self._list_failure(profile_id, "No se pudo iniciar la consulta remota de Kind.")
        if result.returncode != 0:
            return self._list_failure(
                profile_id,
                self._kind_error_message(result.stderr),
                profile,
            )

        clusters: list[KindClusterSummary] = []
        for name in (line.strip() for line in result.stdout.splitlines() if line.strip()):
            if not _SAFE_NAME.fullmatch(name):
                continue
            context = f"kind-{name}"
            try:
                access = await self._remote(
                    profile, "kubectl", "--context", context, "get", "nodes", "--no-headers"
                )
                reachable = access.returncode == 0
            except (FileNotFoundError, TimeoutError):
                reachable = False
            clusters.append(KindClusterSummary(name=name, context=context, reachable=reachable))

        recommended = next(
            (
                item.name
                for item in clusters
                if item.name == self._recommended_cluster and item.reachable
            ),
            next((item.name for item in clusters if item.reachable), None),
        )
        clusters = [
            item.model_copy(update={"selected": item.name == recommended}) for item in clusters
        ]
        return KindClusterListResult(
            profile_id=profile_id,
            ssh_profile=profile.ssh_profile,
            connected=True,
            clusters=clusters,
            recommended_cluster=recommended,
            message=(
                "Clusters Kind recuperados; selecciona explícitamente uno antes de operar."
                if clusters
                else "Kind está accesible, pero no reportó clusters."
            ),
            recommended_action=(
                f"Usa el cluster {recommended!r} en la siguiente operación."
                if recommended
                else "Crea o inicia un cluster Kind en el host remoto."
            ),
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

    async def inspect_cluster(
        self, profile_id: str, cluster_name: str, namespace: str | None = None
    ) -> KindClusterInspectResult:
        selected_namespace = namespace or self._namespace
        if not _SAFE_NAME.fullmatch(cluster_name) or not _SAFE_NAME.fullmatch(selected_namespace):
            return KindClusterInspectResult(
                profile_id=profile_id,
                connected=False,
                namespace=selected_namespace,
                message="El cluster o namespace contiene caracteres no permitidos.",
                recommended_action=(
                    "Usa nombres devueltos por list_kind_clusters y namespaces válidos."
                ),
            )
        listed = await self.list_clusters(profile_id)
        cluster = next((item for item in listed.clusters if item.name == cluster_name), None)
        if cluster is None or not cluster.reachable:
            return KindClusterInspectResult(
                profile_id=profile_id,
                namespace=selected_namespace,
                cluster=cluster,
                connected=listed.connected,
                message="El cluster no está disponible en la lista remota de Kind.",
                recommended_action=(
                    "Consulta list_kind_clusters y selecciona un cluster alcanzable."
                ),
            )
        profile = self._profiles.get(profile_id)
        if profile is None or profile.ssh_profile is None:
            return KindClusterInspectResult(
                profile_id=profile_id,
                connected=False,
                namespace=selected_namespace,
                cluster=cluster,
                message="El perfil SSH no está disponible.",
                recommended_action="Corrige el perfil autorizado y vuelve a validar la conexión.",
            )
        try:
            nodes = await self._remote(
                profile, "kubectl", "--context", cluster.context, "get", "nodes", "-o", "json"
            )
            namespace_result = await self._remote(
                profile,
                "kubectl",
                "--context",
                cluster.context,
                "get",
                "namespace",
                selected_namespace,
                "-o",
                "name",
            )
        except (FileNotFoundError, TimeoutError):
            return KindClusterInspectResult(
                profile_id=profile_id,
                connected=False,
                namespace=selected_namespace,
                cluster=cluster,
                message="No se pudo consultar kubectl en el host remoto.",
                recommended_action="Verifica kubectl y el contexto kubeconfig remoto.",
            )
        if nodes.returncode != 0:
            return KindClusterInspectResult(
                profile_id=profile_id,
                connected=False,
                namespace=selected_namespace,
                cluster=cluster,
                message="kubectl no pudo consultar los nodos del cluster.",
                recommended_action="Verifica el contexto Kind seleccionado.",
            )
        try:
            node_items = json.loads(nodes.stdout).get("items", [])
        except (json.JSONDecodeError, AttributeError):
            node_items = []
        ready = sum(
            1
            for node in node_items
            if any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in node.get("status", {}).get("conditions", [])
            )
        )
        return KindClusterInspectResult(
            profile_id=profile_id,
            namespace=selected_namespace,
            cluster=cluster,
            connected=True,
            node_count=len(node_items),
            nodes_ready=ready,
            namespace_exists=namespace_result.returncode == 0,
            message="Cluster Kind inspeccionado correctamente.",
            recommended_action=(
                None
                if namespace_result.returncode == 0
                else "Aplica el manifiesto del namespace antes del despliegue."
            ),
        )

    async def load_image(
        self,
        profile_id: str,
        cluster_name: str,
        image_reference: str,
        dry_run: bool = True,
        confirmation: str | None = None,
    ) -> KindImageLoadResult:
        command_preview = [
            "ssh",
            "<ssh-profile>",
            "kind",
            "load",
            "docker-image",
            image_reference,
            "--name",
            cluster_name,
        ]
        if not _SAFE_IMAGE.fullmatch(image_reference):
            return self._load_failure(
                profile_id,
                cluster_name,
                image_reference,
                "La referencia de imagen no es segura.",
                command_preview,
            )
        listed = await self.list_clusters(profile_id)
        cluster = next((item for item in listed.clusters if item.name == cluster_name), None)
        profile = self._profiles.get(profile_id)
        if (
            cluster is None
            or not cluster.reachable
            or profile is None
            or profile.ssh_profile is None
        ):
            return self._load_failure(
                profile_id,
                cluster_name,
                image_reference,
                "El cluster o perfil no está disponible.",
                command_preview,
            )
        image = await self._remote(profile, "docker", "image", "inspect", image_reference)
        if image.returncode != 0:
            return self._load_failure(
                profile_id,
                cluster_name,
                image_reference,
                "La imagen no existe en el Docker remoto.",
                command_preview,
            )
        if dry_run:
            return KindImageLoadResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                image_reference=image_reference,
                state="planned",
                executed=False,
                command_preview=command_preview,
                message="Carga planificada; el cluster no fue modificado.",
                recommended_action=f"Confirma con {LOAD_IMAGES_CONFIRMATION} para ejecutar.",
                documentation_hint=MCP_DOCUMENTATION_HINT,
            )
        if confirmation != LOAD_IMAGES_CONFIRMATION:
            return self._load_failure(
                profile_id,
                cluster_name,
                image_reference,
                "Falta la confirmación explícita para cargar la imagen.",
                command_preview,
                "Usa la confirmación LOAD_IMAGES_TO_KIND_ON_WINDOWS_DOCKER.",
            )
        loaded = await self._remote(
            profile, "kind", "load", "docker-image", image_reference, "--name", cluster_name
        )
        return KindImageLoadResult(
            profile_id=profile_id,
            cluster_name=cluster_name,
            image_reference=image_reference,
            state="loaded" if loaded.returncode == 0 else "operation_failed",
            executed=True,
            command_preview=command_preview,
            message=(
                "Imagen cargada en Kind."
                if loaded.returncode == 0
                else "Kind no pudo cargar la imagen."
            ),
            recommended_action=(
                None
                if loaded.returncode == 0
                else "Revisa el estado del cluster y los logs de Kind."
            ),
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

    async def _authorized_profile(
        self, profile_id: str
    ) -> tuple[ConnectionProfile | None, str | None]:
        profile = self._profiles.get(profile_id)
        if profile is None:
            return None, "El perfil solicitado no está registrado en la allowlist local."
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.connected:
            return None, connection.message
        if profile.ssh_profile is None:
            return None, "El perfil no tiene un alias SSH autorizado para Kind."
        return profile, None

    async def _remote(self, profile: ConnectionProfile, *args: str) -> CommandResult:
        assert profile.ssh_profile is not None
        return await self._runner.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "PreferredAuthentications=publickey",
                profile.ssh_profile,
                *args,
            ],
            self._timeout,
        )

    @staticmethod
    def _list_failure(
        profile_id: str, message: str, profile: ConnectionProfile | None = None
    ) -> KindClusterListResult:
        return KindClusterListResult(
            profile_id=profile_id,
            ssh_profile=profile.ssh_profile if profile else None,
            connected=False,
            message=message,
            recommended_action="Valida el perfil y las herramientas kind/kubectl del host remoto.",
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

    @staticmethod
    def _kind_error_message(stderr: str) -> str:
        """Classify common remote failures without returning raw command output."""

        normalized = stderr.lower()
        if "command not found" in normalized or "not recognized" in normalized:
            return (
                "El comando kind no está instalado o no está disponible en PATH "
                "en el host remoto."
            )
        if "permission denied" in normalized or "publickey" in normalized:
            return "El host remoto rechazó la autenticación SSH para consultar Kind."
        if "cannot connect" in normalized or "docker daemon" in normalized:
            return "Kind está instalado, pero no puede acceder al runtime Docker remoto."
        return "Kind no pudo listar los clusters del host remoto; el comando devolvió un error."

    @staticmethod
    def _load_failure(
        profile_id: str,
        cluster_name: str,
        image_reference: str,
        message: str,
        command_preview: list[str],
        recommended_action: str | None = None,
    ) -> KindImageLoadResult:
        return KindImageLoadResult(
            profile_id=profile_id,
            cluster_name=cluster_name,
            image_reference=image_reference,
            state="validation_failed",
            executed=False,
            command_preview=command_preview,
            message=message,
            recommended_action=recommended_action,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
