from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.services.feishu import FeishuConfig
from app.services.minute_signal_scan import (
    ALPHA_KLINES_URL,
    MinuteSignalAlertEngine,
    MinuteSignalScanService,
    build_minute_signal_alert_message,
)


def _row(
    at: datetime,
    basis_bps: float,
    premium_bps: float = 0.0,
    premium_low_bps: float | None = None,
):
    futures = 100.0
    spot = futures * (1.0 + basis_bps / 10_000)
    low = premium_low_bps if premium_low_bps is not None else premium_bps
    return {
        "open_time": int(at.timestamp() * 1000),
        "time_cst": at.astimezone().isoformat(timespec="minutes"),
        "spot_close": spot,
        "fut_close": futures,
        "spot_open": spot,
        "fut_open": futures,
        "premium_low": low / 10_000,
        "premium_high": premium_bps / 10_000,
        "premium_close": premium_bps / 10_000,
    }


def test_scan_emits_shock_entry_and_take_profit() -> None:
    start = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
    rows = []
    for minute in range(27):
        at = start + timedelta(minutes=minute)
        basis = 50.0
        premium = 0.0
        low = 0.0
        if minute == 20:
            basis, premium, low = 150.0, -30.0, -80.0
        elif minute == 25:
            basis, premium, low = 30.0, -10.0, -80.0
        elif minute == 26:
            basis, premium, low = 400.0, 0.0, -80.0
        rows.append(_row(at, basis, premium, low))

    service = MinuteSignalScanService()
    events = service.scan(rows)

    assert [event["event_type"] for event in events] == [
        "SHOCK_ALERT",
        "ENTRY",
        "TAKE_PROFIT",
    ]
    assert events[0]["planned_execution_time_cst"] > events[0]["signal_time_cst"]
    assert events[1]["signal_entry_basis_bps"] == pytest.approx(29.91, abs=0.01)
    assert events[2]["signal_basis_gain_bps"] == pytest.approx(354.71, abs=0.01)


def test_scan_empty_rows_returns_json_safe_empty_result() -> None:
    service = MinuteSignalScanService()

    assert service.scan([]) == []


@pytest.mark.asyncio
async def test_fetch_rows_excludes_any_unclosed_source_bar(monkeypatch) -> None:
    service = MinuteSignalScanService()
    start_ms = 1_000_000
    end_ms = start_ms + 60_000

    async def fake_fetch_klines(url: str, symbol: str, start: int, end: int):
        close_time = end_ms if "premiumIndex" not in url else end_ms + 1
        return [
            {
                "open_time": start_ms,
                "close_time": close_time,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
            }
        ]

    monkeypatch.setattr(service, "_fetch_klines", fake_fetch_klines)

    assert await service._fetch_rows("ALPHA_331USDT", "AKEUSDT", start_ms, end_ms) == []


@pytest.mark.asyncio
async def test_alpha_kline_pagination_stops_when_start_exceeds_latest_alpha_end(monkeypatch) -> None:
    service = MinuteSignalScanService()
    start_ms = 1_000_000
    end_ms = start_ms + 3 * 60_000
    calls: list[dict] = []

    async def fake_get(url: str, params: dict):
        calls.append(params)
        if len(calls) == 1:
            return [
                [
                    start_ms,
                    "1.20",
                    "1.30",
                    "1.10",
                    "1.25",
                    "1000",
                    start_ms + 60_000 - 1,
                ]
            ]
        raise RuntimeError("Binance Alpha 接口失败: Start time is greater than end time (code=-1023)")

    monkeypatch.setattr(service, "_get", fake_get)

    rows = await service._fetch_klines(ALPHA_KLINES_URL, "ALPHA_331USDT", start_ms, end_ms)

    assert len(calls) == 2
    assert rows == [
        {
            "open_time": start_ms,
            "close_time": start_ms + 60_000 - 1,
            "open": 1.20,
            "high": 1.30,
            "low": 1.10,
            "close": 1.25,
        }
    ]


