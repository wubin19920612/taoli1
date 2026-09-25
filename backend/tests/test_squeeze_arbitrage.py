from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.services.squeeze_arbitrage.features import calculate_watch_features
from app.services.squeeze_arbitrage.liquidation import parse_force_order
from app.services.squeeze_arbitrage.models import HOUR, HourCandle, PositionSample, market_key
from app.services.squeeze_arbitrage.provider import BinanceSqueezeProvider
from app.services.squeeze_arbitrage.repository import SqueezeRepository
from app.services.squeeze_arbitrage.runner import SqueezeMonitor, SqueezeMonitorConfig

BUCKET = datetime(2026, 9, 24, 12, tzinfo=UTC)
KEY = market_key("LSKUSDT")


def fixture_data(
    *, raw_growth: bool = True
) -> tuple[list[HourCandle], list[PositionSample]]:
    candles = [
        HourCandle(
            KEY, BUCKET - (171 - index) * HOUR,
            120 if index >= 168 else 100,
            1000 if index >= 168 else 100,
            BUCKET + timedelta(minutes=2), BUCKET + timedelta(minutes=2),
        )
        for index in range(172)
    ]
    positioning = [
        PositionSample(
            KEY, BUCKET - (24 - index) * HOUR,
            130 if raw_growth and index == 23 else (110 if raw_growth and index == 24 else 100),
            "binance_usdm_contract", 2000 if index == 24 else 1000,
            0.8, BUCKET + timedelta(minutes=2), BUCKET + timedelta(minutes=2),
            account_ratio_event_time=BUCKET - (24 - index) * HOUR,
        )
        for index in range(25)
    ]
    return candles, positioning


def test_raw_oi_not_usd_value_drives_observation() -> None:
    candles, positioning = fixture_data(raw_growth=False)
    features = calculate_watch_features(
        KEY, candles, positioning, bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    )
    assert features.status == "ready"
    assert features.return_4h == pytest.approx(0.20)
    assert features.volume_ratio == pytest.approx(10)
    assert features.oi_current_growth == 0
    assert features.oi_peak_growth == 0
    assert not features.qualifies


def test_mixed_market_positioning_never_changes_exact_market_features() -> None:
    candles, positioning = fixture_data(raw_growth=False)
    other = [
        replace(row, market_key=market_key("GIANTSUSDT"), raw_open_interest=10000,
                account_ratio=0.1)
        for row in positioning
    ]
    features = calculate_watch_features(
        KEY, candles, other + positioning, bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    )
    assert features.status == "ready"
    assert features.oi_peak_growth == 0
    assert features.account_ratio == 0.8
    assert not features.qualifies


def test_closed_hour_signal_and_stage_only_use_available_complete_inputs() -> None:
    candles, positioning = fixture_data()
    features = calculate_watch_features(
        KEY, candles, positioning, bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    )
    assert features.qualifies
    assert features.stage == "squeeze_pending"
    assert features.oi_drawdown == pytest.approx(110 / 130 - 1)

    missing_candle = calculate_watch_features(
        KEY, candles[:20] + candles[21:], positioning,
        bucket_at=BUCKET, decision_at=BUCKET + timedelta(minutes=3),
    )
    assert missing_candle.status == "insufficient_data"
    assert "hourly_candle_gap_or_unavailable" in missing_candle.reasons

    late = [*candles[:-1], replace(candles[-1], available_at=BUCKET + HOUR)]
    assert not calculate_watch_features(
        KEY, late, positioning, bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    ).qualifies


