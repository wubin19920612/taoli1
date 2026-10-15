import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

from app.models.market import MarketType
from app.models.opportunity import Opportunity
from app.models.trade_availability import (
    MarketTradeAvailability,
    SpotTransferAvailability,
    TradeActionStatus,
    TradeAvailabilityResult,
    TradeAvailabilityState,
    TransferAvailabilityState,
)
from app.services.hyperliquid_trade_status import hyperliquid_dex_from_raw_symbol
from app.services.trade_availability import TradeAvailabilityService

ALERT_TIMEZONE = timezone(timedelta(hours=8), "UTC+8")
DIAGNOSTIC_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class OpportunityTradeAvailabilityReport:
    text: str
    opening_restricted: bool


@dataclass(frozen=True)
class _Leg:
    label: str
    exchange: str
    market_type: MarketType
    raw_symbol: str | None
    is_buy_leg: bool


async def build_opportunity_trade_availability_report(
    service: TradeAvailabilityService,
    opportunity: Opportunity,
) -> OpportunityTradeAvailabilityReport:
    legs = (
        _Leg(
            label="买入腿",
            exchange=opportunity.buy_exchange.lower(),
            market_type=opportunity.buy_market_type,
            raw_symbol=opportunity.buy_raw_symbol,
            is_buy_leg=True,
        ),
        _Leg(
            label="卖出腿",
            exchange=opportunity.sell_exchange.lower(),
            market_type=opportunity.sell_market_type,
            raw_symbol=opportunity.sell_raw_symbol,
            is_buy_leg=False,
        ),
    )
    exchanges = list(dict.fromkeys(leg.exchange for leg in legs))
    status_group = asyncio.gather(
        *(
            asyncio.wait_for(
                service.fetch_status(opportunity.symbol, exchange=exchange),
                timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
            )
            for exchange in exchanges
        ),
        return_exceptions=True,
    )
    transfer_group = asyncio.gather(
        *(
            asyncio.wait_for(
                service.fetch_transfer_status(
                    opportunity.symbol,
                    exchange=exchange,
                    raw_symbol=_transfer_reference_raw(legs, exchange),
                ),
                timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
            )
            for exchange in exchanges
        ),
        return_exceptions=True,
    )
    fetched, fetched_transfers = await asyncio.gather(status_group, transfer_group)
    results = dict(zip(exchanges, fetched, strict=True))
    transfers = dict(zip(exchanges, fetched_transfers, strict=True))
    markets_by_leg: dict[str, MarketTradeAvailability | None] = {}
    errors_by_exchange: dict[str, str] = {}
    for exchange, value in results.items():
        if isinstance(value, BaseException):
            errors_by_exchange[exchange] = _safe_error(value)
            continue
        for leg in legs:
            if leg.exchange == exchange:
                markets_by_leg[leg.label] = _find_leg_market(value, leg)

    opening_actions = [
        _path_action(leg, markets_by_leg.get(leg.label), opening=True) for leg in legs
    ]
    closing_actions = [
        _path_action(leg, markets_by_leg.get(leg.label), opening=False) for leg in legs
    ]
    opening_state = _path_state([item[1] for item in opening_actions])
    closing_state = _path_state([item[1] for item in closing_actions])
    lines = [
        "【交易与充提状态】",
        f"开仓路径：{opening_state}（{'；'.join(_path_item_text(item) for item in opening_actions)}）",
        f"平仓路径：{closing_state}（{'；'.join(_path_item_text(item) for item in closing_actions)}）",
    ]
    for leg in legs:
        market = markets_by_leg.get(leg.label)
        lines.extend(
            _leg_lines(
                leg,
                market,
                errors_by_exchange.get(leg.exchange),
                opportunity.symbol,
            )
        )

    lines.append("充提参考：")
    for exchange in exchanges:
        value = results[exchange]
        spot_market = (
            None
            if isinstance(value, BaseException)
            else _find_transfer_market(
                value,
                opportunity.symbol,
                tuple(leg for leg in legs if leg.exchange == exchange),
            )
        )
        if spot_market is not None:
            lines.append(_transfer_market_line(spot_market))
            continue
        transfer = transfers[exchange]
        if isinstance(transfer, BaseException):
            lines.append(f"- {exchange}：未知（诊断失败：{_safe_error(transfer)}）")
            continue
        lines.append(_transfer_asset_line(exchange, transfer))
    lines.append(
        "口径：公开可用不代表账户一定可下单；有条件表示仍需对应持仓、数量和 Reduce Only；"
        "未知不等于关闭；账户限制与真实订单错误未核验；未发送探测订单。"
    )
    return OpportunityTradeAvailabilityReport(
        text="\n".join(lines),
        opening_restricted=opening_state != "公开可用",
    )


