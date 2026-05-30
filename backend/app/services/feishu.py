import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_metrics import AlertObservation
from app.services.alert_messages import build_alert_message


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
        self.client = client or httpx.AsyncClient(timeout=10)

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
            return
        payload = self._build_payload(
            rule,
            opportunity,
            dashboard_url,
            observations=observations,
            template=template,
            prebuilt_text=prebuilt_text,
        )
        response = await self.client.post(self.config.webhook_url, json=payload)
        response.raise_for_status()

    async def send_text(self, text: str) -> None:
        if not self.config.webhook_url:
            return
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
        response.raise_for_status()

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
