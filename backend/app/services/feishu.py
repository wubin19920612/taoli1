import base64
import hashlib
import hmac
import time
from dataclasses import dataclass

import httpx

from app.models.alert import AlertRule
from app.models.opportunity import Opportunity


@dataclass(frozen=True)
class FeishuConfig:
    webhook_url: str
    secret: str = ""


class FeishuNotifier:
    def __init__(self, config: FeishuConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client or httpx.AsyncClient(timeout=10)

    async def send_alert(self, rule: AlertRule, opportunity: Opportunity, dashboard_url: str = "") -> None:
        if not self.config.webhook_url:
            return
        payload = self._build_payload(rule, opportunity, dashboard_url)
        response = await self.client.post(self.config.webhook_url, json=payload)
        response.raise_for_status()

    def _build_payload(self, rule: AlertRule, opportunity: Opportunity, dashboard_url: str) -> dict:
        lines = [
            f"Rule: {rule.name} ({rule.severity})",
            f"Symbol: {opportunity.symbol} / {opportunity.type}",
            f"Buy: {opportunity.buy_exchange} {opportunity.buy_market_type}",
            f"Sell: {opportunity.sell_exchange} {opportunity.sell_market_type}",
            f"Open spread: {opportunity.open_spread_pct:.3f}%",
            f"Close spread: {opportunity.close_spread_pct:.3f}%",
            f"Net estimate: {opportunity.fee_adjusted_open_pct:.3f}%",
            f"Funding: {opportunity.funding_rate_buy_pct} / {opportunity.funding_rate_sell_pct}",
            f"Risk: {', '.join(opportunity.risk_labels) if opportunity.risk_labels else 'none'}",
        ]
        if dashboard_url:
            lines.append(f"Dashboard: {dashboard_url}")
        payload: dict = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
        if self.config.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        return payload

    def _sign(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.config.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")
