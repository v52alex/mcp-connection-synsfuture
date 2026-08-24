"""Controlled Docker image pulls through an authorized profile."""

from .docker_read import IMAGE_PATTERN
from .models import ConnectionState, DockerMutationResult
from .service import MCP_DOCUMENTATION_HINT, CommandRunner, ConnectionProfileService

PULL_CONFIRMATION = "PULL_IMAGE_ON_DOCKER_REMOTE"


class DockerPullService:
    def __init__(self, runner: CommandRunner, connections: ConnectionProfileService):
        self._runner = runner
        self._connections = connections
        self._timeout = 900.0

    async def pull(
        self,
        profile_id: str,
        image_reference: str,
        dry_run: bool,
        confirmation: str | None,
    ) -> DockerMutationResult:
        if not IMAGE_PATTERN.fullmatch(image_reference):
            raise ValueError("image_reference has an invalid format")
        preview = [
            "docker",
            "--context",
            "<profile-context>",
            "image",
            "pull",
            image_reference,
        ]
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                profile_id,
                "connection_failed",
                False,
                image_reference,
                preview,
                f"El perfil no está listo: {connection.message}",
            )
        try:
            existing = await self._runner.run(
                [
                    "docker",
                    "--context",
                    connection.docker_context,
                    "image",
                    "inspect",
                    image_reference,
                ],
                30.0,
            )
        except TimeoutError:
            existing = None
        if existing is not None and existing.returncode == 0:
            return self._result(
                profile_id,
                "already_exists",
                False,
                image_reference,
                [],
                "La imagen ya existe en el Docker remoto; no se descargó nuevamente.",
            )
        if dry_run or confirmation != PULL_CONFIRMATION:
            return self._result(
                profile_id,
                "planned" if dry_run else "confirmation_required",
                False,
                image_reference,
                preview,
                f"Pull planificado. Usa confirmation='{PULL_CONFIRMATION}' y "
                "dry_run=false para ejecutar.",
            )
        try:
            result = await self._runner.run(
                [
                    "docker",
                    "--context",
                    connection.docker_context,
                    "image",
                    "pull",
                    image_reference,
                ],
                self._timeout,
            )
        except TimeoutError:
            return self._result(
                profile_id,
                "pull_failed",
                False,
                image_reference,
                preview,
                "La descarga de la imagen agotó el tiempo de espera.",
            )
        if result.returncode != 0:
            return self._result(
                profile_id,
                "pull_failed",
                False,
                image_reference,
                preview,
                self._classify_failure(result.stderr),
            )
        return self._result(
            profile_id,
            "pulled",
            True,
            image_reference,
            preview,
            "Imagen descargada correctamente.",
        )

    @staticmethod
    def _classify_failure(stderr: str) -> str:
        normalized = stderr.lower()
        if "unauthorized" in normalized or "authentication required" in normalized:
            return "El registry requiere autenticación para descargar la imagen."
        if "network" in normalized or "timeout" in normalized or "connection" in normalized:
            return "El Docker remoto no pudo conectarse al registry."
        if "not found" in normalized or "manifest unknown" in normalized:
            return "La imagen o etiqueta no existe en el registry."
        return "Docker no pudo descargar la imagen desde el registry."

    @staticmethod
    def _result(
        profile_id: str,
        state: str,
        executed: bool,
        target: str,
        preview: list[str],
        message: str,
    ) -> DockerMutationResult:
        return DockerMutationResult(
            operation="pull_image",
            profile_id=profile_id,
            state=state,
            executed=executed,
            target=target,
            command_preview=preview,
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
