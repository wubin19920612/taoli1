"""Pure, fixed USD notional per-leg accounting; no network, clock or database I/O."""

from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise
from math import floor, isfinite
from statistics import median

from app.models.market import MarketType
from app.models.pair_range_backtest import PairRangeBacktestRequest, PairRangeBacktestResult
from app.models.pair_spread import (
    PairSpreadCurrentLeg,
    PairSpreadFundingPoint,
    PairSpreadKlinePoint,
)
from app.models.settings import FeeSettings


@dataclass(frozen=True)
class RangeLegData:
    entry_points: tuple[PairSpreadKlinePoint, ...]
    exit_points: tuple[PairSpreadKlinePoint, ...]
    funding: tuple[PairSpreadFundingPoint, ...] = ()
    current: PairSpreadCurrentLeg | None = None
    source: str = "kline_close"
    granularity_seconds: int = 60
    warnings: tuple[str, ...] = ()


def calculate_range_backtest(
    request: PairRangeBacktestRequest,
    *,
    leg1_data: RangeLegData,
    leg2_data: RangeLegData,
    fee_settings: FeeSettings,
) -> PairRangeBacktestResult:
    """Costs use two trades per leg at the supplied per-leg notional.

    Coverage prefers the current interval and settlement phase, falling back to
    the median observed settlement gap with an explicit estimation warning.
    Zero scheduled settlements means coverage 1; an unknown schedule means null. Interior
    price gaps are deliberately not audited: only endpoint neighborhoods are read.
    """
    warnings = list(dict.fromkeys((*leg1_data.warnings, *leg2_data.warnings)))
    warnings.append(
        "估算采用每腿固定名义本金、历史收盘价和当前盘口；未回放区间内价格或核验内部价格缺口。"
    )
    entry, exit_prices, pnl, coverage = {}, {}, {}, {}
    settlements, gaps = [], []
    fees = 0.0
    spread_cost = 0.0
    has_books = True
    notional = request.notional_usd
    for name, leg, data, sign in (
        ("leg1", request.leg1, leg1_data, 1 if request.direction == "long1_short2" else -1),
        ("leg2", request.leg2, leg2_data, -1 if request.direction == "long1_short2" else 1),
    ):
        if data.granularity_seconds <= 0:
            raise ValueError(f"{name}: invalid price granularity")
        for kind, target, points, output in (
            ("entry", request.start_at, data.entry_points, entry),
            ("exit", request.end_at, data.exit_points, exit_prices),
        ):
            valid = [p for p in points if isfinite(p.close) and p.close > 0]
            if not valid:
                raise ValueError(f"{name} {kind}: no historical price near requested endpoint")
            closes = [(p.bucket_at + timedelta(seconds=data.granularity_seconds), p)
                      for p in valid]
            past = [(at, p) for at, p in closes if at <= target]
            if past:
                close_at, point = max(past, key=lambda item: item[0])
            else:
                close_at, point = min(closes, key=lambda item: item[0])
                warnings.append(
                    f"{name} {kind} 无端点时刻及之前已收盘的 K 线，"
                    "使用端点之后最近的收盘价，存在前视偏差。"
                )
            deviation = abs((close_at - target).total_seconds())
            output[name] = {
                "market": leg,
                "price": float(point.close),
                "timestamp": close_at,
                "target_at": target,
                "deviation_seconds": deviation,
            }
            if deviation > 2 * data.granularity_seconds:
                warnings.append(f"{name} {kind} 端点偏差 {deviation:g} 秒，超过数据粒度的 2 倍。")
                gaps.append({"leg": name, "kind": kind, "deviation_seconds": deviation})
        pnl[name] = sign * (exit_prices[name]["price"] / entry[name]["price"] - 1) * notional
        fee_pct = (
            fee_settings.spot_fee_pct
            if leg.market_type == MarketType.SPOT
            else fee_settings.future_fee_pct
        )
        fees += 2 * notional * fee_pct / 100

        current = data.current
        bid = current.bid_price if current else None
        ask = current.ask_price if current else None
        if (
            bid is None
            or ask is None
            or not isfinite(bid)
            or not isfinite(ask)
            or not 0 < bid <= ask
        ):
            has_books = False
            warnings.append(
                f"{name} 当前有效盘口不可用，spread_cost_est 为 null，total 未扣除点差成本。"
            )
        else:
            # Half spread / mid, charged once to open and once to close.
            spread_cost += 2 * notional * (ask - bid) / (ask + bid)

        coverage[name] = None
        if leg.market_type == MarketType.SPOT:
            continue
        unique = {}
        for point in data.funding:
            if (point.exchange, point.symbol, point.dex) != (leg.exchange, leg.symbol, leg.dex):
                raise ValueError(f"{name}: funding market identity mismatch")
            if not isfinite(point.funding_rate_pct):
                warnings.append(f"{name} 资金费记录无效，未计入核算。")
                continue
            if request.start_at < point.funding_time <= request.end_at:
                unique[point.funding_time] = point
        for point in sorted(unique.values(), key=lambda p: p.funding_time):
            settlements.append(
                {
                    "leg": name,
                    "exchange": leg.exchange,
                    "symbol": leg.symbol,
                    "dex": leg.dex,
                    "funding_time": point.funding_time,
                    "rate_pct": point.funding_rate_pct,
                    "pnl": round(-sign * point.funding_rate_pct / 100 * notional, 10),
                }
            )
        interval = current.funding_interval_hours if current else None
        inferred = False
        if interval is None or not isfinite(interval) or interval <= 0:
            times = sorted(unique)
            if len(times) < 2:
                warnings.append(f"{name} 结算周期未知，funding coverage 无法估算；未补零。")
                continue
            interval = median((b - a).total_seconds() for a, b in pairwise(times)) / 3600
            inferred = True
            warnings.append(
                f"{name} 结算周期按区间内实际结算时间相邻间隔中位数推断为 {interval:g} 小时；"
                "缺失记录或周期变化可能影响推断及 funding coverage。"
            )
        period = interval * 3600
        anchor = (
            current.funding_next_time if current and not inferred else None
        ) or min(unique, default=None)
        # Existing venues align funding to UTC. If no phase is available, state
        # that assumption explicitly, rather than inferring cadence from gaps.
        anchor_seconds = anchor.timestamp() if anchor else 0.0
        if anchor is None:
            warnings.append(f"{name} 无结算相位，按 UTC 对齐结算周期估算 coverage。")
        expected = max(
            0,
            floor((request.end_at.timestamp() - anchor_seconds) / period)
            - floor((request.start_at.timestamp() - anchor_seconds) / period),
        )
        actual = len(unique)
        coverage[name] = actual / expected if expected else (1.0 if actual == 0 else None)
        if not inferred:
            warnings.append(
                f"{name} funding coverage 按当前 {interval:g} 小时周期估算，历史周期变化可能影响覆盖率。"
            )
        if actual < expected:
            warnings.append(f"{name} 资金费缺失：实际 {actual} / 预计 {expected} 次结算，未补零。")
            gaps.append({"leg": name, "kind": "funding", "expected": expected, "actual": actual})
        elif actual > expected:
            warnings.append(f"{name} 实际结算次数超过周期估算，历史结算周期可能发生变化。")

    settlements.sort(key=lambda p: (p["funding_time"], p["leg"]))
    price_total = sum(pnl.values())
    funding_total = sum((p["pnl"] for p in settlements), 0.0)
    total = price_total + funding_total - fees - (spread_cost if has_books else 0)
    return PairRangeBacktestResult(
        price_pnl={**{k: round(v, 10) for k, v in pnl.items()}, "total": round(price_total, 10)},
        entry=entry,
        exit=exit_prices,
        funding_pnl={
            "settlements": settlements,
            "total": round(funding_total, 10),
            "coverage": coverage,
        },
        fees=round(fees, 10),
        spread_cost_est=round(spread_cost, 10) if has_books else None,
        total=round(total, 10),
        total_pct_of_notional=round(total / notional * 100, 10),
        data_quality={
            "source": {"leg1": leg1_data.source, "leg2": leg2_data.source},
            "granularity_seconds": {
                "leg1": leg1_data.granularity_seconds,
                "leg2": leg2_data.granularity_seconds,
            },
            "gaps": gaps,
            "funding_coverage": coverage,
            "is_estimate": True,
        },
        warnings=list(dict.fromkeys(warnings)),
    )
