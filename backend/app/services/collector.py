import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from app.exchanges.aster import AsterAdapter
from app.exchanges.base import ExchangeAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bitget import BitgetAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.gate import GateAdapter
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.htx import HTXAdapter
from app.exchanges.okx import OKXAdapter
from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity
from app.models.settings import FeeSettings, RiskSettings
from app.services.data_filters import (
    filter_markets,
    filter_opportunities,
    ignored_exchange_set,
)
from app.services.risk_labels import apply_risk_labels
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_opportunities

logger = logging.getLogger(__name__)

EXCHANGE_COOLDOWN_SECONDS = (15.0, 30.0, 60.0)


@dataclass(frozen=True)
class CollectionResult:
    markets: list[MarketSnapshot]
    opportunities: list[Opportunity]
    exchange_errors: dict[str, str]


@dataclass
class ExchangePollState:
    status: str = "healthy"
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    consecutive_failures: int = 0
    cooldown_until: datetime | None = None
    next_due_at: datetime | None = None
    in_flight: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "consecutive_failures": self.consecutive_failures,
            "cooldown_until": self.cooldown_until,
            "next_due_at": self.next_due_at,
            "in_flight": self.in_flight,
        }


@dataclass(frozen=True)
class AdapterPollResult:
    markets: list[MarketSnapshot]
    errors: dict[str, str]
    should_cool_down: bool


def _is_transient_http_timeout(message: str) -> bool:
    normalized = message.lower()
    return any(
        token in normalized
        for token in (
            "pooltimeout",
            "pool timeout",
            "connecttimeout",
            "connect timeout",
            "readtimeout",
            "read timeout",
            "writetimeout",
            "write timeout",
        )
    )


def _error_message(exc: BaseException) -> str:
    return str(exc) or exc.__class__.__name__


def default_exchange_adapters() -> list[ExchangeAdapter]:
    return [
        BinanceAdapter(),
        OKXAdapter(),
        BybitAdapter(),
        GateAdapter(),
        BitgetAdapter(),
        HTXAdapter(),
        AsterAdapter(),
        HyperliquidAdapter(),
    ]


