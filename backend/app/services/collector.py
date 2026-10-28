import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.exchanges.arcus import ArcusAdapter
from app.exchanges.aster import AsterAdapter
from app.exchanges.base import ExchangeAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bitget import BitgetAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.gate import GateAdapter
from app.exchanges.htx import HTXAdapter
from app.exchanges.hyperliquid import HyperliquidAdapter
from app.exchanges.lighter import LighterAdapter, RobinhoodLighterAdapter
from app.exchanges.okx import OKXAdapter
from app.models.index_component import index_watch_symbol
from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity
from app.models.settings import FeeSettings, RiskSettings
from app.runtime.cooperative import YieldBudget
from app.runtime.supervisor import StageTimings, beat
from app.services.compact_markets import CompactMarkets, pack
from app.services.data_filters import (
    filter_markets_async,
    filter_opportunities,
    ignored_exchange_set,
)
from app.services.risk_labels import risk_labels_for
from app.services.snapshot_consumers import LatestSnapshotSlot, PublishedSnapshot
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_all_opportunities, build_all_opportunities_async
from app.services.symbol_aliases import apply_symbol_aliases_async

logger = logging.getLogger(__name__)

EXCHANGE_COOLDOWN_SECONDS = (15.0, 30.0, 60.0)
LEGAL_RESTRICTION_COOLDOWN_SECONDS = 3600.0
DEFAULT_MAX_DUE_ADAPTERS_PER_CYCLE = 3


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
    cooldown_seconds: float | None = None


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


def _is_http_legal_restriction(message: str) -> bool:
    normalized = message.lower()
    return "451" in normalized and (
        "client error" in normalized
        or "httpstatuserror" in normalized
        or "status" in normalized
    )


