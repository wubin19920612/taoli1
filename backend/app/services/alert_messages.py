from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite

from app.models.alert import ALERT_SEVERITY_DESCRIPTIONS, ALERT_TYPE_DESCRIPTIONS, AlertRule
from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.models.settings import AlertMessageTemplateSettings
from app.services.alert_metrics import AlertObservation, combined_open_edge_pct
from app.services.funding_edge import current_cycle_funding_edge_pct, next_cycle_funding_edge_pct
from app.services.market_labels import is_bitget_rtoken_spot, market_leg_label

ALERT_DISPLAY_TIMEZONE = timezone(timedelta(hours=8), "UTC+8")
HIGH_VOLUME_24H_USDT = 10_000_000
NEUTRAL_FUNDING_PCT = 0.005


def build_alert_rating_header(
    rule: AlertRule,
    opportunity: Opportunity,
    *,
    validation_failed: bool = False,
    include_reason: bool = True,
) -> str:
    funding = next_cycle_funding_edge_pct(opportunity)
    volumes = (opportunity.buy_volume_24h_usdt, opportunity.sell_volume_24h_usdt)
    high_volume_threshold = max(HIGH_VOLUME_24H_USDT, rule.min_volume_24h_usdt * 2)
    if validation_failed:
        rating, reason = "需评估", "最新信号或建卡校验未通过，请看下方详情"
    elif not isfinite(opportunity.open_spread_pct) or opportunity.open_spread_pct <= 0:
        rating, reason = "需评估", "开仓价差不为正"
    elif not isfinite(opportunity.fee_adjusted_open_pct) or opportunity.fee_adjusted_open_pct <= 0:
        rating, reason = "需评估", "扣除基础成本后开仓收益不为正"
    elif opportunity.risk_labels:
        rating, reason = "需评估", f"存在风险标签：{', '.join(opportunity.risk_labels)}"
    elif _has_mixed_funding_intervals(opportunity):
        rating, reason = "需评估", "两腿结算周期不同，原值资金差不可直接比较"
    elif any(
        market_type == MarketType.FUTURE and (interval is None or interval <= 0)
        for market_type, interval in (
            (opportunity.buy_market_type, opportunity.buy_funding_interval_hours),
            (opportunity.sell_market_type, opportunity.sell_funding_interval_hours),
        )
    ):
        rating, reason = "需评估", "合约资金费率结算周期未知"
    elif funding is None or not isfinite(funding):
        rating, reason = "需评估", "资金费率数据不足"
    elif funding < -NEUTRAL_FUNDING_PCT:
        rating, reason = "需评估", "资金费率与正开仓价差方向相反"
    elif abs(funding) <= NEUTRAL_FUNDING_PCT:
        rating, reason = "推荐", "正开仓价差，资金差接近零"
    elif all(
        volume is not None and isfinite(volume) and volume >= high_volume_threshold
        for volume in volumes
    ):
        rating = "强烈推荐"
        reason = (
            "正开仓价差、正资金差，双边24h成交额均不低于"
            f"{high_volume_threshold / 10_000:,.0f}万USDT"
        )
    else:
        rating = "推荐"
        reason = "正开仓价差与资金差同向，双边成交额未达到高成交门槛或数据不全"
    title = (
        f"【{rating}】{opportunity.symbol} {opportunity.type} "
        f"{opportunity.buy_exchange}→{opportunity.sell_exchange}"
    )
    return f"{title}\n评级依据：{reason}（信号分级，非实际收益保证）" if include_reason else title


