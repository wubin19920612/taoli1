from datetime import UTC, datetime, timedelta

from app.models.alert import AlertEvent, AlertRule
from app.models.market import MarketType
from app.models.pair_spread import (
    PairSpreadDiagnosticEvent,
    PairSpreadDiagnosticEventSummary,
    PairSpreadDiagnosticResult,
    PairSpreadDiagnosticRule,
    PairSpreadDiagnosticThresholdRun,
    PairSpreadPoint,
    PairSpreadQueryResult,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def infer_pair_type(result: PairSpreadQueryResult) -> str:
    if result.leg1.market_type == MarketType.FUTURE and result.leg2.market_type == MarketType.FUTURE:
        return "FF"
    if result.leg1.market_type == MarketType.SPOT and result.leg2.market_type == MarketType.SPOT:
        return "SS"
    return "SF"


def _pair_symbols(result: PairSpreadQueryResult) -> set[str]:
    return {result.leg1.symbol.upper(), result.leg2.symbol.upper()}


def _diagnose_rule(
    rule: AlertRule,
    result: PairSpreadQueryResult,
    inferred_type: str,
    peak_spread_pct: float | None,
) -> PairSpreadDiagnosticRule:
    reasons: list[str] = []
    matches = True
    pair_symbols = _pair_symbols(result)
    pair_exchanges = {result.leg1.exchange, result.leg2.exchange}

    if not rule.enabled:
        matches = False
        reasons.append("规则已关闭")
    if inferred_type not in rule.types:
        matches = False
        reasons.append(f"规则未包含 {inferred_type} 类型")
    if rule.include_exchanges and not pair_exchanges.intersection(
        exchange.lower() for exchange in rule.include_exchanges
    ):
        matches = False
        reasons.append("两个交易所都不在规则的包含范围")
    if pair_exchanges.intersection(exchange.lower() for exchange in rule.exclude_exchanges):
        matches = False
        reasons.append("交易所命中规则排除项")
    if rule.include_symbols and not pair_symbols.intersection(
        symbol.upper() for symbol in rule.include_symbols
    ):
        matches = False
        reasons.append("标的不在规则的包含范围")

    if peak_spread_pct is None:
        reasons.append("当前窗口没有可用的历史价差点")
    elif peak_spread_pct >= rule.min_open_spread_pct:
        reasons.append("历史峰值达到规则的开仓价差阈值")
    else:
        reasons.append("历史峰值低于规则的开仓价差阈值")

    if rule.min_fee_adjusted_open_pct > 0:
        reasons.append("历史 K 线无法验证扣除手续费、滑点和资金费率后的阈值")
    if rule.consecutive_hits > 1:
        reasons.append(f"实时告警还要求连续满足 {rule.consecutive_hits} 轮")

    return PairSpreadDiagnosticRule(
        id=rule.id,
        name=rule.name,
        enabled=rule.enabled,
        matches_pair_scope=matches,
        min_open_spread_pct=rule.min_open_spread_pct,
        min_fee_adjusted_open_pct=rule.min_fee_adjusted_open_pct,
        consecutive_hits=rule.consecutive_hits,
        cooldown_seconds=rule.cooldown_seconds,
        reasons=reasons,
    )


def _run_from_points(
    points: list[PairSpreadPoint],
    *,
    threshold_pct: float,
    interval_seconds: int,
) -> PairSpreadDiagnosticThresholdRun:
    longest = PairSpreadDiagnosticThresholdRun()
    current: list[PairSpreadPoint] = []
    max_gap = timedelta(seconds=max(interval_seconds, 60) * 1.5)

    def consider(candidate: list[PairSpreadPoint]) -> None:
        nonlocal longest
        if len(candidate) <= longest.point_count:
            return
        peak = max(candidate, key=lambda point: point.spread_pct)
        longest = PairSpreadDiagnosticThresholdRun(
            start_at=candidate[0].bucket_at,
            end_at=candidate[-1].bucket_at,
            point_count=len(candidate),
            peak_spread_pct=peak.spread_pct,
            peak_at=peak.bucket_at,
        )

    for point in points:
        if point.spread_pct < threshold_pct:
            consider(current)
            current = []
            continue
        if current and _as_utc(point.bucket_at) - _as_utc(current[-1].bucket_at) > max_gap:
            consider(current)
            current = []
        current.append(point)
    consider(current)
    return longest


def _event_summary(events: list[AlertEvent]) -> PairSpreadDiagnosticEventSummary:
    ordered = sorted(events, key=lambda event: _as_utc(event.created_at), reverse=True)
    recent = [
        PairSpreadDiagnosticEvent(
            rule_id=event.rule_id,
            status=event.status,
            created_at=event.created_at,
            message=event.message[:600],
        )
        for event in ordered[:20]
    ]
    counts = {
        "sent": sum(event.status == "sent" for event in events),
        "muted": sum(event.status == "muted" for event in events),
        "failed": sum(event.status == "failed" for event in events),
    }
    latest = ordered[0] if ordered else None
    return PairSpreadDiagnosticEventSummary(
        total=len(events),
        sent=counts["sent"],
        muted=counts["muted"],
        failed=counts["failed"],
        latest_status=latest.status if latest else None,
        latest_at=latest.created_at if latest else None,
        latest_message=latest.message[:600] if latest else None,
        events=recent,
    )


def build_pair_spread_diagnostic(
    result: PairSpreadQueryResult,
    *,
    threshold_pct: float,
    requested_interval_seconds: int,
    interval_seconds: int,
    rules: list[AlertRule],
    events: list[AlertEvent],
    suppress_when_card_conditions_fail: bool,
) -> PairSpreadDiagnosticResult:
    points = sorted(result.points, key=lambda point: _as_utc(point.bucket_at))
    over_threshold = [point for point in points if point.spread_pct >= threshold_pct]
    peak = max(points, key=lambda point: point.spread_pct, default=None)
    longest_run = _run_from_points(
        points,
        threshold_pct=threshold_pct,
        interval_seconds=interval_seconds,
    )
    inferred_type = infer_pair_type(result)

    window_start = _as_utc(result.first_seen_at) if result.first_seen_at else None
    window_end = _as_utc(result.observed_at) + timedelta(seconds=max(interval_seconds, 60))
    event_symbols = _pair_symbols(result)
    window_events = [
        event
        for event in events
        if event.symbol.upper() in event_symbols
        and (window_start is None or _as_utc(event.created_at) >= window_start)
        and _as_utc(event.created_at) <= window_end
    ]

    notes = [
        "价差查询使用交易所历史 K 线收盘价；实时告警使用盘口快照、成交量、资金费率和订单簿复核，两条链路的数值不一定相同。",
        "历史价差超过阈值只说明出现过候选机会，不等于实时告警一定会发送通知。",
    ]
    if suppress_when_card_conditions_fail:
        notes.append("当前告警模板开启了“卡片条件失败时静默”，订单簿深度、可成交金额或最新快照校验失败时会记录 muted，不发送通知。")
    if not window_events:
        notes.append("当前实例在这个历史窗口内没有找到对应的告警事件；如果监控运行在另一台服务器，需要到服务器数据库或日志中核对。")
    elif all(event.status == "muted" for event in window_events):
        notes.append("这个窗口内的告警事件全部是 muted，优先查看事件消息里的订单簿、可成交金额或最新快照失败原因。")

    return PairSpreadDiagnosticResult(
        leg1=result.leg1,
        leg2=result.leg2,
        hours=result.hours,
        requested_interval_seconds=requested_interval_seconds,
        interval_seconds=interval_seconds,
        observed_at=result.observed_at,
        point_count=len(points),
        threshold_pct=threshold_pct,
        peak_at=peak.bucket_at if peak else None,
        peak_spread_pct=peak.spread_pct if peak else None,
        peak_spread_abs=peak.spread_abs if peak else None,
        peak_leg1_close=peak.leg1_close if peak else None,
        peak_leg2_close=peak.leg2_close if peak else None,
        points_over_threshold=len(over_threshold),
        first_over_threshold_at=over_threshold[0].bucket_at if over_threshold else None,
        last_over_threshold_at=over_threshold[-1].bucket_at if over_threshold else None,
        longest_run=longest_run,
        current_spread_pct=result.current.spread_pct if result.current else result.spread_pct.current,
        inferred_type=inferred_type,
        alert_rules=[
            _diagnose_rule(rule, result, inferred_type, peak.spread_pct if peak else None)
            for rule in rules
        ],
        alert_events=_event_summary(window_events),
        suppress_when_card_conditions_fail=suppress_when_card_conditions_fail,
        notes=notes,
        warnings=list(result.warnings),
    )
