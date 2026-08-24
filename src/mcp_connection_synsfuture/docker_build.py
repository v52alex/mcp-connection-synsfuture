"""Local Docker project inspection and profile-scoped image builds."""

import re
from pathlib import Path

from .docker_read import IMAGE_PATTERN
from .models import ConnectionState, DockerBuildResult, DockerProjectInspectionResult
from .service import MCP_DOCUMENTATION_HINT, CommandRunner, ConnectionProfileService


class DockerBuildService:
    def __init__(self, runner: CommandRunner, connections: ConnectionProfileService):
        self._runner = runner
        self._connections = connections
        self._timeout = 900.0

    def inspect_project(self, project_path: str, dockerfile: str) -> DockerProjectInspectionResult:
        project = Path(project_path).expanduser().resolve()
        if not project.is_dir():
            raise ValueError("project_path must be an existing directory")
        dockerfile_path = (project / dockerfile).resolve()
        if project not in dockerfile_path.parents:
            raise ValueError("dockerfile must be inside project_path")
        dockerignore = project / ".dockerignore"
        warnings: list[str] = []
        if not dockerfile_path.is_file():
            warnings.append("El Dockerfile solicitado no existe.")
        ignored = {
            line.strip().rstrip("/")
            for line in dockerignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        } if dockerignore.is_file() else set()
        missing = {".env", ".git", ".venv"} - ignored
        if missing:
            warnings.append(".dockerignore debe excluir: " + ", ".join(sorted(missing)) + ".")
        return DockerProjectInspectionResult(
            project_path=str(project),
            dockerfile_path=str(dockerfile_path) if dockerfile_path.is_file() else None,
            dockerignore_path=str(dockerignore) if dockerignore.is_file() else None,
            build_ready=dockerfile_path.is_file() and not missing,
            warnings=warnings,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )

    async def build(
        self,
        profile_id: str,
        project_path: str,
        image_name: str,
        tag: str,
        dockerfile: str,
        dry_run: bool,
        confirmation: str | None,
    ) -> DockerBuildResult:
        if not IMAGE_PATTERN.fullmatch(image_name) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", tag
        ):
            raise ValueError("image_name or tag has an invalid format")
        inspection = self.inspect_project(project_path, dockerfile)
        reference = f"{image_name}:{tag}"
        project = Path(inspection.project_path)
        dockerfile_relative = str(
            Path(inspection.dockerfile_path or dockerfile).relative_to(project)
        )
        command = [
            "build",
            "--file",
            dockerfile_relative,
            "--tag",
            reference,
            inspection.project_path,
        ]
        preview = ["docker", "--context", "<profile-context>", *command]
        if not inspection.build_ready:
            return self._result(
                profile_id,
                "validation_failed",
                False,
                reference,
                inspection.project_path,
                preview,
                "El proyecto no está listo para build.",
            )
        if dry_run or confirmation != "CONFIRM_BUILD":
            return self._result(
                profile_id,
                "planned",
                False,
                reference,
                inspection.project_path,
                preview,
                "Build planificado. Usa confirmation='CONFIRM_BUILD' y "
                "dry_run=false para ejecutar.",
            )
        connection = await self._connections.connect(profile_id)
        if connection.state is not ConnectionState.READY or not connection.docker_context:
            return self._result(
                profile_id,
                "connection_failed",
                False,
                reference,
                inspection.project_path,
                preview,
                connection.message,
            )
        try:
            result = await self._runner.run(
                ["docker", "--context", connection.docker_context, *command], self._timeout
            )
        except TimeoutError:
            return self._result(
                profile_id,
                "build_failed",
                False,
                reference,
                inspection.project_path,
                preview,
                "El build agotó el tiempo de espera.",
            )
        state = "built" if result.returncode == 0 else "build_failed"
        message = (
            "Imagen construida correctamente."
            if result.returncode == 0
            else self._classify_build_failure(result.stderr)
        )
        return self._result(
            profile_id,
            state,
            result.returncode == 0,
            reference,
            inspection.project_path,
            preview,
            message,
        )

    @staticmethod
    def _classify_build_failure(stderr: str) -> str:
        """Return a bounded, non-sensitive build failure diagnosis."""

        normalized = stderr.lower()
        if "unable to prepare context" in normalized or (
            "context" in normalized and "not found" in normalized
        ):
            return "Docker no pudo preparar el contexto local para el daemon remoto."
        if "dockerfile" in normalized and (
            "not found" in normalized or "no such file" in normalized
        ):
            return "Docker no pudo encontrar el Dockerfile en el contexto enviado."
        if "permission denied" in normalized or "access is denied" in normalized:
            return "Docker rechazó el acceso al contexto o al daemon remoto."
        if "maven" in normalized or "could not transfer artifact" in normalized:
            return "El build falló al resolver o compilar dependencias Maven."
        if "failed to solve" in normalized:
            return "El builder de Docker no pudo resolver una etapa del Dockerfile."
        return "Docker rechazó el build; el diagnóstico remoto no fue concluyente."

    @staticmethod
    def _result(
        profile_id: str,
        state: str,
        executed: bool,
        reference: str,
        project: str,
        preview: list[str],
        message: str,
    ) -> DockerBuildResult:
        return DockerBuildResult(
            profile_id=profile_id,
            state=state,
            executed=executed,
            image_reference=reference,
            project_path=project,
            command_preview=preview,
            message=message,
            documentation_hint=MCP_DOCUMENTATION_HINT,
        )
