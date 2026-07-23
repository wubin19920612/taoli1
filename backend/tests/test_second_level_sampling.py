from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.second_level_sampling import SecondLevelMarketSample, SecondLevelSamplingConfig
from app.services.second_level_sampler import SecondLevelSampler, SecondLevelSamplingRepository


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
                future_mid=101,
                mark_premium_pct=1.2,
            ),
            SecondLevelMarketSample(
                observed_at=observed_at,
                exchange="bitget",
                symbol="DEXEUSDT",
                status="ok",
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
    assert spread.future_spread_pct == pytest.approx((100 / 101 - 1) * 100)
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
