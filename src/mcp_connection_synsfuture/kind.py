"""Controlled discovery and image loading for remote Kind clusters."""

import base64
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Protocol

from .models import (
    ConnectionProfile,
    ConnectionState,
    KindClusterCreateResult,
    KindClusterInspectResult,
    KindClusterListResult,
    KindClusterSummary,
    KindHelmReleaseResult,
    KindImageLoadResult,
    KindManifestApplyResult,
    KindNamespaceEnsureResult,
    KindPortForwardResult,
    KindPrerequisitesResult,
    KindWorkloadInspectResult,
    RemotePlatform,
)
from .process import CommandResult
from .profiles import ProfileRepository
from .service import MCP_DOCUMENTATION_HINT, ConnectionProfileService

_SAFE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")
_SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,254}$")
LOAD_IMAGES_CONFIRMATION = "LOAD_IMAGES_TO_KIND_ON_WINDOWS_DOCKER"
CREATE_CLUSTER_CONFIRMATION = "CREATE_KIND_CLUSTER_ON_DOCKER_REMOTE"
ENSURE_NAMESPACE_CONFIRMATION = "ENSURE_KIND_NAMESPACE_ON_DOCKER_REMOTE"
HELM_RELEASE_CONFIRMATION = "INSTALL_HELM_RELEASE_ON_DOCKER_REMOTE"
PORT_FORWARD_CONFIRMATION = "START_KIND_PORT_FORWARD_ON_DOCKER_REMOTE"
STOP_PORT_FORWARD_CONFIRMATION = "STOP_KIND_PORT_FORWARD_ON_DOCKER_REMOTE"
PROMETHEUS_STACK_CHART = "prometheus-community/kube-prometheus-stack"
PROMETHEUS_STACK_REPO = "https://prometheus-community.github.io/helm-charts"