class MarketCollector:
    def __init__(
        self,
        adapters: list[ExchangeAdapter],
        store: SnapshotStore,
        risk_settings: RiskSettings | None = None,
        fee_settings: FeeSettings | None = None,
        risk_settings_loader=None,
        history_recorder=None,
        poll_interval_seconds: float = 8.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.adapters = adapters
        self.store = store
        self.risk_settings = risk_settings or RiskSettings()
        self.fee_settings = fee_settings or FeeSettings()
        self.risk_settings_loader = risk_settings_loader
        self.history_recorder = history_recorder
        self.poll_interval_seconds = poll_interval_seconds
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._poll_states: dict[str, ExchangePollState] = {}
        self._exchange_snapshots: dict[str, list[MarketSnapshot]] = {}
        self._exchange_errors: dict[str, dict[str, str]] = {}
        self._scheduler_lock = asyncio.Lock()

    def exchange_states(self) -> dict[str, dict[str, object]]:
        return {
            name: state.to_dict()
            for name, state in self._poll_states.items()
        }

    async def _reset_exchange_clients(self) -> None:
        for adapter in self.adapters:
            reset = getattr(adapter, "reset_client", None)
            if reset is not None:
                await reset()

    async def collect_once(self) -> CollectionResult:
        if self.risk_settings_loader is not None:
            self.risk_settings = await self.risk_settings_loader()

        ignored_exchanges = ignored_exchange_set(self.risk_settings)
        active_adapters = [
            adapter for adapter in self.adapters if adapter.name.lower() not in ignored_exchanges
        ]
        if not active_adapters:
            self.store.set_markets([])
            self.store.set_opportunities([])
            self.store.set_exchange_errors({})
            return CollectionResult(markets=[], opportunities=[], exchange_errors={})

        self._seed_exchange_snapshots_from_store()
        now = self._now_fn()
        due_adapters = await self._claim_due_adapters(active_adapters, now)
        if due_adapters:
            results = await asyncio.gather(
                *(self._poll_adapter(adapter) for adapter in due_adapters),
                return_exceptions=True,
            )
            await self._store_poll_results(due_adapters, results, now)

        markets = self._combined_exchange_markets(active_adapters)
        errors = self._combined_exchange_errors(active_adapters)

        filtered_markets = filter_markets(markets, self.risk_settings)

        opportunities = self._build_labeled_opportunities(filtered_markets)
        filtered_opportunities = filter_opportunities(opportunities, self.risk_settings)
        self.store.set_markets(filtered_markets)
        self.store.set_opportunities(filtered_opportunities)
        self.store.set_exchange_errors(errors)
        if self.history_recorder is not None:
            await self.history_recorder.record(filtered_opportunities)
        return CollectionResult(
            markets=filtered_markets,
            opportunities=filtered_opportunities,
            exchange_errors=errors,
        )

    def _state_for(self, exchange_name: str) -> ExchangePollState:
        state = self._poll_states.get(exchange_name)
        if state is None:
            state = ExchangePollState()
            self._poll_states[exchange_name] = state
        return state

    def _seed_exchange_snapshots_from_store(self) -> None:
        if self._exchange_snapshots:
            return
        for market in self.store.get_markets():
            self._exchange_snapshots.setdefault(market.exchange, []).append(market)

    async def _claim_due_adapters(
        self,
        adapters: list[ExchangeAdapter],
        now: datetime,
    ) -> list[ExchangeAdapter]:
        due_adapters: list[ExchangeAdapter] = []
        async with self._scheduler_lock:
            for adapter in adapters:
                state = self._state_for(adapter.name)
                if not self._is_due(state, now):
                    continue
                state.in_flight = True
                due_adapters.append(adapter)
        return due_adapters

    def _is_due(self, state: ExchangePollState, now: datetime) -> bool:
        if state.in_flight:
            return False
        if state.cooldown_until is not None and now < state.cooldown_until:
            return False
        return state.next_due_at is None or now >= state.next_due_at

    async def _store_poll_results(
        self,
        adapters: list[ExchangeAdapter],
        results: list[AdapterPollResult | BaseException],
        now: datetime,
    ) -> None:
        async with self._scheduler_lock:
            for adapter, result in zip(adapters, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning("exchange adapter failed: %s", adapter.name, exc_info=result)
                    result = AdapterPollResult(
                        markets=[],
                        errors={adapter.name: _error_message(result)},
                        should_cool_down=True,
                    )
                self._store_adapter_result(adapter.name, result, now)

    def _store_adapter_result(
        self,
        exchange_name: str,
        result: AdapterPollResult,
        now: datetime,
    ) -> None:
        state = self._state_for(exchange_name)
        state.in_flight = False
        if result.errors:
            self._exchange_errors[exchange_name] = result.errors
            state.last_error_at = now
            state.consecutive_failures += 1
            if result.should_cool_down:
                cooldown_seconds = self._cooldown_seconds(state.consecutive_failures)
                state.status = "cooling_down"
                state.cooldown_until = now + timedelta(seconds=cooldown_seconds)
                state.next_due_at = state.cooldown_until
            else:
                state.status = "degraded"
                state.cooldown_until = None
                state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)
            return

        self._exchange_snapshots[exchange_name] = result.markets
        self._exchange_errors.pop(exchange_name, None)
        state.status = "healthy"
        state.last_success_at = now
        state.consecutive_failures = 0
        state.cooldown_until = None
        state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)

    def _cooldown_seconds(self, consecutive_failures: int) -> float:
        index = min(max(consecutive_failures, 1), len(EXCHANGE_COOLDOWN_SECONDS)) - 1
        return EXCHANGE_COOLDOWN_SECONDS[index]

    async def _poll_adapter(self, adapter: ExchangeAdapter) -> AdapterPollResult:
        pool_error_seen = False
        markets, errors = await self._fetch_adapter_result(adapter)
        if self._has_pool_error(errors):
            pool_error_seen = True
            reset = getattr(adapter, "reset_client", None)
            if reset is not None:
                await reset()
            markets, errors = await self._fetch_adapter_result(adapter)
            pool_error_seen = pool_error_seen or self._has_pool_error(errors)
        return AdapterPollResult(
            markets=markets,
            errors=errors,
            should_cool_down=bool(errors) and (pool_error_seen or self._has_http_timeout(errors) or not markets),
        )

    async def _fetch_adapter_result(
        self,
        adapter: ExchangeAdapter,
    ) -> tuple[list[MarketSnapshot], dict[str, str]]:
        try:
            return await self._fetch_adapter(adapter)
        except Exception as exc:  # noqa: BLE001 - isolate flaky public APIs per exchange.
            logger.warning("exchange adapter failed: %s", adapter.name, exc_info=exc)
            return [], {adapter.name: _error_message(exc)}

    def _has_pool_error(self, errors: dict[str, str]) -> bool:
        return any("pool" in message.lower() and _is_transient_http_timeout(message) for message in errors.values())

    def _has_http_timeout(self, errors: dict[str, str]) -> bool:
        return any(_is_transient_http_timeout(message) for message in errors.values())

    def _combined_exchange_markets(self, adapters: list[ExchangeAdapter]) -> list[MarketSnapshot]:
        markets: list[MarketSnapshot] = []
        for adapter in adapters:
            markets.extend(self._exchange_snapshots.get(adapter.name, []))
        return markets

    def _combined_exchange_errors(self, adapters: list[ExchangeAdapter]) -> dict[str, str]:
        errors: dict[str, str] = {}
        for adapter in adapters:
            errors.update(self._exchange_errors.get(adapter.name, {}))
        return errors

    async def _fetch_adapter(
        self,
        adapter: ExchangeAdapter,
    ) -> tuple[list[MarketSnapshot], dict[str, str]]:
        markets: list[MarketSnapshot] = []
        errors: dict[str, str] = {}
        for label, fetcher in (
            ("spot", adapter.fetch_spot_tickers),
            ("future", adapter.fetch_future_tickers),
        ):
            try:
                markets.extend(await fetcher())
            except Exception as exc:  # noqa: BLE001 - isolate flaky public APIs per market.
                errors[f"{adapter.name}:{label}"] = str(exc) or exc.__class__.__name__
        return markets, errors

    def _build_labeled_opportunities(self, markets: list[MarketSnapshot]) -> list[Opportunity]:
        raw: list[Opportunity] = []
        for mode in ("SF", "FF", "SS"):
            buy_fee = self.fee_settings.spot_fee_pct if mode in {"SF", "SS"} else self.fee_settings.future_fee_pct
            sell_fee = self.fee_settings.future_fee_pct if mode in {"SF", "FF"} else self.fee_settings.spot_fee_pct
            raw.extend(
                build_opportunities(
                    markets,
                    mode=mode,
                    buy_fee_pct=buy_fee,
                    sell_fee_pct=sell_fee,
                    safety_slippage_pct=self.fee_settings.safety_slippage_pct,
                )
            )
        now = datetime.now(UTC)
        labeled = [
            apply_risk_labels(item, settings=self.risk_settings, now=now)
            for item in raw
        ]
        return sorted(labeled, key=lambda item: item.open_spread_pct, reverse=True)

    async def close(self) -> None:
        for adapter in self.adapters:
            await adapter.client.aclose()


async def run_collector_loop(
    collector: MarketCollector,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await collector.collect_once()
        except Exception:
            logger.exception("collector loop failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
