from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.second_level_sampling import (
    SecondLevelIndexComponentSample,
    SecondLevelMarketSample,
    SecondLevelSamplingConfig,
)
from app.services.second_level_sampler import SecondLevelSampler, SecondLevelSamplingRepository


class FutureOnlyFetcher:
    async def fetch(self, exchange: str, symbol: str) -> SecondLevelMarketSample:
        return SecondLevelMarketSample(
            observed_at=datetime(2026, 7, 23, 8, 0, tzinfo=UTC),
            exchange=exchange,
            symbol=symbol,
            status="partial",
            future_mid=10.1,
            mark_price=10.1,
            index_price=10,
            mark_premium_pct=1,
            error="spot: 现货行情不可用：交易所未返回该标的现货价格",
        )

    async def aclose(self) -> None:
        return None


class ComponentMoveFetcher:
    def __init__(self) -> None:
        self.tick = 0
        self.base_at = datetime.now(UTC).replace(microsecond=0)

    async def fetch(self, exchange: str, symbol: str) -> SecondLevelMarketSample:
        observed_at = self.base_at + timedelta(seconds=self.tick)
        index_price = 100 + self.tick * 0.8
        mark_price = 101 + self.tick
        return SecondLevelMarketSample(
            observed_at=observed_at,
            exchange=exchange,
            symbol=symbol,
            status="ok",
            future_mid=mark_price,
            mark_price=mark_price,
            index_price=index_price,
            mark_premium_pct=(mark_price / index_price - 1) * 100,
        )

    async def fetch_index_components(
        self,
        exchange: str,
        symbol: str,
        market_sample: SecondLevelMarketSample | None = None,
    ) -> list[SecondLevelIndexComponentSample]:
        observed_at = self.base_at + timedelta(seconds=self.tick)
        component_price = 100 + self.tick
        sample = SecondLevelIndexComponentSample(
            observed_at=observed_at,
            target_exchange=exchange,
            symbol=symbol,
            component_source="binance",
            component_symbol=symbol,
            weight_pct=80,
            component_price=component_price,
            contribution_price=component_price * 0.8,
            official_index_price=market_sample.index_price if market_sample else None,
            reconstructed_index_price=component_price * 0.8,
            mark_price=market_sample.mark_price if market_sample else None,
            future_mid=market_sample.future_mid if market_sample else None,
            mark_premium_pct=market_sample.mark_premium_pct if market_sample else None,
        )
        self.tick += 1
        return [sample]

    async def aclose(self) -> None:
        return None


def test_second_level_sampling_config_normalizes_targets() -> None:
    config = SecondLevelSamplingConfig(
        enabled=False,
        exchanges=["Bybit", "bitget", "BYBIT"],
        symbols=["dexe", "DEXE/USDT", "btc-usdt"],
    )

    assert config.exchanges == ["bybit", "bitget"]
    assert config.symbols == ["DEXEUSDT", "BTCUSDT"]


@pytest.mark.asyncio
async def test_second_level_sampler_status_builds_latest_spreads() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = SecondLevelSamplingRepository(db)
    sampler = SecondLevelSampler(repo)
    sampler._config = SecondLevelSamplingConfig(
        enabled=False,
        exchanges=["bybit", "bitget"],
        symbols=["DEXEUSDT"],
    )
    observed_at = datetime(2026, 7, 23, 8, 0, tzinfo=UTC)
    await repo.insert_samples(
        [
            SecondLevelMarketSample(
                observed_at=observed_at,
                exchange="bybit",
                symbol="DEXEUSDT",
                status="ok",
                spot_mid=100.5,
                future_mid=101,
                mark_premium_pct=1.2,
            ),
            SecondLevelMarketSample(
                observed_at=observed_at,
                exchange="bitget",
                symbol="DEXEUSDT",
                status="ok",
                spot_mid=99.5,
                future_mid=100,
                mark_premium_pct=0.8,
            ),
        ]
    )

    try:
        status = await sampler.status()
    finally:
        await sampler.aclose()
        await db.close()

    assert status.sample_count == 2
    assert len(status.latest_spreads) == 1
    spread = status.latest_spreads[0]
    assert spread.left_exchange == "bitget"
    assert spread.right_exchange == "bybit"
    assert spread.left_spot_mid == pytest.approx(99.5)
    assert spread.right_spot_mid == pytest.approx(100.5)
    assert spread.spot_spread_pct == pytest.approx((99.5 / 100.5 - 1) * 100)
    assert spread.future_spread_pct == pytest.approx((100 / 101 - 1) * 100)
    assert spread.future_spot_spread_gap_pct == pytest.approx(
        ((100 / 101 - 1) * 100) - ((99.5 / 100.5 - 1) * 100)
    )
    assert spread.left_future_spot_basis_pct == pytest.approx((100 / 99.5 - 1) * 100)
    assert spread.right_future_spot_basis_pct == pytest.approx((101 / 100.5 - 1) * 100)
    assert spread.future_spot_basis_gap_pct == pytest.approx(
        ((100 / 99.5 - 1) * 100) - ((101 / 100.5 - 1) * 100)
    )
    assert spread.premium_gap_pct == pytest.approx(-0.4)