@pytest.mark.asyncio
async def test_global_universe_maps_alpha_token_to_futures_contract(monkeypatch) -> None:
    service = MinuteSignalScanService()
    payloads = {
        "fapi/v1/exchangeInfo": {
            "symbols": [
                {
                    "symbol": "AKEUSDT",
                    "baseAsset": "AKE",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                }
            ]
        },
        "fapi/v1/ticker/24hr": [
            {"symbol": "AKEUSDT", "lastPrice": "1.10", "quoteVolume": "1000000"}
        ],
        "fapi/v1/premiumIndex": [
            {"symbol": "AKEUSDT", "markPrice": "1.10", "indexPrice": "1.00"}
        ],
        "get-exchange-info": {
            "symbols": [
                {
                    "symbol": "ALPHA_331USDT",
                    "baseAsset": "ALPHA_331",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                }
            ]
        },
        "wallet-direct": [{"alphaId": "331", "symbol": "AKE", "price": "1.30"}],
    }

    async def fake_get_payload(url: str, params=None):
        if "alpha-trade/ticker" in url:
            assert params == {"symbol": "ALPHA_331USDT"}
            return {"symbol": "ALPHA_331USDT", "lastPrice": "1.25"}
        for marker, payload in payloads.items():
            if marker in url:
                return payload
        raise AssertionError(url)

    monkeypatch.setattr(service, "_get_payload", fake_get_payload)

    universe = await service._fetch_global_universe()

    assert len(universe) == 1
    assert universe[0]["futures_symbol"] == "AKEUSDT"
    assert universe[0]["alpha_symbol"] == "ALPHA_331USDT"
    assert universe[0]["base_asset"] == "AKE"
    assert universe[0]["alpha_price"] == pytest.approx(1.25)
    assert universe[0]["alpha_price_source"] == "binance_alpha_ticker_last_price"
    assert universe[0]["initial_basis_bps"] == pytest.approx((1.25 - 1.10) / 1.25 * 10_000)


