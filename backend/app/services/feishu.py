import base64
import hashlib
import hmac
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