def _error_message(exc: BaseException) -> str:
    text = str(exc).strip()
    message = f"{exc.__class__.__name__}: {text}" if text else exc.__class__.__name__
    cause = getattr(exc, "__cause__", None)
    if cause is None:
        return message
    cause_text = str(cause).strip()
    cause_message = f"{cause.__class__.__name__}: {cause_text}" if cause_text else cause.__class__.__name__
    return f"{message}; caused by {cause_message}"


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
        LighterAdapter(),
        RobinhoodLighterAdapter(),
        ArcusAdapter(),
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
        index_component_provider=None,
        index_component_monitor=None,
        poll_interval_seconds: float = 5.0,
        max_due_adapters_per_cycle: int = DEFAULT_MAX_DUE_ADAPTERS_PER_CYCLE,
        now_fn: Callable[[], datetime] | None = None,
        market_validation=None,
        timing_window: int = 120,
        snapshot_slots: Sequence[LatestSnapshotSlot] | None = None,
        risk_settings_timeout_seconds: float = 2.0,
    ) -> None:
        self.adapters = adapters
        self.store = store
        self.risk_settings = risk_settings or RiskSettings()
        self.fee_settings = fee_settings or FeeSettings()
        self.risk_settings_loader = risk_settings_loader
        self.history_recorder = history_recorder
        self.index_component_provider = index_component_provider
        self.index_component_monitor = index_component_monitor
        self.poll_interval_seconds = poll_interval_seconds
        self.max_due_adapters_per_cycle = max(1, max_due_adapters_per_cycle)
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._poll_states: dict[str, ExchangePollState] = {}
        self._exchange_snapshots: dict[str, CompactMarkets] = {}
        self._exchange_errors: dict[str, dict[str, str]] = {}
        self._adapter_cursor = 0
        self._scheduler_lock = asyncio.Lock()
        self.market_validation = market_validation
        self.stage_timings = StageTimings(timing_window)
        self.snapshot_slots = snapshot_slots
        self.risk_settings_timeout_seconds = risk_settings_timeout_seconds
        self._risk_settings_loaded = False
        self._risk_settings_fallbacks = 0
        if snapshot_slots is not None:
            self.stage_timings.names = ("poll", "prepare", "build", "publish")

    def runtime_stats(self) -> dict[str, int]:
        return {"risk_settings_fallbacks": self._risk_settings_fallbacks}

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
        sample: dict[str, float] = {}
        try:
            return await self._collect_once(sample)
        finally:
            self.stage_timings.rounds.append(sample)

    async def _collect_once(self, sample: dict[str, float]) -> CollectionResult:
        if self.risk_settings_loader is not None:
            try:
                self.risk_settings = await asyncio.wait_for(
                    self.risk_settings_loader(), timeout=self.risk_settings_timeout_seconds,
                )
            except Exception as exc:
                if not self._risk_settings_loaded:
                    raise
                self._risk_settings_fallbacks += 1
                logger.warning("risk settings read failed (%s); reusing last successful settings", type(exc).__name__)
            else:
                self._risk_settings_loaded = True

        ignored_exchanges = ignored_exchange_set(self.risk_settings)
        active_adapters = [
            adapter for adapter in self.adapters if adapter.name.lower() not in ignored_exchanges
        ]
        if not active_adapters:
            with self.stage_timings.measure(sample, "publish"):
                self.store.set_markets([])
                self.store.set_opportunities([])
                self.store.set_exchange_errors({})
                self.store.mark_published(self._now_fn())
                self._publish_to_slots([], [])
            return CollectionResult(markets=[], opportunities=[], exchange_errors={})

        with self.stage_timings.measure(sample, "poll"):
            self._seed_exchange_snapshots_from_store()
            now = self._now_fn()
            due_adapters = await self._claim_due_adapters(active_adapters, now)
            if due_adapters:
                results = await asyncio.gather(
                    *(self._poll_adapter(adapter) for adapter in due_adapters),
                    return_exceptions=True,
                )
                await self._store_poll_results(due_adapters, results, now)

        with self.stage_timings.measure(sample, "prepare"):
            validation_epoch = getattr(self.market_validation, "_mutation_epoch", None)
            markets = self._combined_exchange_markets(active_adapters)
            errors = self._combined_exchange_errors(active_adapters)

            budget = YieldBudget()
            aliased_markets = await apply_symbol_aliases_async(
                markets, self.risk_settings.symbol_aliases, budget=budget,
            )
            if self.market_validation is not None:
                aliased_markets = await self.market_validation.prepare(aliased_markets, self.risk_settings)
            filtered_markets = await filter_markets_async(
                aliased_markets, self.risk_settings, budget=budget,
            )

        with self.stage_timings.measure(sample, "build"):
            opportunities = await self._build_labeled_opportunities_async(
                filtered_markets, now=now, budget=budget,
            )
            if self.market_validation is not None:
                by_key = {
                    (m.exchange, m.market_type, m.raw_symbol): m for m in filtered_markets
                }
                accepted = []
                for item in opportunities:
                    await budget.maybe_yield()
                    a = by_key.get((item.buy_exchange, item.buy_market_type, item.buy_raw_symbol))
                    b = by_key.get((item.sell_exchange, item.sell_market_type, item.sell_raw_symbol))
                    if a is None or b is None:
                        continue
                    item.validation = self.market_validation.validate(a, b, self.risk_settings)
                    if self.market_validation.should_enforce(item.validation) and not item.validation.capabilities.same_asset_opportunity:
                        continue
                    item.risk_labels = risk_labels_for(item, self.risk_settings, now)
                    accepted.append(item)
                opportunities = accepted
            filtered_opportunities = filter_opportunities(
                opportunities,
                self.risk_settings,
                now=now,
            )
        with self.stage_timings.measure(sample, "publish"):
            # A binding update can revoke capabilities while post-processing yields.
            # Keep its refreshed store intact and retry on the next collector round.
            if validation_epoch != getattr(self.market_validation, "_mutation_epoch", None):
                raise ValueError("mapping changed during collection")
            self.store.set_all_markets(aliased_markets)
            self.store.set_markets(filtered_markets)
            self.store.set_opportunities(filtered_opportunities)
            self.store.set_exchange_errors(errors)
            self.store.mark_published(self._now_fn())
            self._publish_to_slots(filtered_markets, filtered_opportunities)
        if self.snapshot_slots is None:
            with self.stage_timings.measure(sample, "history"):
                if self.history_recorder is not None:
                    # Preserve the recorder's own clock in the rollback path.
                    await self.history_recorder.record(filtered_opportunities)
            with self.stage_timings.measure(sample, "index"):
                await self.process_index_components(filtered_markets)
        return CollectionResult(
            markets=filtered_markets,
            opportunities=filtered_opportunities,
            exchange_errors=errors,
        )

    def _publish_to_slots(
        self, markets: Sequence[MarketSnapshot], opportunities: Sequence[Opportunity],
    ) -> None:
        if self.snapshot_slots is None:
            return
        snapshot = PublishedSnapshot(
            generation=self.store.generation,
            published_at=self.store.published_at,
            markets=tuple(markets),
            opportunities=tuple(opportunities),
        )
        for slot in self.snapshot_slots:
            slot.put(snapshot)

    async def record_history(self, snapshot: PublishedSnapshot) -> None:
        if self.history_recorder is not None:
            await self.history_recorder.record(list(snapshot.opportunities), now=snapshot.published_at)

    async def process_index_components(self, markets: Sequence[MarketSnapshot]) -> None:
        if self.index_component_provider is None or self.index_component_monitor is None:
            return
        try:
            markets = await self._index_component_markets(list(markets))
            observe_markets = getattr(self.index_component_monitor, "observe_markets", None)
            if observe_markets is not None:
                await observe_markets(markets)
            if not markets:
                return
            snapshots = await self.index_component_provider.fetch_components(markets)
            if snapshots:
                await self.index_component_monitor.process_snapshots(snapshots)
        except Exception:
            logger.exception("index component monitor failed")

    async def _index_component_markets(self, markets: list[MarketSnapshot]) -> list[MarketSnapshot]:
        watched_symbols = await self._index_component_watched_symbols()
        if watched_symbols is None:
            return markets
        try:
            normalized_watched = {index_watch_symbol(symbol) for symbol in watched_symbols}
        except ValueError:
            # Preserve lazy matching/error order for malformed injected watch
            # lists instead of failing early before a valid earlier match.
            normalized_watched = None
        return [
            market
            for market in markets
            if self._index_component_symbol_is_watched(
                market.symbol, watched_symbols, normalized_watched,
            )
            or (
                market.symbol_alias_original_symbol is not None
                and self._index_component_symbol_is_watched(
                    market.symbol_alias_original_symbol, watched_symbols, normalized_watched,
                )
            )
        ]

    async def _index_component_watched_symbols(self) -> set[str] | None:
        watched_symbols = getattr(self.index_component_monitor, "watched_symbols", None)
        if watched_symbols is None:
            return None
        symbols = await watched_symbols()
        return {symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()}

    def _index_component_symbol_is_watched(
        self, symbol: str, watched_symbols: set[str], normalized_watched: set[str] | None = None,
    ) -> bool:
        normalized = index_watch_symbol(symbol)
        if normalized_watched is not None:
            return normalized in normalized_watched
        return any(index_watch_symbol(watched) == normalized for watched in watched_symbols)

    def _state_for(self, exchange_name: str) -> ExchangePollState:
        state = self._poll_states.get(exchange_name)
        if state is None:
            state = ExchangePollState()
            self._poll_states[exchange_name] = state
        return state

    def _seed_exchange_snapshots_from_store(self) -> None:
        if self._exchange_snapshots:
            return
        by_exchange: dict[str, list[MarketSnapshot]] = {}
        for market in self.store.get_markets():
            by_exchange.setdefault(market.exchange, []).append(market)
        self._exchange_snapshots = {
            exchange: pack(markets) for exchange, markets in by_exchange.items()
        }

    async def _claim_due_adapters(
        self,
        adapters: list[ExchangeAdapter],
        now: datetime,
    ) -> list[ExchangeAdapter]:
        due_adapters: list[ExchangeAdapter] = []
        async with self._scheduler_lock:
            if not adapters:
                return []
            start = self._adapter_cursor % len(adapters)
            ordered_adapters = adapters[start:] + adapters[:start]
            last_claimed_index: int | None = None
            for offset, adapter in enumerate(ordered_adapters):
                state = self._state_for(adapter.name)
                if not self._is_due(state, now):
                    continue
                state.in_flight = True
                due_adapters.append(adapter)
                last_claimed_index = (start + offset) % len(adapters)
                if len(due_adapters) >= self.max_due_adapters_per_cycle:
                    break
            if last_claimed_index is not None:
                self._adapter_cursor = (last_claimed_index + 1) % len(adapters)
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
            if result.markets and not result.should_cool_down:
                self._exchange_snapshots[exchange_name] = pack(result.markets)
            self._exchange_errors[exchange_name] = result.errors
            state.last_error_at = now
            state.consecutive_failures += 1
            if result.should_cool_down:
                cooldown_seconds = result.cooldown_seconds or self._cooldown_seconds(
                    state.consecutive_failures
                )
                state.status = "cooling_down"
                state.cooldown_until = now + timedelta(seconds=cooldown_seconds)
                state.next_due_at = state.cooldown_until
            else:
                state.status = "degraded"
                state.cooldown_until = None
                state.next_due_at = now + timedelta(seconds=self.poll_interval_seconds)
            return

        self._exchange_snapshots[exchange_name] = pack(result.markets)
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
            should_cool_down=bool(errors)
            and (pool_error_seen or self._has_http_timeout(errors) or not markets),
            cooldown_seconds=(
                LEGAL_RESTRICTION_COOLDOWN_SECONDS
                if self._has_http_legal_restriction(errors)
                else None
            ),
        )

    async def _fetch_adapter_result(
        self,
        adapter: ExchangeAdapter,
    ) -> tuple[list[MarketSnapshot], dict[str, str]]:
        try:
            return await self._fetch_adapter(adapter)
        except Exception as exc:
            logger.warning("exchange adapter failed: %s", adapter.name, exc_info=exc)
            return [], {adapter.name: _error_message(exc)}

    def _has_pool_error(self, errors: dict[str, str]) -> bool:
        return any("pool" in message.lower() and _is_transient_http_timeout(message) for message in errors.values())

    def _has_http_timeout(self, errors: dict[str, str]) -> bool:
        return any(_is_transient_http_timeout(message) for message in errors.values())

    def _has_http_legal_restriction(self, errors: dict[str, str]) -> bool:
        return any(_is_http_legal_restriction(message) for message in errors.values())

    def _combined_exchange_markets(self, adapters: list[ExchangeAdapter]) -> list[MarketSnapshot]:
        markets: list[MarketSnapshot] = []
        for adapter in adapters:
            snapshot = self._exchange_snapshots.get(adapter.name)
            if snapshot is not None:
                markets.extend(snapshot.unpack())
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
                errors[f"{adapter.name}:{label}"] = _error_message(exc)
        market_errors = getattr(adapter, "market_errors", None)
        if isinstance(market_errors, dict):
            errors.update(
                {
                    str(key): str(value)
                    for key, value in market_errors.items()
                    if str(key).strip() and str(value).strip()
                }
            )
        return markets, errors

    def _build_labeled_opportunities(
        self,
        markets: list[MarketSnapshot],
        now: datetime | None = None,
    ) -> list[Opportunity]:
        current = now or self._now_fn()
        opportunities = build_all_opportunities(
            markets,
            spot_fee_pct=self.fee_settings.spot_fee_pct,
            future_fee_pct=self.fee_settings.future_fee_pct,
            safety_slippage_pct=self.fee_settings.safety_slippage_pct,
            now=current,
            stale_after_seconds=self.risk_settings.stale_after_seconds,
        )
        collision_symbols = frozenset(
            symbol.upper() for symbol in self.risk_settings.ticker_collision_symbols
        )
        for item in opportunities:
            # These models were just constructed for this collection, and have
            # not been published to the store or passed to another consumer.
            item.risk_labels = risk_labels_for(
                item, self.risk_settings, current, collision_symbols=collision_symbols,
            )
        return opportunities

    async def _build_labeled_opportunities_async(
        self,
        markets: list[MarketSnapshot],
        now: datetime | None = None,
        *,
        budget: YieldBudget | None = None,
    ) -> list[Opportunity]:
        budget = budget or YieldBudget()
        current = now or self._now_fn()
        opportunities = await build_all_opportunities_async(
            markets,
            budget=budget,
            spot_fee_pct=self.fee_settings.spot_fee_pct,
            future_fee_pct=self.fee_settings.future_fee_pct,
            safety_slippage_pct=self.fee_settings.safety_slippage_pct,
            now=current,
            stale_after_seconds=self.risk_settings.stale_after_seconds,
        )
        collision_symbols = frozenset(
            symbol.upper() for symbol in self.risk_settings.ticker_collision_symbols
        )
        for item in opportunities:
            await budget.maybe_yield()
            # These models were just constructed for this collection, and have
            # not been published to the store or passed to another consumer.
            item.risk_labels = risk_labels_for(
                item, self.risk_settings, current, collision_symbols=collision_symbols,
            )
        return opportunities

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
            beat("market-collector", ok=False)
            logger.exception("collector loop failed")
        else:
            beat("market-collector", ok=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
