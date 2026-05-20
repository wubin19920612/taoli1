from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


ALERT_TYPE_DESCRIPTIONS = {
    "SF": "现货买入 / 永续卖出",
    "FF": "永续买入 / 永续卖出",
    "SS": "现货买入 / 现货卖出",
}

ALERT_SEVERITY_DESCRIPTIONS = {
    "info": "仅记录",
    "warning": "普通告警",
    "critical": "强提醒",
}


DEFAULT_EXCLUDED_RISK_LABELS = [
    "LOW_VOLUME",
    "STALE_DATA",
    "HUGE_SPREAD_VERIFY",
    "WIDE_SPREAD",
    "SAME_TICKER_RISK",
    "MISSING_FUNDING",
]


class AlertRule(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(description="规则名称，只用于你自己识别这条告警模板。")
    enabled: bool = Field(default=True, description="关闭后这条规则不会参与评估，也不会发告警。")
    types: list[str] = Field(
        default_factory=lambda: ["SF", "FF", "SS"],
        description="要监控的套利类型。SF=现货买入 / 永续卖出，FF=永续买入 / 永续卖出，SS=现货买入 / 现货卖出。",
    )
    include_exchanges: list[str] = Field(
        default_factory=list,
        description="只匹配这些交易所，留空表示不限制。",
    )
    exclude_exchanges: list[str] = Field(default_factory=list, description="这些交易所会被排除。")
    include_symbols: list[str] = Field(
        default_factory=list,
        description="只匹配这些标的，留空表示不限制。",
    )
    exclude_symbols: list[str] = Field(default_factory=list, description="这些标的会被排除。")
    min_open_spread_pct: float = Field(default=0.0, description="开仓价差达到这个百分比才算命中。")
    min_fee_adjusted_open_pct: float = Field(
        default=0.0,
        description="扣除手续费和滑点后的净开仓价差阈值。",
    )
    min_volume_24h_usdt: float = Field(
        default=0.0,
        description="买卖两侧较小的 24h 成交额必须达到这个值。",
    )
    max_data_age_seconds: int = Field(default=600, description="行情最后更新时间距离当前时间不能超过这个秒数。")
    excluded_risk_labels: list[str] = Field(
        default_factory=lambda: DEFAULT_EXCLUDED_RISK_LABELS.copy(),
        description="命中这些风险标签就不发告警。",
    )
    consecutive_hits: int = Field(default=3, ge=1, description="同一机会需要连续满足多少轮才触发。")
    cooldown_seconds: int = Field(default=300, ge=0, description="同一机会触发后，多少秒内不重复发送。")
    severity: AlertSeverity = Field(
        default=AlertSeverity.INFO,
        description="告警等级：info=仅记录，warning=普通告警，critical=强提醒。",
    )


class AlertEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    rule_id: str
    opportunity_id: str
    symbol: str
    status: str
    message: str
    created_at: datetime
