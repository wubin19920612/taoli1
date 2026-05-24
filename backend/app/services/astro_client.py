from dataclasses import dataclass
import json
from typing import Any

import httpx


class AstroClientError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class AstroSdkConfig:
    base_url: str
    admin_prefix: str
    api_key: str
    verify_tls: bool = True
    timeout_seconds: float = 10.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.admin_prefix.strip() and self.api_key.strip())

    @property
    def normalized_admin_prefix(self) -> str:
        return self.admin_prefix.strip().strip("/")

    def _path_with_prefix(self, suffix: str) -> str:
        prefix = self.normalized_admin_prefix
        return f"/{prefix}{suffix}" if prefix else suffix

    @property
    def list_path(self) -> str:
        return self._path_with_prefix("/api/config/sdk-update-pair")

    @property
    def message_path(self) -> str:
        return self._path_with_prefix("/api/config/sdk-send-message")


class AstroSdkClient:
    def __init__(self, config: AstroSdkConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(
            timeout=config.timeout_seconds,
            verify=config.verify_tls,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    def status(self, dry_run_only: bool) -> dict[str, Any]:
        return {
            "configured": self.config.configured,
            "dry_run_only": dry_run_only,
            "base_url": self.config.base_url,
            "admin_prefix": self.config.admin_prefix,
            "api_key_configured": bool(self.config.api_key.strip()),
            "list_path": self.config.list_path,
            "pair_path": self.config.list_path,
            "message_path": self.config.message_path,
            "message": None if self.config.configured else "Astro SDK environment variables are incomplete.",
        }

    async def list_pairs(self) -> list[dict[str, Any]]:
        payload = {"action": "list"}
        response = await self._post(self.config.list_path, payload)
        if response.get("code") != 0:
            raise AstroClientError(response.get("message", "Astro list request failed"))
        data = response.get("data", [])
        if not isinstance(data, list):
            raise AstroClientError("Astro list response did not contain a list", 502)
        return [item for item in data if isinstance(item, dict)]

    async def add_pair(self, pair: dict[str, Any]) -> dict[str, Any]:
        return await self._mutate_pair("add", pair)

    async def update_pair(self, pair: dict[str, Any]) -> dict[str, Any]:
        return await self._mutate_pair("update", pair)

    async def _mutate_pair(self, action: str, pair: dict[str, Any]) -> dict[str, Any]:
        response = await self._post(self.config.list_path, {"action": action, "pair": pair})
        if response.get("code") != 0:
            raise AstroClientError(response.get("message", f"Astro {action} request failed"))
        return response

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.configured:
            raise AstroClientError("Astro SDK is not configured", 503)
        timestamp = self._timestamp()
        nonce = self._nonce()
        raw_body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        sign = self._sign(timestamp, nonce, path, raw_body)
        try:
            response = await self.client.post(
                self._url(path),
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "x-timestamp": str(timestamp),
                    "x-nonce": nonce,
                    "x-sign": sign,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            suffix = f": {detail[:200]}" if detail else ""
            raise AstroClientError(
                f"Astro HTTP {exc.response.status_code}{suffix}",
                exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise AstroClientError(f"Astro request failed: {exc}", 502) from exc
        try:
            parsed = response.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise AstroClientError("Astro returned invalid JSON", 502) from exc
        if not isinstance(parsed, dict):
            raise AstroClientError("Astro response must be an object", 502)
        return parsed

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _timestamp(self) -> int:
        import time

        return int(time.time() * 1000)

    def _nonce(self) -> str:
        import secrets

        return secrets.token_urlsafe(24)

    def _sign(self, timestamp: int, nonce: str, path: str, raw_body: str) -> str:
        import hashlib
        import hmac

        canonical_message = "\n".join(
            [str(timestamp), nonce, "POST", path, raw_body]
        )
        return hmac.new(
            self.config.api_key.encode("utf-8"),
            canonical_message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