def _find_leg_market(result: TradeAvailabilityResult, leg: _Leg) -> MarketTradeAvailability | None:
    candidates = [
        market
        for market in result.markets
        if market.exchange == leg.exchange and market.market_type == leg.market_type
    ]
    if not leg.raw_symbol:
        return candidates[0] if len(candidates) == 1 else None
    expected_raw = leg.raw_symbol.upper()
    if leg.exchange != "hyperliquid":
        return next(
            (market for market in candidates if market.raw_symbol.upper() == expected_raw),
            None,
        )
    expected_dex = hyperliquid_dex_from_raw_symbol(leg.raw_symbol)
    expected_coin = expected_raw.split(":", 1)[-1]
    return next(
        (
            market
            for market in candidates
            if (market.dex or "main") == expected_dex
            and market.raw_symbol.upper().split(":", 1)[-1] == expected_coin
        ),
        None,
    )


def _find_transfer_market(
    result: TradeAvailabilityResult,
    symbol: str,
    legs: tuple[_Leg, ...],
) -> MarketTradeAvailability | None:
    for leg in legs:
        if leg.market_type != MarketType.SPOT:
            continue
        exact_market = _find_leg_market(result, leg)
        if exact_market is not None and exact_market.spot_transfer is not None:
            return exact_market
    normalized_symbol = _normalize_symbol(symbol)
    candidates = [
        market
        for market in result.markets
        if market.market_type == MarketType.SPOT
        and market.spot_transfer is not None
        and _normalize_symbol(market.raw_symbol) == normalized_symbol
    ]
    return candidates[0] if candidates else None


def _leg_lines(
    leg: _Leg,
    market: MarketTradeAvailability | None,
    query_error: str | None,
    symbol: str,
) -> list[str]:
    if market is None:
        identity = _expected_identity(leg, symbol)
        detail = f"诊断失败：{query_error}" if query_error else "本次诊断未返回该精确原始市场"
        return [f"{leg.label}：{identity}", f"  交易状态：未知（{detail}）"]
    lines = [
        f"{leg.label}：{_market_identity(market)}",
        f"  公开状态：{market.public_status_code}；检测 {_format_time(market.observed_at)}",
    ]
    if market.market_type == MarketType.SPOT:
        lines.append(
            "  现货交易："
            f"买入 {_action_state(market.buy_open)}；"
            f"卖出 {_action_state(market.sell_open)}"
        )
    else:
        lines.extend(
            [
                (
                    "  买入方向："
                    f"开多 {_action_state(market.buy_open)}；"
                    f"平空 {_action_state(market.buy_reduce_only)}"
                ),
                (
                    "  卖出方向："
                    f"开空 {_action_state(market.sell_open)}；"
                    f"平多 {_action_state(market.sell_reduce_only)}"
                ),
            ]
        )
    if market.public_restrictions:
        lines.append(f"  公开限制：{'；'.join(dict.fromkeys(market.public_restrictions))}")
    return lines


def _path_action(
    leg: _Leg,
    market: MarketTradeAvailability | None,
    *,
    opening: bool,
) -> tuple[str, TradeAvailabilityState | None]:
    if market is None:
        if leg.market_type == MarketType.SPOT:
            action = "买入" if leg.is_buy_leg == opening else "卖出"
        else:
            action = (
                ("开多" if leg.is_buy_leg else "开空")
                if opening
                else ("平多" if leg.is_buy_leg else "平空")
            )
        return f"{leg.label}{action}", None
    if opening:
        action = market.buy_open if leg.is_buy_leg else market.sell_open
        label = (
            ("买入" if leg.is_buy_leg else "卖出")
            if market.market_type == MarketType.SPOT
            else ("开多" if leg.is_buy_leg else "开空")
        )
    else:
        action = (
            (market.sell_open if leg.is_buy_leg else market.buy_open)
            if market.market_type == MarketType.SPOT
            else (market.sell_reduce_only if leg.is_buy_leg else market.buy_reduce_only)
        )
        label = (
            ("卖出" if leg.is_buy_leg else "买入")
            if market.market_type == MarketType.SPOT
            else ("平多" if leg.is_buy_leg else "平空")
        )
    return f"{leg.label}{label}", action.state


