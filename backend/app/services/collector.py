import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.exchanges.aster import AsterAdapter
from app.exchanges.base import ExchangeAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.bitget import BitgetAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.gate import GateAdapter
from app.exchanges.htx import HTXAdapter
from app.exchanges.okx import OKXAdapter
from app.models.market import MarketSnapshot
from app.models.opportunity import Opportunity
from app.models.settings import FeeSettings, RiskSettings
from app.services.risk_labels import apply_risk_labels
from app.services.snapshot_store import SnapshotStore
from app.services.spread_engine import build_opportunities

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionResult:
    markets: list[MarketSnapshot]
    opportunities: list[Opportunity]
    exchange_errors: dict[str, str]


def default_exchange_adapters() -> list[ExchangeAdapter]:
    return [
        BinanceAdapter(),
        OKXAdapter(),
        BybitAdapter(),
        GateAdapter(),
        BitgetAdapter(),
        HTXAdapter(),
        AsterAdapter(),
    ]


class MarketCollector:
    def __init__(
        self,
        adapters: list[ExchangeAdapter],
        store: SnapshotStore,
        risk_settings: RiskSettings | None = None,
        fee_settings: FeeSettings | None = None,
        risk_settings_loader=None,
    ) -> None:
        self.adapters = adapters
        self.store = store
        self.risk_settings = risk_settings or RiskSettings()
        self.fee_settings = fee_settings or FeeSettings()
        self.risk_settings_loader = risk_settings_loader

    async def _reset_exchange_clients(self) -> None:
        for adapter in self.adapters:
            reset = getattr(adapter, "reset_client", None)
            if reset is not None:
                await reset()

    async def collect_once(self) -> CollectionResult:
        markets: list[MarketSnapshot] = []
        errors: dict[str, str] = {}
        results = await asyncio.gather(
            *(self._fetch_adapter(adapter) for adapter in self.adapters),
            return_exceptions=True,
        )
        for adapter, result in zip(self.adapters, results, strict=True):
            if isinstance(result, Exception):
                errors[adapter.name] = str(result)
                logger.warning("exchange adapter failed: %s", adapter.name, exc_info=result)
                continue
            adapter_markets, adapter_errors = result
            markets.extend(adapter_markets)
            errors.update(adapter_errors)

        if not markets and self.store.get_markets():
            self.store.set_exchange_errors(errors)
            return CollectionResult(
                markets=self.store.get_markets(),
                opportunities=self.store.get_opportunities(),
                exchange_errors=errors,
            )

        if not markets and errors:
            await self._reset_exchange_clients()
            retry_results = await asyncio.gather(
                *(self._fetch_adapter(adapter) for adapter in self.adapters),
                return_exceptions=True,
            )
            errors = {}
            markets = []
            for adapter, result in zip(self.adapters, retry_results, strict=True):
                if isinstance(result, Exception):
                    errors[adapter.name] = str(result)
                    logger.warning("exchange adapter retry failed: %s", adapter.name, exc_info=result)
                    continue
                adapter_markets, adapter_errors = result
                markets.extend(adapter_markets)
                errors.update(adapter_errors)

        if self.risk_settings_loader is not None:
            self.risk_settings = await self.risk_settings_loader()

        opportunities = self._build_labeled_opportunities(markets)
        self.store.set_markets(markets)
        self.store.set_opportunities(opportunities)
        self.store.set_exchange_errors(errors)
        return CollectionResult(markets=markets, opportunities=opportunities, exchange_errors=errors)

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