def test_stale_or_missing_positioning_does_not_trigger() -> None:
    candles, positioning = fixture_data()
    stale = calculate_watch_features(
        KEY, candles, positioning[:-2], bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    )
    assert stale.status == "stale_data"
    assert not stale.qualifies

    gap = calculate_watch_features(
        KEY, candles, positioning[:5] + positioning[6:], bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    )
    assert gap.status == "insufficient_data"
    assert "open_interest_window_gap" in gap.reasons

    no_ratio = [replace(row, account_ratio=None) for row in positioning]
    assert not calculate_watch_features(
        KEY, candles, no_ratio, bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    ).qualifies

    stale_ratio = [
        *positioning[:-2],
        replace(positioning[-2], account_ratio_event_time=BUCKET - 2 * HOUR),
        replace(positioning[-1], account_ratio_event_time=BUCKET - 2 * HOUR),
    ]
    assert calculate_watch_features(
        KEY, candles, stale_ratio, bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    ).status == "stale_data"


def test_last_oi_sample_may_be_one_sampling_period_old() -> None:
    candles, positioning = fixture_data()
    features = calculate_watch_features(
        KEY, candles, positioning[:-1], bucket_at=BUCKET,
        decision_at=BUCKET + timedelta(minutes=3),
    )
    assert features.status == "ready"
    assert features.oi_age_seconds == 3600


@pytest.mark.asyncio
async def test_collector_replay_records_one_observation_event() -> None:
    candles, positioning = fixture_data()

    class FakeProvider:
        async def verified_symbols(self, symbols):
            assert symbols == ("LSKUSDT",)
            return {"LSKUSDT": {"symbol": "LSKUSDT"}}

        async def fetch_candles(self, symbol, bucket_at):
            assert symbol == "LSKUSDT" and bucket_at == BUCKET
            return candles

        async def fetch_positioning(self, symbol, bucket_at):
            assert symbol == "LSKUSDT" and bucket_at == BUCKET
            return positioning

        async def aclose(self):
            pass

    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRepository(db)
        monitor = SqueezeMonitor(
            repo, SqueezeMonitorConfig(symbols=("LSKUSDT",)), FakeProvider()
        )
        now = BUCKET + timedelta(minutes=3)
        await monitor.scan_once(now)
        await monitor.scan_once(now)
        status = await repo.status(enabled=True, now=now)
        assert status["last_bucket_at"] == BUCKET.isoformat()
        assert status["active_watch_count"] == 1
        assert status["latest_market_scans"][0]["candle_count"] == 172
        cursor = await db.execute(
            "SELECT verification_status FROM squeeze_market_identity WHERE market_key=?", (KEY,)
        )
        assert (await cursor.fetchone())["verification_status"] == "verified_for_structure"
        assert len(await repo.list_events(now=now)) == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_liquidation_disconnect_gap_is_recorded_and_closed() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRepository(db)
        await repo.set_coverage("disconnected", BUCKET, error="network")
        await repo.set_coverage("disconnected", BUCKET + timedelta(seconds=1), error="network")
        status = await repo.status(enabled=True, now=BUCKET)
        assert status["liquidation_coverage"]["open_gaps"] == 1
        await repo.set_coverage("throttled_public_stream", BUCKET + timedelta(seconds=2))
        status = await repo.status(enabled=True, now=BUCKET)
        assert status["liquidation_coverage"]["open_gaps"] == 0
        assert status["liquidation_coverage"]["public_stream_complete"] is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_watch_event_survives_repository_restart_without_duplication() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRepository(db)
        candles, positioning = fixture_data()
        await repo.save_market_data(candles, positioning)
        saved_candles, saved_positioning = await repo.load_market_data(KEY, BUCKET)
        features = calculate_watch_features(
            KEY, saved_candles, saved_positioning, bucket_at=BUCKET,
            decision_at=BUCKET + timedelta(minutes=3),
        )
        assert await repo.save_features(features)
        restarted = SqueezeRepository(db)
        assert await restarted.save_features(features)
        events = await restarted.list_events(now=BUCKET + timedelta(minutes=4))
        assert len(events) == 1
        assert events[0]["stage"] == "squeeze_pending"
        assert events[0]["cooldown_until"] > events[0]["created_at"]
        assert len(await restarted.list_events(now=BUCKET + timedelta(days=4), active_only=True)) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_liquidation_duplicate_cumulative_update_only_counts_delta() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SqueezeRepository(db)
        payload = {
            "e": "forceOrder", "o": {
                "s": "LSKUSDT", "S": "BUY", "T": int(BUCKET.timestamp() * 1000),
                "q": "3", "p": "100", "z": "1", "ap": "101",
            },
        }
        first = parse_force_order(payload, BUCKET)
        assert first is not None
        assert await repo.record_liquidation(first) == 101
        assert await repo.record_liquidation(first) == 0
        payload["o"]["z"] = "2"
        payload["o"]["ap"] = "102"
        second = parse_force_order(payload, BUCKET + timedelta(seconds=1))
        assert second is not None
        assert await repo.record_liquidation(second) == 102
        status = await repo.status(enabled=True, now=BUCKET + timedelta(minutes=1))
        assert status["liquidation_observed_last_hour"]["BUY"]["observed_notional_usdt"] == 203
        assert status["liquidation_coverage"]["public_stream_complete"] is False
    finally:
        await db.close()


