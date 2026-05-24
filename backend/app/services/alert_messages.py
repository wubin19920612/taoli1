from datetime import UTC, datetime, timedelta, timezone

from app.models.alert import ALERT_SEVERITY_DESCRIPTIONS, ALERT_TYPE_DESCRIPTIONS, AlertRule
from app.models.opportunity import Opportunity
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_metrics import AlertObservation, combined_open_edge_pct

ALERT_DISPLAY_TIMEZONE = timezone(timedelta(hours=8), "UTC+8")


def build_alert_message(
    rule: AlertRule,
    opportunity: Opportunity,
    dashboard_url: str = "",
    observations: list[AlertObservation] | None = None,
    template: AlertMessageTemplateSettings | None = None,
) -> str:
    settings = template or AlertMessageTemplateSettings()
    lines: list[str] = []

    if settings.include_trigger_summary:
        _append_block(
            lines,
            "【告警触发】",
            [
                f"规则：{rule.name}",
                (
                    f"等级：{rule.severity}"
                    f"（{ALERT_SEVERITY_DESCRIPTIONS.get(rule.severity.value, rule.severity.value)}）"
                ),
            ],
        )

    if settings.include_rule_details:
        _append_block(
            lines,
            "【规则参数】",
            [
                f"套利类型：{_describe_types(rule.types)}",
                f"包含交易所：{_describe_values(rule.include_exchanges)}",
                f"排除交易所：{_describe_values(rule.exclude_exchanges)}",
                f"包含标的：{_describe_values(rule.include_symbols)}",
                "排除标的：继承实时机会页隐藏黑名单",
                f"开仓阈值：>= {_format_percent(rule.min_open_spread_pct)}",
                f"综合开仓阈值：>= {_format_percent(rule.min_fee_adjusted_open_pct)}",
                f"最低成交额：>= {_format_volume_k(rule.min_volume_24h_usdt)}",
                f"数据时效：<= {rule.max_data_age_seconds}s",
                f"排除风险：{_describe_values(rule.excluded_risk_labels)}",
                f"连续命中：{rule.consecutive_hits} 次",
                f"冷却时间：{rule.cooldown_seconds}s",
            ],
        )

    snapshot_lines: list[str] = []
    if settings.include_pair:
        snapshot_lines.extend(
            [
                f"标的：{opportunity.symbol} / {opportunity.type}",
                (
                    "价差对："
                    f"{opportunity.symbol} | "
                    f"{opportunity.buy_exchange} {opportunity.buy_market_type} -> "
                    f"{opportunity.sell_exchange} {opportunity.sell_market_type}"
                ),
                (
                    "方向："
                    f"买入 {opportunity.buy_exchange} {opportunity.buy_market_type} {opportunity.symbol}，"
                    f"卖出 {opportunity.sell_exchange} {opportunity.sell_market_type} {opportunity.symbol}"
                ),
                f"买入腿：{opportunity.buy_exchange} {opportunity.buy_market_type}",
                f"卖出腿：{opportunity.sell_exchange} {opportunity.sell_market_type}",
            ]
        )
    if settings.include_spread:
        snapshot_lines.extend(
            [
                (
                    "价差："
                    f"开仓 {_format_percent(opportunity.open_spread_pct)} / "
                    f"平仓 {_format_percent(opportunity.close_spread_pct)}"
                ),
                f"开仓价差：{_format_percent(opportunity.open_spread_pct)}",
                f"平仓价差：{_format_percent(opportunity.close_spread_pct)}",
                f"净估算：{_format_percent(opportunity.fee_adjusted_open_pct)}",
                f"综合开仓：{_format_percent(combined_open_edge_pct(opportunity))}",
            ]
        )
    if settings.include_funding:
        snapshot_lines.extend(
            [
                (
                    "资金费率差（日化）："
                    f"当前 {_format_percent(_current_funding_daily_pct(opportunity), digits=2)} / "
                    f"预测 {_format_percent(_next_funding_daily_pct(opportunity), digits=2)}"
                ),
                (
                    "资金费率："
                    f"{_format_percent(opportunity.funding_rate_buy_pct, digits=2)} / "
                    f"{_format_percent(opportunity.funding_rate_sell_pct, digits=2)}"
                    f"（日化净：{_format_percent(_current_funding_daily_pct(opportunity), digits=2)}）"
                ),
                (
                    "预测资金费率："
                    f"{_format_percent(opportunity.funding_next_rate_buy_pct, digits=2)} / "
                    f"{_format_percent(opportunity.funding_next_rate_sell_pct, digits=2)}"
                    f"（日化净：{_format_percent(_next_funding_daily_pct(opportunity), digits=2)}）"
                ),
                (
                    "下一次结算："
                    f"{_format_time(opportunity.funding_next_time_buy)} / "
                    f"{_format_time(opportunity.funding_next_time_sell)}"
                ),
                (
                    "结算周期："
                    f"{_format_interval(opportunity.buy_funding_interval_hours)} / "
                    f"{_format_interval(opportunity.sell_funding_interval_hours)}"
                ),
            ]
        )
    if settings.include_volume:
        snapshot_lines.append(
            "成交额："
            f"买入侧 {_format_volume_k(opportunity.buy_volume_24h_usdt)} / "
            f"卖出侧 {_format_volume_k(opportunity.sell_volume_24h_usdt)}"
        )
    if settings.include_risk:
        snapshot_lines.append(
            f"风险：{', '.join(opportunity.risk_labels) if opportunity.risk_labels else '无'}"
        )
    _append_block(lines, "【行情快照】", snapshot_lines)

    if settings.include_observations and observations:
        observation_lines = []
        selected_observations = observations[-settings.observation_limit :]
        for index, item in enumerate(selected_observations, start=1):
            observation_lines.append(
                f"{index}. {_format_time_with_seconds(item.observed_at)} | "
                f"价差 {_format_percent(item.open_spread_pct)} | "
                f"净估算 {_format_percent(item.fee_adjusted_open_pct)} | "
                f"资金差（日化） {_format_percent(item.funding_edge_pct, digits=2)} | "
                f"综合 {_format_percent(item.combined_open_edge_pct)}"
            )
        _append_block(lines, "【连续监测】", observation_lines)

    if settings.include_dashboard_link and dashboard_url:
        _append_block(lines, "", [f"Dashboard: {dashboard_url}"])
    if not lines:
        lines = [f"{opportunity.symbol} / {opportunity.type}"]
    return "\n".join(lines)