def test_second_level_sampling_api_saves_config() -> None:
    app = create_app(
        settings=Settings(
            dashboard_password="secret",
            database_url="sqlite:///:memory:",
        )
    )
    headers = {"X-Dashboard-Password": "secret"}

    with TestClient(app) as client:
        response = client.put(
            "/api/second-level-sampling/config",
            headers=headers,
            json={
                "enabled": False,
                "interval_seconds": 1,
                "retention_hours": 48,
                "exchanges": ["Bybit", "Bitget"],
                "symbols": ["dexe", "btc"],
                "max_concurrent_requests": 4,
            },
        )
        status_response = client.get("/api/second-level-sampling/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["exchanges"] == ["bybit", "bitget"]
    assert payload["symbols"] == ["DEXEUSDT", "BTCUSDT"]
    assert payload["max_concurrent_requests"] == 4
    assert status_response.status_code == 200
    assert status_response.json()["config"]["symbols"] == ["DEXEUSDT", "BTCUSDT"]


@pytest.mark.asyncio
async def test_second_level_sampler_keeps_future_samples_when_spot_is_missing() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = SecondLevelSamplingRepository(db)
    sampler = SecondLevelSampler(repo, fetcher=FutureOnlyFetcher())  # type: ignore[arg-type]
    config = SecondLevelSamplingConfig(
        enabled=True,
        exchanges=["bybit"],
        symbols=["DEXEUSDT"],
    )

    try:
        await sampler._collect_config(config)
        samples = await repo.list_samples(exchange="bybit", symbol="DEXEUSDT")
    finally:
        await sampler.aclose()
        await db.close()

    assert len(samples) == 1
    assert samples[0].status == "partial"
    assert samples[0].future_mid == pytest.approx(10.1)
    assert samples[0].error is not None
    assert "现货行情不可用" in samples[0].error


@pytest.mark.asyncio
async def test_second_level_sampler_records_index_component_lead_signals() -> None:
    db = await connect_database(":memory:")
    await initialize_schema(db)
    repo = SecondLevelSamplingRepository(db)
    sampler = SecondLevelSampler(repo, fetcher=ComponentMoveFetcher())  # type: ignore[arg-type]
    config = SecondLevelSamplingConfig(
        enabled=True,
        exchanges=["bybit"],
        symbols=["DEXEUSDT"],
        capture_index_components=True,
        component_signal_window_seconds=10,
    )

    try:
        await sampler._collect_config(config)
        await sampler._collect_config(config)
        component_samples = await repo.list_component_samples(
            target_exchange="bybit",
            symbol="DEXEUSDT",
        )
        status = await sampler.status()
    finally:
        await sampler.aclose()
        await db.close()

    assert len(component_samples) == 2
    assert status.component_sample_count == 2
    assert len(status.latest_component_samples) == 1
    assert status.latest_component_samples[0].component_source == "binance"
    assert len(status.latest_component_signals) == 1
    signal = status.latest_component_signals[0]
    assert signal.signal_level == "high"
    assert signal.component_price_change_pct == pytest.approx(1)
    assert signal.estimated_index_impact_pct == pytest.approx(0.8 / 100.8 * 100)
