from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.services.minute_signal_scan import MinuteSignalScanService


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