def test_api_only_never_starts_squeeze_worker_even_when_enabled(monkeypatch) -> None:
    start_task = Mock()
    monkeypatch.setattr("app.main._start_background_task", start_task)
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:", squeeze_monitor_enabled=True),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        status = client.get("/api/squeeze-arbitrage/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is False
        assert client.get("/api/squeeze-arbitrage/watchlist").json() == []
        assert client.get("/api/minute-signals/scan").status_code == 404
    start_task.assert_not_called()


def test_squeeze_config_accepts_single_character_raw_base_and_rejects_aliases() -> None:
    config = SqueezeMonitorConfig(symbols=("GUSDT", "LSKUSDT"))
    assert config.symbols == ("GUSDT", "LSKUSDT")
    with pytest.raises(ValueError):
        SqueezeMonitorConfig(symbols=("G/USDT",))
    with pytest.raises(ValueError):
        SqueezeMonitorConfig(symbols=("LSKUSDT", "LSKUSDT"))


@pytest.mark.asyncio
async def test_binance_public_provider_keeps_raw_oi_and_ratio_source_time() -> None:
    requested: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json={"symbols": [
                {"symbol": "LSKUSDT", "baseAsset": "LSK", "quoteAsset": "USDT",
                 "status": "TRADING", "contractType": "PERPETUAL"},
                {"symbol": "GUSDT", "status": "BREAK", "contractType": "PERPETUAL",
                 "quoteAsset": "USDT"},
            ]})
        if request.url.path.endswith("klines"):
            opened = int((BUCKET - HOUR).timestamp() * 1000)
            return httpx.Response(200, json=[
                [opened, "100", "130", "90", "120", "10", opened + 3599999, "1000"]
            ])
        if request.url.path.endswith("openInterestHist"):
            return httpx.Response(200, json=[{
                "timestamp": int(BUCKET.timestamp() * 1000),
                "sumOpenInterest": "100", "sumOpenInterestValue": "2000",
            }])
        if request.url.path.endswith("globalLongShortAccountRatio"):
            return httpx.Response(200, json=[{
                "timestamp": int((BUCKET + timedelta(minutes=10)).timestamp() * 1000),
                "longShortRatio": "0.8",
            }])
        raise AssertionError(request.url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = BinanceSqueezeProvider(client)
        assert list(await provider.verified_symbols(("LSKUSDT", "GUSDT"))) == ["LSKUSDT"]
        candles = await provider.fetch_candles("LSKUSDT", BUCKET)
        positioning = await provider.fetch_positioning("LSKUSDT", BUCKET)
    assert candles[0].event_time == BUCKET
    assert candles[0].quote_volume == 1000
    assert positioning[0].raw_open_interest == 100
    assert positioning[0].open_interest_usdt == 2000
    assert positioning[0].account_ratio == 0.8
    assert positioning[0].account_ratio_event_time == BUCKET + timedelta(minutes=10)
    assert len(requested) == 4
