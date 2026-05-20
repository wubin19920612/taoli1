import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from app.models.service_control import (
    ServiceControlDetail,
    ServiceControlStatus,
    ServiceRestartResult,
)

logger = logging.getLogger(__name__)

ALLOWED_SERVICES = ("backend", "frontend")
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"


class ServiceControlError(Exception):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ServiceControlConfig:
    enabled: bool
    environment: str
    compose_project_name: str | None = ""
    docker_socket_path: str = "/var/run/docker.sock"
    restart_delay_seconds: float = 1.0
    services: tuple[str, ...] = ALLOWED_SERVICES


class DockerServiceController:
    def __init__(self, config: ServiceControlConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._tasks: set[asyncio.Task] = set()

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()

    async def get_status(self) -> ServiceControlStatus:
        if not self.config.enabled:
            return self._disabled_status("Service control is disabled")
        if not os.path.exists(self.config.docker_socket_path):
            return self._disabled_status(
                f"Docker socket is not mounted: {self.config.docker_socket_path}"
            )

        try:
            project_name = await self._compose_project_name()
            if project_name is None:
                return self._disabled_status("Compose project name is not configured")
            details = [
                await self._service_detail(service, project_name)
                for service in self.config.services
            ]
            return ServiceControlStatus(
                enabled=True,
                environment=self.config.environment,
                services=list(self.config.services),
                details=details,
            )
        except Exception as exc:  # noqa: BLE001 - report safe status instead of breaking settings.
            logger.warning("service control status failed", exc_info=exc)
            return self._disabled_status(str(exc) or exc.__class__.__name__)

    async def restart(self, service: str) -> ServiceRestartResult:
        normalized = service.lower()
        if normalized not in self.config.services:
            raise ServiceControlError(f"Unsupported service: {service}", status_code=404)
        if not self.config.enabled:
            raise ServiceControlError("Service control is disabled", status_code=403)
        if not os.path.exists(self.config.docker_socket_path):
            raise ServiceControlError(
                f"Docker socket is not mounted: {self.config.docker_socket_path}",
                status_code=503,
            )

        project_name = await self._compose_project_name()
        if project_name is None:
            raise ServiceControlError("Compose project name is not configured", status_code=503)
        container = await self._find_service_container(normalized, project_name)
        if container is None:
            raise ServiceControlError(f"Container for service {normalized} was not found", 404)

        if normalized == "backend":
            task = asyncio.create_task(self._restart_after_delay(normalized, str(container["Id"])))
            self._tasks.add(task)
            task.add_done_callback(self._on_task_done)
            return ServiceRestartResult(
                service=normalized,
                status="queued",
                message="Backend restart queued; confirmation will arrive after the API reconnects.",
            )

        await self._restart_container(str(container["Id"]))
        return ServiceRestartResult(
            service=normalized,
            status="restarted",
            message="Frontend restart triggered.",
        )

    def _disabled_status(self, message: str) -> ServiceControlStatus:
        return ServiceControlStatus(
            enabled=False,
            environment=self.config.environment,
            services=list(self.config.services),
            details=[
                ServiceControlDetail(name=service, available=False)
                for service in self.config.services
            ],
            message=message,
        )

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("service restart task failed")

    async def _restart_after_delay(self, service: str, container_id: str) -> None:
        await asyncio.sleep(self.config.restart_delay_seconds)
        await self._restart_container(container_id)
        logger.info("queued docker restart for service %s", service)

    async def _restart_container(self, container_id: str) -> None:
        await self._request("POST", f"/containers/{container_id}/restart", params={"t": "10"})

    async def _service_detail(
        self,
        service: str,
        project_name: str | None,
    ) -> ServiceControlDetail:
        container = await self._find_service_container(service, project_name)
        if container is None:
            return ServiceControlDetail(name=service, available=False)
        names = container.get("Names") or []
        container_name = names[0].lstrip("/") if names else None
        return ServiceControlDetail(
            name=service,
            available=True,
            container_id=str(container.get("Id", ""))[:12] or None,
            container_name=container_name,
            state=container.get("State"),
            status=container.get("Status"),
        )

    async def _compose_project_name(self) -> str | None:
        project_name = (self.config.compose_project_name or "").strip()
        return project_name or None

    async def _find_service_container(
        self,
        service: str,
        project_name: str | None,
    ) -> dict[str, Any] | None:
        labels = [f"{COMPOSE_SERVICE_LABEL}={service}"]
        if project_name:
            labels.append(f"{COMPOSE_PROJECT_LABEL}={project_name}")
        filters = json.dumps({"label": labels})
        containers = await self._request(
            "GET",
            "/containers/json",
            params={"all": "true", "filters": filters},
        )
        if not isinstance(containers, list):
            return None
        running = [item for item in containers if item.get("State") == "running"]
        candidates = running or containers
        return candidates[0] if candidates else None

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        client = self._docker_client()
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def _docker_client(self) -> httpx.AsyncClient:
        if self._client is None:
            transport = httpx.AsyncHTTPTransport(uds=self.config.docker_socket_path)
            self._client = httpx.AsyncClient(transport=transport, base_url="http://docker")
        return self._client
