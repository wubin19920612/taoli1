import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.models.alert import ALERT_SEVERITY_DESCRIPTIONS, ALERT_TYPE_DESCRIPTIONS, AlertRule
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
            "【告警触发】",
            f"规则：{rule.name}",
            f"等级：{rule.severity}（{ALERT_SEVERITY_DESCRIPTIONS.get(rule.severity.value, rule.severity.value)}）",
            "",
            "【规则参数】",
            f"套利类型：{self._describe_types(rule.types)}",
            f"包含交易所：{self._describe_values(rule.include_exchanges)}",
            f"排除交易所：{self._describe_values(rule.exclude_exchanges)}",
            f"包含标的：{self._describe_values(rule.include_symbols)}",
            f"排除标的：{self._describe_values(rule.exclude_symbols)}",
            f"开仓阈值：>= {self._format_percent(rule.min_open_spread_pct)}",
            f"净估算阈值：>= {self._format_percent(rule.min_fee_adjusted_open_pct)}",
            f"最低成交额：>= {self._format_volume_k(rule.min_volume_24h_usdt)}",
            f"数据时效：<= {rule.max_data_age_seconds}s",
            f"排除风险：{self._describe_values(rule.excluded_risk_labels)}",
            f"连续命中：{rule.consecutive_hits} 次",
            f"冷却时间：{rule.cooldown_seconds}s",
            "",
            "【行情快照】",
            f"标的：{opportunity.symbol} / {opportunity.type}",
            f"买入腿：{opportunity.buy_exchange} {opportunity.buy_market_type}",
            f"卖出腿：{opportunity.sell_exchange} {opportunity.sell_market_type}",
            f"开仓价差：{self._format_percent(opportunity.open_spread_pct)}",
            f"平仓价差：{self._format_percent(opportunity.close_spread_pct)}",
            f"净估算：{self._format_percent(opportunity.fee_adjusted_open_pct)}",
            (
                "资金费率："
                f"{self._format_percent(opportunity.funding_rate_buy_pct, digits=2)} / "
                f"{self._format_percent(opportunity.funding_rate_sell_pct, digits=2)}"
                f"（净：{self._format_percent(opportunity.net_funding_pct, digits=2)}）"
            ),
            (
                "预测资金费率："
                f"{self._format_percent(opportunity.funding_next_rate_buy_pct, digits=2)} / "
                f"{self._format_percent(opportunity.funding_next_rate_sell_pct, digits=2)}"
                f"（净：{self._format_percent(opportunity.net_funding_next_pct, digits=2)}）"
            ),
            (
                "下一次结算："
                f"{self._format_time(opportunity.funding_next_time_buy)} / "
                f"{self._format_time(opportunity.funding_next_time_sell)}"
            ),
            f"风险：{', '.join(opportunity.risk_labels) if opportunity.risk_labels else '无'}",
        ]
        if dashboard_url:
            lines.extend(["", f"Dashboard: {dashboard_url}"])
        payload: dict = {"msg_type": "text", "content": {"text": "\n".join(lines)}}
        if self.config.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp)
        return payload

    def _format_percent(self, value: float | None, digits: int = 3) -> str:
        if value is None:
            return "-"
        return f"{value:.{digits}f}%"

    def _format_volume_k(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{int(round(value / 1000))}K USDT"

    def _format_time(self, value: datetime | None) -> str:
        if value is None:
            return "-"
        return value.strftime("%H:%M")

    def _describe_values(self, values: list[str], empty: str = "全部") -> str:
        return ", ".join(values) if values else empty

    def _describe_types(self, values: list[str]) -> str:
        if not values:
            return "全部"
        items: list[str] = []
        for item in values:
            items.append(f"{item}（{ALERT_TYPE_DESCRIPTIONS.get(item, item)}）")
        return ", ".join(items)

    def _sign(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.config.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")
