"""Firebase HTTP v1 transport. Raw credentials and provider error bodies stay private."""

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

logger = logging.getLogger(__name__)
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class PushMessage:
    kind: str
    title: str
    body: str
    card_id: str = ""
    card_name: str = ""
    channel: str = "normal"

    def payload(self, token):
        return {
            "message": {
                "token": token,
                "notification": {"title": self.title, "body": self.body},
                "data": {"kind": self.kind, "card_id": self.card_id, "card_name": self.card_name},
                "android": {
                    "priority": "high",
                    "notification": {
                        "channel_id": self.channel,
                        "tag": f"{self.kind}:{self.card_id}",
                    },
                },
            }
        }


@dataclass(frozen=True)
class SendResult:
    sent: bool
    reason: str
    invalid: bool = False


def _invalid_device(response):
    if response.status_code == 404:
        return True
    error = response.json().get("error", {})
    details = error.get("details", [])
    codes = {error.get("status")} | {entry.get("errorCode") for entry in details}
    if "UNREGISTERED" in codes:
        return True
    if "INVALID_ARGUMENT" not in codes:
        return False
    # INVALID_ARGUMENT can also refer to a malformed payload. Only retire the
    # device if Google specifically attributes the rejection to its token.
    detail = str(error.get("message", "")).lower()
    return (
        "registration token" in detail
        and ("not a valid" in detail or "invalid registration token" in detail)
    ) or any(
        v.get("field") == "message.token"
        for entry in details
        for v in entry.get("fieldViolations", [])
    )


class FcmClient:
    def __init__(self, encoded_account, client=None):
        self._client = client
        self._key = None
        self._access = ""
        self._refresh_at = self._oauth_backoff = 0.0
        self._lock = asyncio.Lock()
        if not encoded_account:
            logger.info("FCM 未配置，手机推送已关闭")
            return
        try:
            account = json.loads(base64.b64decode(encoded_account, validate=True))
            self._project = account["project_id"]
            self._email = account["client_email"]
            key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
            if not (
                re.fullmatch(r"[A-Za-z0-9._-]+", self._project)
                and isinstance(self._email, str)
                and self._email
                and isinstance(key, rsa.RSAPrivateKey)
            ):
                raise ValueError
            self._key = key
        except Exception:  # noqa: BLE001 - key parsers can include private input in diagnostics.
            logger.warning("FCM 服务账号配置无效，手机推送已关闭")

    @property
    def enabled(self):
        return self._key is not None

    def _assertion(self):
        now = int(time.time())
        claims = {
            "iss": self._email,
            "scope": SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        unsigned = ".".join(
            _base64url(json.dumps(part).encode())
            for part in (
                {"alg": "RS256", "typ": "JWT"},
                claims,
            )
        )
        signature = self._key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{unsigned}.{_base64url(signature)}"

    async def _access_token(self):
        async with self._lock:
            now = time.monotonic()
            if self._access and now < self._refresh_at:
                return self._access
            if now < self._oauth_backoff:
                return None
            self._oauth_backoff = now + 30
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self._assertion(),
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            access, ttl = data["access_token"], int(data["expires_in"])
            if not isinstance(access, str) or not access or ttl <= 0:
                raise ValueError
            self._access, self._refresh_at = access, now + ttl - 300
            self._oauth_backoff = 0
            return access

    async def send(self, token: str, message: PushMessage) -> SendResult:
        if not self.enabled:
            return SendResult(False, "手机推送未启用")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        try:
            url = f"https://fcm.googleapis.com/v1/projects/{self._project}/messages:send"
            for attempt in range(2):
                access = await self._access_token()
                if not access:
                    return SendResult(False, "推送授权暂不可用")
                response = await self._client.post(
                    url,
                    headers={"Authorization": f"Bearer {access}"},
                    json=message.payload(token),
                    timeout=10,
                )
                if response.is_success:
                    return SendResult(True, "已发送")
                if response.status_code == 401 and attempt == 0:
                    if self._access == access:
                        self._access = ""
                    continue
                logger.warning("FCM 发送失败，HTTP %d", response.status_code)
                if _invalid_device(response):
                    return SendResult(False, "设备令牌已失效", invalid=True)
                break
        except Exception:  # noqa: BLE001 - never surface raw provider errors or credentials.
            logger.warning("FCM 授权或发送失败")
        return SendResult(False, "推送服务暂不可用")

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