@pytest.mark.asyncio
async def test_global_universe_falls_back_to_token_price_when_ticker_missing(monkeypatch) -> None:
    service = MinuteSignalScanService()
    payloads = {
        "fapi/v1/exchangeInfo": {
            "symbols": [
                {
                    "symbol": "AKEUSDT",
                    "baseAsset": "AKE",
                    "quoteAsset": "USDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                }
            ]
        },
        "fapi/v1/ticker/24hr": [
            {"symbol": "AKEUSDT", "lastPrice": "1.10", "quoteVolume": "1000000"}
        ],
        "fapi/v1/premiumIndex": [
            {"symbol": "AKEUSDT", "markPrice": "1.10", "indexPrice": "1.00"}
        ],
        "get-exchange-info": {
            "symbols": [
                {
                    "symbol": "ALPHA_331USDT",
                    "baseAsset": "ALPHA_331",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                }
            ]
        },
        "wallet-direct": [{"alphaId": "331", "symbol": "AKE", "price": "1.30"}],
    }

    async def fake_get_payload(url: str, params=None):
        if "alpha-trade/ticker" in url:
            raise RuntimeError("temporary alpha ticker failure")
        for marker, payload in payloads.items():
            if marker in url:
                return payload
        raise AssertionError(url)

    monkeypatch.setattr(service, "_get_payload", fake_get_payload)

    universe = await service._fetch_global_universe()

    assert len(universe) == 1
    assert universe[0]["alpha_price"] == pytest.approx(1.30)
    assert universe[0]["alpha_price_source"] == "binance_alpha_token_list_price"
    assert universe[0]["initial_basis_bps"] == pytest.approx((1.30 - 1.10) / 1.30 * 10_000)


@pytest.mark.asyncio
async def test_scan_all_returns_signal_candidates(monkeypatch) -> None:
    service = MinuteSignalScanService()
    monkeypatch.setattr(
        service,
        "_fetch_global_universe",
        lambda: _async_value(
            [
                {
                    "base_asset": "AKE",
                    "alpha_id": "ALPHA_331",
                    "alpha_symbol": "ALPHA_331USDT",
                    "futures_symbol": "AKEUSDT",
                    "alpha_price": 1.3,
                    "futures_price": 1.1,
                    "index_price": 1.0,
                    "volume_24h_usdt": 1_000_000,
                    "initial_basis_bps": 30.0,
                    "initial_premium_bps": -10.0,
                }
            ]
        ),
    )

    async def fake_scan_symbol(*, alpha_symbol: str, futures_symbol: str, hours: int):
        return {
            "bar_count": 60,
            "latest": {
                "basis_bps": 150.0,
                "premium_bps": -50.0,
                "basis_peak_60m_bps": 180.0,
                "compression_ratio": 0.7,
            },
            "events": [
                {
                    "event_type": "SHOCK_ALERT",
                    "signal_time_cst": "2026-07-25T10:00+08:00",
                    "planned_execution_time_cst": "2026-07-25T10:01+08:00",
                    "reason": "basis_expansion_with_negative_premium",
                }
            ],
        }

    monkeypatch.setattr(service, "scan_symbol", fake_scan_symbol)

    result = await service.scan_all(hours=2, max_symbols=5, min_volume_24h_usdt=0)

    assert result["universe_count"] == 1
    assert result["scanned_count"] == 1
    assert result["signal_count"] == 1
    assert result["candidates"][0]["event_type"] == "SHOCK_ALERT"


@pytest.mark.asyncio
async def test_scan_all_filters_spot_above_future_when_premium_is_not_negative(monkeypatch) -> None:
    service = MinuteSignalScanService()
    monkeypatch.setattr(
        service,
        "_fetch_global_universe",
        lambda: _async_value(
            [
                {
                    "base_asset": "ESPORTS",
                    "alpha_id": "ALPHA_999",
                    "alpha_symbol": "ALPHA_999USDT",
                    "futures_symbol": "ESPORTSUSDT",
                    "alpha_price": 1.05,
                    "futures_price": 1.0,
                    "index_price": 1.0,
                    "volume_24h_usdt": 1_000_000,
                    "initial_basis_bps": 476.19,
                    "initial_premium_bps": 0.0,
                }
            ]
        ),
    )

    async def fail_if_scanned(**kwargs):
        raise AssertionError(f"候选不应进入 1m 复核: {kwargs}")

    monkeypatch.setattr(service, "scan_symbol", fail_if_scanned)

    result = await service.scan_all(hours=2, max_symbols=5, min_volume_24h_usdt=0)

    assert result["eligible_count"] == 0
    assert result["scanned_count"] == 0
    assert result["filtered_by_basis_count"] == 1
    assert result["filtered_by_premium_count"] == 0


@pytest.mark.asyncio
async def test_scan_all_requires_negative_premium_when_spot_is_slightly_above(monkeypatch) -> None:
    service = MinuteSignalScanService()
    monkeypatch.setattr(
        service,
        "_fetch_global_universe",
        lambda: _async_value(
            [
                {
                    "base_asset": "AKE",
                    "alpha_id": "ALPHA_331",
                    "alpha_symbol": "ALPHA_331USDT",
                    "futures_symbol": "AKEUSDT",
                    "alpha_price": 1.004,
                    "futures_price": 1.0,
                    "index_price": 1.0,
                    "volume_24h_usdt": 1_000_000,
                    "initial_basis_bps": 39.84,
                    "initial_premium_bps": 0.0,
                }
            ]
        ),
    )

    result = await service.scan_all(hours=2, max_symbols=5, min_volume_24h_usdt=0)

    assert result["eligible_count"] == 0
    assert result["filtered_by_basis_count"] == 0
    assert result["filtered_by_premium_count"] == 1


@pytest.mark.asyncio
async def test_scan_all_allows_spot_below_future_without_negative_premium(monkeypatch) -> None:
    service = MinuteSignalScanService()
    monkeypatch.setattr(
        service,
        "_fetch_global_universe",
        lambda: _async_value(
            [
                {
                    "base_asset": "AKE",
                    "alpha_id": "ALPHA_331",
                    "alpha_symbol": "ALPHA_331USDT",
                    "futures_symbol": "AKEUSDT",
                    "alpha_price": 0.99,
                    "futures_price": 1.0,
                    "index_price": 1.0,
                    "volume_24h_usdt": 1_000_000,
                    "initial_basis_bps": -101.01,
                    "initial_premium_bps": 0.0,
                }
            ]
        ),
    )

    async def fake_scan_symbol(*, alpha_symbol: str, futures_symbol: str, hours: int):
        return {
            "bar_count": 60,
            "latest": {
                "basis_bps": -20.0,
                "premium_bps": 0.0,
                "basis_peak_60m_bps": 30.0,
                "compression_ratio": 0.7,
            },
            "events": [],
        }

    monkeypatch.setattr(service, "scan_symbol", fake_scan_symbol)

    result = await service.scan_all(hours=2, max_symbols=5, min_volume_24h_usdt=0)

    assert result["eligible_count"] == 1
    assert result["scanned_count"] == 1
    assert result["filtered_by_basis_count"] == 0
    assert result["filtered_by_premium_count"] == 0


def _signal_candidate(
    *,
    event_type: str | None = "ENTRY",
    signal_time_cst: str | None = "2026-07-25T10:00+08:00",
):
    return {
        "base_asset": "AKE",
        "alpha_id": "ALPHA_331",
        "alpha_symbol": "ALPHA_331USDT",
        "futures_symbol": "AKEUSDT",
        "alpha_price": 1.3,
        "futures_price": 1.1,
        "index_price": 1.0,
        "volume_24h_usdt": 1_000_000,
        "initial_basis_bps": 1538.46,
        "initial_premium_bps": -50.0,
        "score": 50_000,
        "event_type": event_type,
        "signal_time_cst": signal_time_cst,
        "planned_execution_time_cst": "2026-07-25T10:01+08:00",
        "reason": "shock_compressed_and_entry_confirmed",
        "basis_bps": 30.0,
        "premium_bps": -10.0,
        "basis_peak_60m_bps": 180.0,
        "compression_ratio": 0.7,
        "bar_count": 60,
        "recent_events": [],
        "error": None,
    }


def _scan_all_result(candidates: list[dict]) -> dict:
    return {
        "observed_at": "2026-07-25T02:00:30+00:00",
        "hours": 4,
        "max_symbols": 30,
        "min_volume_24h_usdt": 100_000,
        "universe_count": 123,
        "eligible_count": 10,
        "scanned_count": len(candidates),
        "signal_count": sum(1 for item in candidates if item.get("event_type") is not None),
        "error_count": sum(1 for item in candidates if item.get("error")),
        "candidates": candidates,
        "warnings": [],
    }


def test_minute_signal_alert_engine_applies_pair_cooldown() -> None:
    engine = MinuteSignalAlertEngine()
    now = datetime(2026, 7, 25, 2, 0, tzinfo=UTC)
    first = _scan_all_result([_signal_candidate()])
    second = _scan_all_result([_signal_candidate()])
    next_wave = _scan_all_result([_signal_candidate(signal_time_cst="2026-07-25T10:15+08:00")])
    after_cooldown = _scan_all_result(
        [_signal_candidate(signal_time_cst="2026-07-25T11:01+08:00")]
    )

    assert len(engine.evaluate(first, alert_cooldown_seconds=3600, now=now)) == 1
    assert engine.evaluate(
        second,
        alert_cooldown_seconds=3600,
        now=now + timedelta(minutes=1),
    ) == []
    assert engine.evaluate(
        next_wave,
        alert_cooldown_seconds=3600,
        now=now + timedelta(minutes=15),
    ) == []
    assert len(
        engine.evaluate(
            after_cooldown,
            alert_cooldown_seconds=3600,
            now=now + timedelta(minutes=61),
        )
    ) == 1


def test_minute_signal_alert_message_uses_distinct_title_and_candidate_details() -> None:
    result = _scan_all_result([_signal_candidate()])

    message = build_minute_signal_alert_message(result["candidates"], result)

    assert "【1分钟价差信号】发现目标" in message
    assert "[机会雷达]" not in message
    assert "AKEUSDT / ALPHA_331USDT" in message
    assert "候选入场" in message
    assert "买 Binance Alpha 现货 / 空 Binance Futures 永续" in message
    assert "basis +30.00 bps" in message


class FakeMinuteSignalNotifier:
    def __init__(self) -> None:
        self.config = FeishuConfig(webhook_url="https://example.test/hook")
        self.messages: list[str] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(text)


def test_minute_signal_scan_all_route_sends_feishu_alert_for_targets() -> None:
    class FakeService:
        closed = False

        async def scan_all(self, *, hours: int, max_symbols: int, min_volume_24h_usdt: float, **kwargs):
            return _scan_all_result([_signal_candidate()])

        async def aclose(self) -> None:
            self.closed = True

    service = FakeService()
    notifier = FakeMinuteSignalNotifier()
    app = create_app()
    app.state.minute_signal_scan_service_factory = lambda: service
    app.state.feishu_notifier = notifier

    with TestClient(app) as client:
        first = client.get(
            "/api/minute-signals/scan-all?hours=4&max_symbols=5&alert_cooldown_minutes=30"
        )
        second = client.get(
            "/api/minute-signals/scan-all?hours=4&max_symbols=5&alert_cooldown_minutes=30"
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["alert_cooldown_minutes"] == 30
    assert len(notifier.messages) == 1
    assert "【1分钟价差信号】发现目标" in notifier.messages[0]
    assert "[机会雷达]" not in notifier.messages[0]
    assert service.closed is True


def test_minute_signal_scan_all_route_skips_feishu_alert_without_targets() -> None:
    class FakeService:
        async def scan_all(self, *, hours: int, max_symbols: int, min_volume_24h_usdt: float, **kwargs):
            return _scan_all_result([_signal_candidate(event_type=None)])

        async def aclose(self) -> None:
            return None

    notifier = FakeMinuteSignalNotifier()
    app = create_app()
    app.state.minute_signal_scan_service_factory = lambda: FakeService()
    app.state.feishu_notifier = notifier

    with TestClient(app) as client:
        response = client.get("/api/minute-signals/scan-all?hours=4&max_symbols=5")

    assert response.status_code == 200
    assert notifier.messages == []


async def _async_value(value):
    return value


def test_minute_signal_route_uses_factory_and_normalizes_symbols() -> None:
    class FakeService:
        closed = False

        async def scan_symbol(self, *, alpha_symbol: str, futures_symbol: str, hours: int):
            return {
                "alpha_symbol": alpha_symbol,
                "futures_symbol": futures_symbol,
                "hours": hours,
                "observed_at": "2026-07-25T00:00:00+00:00",
                "bar_count": 0,
                "latest": None,
                "points": [],
                "events": [],
                "warnings": [],
            }

        async def aclose(self) -> None:
            self.closed = True

    service = FakeService()
    app = create_app()
    app.state.minute_signal_scan_service_factory = lambda: service

    with TestClient(app) as client:
        response = client.get(
            "/api/minute-signals/scan?symbol=akeusdt&alpha_symbol=alpha_331usdt&hours=2"
        )

    assert response.status_code == 200
    assert response.json()["futures_symbol"] == "AKEUSDT"
    assert response.json()["alpha_symbol"] == "ALPHA_331USDT"
    assert response.json()["hours"] == 2
    assert service.closed is True


def test_minute_signal_scan_all_route_uses_factory() -> None:
    class FakeService:
        closed = False

        async def scan_all(self, *, hours: int, max_symbols: int, min_volume_24h_usdt: float, **kwargs):
            return {
                "observed_at": "2026-07-25T00:00:00+00:00",
                "hours": hours,
                "max_symbols": max_symbols,
                "min_volume_24h_usdt": min_volume_24h_usdt,
                "universe_count": 123,
                "eligible_count": 10,
                "scanned_count": 5,
                "signal_count": 2,
                "error_count": 0,
                "candidates": [],
                "warnings": [],
            }

        async def aclose(self) -> None:
            self.closed = True

    service = FakeService()
    app = create_app()
    app.state.minute_signal_scan_service_factory = lambda: service

    with TestClient(app) as client:
        response = client.get(
            "/api/minute-signals/scan-all"
            "?hours=2&max_symbols=5&min_volume_24h_usdt=250000"
        )

    assert response.status_code == 200
    assert response.json()["universe_count"] == 123
    assert response.json()["signal_count"] == 2
    assert service.closed is True