class CommandRunner(Protocol):
    async def run(
        self,
        args: list[str],
        timeout_seconds: float,
        input_data: bytes | None = None,
    ) -> CommandResult: ...


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

    async def check_prerequisites(self, profile_id: str) -> KindPrerequisitesResult:
        """Check fixed remote binaries without returning their raw output."""

        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return KindPrerequisitesResult(
                profile_id=profile_id,
                connected=False,
                message=error or "Perfil no disponible.",
                recommended_action="Valida el perfil autorizado antes de consultar el host remoto.",
            )
        checks: list[tuple[str, tuple[str, ...]]] = [
            ("kind", ("kind", "version")),
            ("kubectl", ("kubectl", "version", "--client")),
        ]
        availability: dict[str, bool] = {}
        try:
            for name, command in checks:
                result = await self._remote(profile, *command)
                availability[name] = result.returncode == 0
            docker_context = profile.docker_context
            if docker_context is None:
                availability["docker"] = False
            else:
                docker_result = await self._runner.run(
                    ["docker", "--context", docker_context, "info"], self._timeout
                )
                availability["docker"] = docker_result.returncode == 0
        except (FileNotFoundError, TimeoutError):
            return KindPrerequisitesResult(
                profile_id=profile_id,
                ssh_profile=profile.ssh_profile,
                connected=False,
                message="No se pudo ejecutar la validación remota de prerrequisitos.",
                recommended_action="Verifica OpenSSH y el host remoto.",
            )
        missing = [name for name, available in availability.items() if not available]
        return KindPrerequisitesResult(
            profile_id=profile_id,
            ssh_profile=profile.ssh_profile,
            connected=True,
            kind_available=availability["kind"],
            kubectl_available=availability["kubectl"],
            docker_available=availability["docker"],
            message=(
                "Los prerrequisitos remotos están disponibles."
                if not missing
                else f"Faltan o fallan prerrequisitos remotos: {', '.join(missing)}."
            ),
            recommended_action=(
                None
                if not missing
                else "Instala o corrige los prerrequisitos indicados en el host remoto."
            ),
        )

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

    async def create_cluster(
        self,
        profile_id: str,
        cluster_name: str,
        dry_run: bool = True,
        confirmation: str | None = None,
    ) -> KindClusterCreateResult:
        """Plan or create a fixed-shape Kind cluster on the authorized host."""

        command = ("kind", "create", "cluster", "--name", cluster_name, "--wait", "5m")
        preview = list(command)
        if not _SAFE_NAME.fullmatch(cluster_name):
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="validation_failed",
                executed=False,
                command_preview=preview,
                message="El nombre del clúster no cumple el formato permitido.",
                recommended_action="Usa minúsculas, números, puntos o guiones.",
            )

        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="connection_failed",
                executed=False,
                command_preview=preview,
                message=error or "Perfil no disponible.",
                recommended_action="Valida el perfil autorizado antes de crear el clúster.",
            )
        if not dry_run and confirmation != CREATE_CLUSTER_CONFIRMATION:
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="confirmation_required",
                executed=False,
                command_preview=preview,
                message="La creación del clúster requiere confirmación explícita.",
                recommended_action=f"Proporciona confirmation={CREATE_CLUSTER_CONFIRMATION}.",
            )
        try:
            existing = await self._remote(profile, "kind", "get", "clusters")
        except (FileNotFoundError, TimeoutError):
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="discovery_failed",
                executed=False,
                command_preview=preview,
                message="No se pudo consultar los clústeres Kind existentes.",
                recommended_action="Verifica la conexión y vuelve a listar los clústeres.",
            )
        if existing.returncode != 0:
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="discovery_failed",
                executed=False,
                command_preview=preview,
                message="Kind no pudo listar los clústeres existentes.",
                recommended_action="Corrige la disponibilidad de Kind antes de crear un clúster.",
            )
        if cluster_name in {line.strip() for line in existing.stdout.splitlines()}:
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="already_exists",
                executed=False,
                command_preview=[],
                message="El clúster Kind ya existe; no se planificó una nueva creación.",
                recommended_action=f"Inspecciona el contexto kind-{cluster_name}.",
            )
        if dry_run:
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="planned",
                executed=False,
                command_preview=preview,
                message="Creación de clúster Kind planificada; no se ejecutó ningún cambio.",
                recommended_action=f"Confirma con {CREATE_CLUSTER_CONFIRMATION} para ejecutar.",
            )
        prerequisites = await self.check_prerequisites(profile_id)
        if not (
            prerequisites.connected
            and prerequisites.kind_available
            and prerequisites.kubectl_available
            and prerequisites.docker_available
        ):
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="prerequisites_failed",
                executed=False,
                command_preview=preview,
                message="No se puede crear el clúster porque faltan prerrequisitos.",
                recommended_action=prerequisites.message,
            )
        try:
            result = await self._remote(profile, *command)
        except (FileNotFoundError, TimeoutError):
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="execution_failed",
                executed=False,
                command_preview=preview,
                message="No se pudo ejecutar la creación remota del clúster.",
                recommended_action="Revisa la conexión SSH y vuelve a validar los prerrequisitos.",
            )
        if result.returncode != 0:
            return KindClusterCreateResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                state="execution_failed",
                executed=False,
                command_preview=preview,
                message=self._kind_error_message(result.stderr),
                recommended_action="Consulta el estado del clúster y revisa los logs de Kind.",
            )
        return KindClusterCreateResult(
            profile_id=profile_id,
            cluster_name=cluster_name,
            state="created",
            executed=True,
            command_preview=preview,
            message="Clúster Kind creado correctamente.",
            recommended_action=(
                "Lista los clústeres y verifica el contexto kind-" + cluster_name + "."
            ),
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
        try:
            image = await self._remote(profile, "docker", "image", "inspect", image_reference)
        except (TimeoutError, OSError) as error:
            return self._load_failure(
                profile_id,
                cluster_name,
                image_reference,
                "No se pudo inspeccionar la imagen en el Docker remoto.",
                command_preview,
                f"{type(error).__name__}: verifica la disponibilidad del Docker remoto.",
            )
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
        try:
            loaded = await self._remote(
                profile,
                "kind",
                "load",
                "docker-image",
                image_reference,
                "--name",
                cluster_name,
                timeout_seconds=300.0,
            )
        except (TimeoutError, OSError) as error:
            return self._load_failure(
                profile_id,
                cluster_name,
                image_reference,
                "No se pudo ejecutar la carga de la imagen en Kind.",
                command_preview,
                f"{type(error).__name__}: la carga puede tardar varios minutos; "
                "reintenta la operación.",
            )
        succeeded = loaded.returncode == 0
        diagnostic = self._sanitize_diagnostic(loaded.stderr or loaded.stdout)
        return KindImageLoadResult(
            profile_id=profile_id,
            cluster_name=cluster_name,
            image_reference=image_reference,
            state="loaded" if succeeded else "operation_failed",
            executed=succeeded,
            command_preview=command_preview,
            message=(
                "Imagen cargada en Kind."
                if succeeded
                else self._kind_error_message(loaded.stderr or loaded.stdout)
            ),
            recommended_action=(
                None
                if succeeded
                else "Revisa el estado del cluster y los logs de Kind."
            ),
            diagnostic=diagnostic,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

    async def apply_manifest(
        self,
        profile_id: str,
        cluster_name: str,
        manifest_path: str,
        namespace: str,
        dry_run: bool = True,
        confirmation: str | None = None,
    ) -> KindManifestApplyResult:
        path = Path(manifest_path).expanduser().resolve()
        preview = [
            "ssh", "<ssh-profile>", "kubectl", "--context", f"kind-{cluster_name}",
            "apply", "--namespace", namespace, "-f", "-",
        ]
        base: dict[str, object] = dict(
            profile_id=profile_id,
            cluster_name=cluster_name,
            namespace=namespace,
            manifest_path=str(path),
            command_preview=preview,
        )
        if not path.is_file():
            return KindManifestApplyResult.model_validate(
                {**base, "state": "validation_failed", "executed": False,
                 "message": "El manifiesto no existe."}
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
            return KindManifestApplyResult.model_validate(
                {**base, "state": "connection_failed", "executed": False,
                 "message": "El cluster o perfil no está disponible."}
            )
        if dry_run:
            return KindManifestApplyResult.model_validate(
                {**base, "state": "planned", "executed": False,
                 "message": "Aplicación planificada; el cluster no fue modificado.",
                 "recommended_action": (
                     "Confirma con APPLY_KIND_MANIFEST_ON_DOCKER_REMOTE para ejecutar."
                 )}
            )
        if confirmation != "APPLY_KIND_MANIFEST_ON_DOCKER_REMOTE":
            return KindManifestApplyResult.model_validate(
                {**base, "state": "confirmation_required", "executed": False,
                 "message": "La aplicación requiere confirmación explícita.",
                 "recommended_action": "Usa confirmation='APPLY_KIND_MANIFEST_ON_DOCKER_REMOTE'."}
            )
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        if profile.remote_platform is RemotePlatform.WINDOWS:
            remote_script = (
                "$p=Join-Path $env:TEMP 'mcp-kind-manifest.yaml';"
                f"$b=[Convert]::FromBase64String('{encoded}');"
                "[IO.File]::WriteAllBytes($p,$b);"
                f"kubectl --context kind-{cluster_name} apply --namespace {namespace} -f $p;"
                "$c=$LASTEXITCODE;Remove-Item -LiteralPath $p -Force;exit $c"
            )
            remote_command = [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", remote_script
            ]
        else:
            remote_script = (
                "p=$(mktemp /tmp/mcp-kind-manifest.XXXXXX.yaml);"
                f"printf '%s' '{encoded}' | base64 -d >\"$p\";"
                f"kubectl --context kind-{cluster_name} apply --namespace {namespace} -f \"$p\";"
                "c=$?;rm -f \"$p\";exit $c"
            )
            remote_command = ["sh", "-c", remote_script]
        try:
            result = await self._remote(profile, *remote_command, timeout_seconds=120.0)
        except TimeoutError:
            return KindManifestApplyResult.model_validate(
                {**base, "state": "operation_failed", "executed": False,
                 "message": "kubectl agotó el tiempo de espera.",
                 "recommended_action": "Revisa el estado del cluster y reintenta."}
            )
        success = result.returncode == 0
        return KindManifestApplyResult.model_validate(
            {**base,
             "state": "applied" if success else "operation_failed",
             "executed": success,
             "message": (
                 "Manifiesto aplicado correctamente."
                 if success
                 else "kubectl no pudo aplicar el manifiesto."
             ),
             "diagnostic": self._sanitize_diagnostic(result.stderr or result.stdout),
             "recommended_action": (
                 None if success else "Revisa el diagnóstico y el namespace remoto."
             )}
        )

    async def inspect_workload(
        self,
        profile_id: str,
        cluster_name: str,
        workload_name: str,
        namespace: str,
    ) -> KindWorkloadInspectResult:
        base: dict[str, Any] = dict(
            profile_id=profile_id,
            cluster_name=cluster_name,
            namespace=namespace,
            workload_name=workload_name,
        )
        if not all(
            _SAFE_NAME.fullmatch(value)
            for value in (cluster_name, workload_name, namespace)
        ):
            return KindWorkloadInspectResult.model_validate(
                {**base, "connected": False,
                 "message": "El cluster, workload o namespace no cumple el formato permitido."}
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
            return KindWorkloadInspectResult.model_validate(
                {**base, "connected": False, "message": "El cluster o perfil no está disponible."}
            )
        deployment = await self._remote(
            profile, "kubectl", "--context", f"kind-{cluster_name}", "get",
            "deployment", workload_name, "--namespace", namespace, "-o", "json",
        )
        pods = await self._remote(
            profile, "kubectl", "--context", f"kind-{cluster_name}", "get",
            "pods", "--namespace", namespace, "-l", f"app={workload_name}", "-o", "json",
        )
        service = await self._remote(
            profile, "kubectl", "--context", f"kind-{cluster_name}", "get",
            "service", workload_name, "--namespace", namespace, "-o", "json",
        )
        events = await self._remote(
            profile, "kubectl", "--context", f"kind-{cluster_name}", "get", "events",
            "--namespace", namespace, "--sort-by=.lastTimestamp", "-o", "json",
        )
        if deployment.returncode != 0:
            return KindWorkloadInspectResult.model_validate(
                {**base, "connected": True,
                 "message": "El Deployment no existe o kubectl no pudo consultarlo.",
                 "recommended_action": (
                     "Revisa el manifiesto aplicado y los eventos del namespace."
                 )}
            )
        deployment_data = self._parse_json(deployment.stdout)
        pods_data = self._parse_json(pods.stdout)
        service_data = self._parse_json(service.stdout)
        status = deployment_data.get("status", {}) if isinstance(deployment_data, dict) else {}
        pod_items = pods_data.get("items", []) if isinstance(pods_data, dict) else []
        pod_records = [
            {"name": item.get("metadata", {}).get("name"),
             "phase": item.get("status", {}).get("phase"),
             "reason": item.get("status", {}).get("reason"),
             "message": item.get("status", {}).get("message"),
             "node": item.get("spec", {}).get("nodeName"),
             "ready": any(
                 condition.get("type") == "Ready" and condition.get("status") == "True"
                 for condition in item.get("status", {}).get("conditions", [])
             )}
            for item in pod_items if isinstance(item, dict)
        ]
        service_records = []
        if isinstance(service_data, dict) and service_data.get("metadata"):
            service_records.append({
                "name": service_data.get("metadata", {}).get("name"),
                "type": service_data.get("spec", {}).get("type"),
                "cluster_ip": service_data.get("spec", {}).get("clusterIP"),
                "ports": service_data.get("spec", {}).get("ports", []),
            })
        event_items = self._parse_json(events.stdout).get("items", [])
        event_records = [
            {
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "object": item.get("involvedObject", {}).get("name"),
                "count": item.get("count"),
            }
            for item in event_items[-10:] if isinstance(item, dict)
        ]
        ready = int(status.get("readyReplicas", 0) or 0)
        desired = int(status.get("replicas", 0) or 0)
        return KindWorkloadInspectResult(
            **base, connected=True, deployment_ready=ready, deployment_desired=desired,
            pod_records=pod_records, service_records=service_records,
            event_records=event_records,
            message=("Workload listo." if ready >= desired and desired > 0
                     else "Workload creado, pero aún no está listo."),
            recommended_action=(None if ready >= desired and desired > 0
                                else "Revisa los pods y eventos del workload."),
        )

    async def apply_secret_from_env(
        self,
        profile_id: str,
        cluster_name: str,
        secret_name: str,
        env_file: str,
        keys: list[str],
        namespace: str,
        dry_run: bool,
        confirmation: str | None,
    ) -> KindManifestApplyResult:
        if not _SAFE_NAME.fullmatch(cluster_name) or not _SAFE_NAME.fullmatch(secret_name):
            raise ValueError("cluster_name or secret_name has an invalid format")
        if not _SAFE_NAME.fullmatch(namespace) or not keys:
            raise ValueError("namespace and keys are required")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in keys):
            raise ValueError("keys contain an invalid environment variable name")
        values = self._read_env_keys(Path(env_file).expanduser().resolve(), keys)
        aliases = {
            "DB_PASSWORD": "MYSQL_PASSWORD",
            "DATABASE_USER": "MYSQL_USER",
        }
        for target, source in aliases.items():
            if target in keys and target not in values:
                source_value = self._read_env_keys(Path(env_file).expanduser().resolve(), [source])
                if source in source_value:
                    values[target] = source_value[source]
        if "DATABASE_URL" in keys and "DATABASE_URL" not in values:
            mysql = self._read_env_keys(Path(env_file).expanduser().resolve(), ["MYSQL_DATABASE"])
            if mysql.get("MYSQL_DATABASE"):
                values["DATABASE_URL"] = (
                    "jdbc:mysql://mysql:3306/" + mysql["MYSQL_DATABASE"]
                )
        missing = [key for key in keys if key not in values]
        if missing:
            raise ValueError(f"Missing requested environment keys: {', '.join(missing)}")
        lines = [
            "apiVersion: v1", "kind: Secret", "metadata:",
            f"  name: {secret_name}", f"  namespace: {namespace}",
            "type: Opaque", "data:",
        ]
        lines.extend(f"  {key}: {base64.b64encode(values[key].encode()).decode()}" for key in keys)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
                handle.write("\n".join(lines) + "\n")
                temporary = Path(handle.name)
            result = await self.apply_manifest(
                profile_id, cluster_name, str(temporary), namespace, dry_run, confirmation
            )
            return result.model_copy(
                update={"manifest_path": str(Path(env_file).expanduser().resolve())}
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_env_keys(path: Path, keys: list[str]) -> dict[str, str]:
        requested = set(keys)
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[7:].lstrip()
            name, separator, value = stripped.partition("=")
            if separator and name.strip() in requested:
                values[name.strip()] = value.strip().strip("'\"")
        return values

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    async def install_helm_release(
        self,
        profile_id: str,
        cluster_name: str,
        release_name: str = "kube-prometheus-stack",
        namespace: str = "monitoring",
        dry_run: bool = True,
        confirmation: str | None = None,
    ) -> KindHelmReleaseResult:
        """Plan or install the allowlisted Prometheus Operator Helm chart."""

        preview = [
            "helm", "repo", "add", "prometheus-community", PROMETHEUS_STACK_REPO,
            "--force-update", "&&", "helm", "upgrade", "--install", release_name,
            PROMETHEUS_STACK_CHART, "--namespace", namespace, "--create-namespace",
            "--set", "prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false",
        ]
        base: dict[str, Any] = dict(
            profile_id=profile_id,
            cluster_name=cluster_name,
            release_name=release_name,
            chart=PROMETHEUS_STACK_CHART,
            namespace=namespace,
            command_preview=preview,
        )
        if not (_SAFE_NAME.fullmatch(cluster_name) and _SAFE_NAME.fullmatch(release_name)
                and _SAFE_NAME.fullmatch(namespace)):
            return KindHelmReleaseResult(
                **base, state="validation_failed", executed=False,
                message="El clúster, release o namespace contiene caracteres no permitidos.",
                recommended_action="Usa nombres en minúsculas, números, puntos o guiones.",
            )
        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return KindHelmReleaseResult(
                **base, state="connection_failed", executed=False,
                message=error or "Perfil no disponible.",
                recommended_action="Valida el perfil autorizado antes de instalar Helm.",
            )
        if not dry_run and confirmation != HELM_RELEASE_CONFIRMATION:
            return KindHelmReleaseResult(
                **base, state="confirmation_required", executed=False,
                message="La instalación Helm requiere confirmación explícita.",
                recommended_action=f"Proporciona confirmation={HELM_RELEASE_CONFIRMATION}.",
            )
        clusters = await self.list_clusters(profile_id)
        cluster = next((item for item in clusters.clusters if item.name == cluster_name), None)
        if cluster is None or not cluster.reachable:
            return KindHelmReleaseResult(
                **base, state="cluster_unavailable", executed=False,
                message="El clúster solicitado no existe o no es alcanzable.",
                recommended_action="Lista los clústeres y selecciona uno alcanzable.",
            )
        try:
            helm = await self._remote(profile, "helm", "version", "--short")
        except (FileNotFoundError, TimeoutError):
            return KindHelmReleaseResult(
                **base, state="prerequisites_failed", executed=False,
                message="No se pudo validar Helm en el host remoto.",
                recommended_action="Instala Helm en el host remoto y vuelve a intentar.",
            )
        if helm.returncode != 0:
            return KindHelmReleaseResult(
                **base, state="prerequisites_failed", executed=False,
                message="Helm no está disponible en el host remoto.",
                diagnostic=self._sanitize_diagnostic(helm.stderr),
                recommended_action="Instala Helm en el host remoto y vuelve a intentar.",
            )
        if dry_run:
            return KindHelmReleaseResult(
                **base, state="planned", executed=False,
                message="Instalación Helm planificada; no se ejecutó ningún cambio.",
                recommended_action=f"Confirma con {HELM_RELEASE_CONFIRMATION} para ejecutar.",
            )
        commands = [
            (
                "helm", "repo", "add", "prometheus-community", PROMETHEUS_STACK_REPO,
                "--force-update",
            ),
            ("helm", "upgrade", "--install", release_name, PROMETHEUS_STACK_CHART,
             "--namespace", namespace, "--create-namespace",
             "--set", "prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false"),
        ]
        for command in commands:
            try:
                result = await self._remote(profile, *command)
            except (FileNotFoundError, TimeoutError):
                return KindHelmReleaseResult(
                    **base, state="execution_failed", executed=False,
                    message="No se pudo ejecutar la operación Helm remota.",
                    recommended_action="Verifica la conexión y el acceso al registry de charts.",
                )
            if result.returncode != 0:
                return KindHelmReleaseResult(
                    **base, state="execution_failed", executed=False,
                    message="Helm no pudo instalar el release.",
                    diagnostic=self._sanitize_diagnostic(result.stderr or result.stdout),
                    recommended_action="Revisa Helm y la conectividad al repositorio de charts.",
                )
        return KindHelmReleaseResult(
            **base, state="installed", executed=True,
            message="Release Helm instalado correctamente.",
        )

    async def start_port_forward(
        self,
        profile_id: str,
        cluster_name: str,
        service_name: str,
        namespace: str,
        local_port: int,
        remote_port: int,
        dry_run: bool = True,
        confirmation: str | None = None,
    ) -> KindPortForwardResult:
        """Start a controlled Windows remote kubectl port-forward."""

        preview = [
            "kubectl", "--context", f"kind-{cluster_name}", "--namespace", namespace,
            "port-forward", f"service/{service_name}", f"{local_port}:{remote_port}",
            "--address", "0.0.0.0",
        ]
        base: dict[str, Any] = dict(
            profile_id=profile_id, cluster_name=cluster_name, namespace=namespace,
            service_name=service_name, local_port=local_port, remote_port=remote_port,
            command_preview=preview,
        )
        valid_ports = all(isinstance(port, int) and 1024 <= port <= 65535
                          for port in (local_port, remote_port))
        if not (_SAFE_NAME.fullmatch(cluster_name) and _SAFE_NAME.fullmatch(namespace)
                and _SAFE_NAME.fullmatch(service_name) and valid_ports):
            return KindPortForwardResult(
                **base, state="validation_failed", executed=False,
                message="Servicio, namespace o puertos inválidos.",
                recommended_action="Usa nombres seguros y puertos entre 1024 y 65535.",
            )
        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return KindPortForwardResult(
                **base, state="connection_failed", executed=False,
                message=error or "Perfil no disponible.",
                recommended_action="Valida el perfil autorizado antes de abrir el port-forward.",
            )
        if profile.remote_platform != RemotePlatform.WINDOWS:
            return KindPortForwardResult(
                **base, state="unsupported_platform", executed=False,
                message="El port-forward controlado requiere el host remoto Windows autorizado.",
                recommended_action="Usa el perfil Windows docker-remote1.",
            )
        if not dry_run and confirmation != PORT_FORWARD_CONFIRMATION:
            return KindPortForwardResult(
                **base, state="confirmation_required", executed=False,
                message="Abrir el port-forward requiere confirmación explícita.",
                recommended_action=f"Proporciona confirmation={PORT_FORWARD_CONFIRMATION}.",
            )
        clusters = await self.list_clusters(profile_id)
        cluster = next((item for item in clusters.clusters if item.name == cluster_name), None)
        if cluster is None or not cluster.reachable:
            return KindPortForwardResult(
                **base, state="cluster_unavailable", executed=False,
                message="El clúster solicitado no existe o no es alcanzable.",
                recommended_action="Lista los clústeres y selecciona uno alcanzable.",
            )
        if dry_run:
            return KindPortForwardResult(
                **base, state="planned", executed=False,
                message="Port-forward planificado; no se abrió ningún proceso.",
                recommended_action=f"Confirma con {PORT_FORWARD_CONFIRMATION} para ejecutar.",
            )
        script = (
            "$args=@('--context','kind-" + cluster_name + "','--namespace','" + namespace
            + "','port-forward','service/" + service_name + "','" + str(local_port) + ":"
            + str(remote_port) + "','--address','0.0.0.0');"
            + "$p=Start-Process -FilePath 'kubectl' -ArgumentList $args -PassThru "
            + "-WindowStyle Hidden;"
            + "Write-Output $p.Id"
        )
        try:
            result = await self._remote(
                profile, "powershell", "-NoProfile", "-NonInteractive", "-Command", script
            )
        except (TimeoutError, OSError) as error:
            return KindPortForwardResult(
                **base, state="execution_failed", executed=False,
                message="No se pudo iniciar el port-forward remoto.",
                diagnostic=f"{type(error).__name__}",
                recommended_action="Verifica kubectl y el perfil remoto.",
            )
        if result.returncode != 0:
            return KindPortForwardResult(
                **base, state="execution_failed", executed=False,
                message="No se pudo iniciar el port-forward remoto.",
                diagnostic=self._sanitize_diagnostic(result.stderr or result.stdout),
                recommended_action="Revisa el servicio y el puerto remoto.",
            )
        try:
            pid = int(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return KindPortForwardResult(
                **base, state="execution_failed", executed=False,
                message="El port-forward se inició sin devolver un PID válido.",
                recommended_action="Revisa el proceso kubectl en el host remoto.",
            )
        return KindPortForwardResult(
            **base, state="started", executed=True, pid=pid,
            endpoint=f"docker-remote1:{local_port}",
            message="Port-forward iniciado correctamente.",
            recommended_action=f"Cierra el proceso con stop_kind_port_forward(pid={pid}).",
        )

    async def stop_port_forward(
        self, profile_id: str, pid: int, confirmation: str | None = None
    ) -> KindPortForwardResult:
        """Stop one remote port-forward process previously returned by this MCP."""

        base: dict[str, Any] = dict(
            profile_id=profile_id, cluster_name="", namespace="", service_name="",
            local_port=0, remote_port=0,
            command_preview=["Stop-Process", "-Id", str(pid), "-Force"],
        )
        if not isinstance(pid, int) or pid <= 0:
            return KindPortForwardResult(
                **base, state="validation_failed", executed=False,
                message="El PID no es válido.",
            )
        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return KindPortForwardResult(
                **base, state="connection_failed", executed=False,
                message=error or "Perfil no disponible.",
            )
        if confirmation != STOP_PORT_FORWARD_CONFIRMATION:
            return KindPortForwardResult(
                **base, state="confirmation_required", executed=False,
                message="Cerrar el port-forward requiere confirmación explícita.",
                recommended_action=f"Proporciona confirmation={STOP_PORT_FORWARD_CONFIRMATION}.",
            )
        script = f"Stop-Process -Id {pid} -Force"
        try:
            result = await self._remote(
                profile, "powershell", "-NoProfile", "-NonInteractive", "-Command", script
            )
        except (TimeoutError, OSError) as error:
            return KindPortForwardResult(
                **base, state="execution_failed", executed=False,
                message="No se pudo cerrar el port-forward remoto.",
                diagnostic=f"{type(error).__name__}",
            )
        if result.returncode != 0:
            return KindPortForwardResult(
                **base, state="execution_failed", executed=False,
                message="No se pudo cerrar el port-forward remoto.",
                diagnostic=self._sanitize_diagnostic(result.stderr or result.stdout),
            )
        return KindPortForwardResult(
            **base, state="stopped", executed=True, pid=pid,
            message="Port-forward cerrado correctamente.",
        )

    async def ensure_namespace(
        self,
        profile_id: str,
        cluster_name: str,
        namespace: str,
        dry_run: bool = True,
        confirmation: str | None = None,
    ) -> KindNamespaceEnsureResult:
        """Plan or create a namespace on an explicitly identified Kind cluster."""

        command = ("kubectl", "--context", f"kind-{cluster_name}", "create", "namespace", namespace)
        preview = list(command)
        if not _SAFE_NAME.fullmatch(cluster_name) or not _SAFE_NAME.fullmatch(namespace):
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="validation_failed",
                executed=False,
                command_preview=preview,
                message="El nombre del clúster o namespace no cumple el formato permitido.",
                recommended_action="Usa minúsculas, números, puntos o guiones.",
            )
        profile, error = await self._authorized_profile(profile_id)
        if error is not None or profile is None:
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="connection_failed",
                executed=False,
                command_preview=preview,
                message=error or "Perfil no disponible.",
                recommended_action="Valida el perfil autorizado antes de operar el namespace.",
            )
        if not dry_run and confirmation != ENSURE_NAMESPACE_CONFIRMATION:
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="confirmation_required",
                executed=False,
                command_preview=preview,
                message="La creación del namespace requiere confirmación explícita.",
                recommended_action=f"Proporciona confirmation={ENSURE_NAMESPACE_CONFIRMATION}.",
            )
        clusters = await self.list_clusters(profile_id)
        cluster = next((item for item in clusters.clusters if item.name == cluster_name), None)
        if cluster is None or not cluster.reachable:
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="cluster_unavailable",
                executed=False,
                command_preview=preview,
                message="El clúster solicitado no existe o no es alcanzable.",
                recommended_action="Lista los clústeres y selecciona uno alcanzable.",
            )
        try:
            existing = await self._remote(
                profile,
                "kubectl",
                "--context",
                cluster.context,
                "get",
                "namespace",
                namespace,
            )
        except (FileNotFoundError, TimeoutError):
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="discovery_failed",
                executed=False,
                command_preview=preview,
                message="No se pudo consultar el namespace existente.",
                recommended_action="Verifica kubectl y la conexión remota.",
            )
        if existing.returncode == 0:
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="already_exists",
                executed=False,
                command_preview=[],
                message="El namespace ya existe; no se planificó una creación.",
                recommended_action=f"Continúa con el despliegue en {namespace}.",
            )
        if dry_run:
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="planned",
                executed=False,
                command_preview=preview,
                message="Creación de namespace planificada; no se ejecutó ningún cambio.",
                recommended_action=f"Confirma con {ENSURE_NAMESPACE_CONFIRMATION} para ejecutar.",
            )
        try:
            result = await self._remote(profile, *command)
        except (FileNotFoundError, TimeoutError):
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="execution_failed",
                executed=False,
                command_preview=preview,
                message="No se pudo ejecutar la creación remota del namespace.",
                recommended_action="Revisa la conexión SSH y vuelve a intentarlo.",
            )
        if result.returncode != 0:
            return KindNamespaceEnsureResult(
                profile_id=profile_id,
                cluster_name=cluster_name,
                namespace=namespace,
                state="execution_failed",
                executed=False,
                command_preview=preview,
                message="kubectl no pudo crear el namespace.",
                recommended_action="Revisa el estado del clúster y los permisos de kubectl.",
            )
        return KindNamespaceEnsureResult(
            profile_id=profile_id,
            cluster_name=cluster_name,
            namespace=namespace,
            state="created",
            executed=True,
            command_preview=preview,
            message="Namespace creado correctamente.",
            recommended_action=f"Inspecciona nuevamente el namespace {namespace}.",
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

    async def _remote(
        self,
        profile: ConnectionProfile,
        *args: str,
        timeout_seconds: float | None = None,
        input_data: bytes | None = None,
    ) -> CommandResult:
        assert profile.ssh_profile is not None
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "PreferredAuthentications=publickey",
            profile.ssh_profile,
            *args,
        ]
        if input_data is None:
            return await self._runner.run(command, timeout_seconds or self._timeout)
        return await self._runner.run(command, timeout_seconds or self._timeout, input_data)

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
        if "no such image" in normalized or "image not found" in normalized:
            return "Kind no encuentra la imagen en el runtime Docker del host remoto."
        if "cannot load" in normalized or "failed to load" in normalized:
            return "Kind no pudo importar la imagen al cluster."
        return "Kind no pudo listar los clusters del host remoto; el comando devolvió un error."

    @staticmethod
    def _sanitize_diagnostic(output: str) -> str | None:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return " | ".join(lines[-3:])[:800] if lines else None

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