def build_alert_message(
    rule: AlertRule,
    opportunity: Opportunity,
    dashboard_url: str = "",
    observations: list[AlertObservation] | None = None,
    template: AlertMessageTemplateSettings | None = None,
    include_rating: bool = True,
) -> str:
    settings = template or AlertMessageTemplateSettings(format="detailed")
    if settings.format == "compact":
        return build_compact_alert_message(rule, opportunity, include_rating=include_rating)
    lines: list[str] = []
    if include_rating:
        lines.append(
            build_alert_rating_header(
                rule,
                opportunity,
                include_reason=(
                    settings.include_funding and settings.include_volume and settings.include_risk
                ),
            )
        )
    mixed_funding_intervals = _has_mixed_funding_intervals(opportunity)

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
                (
                    "资金费率条件开仓阈值："
                    f">= {_format_percent(rule.favorable_funding_open_spread_pct)}"
                    if rule.favorable_funding_open_spread_pct is not None
                    else "资金费率条件开仓阈值：关闭"
                ),
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
        buy_leg = market_leg_label(
            opportunity.buy_exchange,
            opportunity.buy_market_type,
            getattr(opportunity, "buy_raw_symbol", None),
            opportunity.symbol,
        )
        sell_leg = market_leg_label(
            opportunity.sell_exchange,
            opportunity.sell_market_type,
            getattr(opportunity, "sell_raw_symbol", None),
            opportunity.symbol,
        )
        snapshot_lines.extend(
            [
                f"标的：{opportunity.symbol} / {opportunity.type}",
                (
                    "价差对："
                    f"{opportunity.symbol} | "
                    f"{buy_leg} -> {sell_leg}"
                ),
                (
                    "方向："
                    f"买入 {buy_leg} {opportunity.symbol}，"
                    f"卖出 {sell_leg} {opportunity.symbol}"
                ),
                f"买入腿：{buy_leg}",
                f"卖出腿：{sell_leg}",
            ]
        )
        snapshot_lines.extend(_raw_symbol_lines(opportunity))
        if _has_bitget_rtoken_spot_leg(opportunity):
            snapshot_lines.append(
                "提示：Bitget 股票现货使用 RToken 原始标的，"
                "Astro 对应交易所使用 bitgetr。"
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
                (
                    "综合开仓（含异周期原值资金差）："
                    if mixed_funding_intervals else "综合开仓："
                ) + _format_percent(combined_open_edge_pct(opportunity)),
            ]
        )
    if settings.include_funding:
        if mixed_funding_intervals:
            snapshot_lines.extend(_mixed_interval_funding_lines(opportunity))
        else:
            snapshot_lines.extend(
                [
                    (
                        "资金费率差（周期）："
                        f"当前 {_format_percent(_current_funding_cycle_pct(opportunity), digits=2)} / "
                        f"预测 {_format_percent(_next_funding_cycle_pct(opportunity), digits=2)}"
                    ),
                    (
                        "资金费率："
                        f"{_format_percent(opportunity.funding_rate_buy_pct, digits=2)} / "
                        f"{_format_percent(opportunity.funding_rate_sell_pct, digits=2)}"
                        f"（周期净：{_format_percent(_current_funding_cycle_pct(opportunity), digits=2)}）"
                    ),
                    (
                        "预测资金费率："
                        f"{_format_percent(opportunity.funding_next_rate_buy_pct, digits=2)} / "
                        f"{_format_percent(opportunity.funding_next_rate_sell_pct, digits=2)}"
                        f"（周期净：{_format_percent(_next_funding_cycle_pct(opportunity), digits=2)}）"
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
        funding_label = "资金差（异周期原值）" if mixed_funding_intervals else "资金差（周期）"
        combined_label = "综合（含异周期原值差）" if mixed_funding_intervals else "综合"
        for index, item in enumerate(selected_observations, start=1):
            observation_lines.append(
                f"{index}. {_format_time_with_seconds(item.observed_at)} | "
                f"价差 {_format_percent(item.open_spread_pct)} | "
                f"净估算 {_format_percent(item.fee_adjusted_open_pct)} | "
                f"{funding_label} {_format_percent(item.funding_edge_pct, digits=2)} | "
                f"{combined_label} {_format_percent(item.combined_open_edge_pct)}"
            )
        _append_block(lines, "【连续监测】", observation_lines)

    if settings.include_dashboard_link and dashboard_url:
        _append_block(lines, "", [f"Dashboard: {dashboard_url}"])
    if not lines:
        lines = [f"{opportunity.symbol} / {opportunity.type}"]
    return "\n".join(lines)


def build_compact_alert_message(
    rule: AlertRule,
    opportunity: Opportunity,
    *,
    include_rating: bool = True,
) -> str:
    def leg(side: str) -> str:
        exchange = getattr(opportunity, f"{side}_exchange")
        market_type = getattr(opportunity, f"{side}_market_type")
        raw_symbol = getattr(opportunity, f"{side}_raw_symbol")
        dex = getattr(opportunity, f"{side}_dex")
        price_multiplier = getattr(opportunity, f"{side}_price_multiplier")
        contract_multiplier = getattr(opportunity, f"{side}_contract_size_multiplier")
        identity = raw_symbol or opportunity.symbol
        details = [f"DEX {dex}"] if dex else []
        if price_multiplier != 1:
            details.append(f"价格倍率 {price_multiplier:g}")
        if contract_multiplier not in (None, 1):
            details.append(f"合约倍率 {contract_multiplier:g}")
        suffix = f"（{', '.join(details)}）" if details else ""
        return f"{market_leg_label(exchange, market_type, raw_symbol, opportunity.symbol)} {identity}{suffix}"

    def funding(side: str, *, next_cycle: bool) -> str:
        market_type = getattr(opportunity, f"{side}_market_type")
        if market_type == MarketType.SPOT:
            return "现货无资金费率"
        field = f"funding_next_rate_{side}_pct" if next_cycle else f"funding_rate_{side}_pct"
        rate = getattr(opportunity, field)
        interval = getattr(opportunity, f"{side}_funding_interval_hours")
        return f"{_format_percent(rate, digits=3)}/{_format_interval(interval)}"

    lines = [
        build_alert_rating_header(rule, opportunity, include_reason=False)
        if include_rating else f"{opportunity.symbol} {opportunity.type}",
        f"买 {leg('buy')} → 卖 {leg('sell')}",
        (
            f"盘口价差：开仓 {_format_percent(opportunity.open_spread_pct)} / "
            f"平仓 {_format_percent(opportunity.close_spread_pct)}；"
            f"费后开仓估算 {_format_percent(opportunity.fee_adjusted_open_pct)}"
        ),
        (
            f"资金费率：当前 买 {funding('buy', next_cycle=False)} / "
            f"卖 {funding('sell', next_cycle=False)}；"
            f"下期预估 买 {funding('buy', next_cycle=True)} / "
            f"卖 {funding('sell', next_cycle=True)}"
        ),
        (
            "24h成交额："
            f"买 {_format_compact_volume(opportunity.buy_volume_24h_usdt)} / "
            f"卖 {_format_compact_volume(opportunity.sell_volume_24h_usdt)}"
        ),
    ]
    if opportunity.risk_labels:
        lines.append(f"风险：{', '.join(opportunity.risk_labels)}")
    return "\n".join(lines)


def _format_compact_volume(value: float | None) -> str:
    if value is None or not isfinite(value):
        return "-"
    return f"{value:,.0f} USDT"


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


def _format_signed_percent(value: float | Decimal | None, digits: int = 3) -> str:
    if value is None:
        return "-"
    return f"{value:+.{digits}f}%"


def _has_mixed_funding_intervals(opportunity: Opportunity) -> bool:
    buy_interval = opportunity.buy_funding_interval_hours
    sell_interval = opportunity.sell_funding_interval_hours
    return (
        opportunity.buy_market_type == opportunity.sell_market_type == MarketType.FUTURE
        and buy_interval is not None
        and sell_interval is not None
        and buy_interval > 0
        and sell_interval > 0
        and buy_interval != sell_interval
    )


def _hourly_funding_difference(opportunity: Opportunity, *, next_cycle: bool) -> Decimal | None:
    buy_rate = opportunity.funding_rate_buy_pct
    sell_rate = opportunity.funding_rate_sell_pct
    if next_cycle:
        buy_rate = (
            opportunity.funding_next_rate_buy_pct
            if opportunity.funding_next_rate_buy_pct is not None else buy_rate
        )
        sell_rate = (
            opportunity.funding_next_rate_sell_pct
            if opportunity.funding_next_rate_sell_pct is not None else sell_rate
        )
    buy_interval = opportunity.buy_funding_interval_hours
    sell_interval = opportunity.sell_funding_interval_hours
    if buy_rate is None or sell_rate is None or not buy_interval or not sell_interval:
        return None
    return Decimal(str(sell_rate)) / sell_interval - Decimal(str(buy_rate)) / buy_interval


def _mixed_interval_funding_lines(opportunity: Opportunity) -> list[str]:
    buy_interval = _format_interval(opportunity.buy_funding_interval_hours)
    sell_interval = _format_interval(opportunity.sell_funding_interval_hours)
    fallback_legs: list[str] = []
    missing_legs: list[str] = []
    for label, predicted_rate, current_rate in (
        ("买入腿", opportunity.funding_next_rate_buy_pct, opportunity.funding_rate_buy_pct),
        ("卖出腿", opportunity.funding_next_rate_sell_pct, opportunity.funding_rate_sell_pct),
    ):
        if predicted_rate is None:
            if current_rate is None:
                missing_legs.append(label)
            else:
                fallback_legs.append(label)
    fallback_note = (
        f"（{'、'.join(fallback_legs)}缺预测，按当前费率代入）"
        if fallback_legs else ""
    )
    if missing_legs:
        fallback_note += f"（{'、'.join(missing_legs)}当前与预测费率均缺失）"
    return [
        (
            "资金费率差（异周期原值相减）："
            f"当前 {_format_signed_percent(_current_funding_cycle_pct(opportunity), digits=2)} / "
            f"各腿下次结算估算 {_format_signed_percent(_next_funding_cycle_pct(opportunity), digits=2)}"
            f"{fallback_note}"
        ),
        (
            "当前资金费率（每腿每次结算）："
            f"买入 {opportunity.buy_exchange} "
            f"{_format_percent(opportunity.funding_rate_buy_pct, digits=2)}/{buy_interval}；"
            f"卖出 {opportunity.sell_exchange} "
            f"{_format_percent(opportunity.funding_rate_sell_pct, digits=2)}/{sell_interval}"
        ),
        (
            "预测资金费率（每腿每次结算）："
            f"买入 {opportunity.buy_exchange} "
            f"{_format_percent(opportunity.funding_next_rate_buy_pct, digits=2)}/{buy_interval}；"
            f"卖出 {opportunity.sell_exchange} "
            f"{_format_percent(opportunity.funding_next_rate_sell_pct, digits=2)}/{sell_interval}"
        ),
        (
            "同口径资金差（按小时线性估算）："
            f"当前 {_format_signed_percent(_hourly_funding_difference(opportunity, next_cycle=False))}/h / "
            f"下期 {_format_signed_percent(_hourly_funding_difference(opportunity, next_cycle=True))}/h"
        ),
        (
            "下一次结算："
            f"买入 {_format_time(opportunity.funding_next_time_buy)} / "
            f"卖出 {_format_time(opportunity.funding_next_time_sell)}"
        ),
        f"结算周期：买入 {buy_interval} / 卖出 {sell_interval}",
        (
            "提示：异周期原值差仅是两腿各一次结算费率相减；"
            "综合开仓和连续监测也使用该原值差，不代表同一持仓时长的实际收益。"
            "小时口径仅供比较，实际收益取决于结算时点及后续费率。"
        ),
    ]


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


def _normalize_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "").replace("/", "")


def _raw_symbol_lines(opportunity: Opportunity) -> list[str]:
    rows: list[str] = []
    buy_raw = getattr(opportunity, "buy_raw_symbol", None)
    sell_raw = getattr(opportunity, "sell_raw_symbol", None)
    if buy_raw and _normalize_symbol(buy_raw) != _normalize_symbol(opportunity.symbol):
        rows.append(f"买入原始标的：{opportunity.buy_exchange} {buy_raw}")
    if sell_raw and _normalize_symbol(sell_raw) != _normalize_symbol(opportunity.symbol):
        rows.append(f"卖出原始标的：{opportunity.sell_exchange} {sell_raw}")
    return rows


def _has_bitget_rtoken_spot_leg(opportunity: Opportunity) -> bool:
    return is_bitget_rtoken_spot(
        opportunity.buy_exchange,
        opportunity.buy_market_type,
        getattr(opportunity, "buy_raw_symbol", None),
        opportunity.symbol,
    ) or is_bitget_rtoken_spot(
        opportunity.sell_exchange,
        opportunity.sell_market_type,
        getattr(opportunity, "sell_raw_symbol", None),
        opportunity.symbol,
    )


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


def _current_funding_cycle_pct(opportunity: Opportunity) -> float | None:
    return current_cycle_funding_edge_pct(opportunity)


def _next_funding_cycle_pct(opportunity: Opportunity) -> float | None:
    return next_cycle_funding_edge_pct(opportunity)