def _path_state(states: list[TradeAvailabilityState | None]) -> str:
    if any(state == TradeAvailabilityState.BLOCKED for state in states):
        return "不可用"
    if any(
        state is None
        or state in {
            TradeAvailabilityState.CONDITIONAL,
            TradeAvailabilityState.UNKNOWN,
            TradeAvailabilityState.NOT_APPLICABLE,
        }
        for state in states
    ):
        return "未知"
    return "公开可用"


def _path_item_text(item: tuple[str, TradeAvailabilityState | None]) -> str:
    label, state = item
    return f"{label} {_state_label(state)}"


def _action_state(action: TradeActionStatus) -> str:
    label = _state_label(action.state)
    if action.state == TradeAvailabilityState.AVAILABLE:
        return label
    return f"{label}[{action.reason_code}]"


def _state_label(state: TradeAvailabilityState | None) -> str:
    return {
        TradeAvailabilityState.AVAILABLE: "公开可用",
        TradeAvailabilityState.BLOCKED: "公开受限",
        TradeAvailabilityState.CONDITIONAL: "未知",
        TradeAvailabilityState.UNKNOWN: "未知",
        TradeAvailabilityState.NOT_APPLICABLE: "不适用",
        None: "未知",
    }[state]


def _transfer_market_line(market: MarketTradeAvailability) -> str:
    transfer = market.spot_transfer
    if transfer is None:
        return f"- {_market_identity(market)}：未知（未返回充提状态，未推断为关闭）"
    return _format_transfer_line(f"- {_market_identity(market)}", transfer)


def _transfer_asset_line(exchange: str, transfer: SpotTransferAvailability) -> str:
    return _format_transfer_line(f"- {exchange} / asset {transfer.asset}", transfer)


def _format_transfer_line(prefix: str, transfer: SpotTransferAvailability) -> str:
    line = (
        f"{prefix}："
        f"充币 {_transfer_state(transfer.deposit_state)}；"
        f"提币 {_transfer_state(transfer.withdraw_state)}；"
        f"{len(transfer.networks)} 条链；来源 {transfer.source}；"
        f"检测 {_format_time(transfer.observed_at)}"
    )
    detail = _transfer_detail(transfer)
    return f"{line}；说明 {detail}" if detail else line


def _transfer_reference_raw(legs: tuple[_Leg, ...], exchange: str) -> str | None:
    exchange_legs = [leg for leg in legs if leg.exchange == exchange]
    spot_leg = next(
        (leg for leg in exchange_legs if leg.market_type == MarketType.SPOT),
        None,
    )
    reference = spot_leg or (exchange_legs[0] if exchange_legs else None)
    return reference.raw_symbol if reference is not None else None


def _transfer_detail(transfer: SpotTransferAvailability) -> str:
    if transfer.error:
        return _truncate(transfer.error)
    if (
        transfer.deposit_state == TransferAvailabilityState.UNKNOWN
        or transfer.withdraw_state == TransferAvailabilityState.UNKNOWN
    ):
        return _truncate(transfer.note)
    return ""


def _transfer_state(state: TransferAvailabilityState) -> str:
    return {
        TransferAvailabilityState.ENABLED: "全开",
        TransferAvailabilityState.PARTIAL: "部分开放",
        TransferAvailabilityState.DISABLED: "已关闭",
        TransferAvailabilityState.UNKNOWN: "未知",
    }[state]


def _market_identity(market: MarketTradeAvailability) -> str:
    dex = f" / DEX {market.dex}" if market.dex else ""
    return f"{market.exchange} / {market.market_type.value}{dex} / {market.raw_symbol}"


def _expected_identity(leg: _Leg, symbol: str) -> str:
    raw_symbol = leg.raw_symbol or symbol
    dex = ""
    if leg.exchange == "hyperliquid":
        dex = f" / DEX {hyperliquid_dex_from_raw_symbol(raw_symbol)}"
    return f"{leg.exchange} / {leg.market_type.value}{dex} / {raw_symbol}"


def _normalize_symbol(value: str) -> str:
    return value.upper().replace("-", "").replace("_", "").replace("/", "")


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ALERT_TIMEZONE).strftime("%m-%d %H:%M:%S")


def _safe_error(error: BaseException) -> str:
    detail = str(error).strip() or error.__class__.__name__
    return _truncate(f"{error.__class__.__name__}: {detail}")


def _truncate(value: str, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"
