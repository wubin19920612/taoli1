from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import combinations
from math import isfinite
from statistics import median

from app.models.fat_finger_backtest import (
    FatFingerBacktestRequest,
    FatFingerBacktestResult,
    FatFingerBacktestRouteSummary,
    FatFingerBacktestTrade,
    FatFingerMakerSide,
)
from app.models.market import MarketType
from app.models.second_level_sampling import SecondLevelMarketSample


def _positive(value: float | None) -> float | None:
    if value is None or not isfinite(value) or value <= 0:
        return None
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _percentile_median(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def _market_types(mode: str) -> tuple[MarketType, MarketType]:
    if mode == "SF":
        return MarketType.SPOT, MarketType.FUTURE
    return MarketType.FUTURE, MarketType.FUTURE


def _leg_values(
    sample: SecondLevelMarketSample,
    market_type: MarketType,
) -> tuple[float | None, float | None, float | None, float | None]:
    if market_type == MarketType.SPOT:
        return (
            _positive(sample.spot_bid),
            _positive(sample.spot_ask),
            _positive(sample.spot_bid_size),
            _positive(sample.spot_ask_size),
        )
    return (
        _positive(sample.future_bid),
        _positive(sample.future_ask),
        _positive(sample.future_bid_size),
        _positive(sample.future_ask_size),
    )


def _leg_quote(
    sample: SecondLevelMarketSample | None,
    market_type: MarketType,
) -> "_Quote | None":
    if sample is None:
        return None
    bid, ask, bid_size, ask_size = _leg_values(sample, market_type)
    if bid is None or ask is None:
        return None
    return _Quote(
        exchange=sample.exchange,
        market_type=market_type,
        observed_at=_as_utc(sample.observed_at),
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


@dataclass(frozen=True)
class _Quote:
    exchange: str
    market_type: MarketType
    observed_at: datetime
    bid: float
    ask: float
    bid_size: float | None
    ask_size: float | None

    def side_depth(self, side: FatFingerMakerSide) -> float | None:
        if side == "buy":
            return self.bid * self.bid_size if self.bid_size is not None else None
        return self.ask * self.ask_size if self.ask_size is not None else None


@dataclass(frozen=True)
class _Route:
    maker_exchange: str
    maker_market_type: MarketType
    hedge_exchange: str
    hedge_market_type: MarketType
    maker_side: FatFingerMakerSide
    tier: int

    @property
    def key(self) -> tuple[str, str, str, str, str, int]:
        return (
            self.maker_exchange,
            self.maker_market_type.value,
            self.hedge_exchange,
            self.hedge_market_type.value,
            self.maker_side,
            self.tier,
        )

    @property
    def summary_key(self) -> tuple[str, str, str, str, str]:
        return self.key[:-1]


@dataclass
class _OpenOrder:
    route: _Route
    created_at: datetime
    expires_at: datetime
    limit_price: float
    target_spread_pct: float
    planned_notional_usdt: float


@dataclass
class _PendingHedge:
    route: _Route
    order_placed_at: datetime
    maker_filled_at: datetime
    maker_entry_price: float
    target_spread_pct: float
    requested_notional_usdt: float
    hedge_due_at: datetime


@dataclass
class _OpenPosition:
    id: str
    route: _Route
    order_placed_at: datetime
    maker_filled_at: datetime
    hedge_filled_at: datetime
    maker_entry_price: float
    hedge_entry_price: float
    target_spread_pct: float
    notional_usdt: float
    hedge_depth_usdt: float | None
    entry_hedge_edge_pct: float
    max_favorable_pnl_pct: float
    max_adverse_pnl_pct: float


@dataclass
class _RouteStats:
    touch_count: int = 0
    hedge_count: int = 0
    unhedged_count: int = 0
    trades: list[FatFingerBacktestTrade] | None = None

    def __post_init__(self) -> None:
        if self.trades is None:
            self.trades = []


def _fresh_quote(
    latest_by_exchange: dict[str, SecondLevelMarketSample],
    exchange: str,
    market_type: MarketType,
    now: datetime,
    max_age_seconds: float,
) -> _Quote | None:
    quote = _leg_quote(latest_by_exchange.get(exchange), market_type)
    if quote is None:
        return None
    if (now - quote.observed_at).total_seconds() > max_age_seconds:
        return None
    return quote


def _route_quotes(
    route: _Route,
    latest_by_exchange: dict[str, SecondLevelMarketSample],
    now: datetime,
    max_age_seconds: float,
) -> tuple[_Quote | None, _Quote | None]:
    return (
        _fresh_quote(
            latest_by_exchange,
            route.maker_exchange,
            route.maker_market_type,
            now,
            max_age_seconds,
        ),
        _fresh_quote(
            latest_by_exchange,
            route.hedge_exchange,
            route.hedge_market_type,
            now,
            max_age_seconds,
        ),
    )


def _route_candidates(
    latest_by_exchange: dict[str, SecondLevelMarketSample],
    request: FatFingerBacktestRequest,
    now: datetime,
) -> list[_Route]:
    first_market, second_market = _market_types(request.market_mode)
    first_quotes = [
        _fresh_quote(latest_by_exchange, exchange, first_market, now, request.max_quote_age_seconds)
        for exchange in latest_by_exchange
    ]
    second_quotes = [
        _fresh_quote(latest_by_exchange, exchange, second_market, now, request.max_quote_age_seconds)
        for exchange in latest_by_exchange
    ]
    first_quotes = [quote for quote in first_quotes if quote is not None]
    second_quotes = [quote for quote in second_quotes if quote is not None]
    pairs: list[tuple[_Quote, _Quote]] = []
    if request.market_mode == "SF":
        pairs = [
            (first, second)
            for first in first_quotes
            for second in second_quotes
        ]
    else:
        pairs = list(combinations(first_quotes, 2))
    routes: list[_Route] = []
    for left, right in pairs:
        for maker, hedge in ((left, right), (right, left)):
            for maker_side in ("buy", "sell"):
                for tier in range(1, request.ladder_levels + 1):
                    routes.append(
                        _Route(
                            maker_exchange=maker.exchange,
                            maker_market_type=maker.market_type,
                            hedge_exchange=hedge.exchange,
                            hedge_market_type=hedge.market_type,
                            maker_side=maker_side,
                            tier=tier,
                        )
                    )
    return routes


def _target_spread(request: FatFingerBacktestRequest, tier: int) -> float:
    return request.entry_spread_pct + (tier - 1) * request.ladder_step_pct


def _limit_price(hedge: _Quote, maker_side: FatFingerMakerSide, target_spread_pct: float) -> float:
    ratio = target_spread_pct / 100
    if maker_side == "buy":
        return hedge.bid / (1 + ratio)
    return hedge.ask * (1 + ratio)


def _hedge_depth(hedge: _Quote, maker_side: FatFingerMakerSide) -> float | None:
    if maker_side == "buy":
        return hedge.bid * hedge.bid_size if hedge.bid_size is not None else None
    return hedge.ask * hedge.ask_size if hedge.ask_size is not None else None


def _exit_depths(
    position: _OpenPosition,
    maker: _Quote,
    hedge: _Quote,
) -> tuple[float | None, float | None]:
    if position.route.maker_side == "buy":
        maker_depth = maker.bid * maker.bid_size if maker.bid_size is not None else None
        hedge_depth = hedge.ask * hedge.ask_size if hedge.ask_size is not None else None
    else:
        maker_depth = maker.ask * maker.ask_size if maker.ask_size is not None else None
        hedge_depth = hedge.bid * hedge.bid_size if hedge.bid_size is not None else None
    return maker_depth, hedge_depth


def _entry_edge_pct(
    maker_side: FatFingerMakerSide,
    maker_entry_price: float,
    hedge_entry_price: float,
) -> float:
    if maker_side == "buy":
        return (hedge_entry_price / maker_entry_price - 1) * 100
    return (maker_entry_price / hedge_entry_price - 1) * 100


def _position_mark(
    position: _OpenPosition,
    maker: _Quote,
    hedge: _Quote,
    request: FatFingerBacktestRequest,
) -> tuple[float, float, float, float]:
    slip = request.taker_slippage_pct / 100
    taker_fee = request.taker_fee_pct / 100
    maker_fee = request.maker_fee_pct / 100
    quantity = position.notional_usdt / position.maker_entry_price
    if position.route.maker_side == "buy":
        maker_exit = maker.bid * (1 - slip)
        hedge_exit = hedge.ask * (1 + slip)
        gross = (
            position.hedge_entry_price
            - position.maker_entry_price
            + maker_exit
            - hedge_exit
        ) * quantity
    else:
        maker_exit = maker.ask * (1 + slip)
        hedge_exit = hedge.bid * (1 - slip)
        gross = (
            position.maker_entry_price
            - position.hedge_entry_price
            + hedge_exit
            - maker_exit
        ) * quantity
    fee_cost = (
        position.maker_entry_price * quantity * maker_fee
        + position.hedge_entry_price * quantity * taker_fee
        + maker_exit * quantity * taker_fee
        + hedge_exit * quantity * taker_fee
    )
    net = gross - fee_cost
    return maker_exit, hedge_exit, gross, net


def _trade_from_position(
    position: _OpenPosition,
    *,
    maker: _Quote,
    hedge: _Quote,
    closed_at: datetime,
    exit_reason: str,
    request: FatFingerBacktestRequest,
) -> FatFingerBacktestTrade:
    maker_exit, hedge_exit, gross, net = _position_mark(position, maker, hedge, request)
    net_pct = net / position.notional_usdt * 100
    return FatFingerBacktestTrade(
        id=position.id,
        symbol=request.symbol,
        market_mode=request.market_mode,
        maker_exchange=position.route.maker_exchange,
        maker_market_type=position.route.maker_market_type.value,
        hedge_exchange=position.route.hedge_exchange,
        hedge_market_type=position.route.hedge_market_type.value,
        maker_side=position.route.maker_side,
        tier=position.route.tier,
        entry_target_spread_pct=position.target_spread_pct,
        order_placed_at=position.order_placed_at,
        maker_filled_at=position.maker_filled_at,
        hedge_filled_at=position.hedge_filled_at,
        closed_at=closed_at,
        exit_reason=exit_reason,
        maker_entry_price=position.maker_entry_price,
        hedge_entry_price=position.hedge_entry_price,
        maker_exit_price=maker_exit,
        hedge_exit_price=hedge_exit,
        notional_usdt=position.notional_usdt,
        hedge_depth_usdt=position.hedge_depth_usdt,
        entry_hedge_edge_pct=position.entry_hedge_edge_pct,
        gross_pnl_usdt=gross,
        net_pnl_usdt=net,
        net_pnl_pct=net_pct,
        max_favorable_pnl_pct=max(position.max_favorable_pnl_pct, net_pct),
        max_adverse_pnl_pct=min(position.max_adverse_pnl_pct, net_pct),
        hedge_delay_seconds=(position.hedge_filled_at - position.maker_filled_at).total_seconds(),
        hold_seconds=(closed_at - position.hedge_filled_at).total_seconds(),
    )


def run_fat_finger_backtest(
    samples: list[SecondLevelMarketSample],
    request: FatFingerBacktestRequest,
) -> FatFingerBacktestResult:
    chronological = sorted(samples, key=lambda item: (_as_utc(item.observed_at), item.id or 0))
    if not chronological:
        now = datetime.now(UTC)
        return FatFingerBacktestResult(
            request=request,
            start_at=now,
            end_at=now,
            raw_sample_count=0,
            samples_truncated=False,
            frame_count=0,
            exchange_count=0,
            order_placed_count=0,
            order_expired_count=0,
            order_skipped_depth_count=0,
            exit_skipped_depth_count=0,
            quote_touch_count=0,
            hedge_completed_count=0,
            unhedged_touch_count=0,
            open_position_count=0,
            closed_trade_count=0,
            target_exit_count=0,
            timeout_exit_count=0,
            win_count=0,
            loss_count=0,
            warnings=[
                "该时间段没有 1 秒盘口样本，当前无法回测乌龙指挂单策略。",
                "先在“1s 采样”中持续记录目标标的和交易所，再回来查看结果。",
            ],
        )

    latest_by_exchange: dict[str, SecondLevelMarketSample] = {}
    open_orders: dict[tuple[str, str, str, str, str, int], _OpenOrder] = {}
    pending_hedges: list[_PendingHedge] = []
    positions: dict[tuple[str, str, str, str, str, int], _OpenPosition] = {}
    cooldown_until: dict[tuple[str, str, str, str, str, int], datetime] = {}
    route_stats: dict[tuple[str, str, str, str, str], _RouteStats] = defaultdict(_RouteStats)
    trades: list[FatFingerBacktestTrade] = []

    order_placed_count = 0
    order_expired_count = 0
    order_skipped_depth_count = 0
    exit_skipped_depth_count = 0
    quote_touch_count = 0
    hedge_completed_count = 0
    unhedged_touch_count = 0
    target_exit_count = 0
    timeout_exit_count = 0
    frame_count = 0

    def create_orders(now: datetime) -> None:
        nonlocal order_placed_count, order_skipped_depth_count
        for route in _route_candidates(latest_by_exchange, request, now):
            if route.key in open_orders or route.key in positions:
                continue
            if any(item.route.key == route.key for item in pending_hedges):
                continue
            if now < cooldown_until.get(route.key, datetime.min.replace(tzinfo=UTC)):
                continue
            maker, hedge = _route_quotes(route, latest_by_exchange, now, request.max_quote_age_seconds)
            if maker is None or hedge is None:
                continue
            planned_notional = request.order_notional_usdt * request.maker_fill_assumption_pct / 100
            hedge_depth = _hedge_depth(hedge, route.maker_side)
            if request.require_known_hedge_depth and hedge_depth is None:
                order_skipped_depth_count += 1
                continue
            if hedge_depth is not None and hedge_depth < max(planned_notional, request.min_hedge_depth_usdt):
                order_skipped_depth_count += 1
                continue
            target_spread_pct = _target_spread(request, route.tier)
            open_orders[route.key] = _OpenOrder(
                route=route,
                created_at=now,
                expires_at=now + timedelta(seconds=request.order_expiry_seconds),
                limit_price=_limit_price(hedge, route.maker_side, target_spread_pct),
                target_spread_pct=target_spread_pct,
                planned_notional_usdt=planned_notional,
            )
            order_placed_count += 1

    def process_open_positions(now: datetime) -> None:
        nonlocal target_exit_count, timeout_exit_count, exit_skipped_depth_count
        for key, position in list(positions.items()):
            maker, hedge = _route_quotes(position.route, latest_by_exchange, now, request.max_quote_age_seconds)
            if maker is None or hedge is None:
                continue
            _, _, _, net = _position_mark(position, maker, hedge, request)
            net_pct = net / position.notional_usdt * 100
            position.max_favorable_pnl_pct = max(position.max_favorable_pnl_pct, net_pct)
            position.max_adverse_pnl_pct = min(position.max_adverse_pnl_pct, net_pct)
            held_seconds = (now - position.hedge_filled_at).total_seconds()
            if net_pct >= request.take_profit_pct:
                exit_reason = "target"
            elif held_seconds >= request.max_hold_seconds:
                exit_reason = "timeout"
            else:
                continue
            maker_depth, hedge_depth = _exit_depths(position, maker, hedge)
            required_depth = max(position.notional_usdt, request.min_hedge_depth_usdt)
            if request.require_known_hedge_depth and (maker_depth is None or hedge_depth is None):
                exit_skipped_depth_count += 1
                continue
            if (maker_depth is not None and maker_depth < required_depth) or (
                hedge_depth is not None and hedge_depth < required_depth
            ):
                exit_skipped_depth_count += 1
                continue
            trade = _trade_from_position(
                position,
                maker=maker,
                hedge=hedge,
                closed_at=now,
                exit_reason=exit_reason,
                request=request,
            )
            trades.append(trade)
            route_stats[position.route.summary_key].trades.append(trade)
            if exit_reason == "target":
                target_exit_count += 1
            else:
                timeout_exit_count += 1
            cooldown_until[key] = now + timedelta(seconds=request.cooldown_seconds)
            del positions[key]

    def process_pending_hedges(now: datetime) -> None:
        nonlocal hedge_completed_count, unhedged_touch_count
        remaining: list[_PendingHedge] = []
        for pending in pending_hedges:
            if now < pending.hedge_due_at:
                remaining.append(pending)
                continue
            _, hedge = _route_quotes(pending.route, latest_by_exchange, now, request.max_quote_age_seconds)
            stats = route_stats[pending.route.summary_key]
            if hedge is None:
                unhedged_touch_count += 1
                stats.unhedged_count += 1
                cooldown_until[pending.route.key] = now + timedelta(seconds=request.cooldown_seconds)
                continue
            hedge_depth = _hedge_depth(hedge, pending.route.maker_side)
            if request.require_known_hedge_depth and hedge_depth is None:
                unhedged_touch_count += 1
                stats.unhedged_count += 1
                cooldown_until[pending.route.key] = now + timedelta(seconds=request.cooldown_seconds)
                continue
            required_depth = max(pending.requested_notional_usdt, request.min_hedge_depth_usdt)
            if hedge_depth is not None and hedge_depth < required_depth:
                unhedged_touch_count += 1
                stats.unhedged_count += 1
                cooldown_until[pending.route.key] = now + timedelta(seconds=request.cooldown_seconds)
                continue
            slip = request.taker_slippage_pct / 100
            hedge_entry = hedge.bid * (1 - slip) if pending.route.maker_side == "buy" else hedge.ask * (1 + slip)
            entry_edge = _entry_edge_pct(
                pending.route.maker_side,
                pending.maker_entry_price,
                hedge_entry,
            )
            position = _OpenPosition(
                id=f"{pending.route.key}:{pending.maker_filled_at.isoformat()}",
                route=pending.route,
                order_placed_at=pending.order_placed_at,
                maker_filled_at=pending.maker_filled_at,
                hedge_filled_at=now,
                maker_entry_price=pending.maker_entry_price,
                hedge_entry_price=hedge_entry,
                target_spread_pct=pending.target_spread_pct,
                notional_usdt=pending.requested_notional_usdt,
                hedge_depth_usdt=hedge_depth,
                entry_hedge_edge_pct=entry_edge,
                max_favorable_pnl_pct=0,
                max_adverse_pnl_pct=0,
            )
            positions[pending.route.key] = position
            hedge_completed_count += 1
            stats.hedge_count += 1
        pending_hedges[:] = remaining

    def process_open_orders(now: datetime) -> None:
        nonlocal order_expired_count, quote_touch_count
        for key, order in list(open_orders.items()):
            if now >= order.expires_at:
                order_expired_count += 1
                del open_orders[key]
                continue
            maker, _ = _route_quotes(order.route, latest_by_exchange, now, request.max_quote_age_seconds)
            if maker is None:
                continue
            touched = maker.ask <= order.limit_price if order.route.maker_side == "buy" else maker.bid >= order.limit_price
            if not touched:
                continue
            quote_touch_count += 1
            route_stats[order.route.summary_key].touch_count += 1
            pending_hedges.append(
                _PendingHedge(
                    route=order.route,
                    order_placed_at=order.created_at,
                    maker_filled_at=now,
                    maker_entry_price=order.limit_price,
                    target_spread_pct=order.target_spread_pct,
                    requested_notional_usdt=order.planned_notional_usdt,
                    hedge_due_at=now + timedelta(seconds=request.hedge_delay_seconds),
                )
            )
            del open_orders[key]

    for sample in chronological:
        now = _as_utc(sample.observed_at)
        latest_by_exchange[sample.exchange] = sample
        frame_count += 1
        process_open_positions(now)
        process_pending_hedges(now)
        create_orders(now)
        process_open_orders(now)
        process_pending_hedges(now)

    # A fill close to the end of the selected history may not yet have reached
    # its configured hedge time. Count it conservatively instead of silently
    # improving the displayed hedge completion rate.
    for pending in pending_hedges:
        unhedged_touch_count += 1
        route_stats[pending.route.summary_key].unhedged_count += 1

    summaries: list[FatFingerBacktestRouteSummary] = []
    for key, stats in route_stats.items():
        route_trades = stats.trades
        net_values = [trade.net_pnl_pct for trade in route_trades]
        summaries.append(
            FatFingerBacktestRouteSummary(
                maker_exchange=key[0],
                maker_market_type=key[1],
                hedge_exchange=key[2],
                hedge_market_type=key[3],
                maker_side=key[4],
                touch_count=stats.touch_count,
                hedge_count=stats.hedge_count,
                unhedged_count=stats.unhedged_count,
                closed_trade_count=len(route_trades),
                win_count=sum(1 for trade in route_trades if trade.net_pnl_usdt > 0),
                total_notional_usdt=sum(trade.notional_usdt for trade in route_trades),
                total_net_pnl_usdt=sum(trade.net_pnl_usdt for trade in route_trades),
                average_net_pnl_pct=sum(net_values) / len(net_values) if net_values else None,
                median_net_pnl_pct=_percentile_median(net_values),
                worst_net_pnl_pct=min(net_values, default=None),
                average_hold_seconds=(
                    sum(trade.hold_seconds for trade in route_trades) / len(route_trades)
                    if route_trades
                    else None
                ),
            )
        )

    closed_trades = sorted(trades, key=lambda item: item.closed_at, reverse=True)
    net_values = [trade.net_pnl_pct for trade in closed_trades]
    win_count = sum(1 for trade in closed_trades if trade.net_pnl_usdt > 0)
    warnings = [
        "“触价”仅代表买一/卖一穿过预挂价格；没有逐笔成交和队列位置，不能视为真实 maker 成交率。",
        "回测对冲与平仓按当时可见买一/卖一、设定的延迟、手续费和滑点计算；盘口多档冲击成本仍未包含。",
    ]
    if request.require_known_hedge_depth:
        warnings.append("已启用严格深度：对冲腿未返回数量或可见顶层金额不足时，订单会跳过或记为未完成对冲。")
    else:
        warnings.append("未要求已知对冲深度：未知深度的结果仅供探索，不适合作为真实挂单额度依据。")
    if unhedged_touch_count:
        warnings.append(f"出现 {unhedged_touch_count} 次触价后未能按假设完成全额对冲，真实交易中这类单边风险必须优先处理。")
    if pending_hedges:
        warnings.append(
            f"有 {len(pending_hedges)} 次触价发生在历史结尾，对冲等待时间尚未走完；已保守计入未对冲。"
        )
    if positions:
        warnings.append(f"回测结束时仍有 {len(positions)} 笔未到退出条件的纸面仓位，未计入已平仓收益。")

    return FatFingerBacktestResult(
        request=request,
        start_at=_as_utc(chronological[0].observed_at),
        end_at=_as_utc(chronological[-1].observed_at),
        raw_sample_count=len(chronological),
        samples_truncated=False,
        frame_count=frame_count,
        exchange_count=len({sample.exchange for sample in chronological}),
        order_placed_count=order_placed_count,
        order_expired_count=order_expired_count,
        order_skipped_depth_count=order_skipped_depth_count,
        exit_skipped_depth_count=exit_skipped_depth_count,
        quote_touch_count=quote_touch_count,
        hedge_completed_count=hedge_completed_count,
        unhedged_touch_count=unhedged_touch_count,
        open_position_count=len(positions),
        closed_trade_count=len(closed_trades),
        target_exit_count=target_exit_count,
        timeout_exit_count=timeout_exit_count,
        win_count=win_count,
        loss_count=len(closed_trades) - win_count,
        win_rate_pct=win_count / len(closed_trades) * 100 if closed_trades else None,
        hedge_success_rate_pct=(
            hedge_completed_count / quote_touch_count * 100 if quote_touch_count else None
        ),
        total_notional_usdt=sum(trade.notional_usdt for trade in closed_trades),
        total_net_pnl_usdt=sum(trade.net_pnl_usdt for trade in closed_trades),
        average_net_pnl_pct=sum(net_values) / len(net_values) if net_values else None,
        median_net_pnl_pct=_percentile_median(net_values),
        worst_net_pnl_pct=min(net_values, default=None),
        average_hold_seconds=(
            sum(trade.hold_seconds for trade in closed_trades) / len(closed_trades)
            if closed_trades
            else None
        ),
        average_hedge_delay_seconds=(
            sum(trade.hedge_delay_seconds for trade in closed_trades) / len(closed_trades)
            if closed_trades
            else None
        ),
        route_summaries=sorted(
            summaries,
            key=lambda item: (
                item.total_net_pnl_usdt,
                item.closed_trade_count,
                item.hedge_count,
            ),
            reverse=True,
        ),
        trades=closed_trades[:1000],
        warnings=warnings,
    )
