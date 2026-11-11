"""Deterministic, entirely offline 12,000-market / 4,000-opportunity workload."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.market import MarketSnapshot
from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey
from app.models.opportunity import Opportunity
from app.models.settings import RiskSettings
from app.services.market_identity import MarketIdentityRegistry
from app.services.snapshot_store import SnapshotStore

NOW = datetime(2026, 11, 11, 1, tzinfo=UTC)


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz else cls.current.replace(tzinfo=None)


def workload(groups=4000):
    rows, bindings, opportunities = [], [], []
    for i in range(groups):
        symbol = f"TOKEN{i}USDT"
        for leg, exchange in enumerate(("bitget", "gate", "hyperliquid")):
            raw = f"xyz:TOKEN{i}" if leg == 2 else symbol
            row = MarketSnapshot(
                symbol=symbol, base=f"TOKEN{i}", exchange=exchange, raw_symbol=raw,
                dex="xyz" if leg == 2 else None, market_type="future",
                bid=100 + leg, ask=100.1 + leg, bid_size=100, ask_size=110,
                timestamp=NOW, upstream_timestamp=NOW,
                quote_time_source=("received_batch" if i % 2 else "ws") if leg == 1 else "upstream",
                volume_24h_usdt=2_000_000, funding_rate_pct=0.01, funding_interval_hours=8,
            )
            rows.append(row)
            if i % 17 == 0 and leg == 1:
                continue
            bindings.append(MarketBinding(
                market=MarketKey.from_snapshot(row),
                asset_id=f"asset{i}" + ("-collision" if i % 11 == 0 and leg == 1 else ""),
                display_name=f"Asset {i}", identity_status="verified", unit_status="verified",
                verified_at=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(seconds=-1 if i % 13 == 0 else 3600),
                quote_currency="USDC" if i % 5 == 0 and leg == 0 else "USDT",
                evidence=[IdentityEvidence(source_type="official_announcement",
                    url="https://example.invalid/offline", summary="Offline fixture", fetched_at=NOW)],
            ))
        a, b = rows[-3:-1]
        opportunities.append(Opportunity(
            id=f"fixture-{i}", type="FF", symbol=symbol,
            buy_exchange=a.exchange, buy_market_type=a.market_type, buy_raw_symbol=a.raw_symbol,
            sell_exchange=b.exchange, sell_market_type=b.market_type, sell_raw_symbol=b.raw_symbol,
            open_spread_pct=1, close_spread_pct=1, fee_adjusted_open_pct=0.9,
            spread_width_pct=0.1, buy_bid=a.bid, buy_ask=a.ask, sell_bid=b.bid, sell_ask=b.ask,
            buy_volume_24h_usdt=2_000_000, sell_volume_24h_usdt=2_000_000,
            risk_labels=[], last_seen_at=NOW,
        ))
    return rows, bindings, opportunities


def service(cls, bindings, mode="shadow"):
    instance = cls(SimpleNamespace(discover=AsyncMock(), invalidate_opportunities=AsyncMock()),
                   SnapshotStore())
    instance.settings = instance.settings.model_copy(update={"mode": mode})
    instance.registry = MarketIdentityRegistry(bindings, mode=mode)
    instance.fx = (0.999, 1.001, NOW, NOW + timedelta(seconds=60))
    instance._refresh_fx = AsyncMock()
    instance._background_review = AsyncMock()
    return instance


async def prepared(cls, data, mode="shadow"):
    rows, bindings, opportunities = data
    instance = service(cls, bindings, mode)
    annotated = await instance.prepare(rows, RiskSettings())
    instance.store.set_all_markets(annotated)
    instance.store.mark_published(NOW)
    return instance, annotated, opportunities


def dump(items):
    return [item.model_dump(exclude={"validated_at": True, "validation": {"validated_at"}})
            for item in items]