def _append_block(lines: list[str], title: str, rows: list[str]) -> None:
    if not rows:
        return
    if lines:
        lines.append("")
    if title:
        lines.append(title)
    lines.extend(rows)


def _format_percent(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def _format_volume_k(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value / 1000))}K USDT"


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    return _to_alert_display_timezone(value).strftime("%H:%M")


def _format_interval(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value}h"


def _format_time_with_seconds(value: datetime | None) -> str:
    if value is None:
        return "-"
    return _to_alert_display_timezone(value).strftime("%H:%M:%S")


def _to_alert_display_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ALERT_DISPLAY_TIMEZONE)


def _describe_values(values: list[str], empty: str = "全部") -> str:
    return ", ".join(values) if values else empty


def _describe_types(values: list[str]) -> str:
    if not values:
        return "全部"
    items: list[str] = []
    for item in values:
        items.append(f"{item}（{ALERT_TYPE_DESCRIPTIONS.get(item, item)}）")
    return ", ".join(items)


def _current_funding_daily_pct(opportunity: Opportunity) -> float | None:
    if opportunity.net_funding_daily_pct is not None:
        return opportunity.net_funding_daily_pct
    if opportunity.net_funding_hourly_pct is not None:
        return opportunity.net_funding_hourly_pct * 24
    return opportunity.net_funding_pct


def _next_funding_daily_pct(opportunity: Opportunity) -> float | None:
    if opportunity.net_funding_next_daily_pct is not None:
        return opportunity.net_funding_next_daily_pct
    if opportunity.net_funding_next_hourly_pct is not None:
        return opportunity.net_funding_next_hourly_pct * 24
    return opportunity.net_funding_next_pct
