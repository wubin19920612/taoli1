import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_messages import build_alert_message
from app.services.alert_metrics import AlertObservation
from app.services.big_spread_alert import BIG_SPREAD_RULE_ID

logger = logging.getLogger(__name__)


def _event_text(text: str, secrets: tuple[str, ...]) -> str:
    # Redact signed URLs, credential headers and echoed configuration from
    # the copy used for the mobile alert journal and push.
    text = re.sub(r"https?://[^\s<>]+", "[链接]", text, flags=re.IGNORECASE)
    text = re.sub(r"-----BEGIN [\w ]*PRIVATE KEY-----.*?(?:-----END [\w ]*PRIVATE KEY-----|$)",
                  "[已隐藏]", text, flags=re.DOTALL)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+", r"\1 [已隐藏]", text)
    # A Cookie header can contain several credentials separated by semicolons.
    text = re.sub(
        r"""(?i)(\b(?:set-cookie|cookie)["']?[ \t]*[:=][ \t]*)[^\r\n]+""",
        r"\1[已隐藏]", text,
    )
    text = re.sub(
        r"""(?ix)(?<![\w-])(["']?(?:[\w-]*(?:token|secret|password|passphrase|api[_-]?key|access[_-]?key|private[_-]?key)"""
        r"""|ok-access-[\w-]+|sign(?:ature)?|authorization|taoli1_session)["']?\s*[:=]\s*)"""
        r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)""",
        r"\1[已隐藏]", text,
    )
    text = re.sub(r"(?i)(?<![a-z0-9])(?:0x)?[a-f0-9]{64}(?![a-z0-9])", "[已隐藏]", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[已隐藏]")
    return text


@dataclass(frozen=True)
class FeishuConfig:
    webhook_url: str
    secret: str = ""
    app_id: str = ""
    app_secret: str = ""
    alert_chat_id: str = ""
    phone_user_ids: list[str] | None = None
    phone_user_id_type: str = "open_id"
    phone_enabled: bool = False


class FeishuNotifier:
    def __init__(self, config: FeishuConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client
        self._owns_client = client is None
        self.event_repo = None
        self._record_tasks: set[asyncio.Task] = set()

    def _schedule_record(self, text: str, *, kind=None, symbol=None, level="normal"):
        if self.event_repo is None:
            return
        task = asyncio.create_task(self._record(text, kind=kind, symbol=symbol, level=level))
        self._record_tasks.add(task)
        task.add_done_callback(self._record_tasks.discard)

    async def _record(self, text: str, *, kind, symbol, level):
        try:
            secrets = (self.config.webhook_url, self.config.secret, self.config.app_secret)
            body = _event_text(text, secrets)[:2000]
            title = next((line.strip() for line in body.splitlines() if line.strip()), "提醒")[:160]
            if kind is None:
                kind = next((key for word, key in (
                    ("[Phone price alert]", "price"),
                    ("大价差", "big_spread"),
                    ("公告", "announcement"), ("快到时间提醒", "announcement"),
                    ("登录", "auth"), ("价格提醒", "price"),
                    ("Hyperliquid 增仓恢复", "trade_status"), ("交易可用性", "trade_status"),
                    ("交易状态", "trade_status"), ("可交易性", "trade_status"),
                    ("指数成分", "index_component"), ("溢价", "premium"),
                    ("资金费", "funding"),
                ) if word in title), "general")
            await asyncio.wait_for(self.event_repo.record(
                kind=kind, title=title, body=body, level=level,
                symbol=_event_text(symbol, secrets) if symbol else None,
            ), timeout=1)
            push = getattr(self, "mobile_push", None)
            if push is not None:
                if kind == "big_spread":
                    push.queue_big_spread(title, body, _event_text(symbol, secrets) if symbol else "")
                else:
                    push.queue_feishu(kind, title, body, urgent=level == "urgent")
        except Exception:  # noqa: BLE001 - journal failures must never affect delivery.
            logger.warning("Failed to record mobile alert event")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def aclose(self) -> None:
        if self._record_tasks:
            await asyncio.gather(*self._record_tasks)
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def send_alert(
        self,
        rule: AlertRule,
        opportunity: Opportunity,
        dashboard_url: str = "",
        observations: list[AlertObservation] | None = None,
        template: AlertMessageTemplateSettings | None = None,
        prebuilt_text: str | None = None,
    ) -> None:
        if not self.config.webhook_url:
            raise RuntimeError("Feishu webhook is not configured")
        payload = self._build_payload(
            rule,
            opportunity,
            dashboard_url,
            observations=observations,
            template=template,
            prebuilt_text=prebuilt_text,
        )
        response = await self.client.post(self.config.webhook_url, json=payload)
        self._checked_webhook_payload(response, "send alert")
        kind = "big_spread" if getattr(rule, "id", None) == BIG_SPREAD_RULE_ID else "spread"
        self._schedule_record(payload["content"]["text"], kind=kind, symbol=opportunity.symbol)

    async def send_text(self, text: str) -> None:
        if not self.config.webhook_url:
            raise RuntimeError("Feishu webhook is not configured")
        payload: dict = {
            "msg_type": "text",
            "content": {
                "text": text,
            },
        }
        if self.config.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        response = await self.client.post(self.config.webhook_url, json=payload)
        self._checked_webhook_payload(response, "send text")
        self._schedule_record(text)

    async def send_phone_urgent_text(self, text: str) -> None:
        user_ids = self.config.phone_user_ids or []
        if (
            not self.config.phone_enabled
            or not self.config.app_id
            or not self.config.app_secret
            or not self.config.alert_chat_id
            or not user_ids
        ):
            return
        token = await self._tenant_access_token()
        message_id = await self._create_open_platform_text_message(token, text)
        await self._send_phone_urgent(token, message_id, user_ids)
        self._schedule_record(text, level="urgent")

    async def _tenant_access_token(self) -> str:
        response = await self.client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.config.app_id,
                "app_secret": self.config.app_secret,
            },
        )
        payload = self._checked_open_platform_payload(response, "get tenant access token")
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Feishu tenant_access_token response missing token")
        return token

    async def _create_open_platform_text_message(self, token: str, text: str) -> str:
        response = await self.client.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": self.config.alert_chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False, separators=(",", ":")),
            },
        )
        payload = self._checked_open_platform_payload(response, "create text message")
        data = payload.get("data")
        message_id = data.get("message_id") if isinstance(data, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("Feishu create message response missing message_id")
        return message_id

    async def _send_phone_urgent(self, token: str, message_id: str, user_ids: list[str]) -> None:
        response = await self.client.patch(
            (
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/urgent_phone"
                f"?user_id_type={self.config.phone_user_id_type}"
            ),
            headers={"Authorization": f"Bearer {token}"},
            json={"user_id_list": user_ids},
        )
        payload = self._checked_open_platform_payload(response, "send phone urgent")
        data = payload.get("data")
        invalid_user_ids = data.get("invalid_user_id_list") if isinstance(data, dict) else None
        if invalid_user_ids:
            invalid = ", ".join(str(user_id) for user_id in invalid_user_ids)
            raise RuntimeError(f"Feishu send phone urgent invalid phone user IDs: {invalid}")

    def _checked_open_platform_payload(self, response: httpx.Response, action: str) -> dict:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Feishu {action} response must be a JSON object")
        code = payload.get("code")
        if code not in (None, 0):
            msg = payload.get("msg") or payload.get("message") or "unknown error"
            error = payload.get("error")
            log_id = error.get("log_id") if isinstance(error, dict) else None
            detail = f"Feishu {action} failed: code={code}, msg={msg}"
            if log_id:
                detail = f"{detail}, log_id={log_id}"
            raise RuntimeError(detail)
        return payload

    def _checked_webhook_payload(self, response: httpx.Response, action: str) -> dict:
        response.raise_for_status()
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Feishu webhook {action} response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Feishu webhook {action} response must be a JSON object")

        code = payload.get("code")
        if code is None:
            code = payload.get("StatusCode")
        if code not in (0, "0"):
            message = (
                payload.get("msg")
                or payload.get("message")
                or payload.get("StatusMessage")
                or "unknown error"
            )
            raise RuntimeError(
                f"Feishu webhook {action} failed: code={code}, message={message}"
            )
        return payload

    def _build_payload(
        self,
        rule: AlertRule,
        opportunity: Opportunity,
        dashboard_url: str,
        observations: list[AlertObservation] | None = None,
        template: AlertMessageTemplateSettings | None = None,
        prebuilt_text: str | None = None,
    ) -> dict:
        text = prebuilt_text or build_alert_message(
            rule,
            opportunity,
            dashboard_url,
            observations=observations,
            template=template,
        )
        payload: dict = {
            "msg_type": "text",
            "content": {
                "text": text
            },
        }
        if self.config.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        return payload

    def _sign(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.config.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")
