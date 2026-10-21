import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.main import create_app
from app.models.account_connection import AccountConnectionSecretPayload
from app.models.fs_monitor import FsCandidate, FsReport, FsSettings
from app.models.market import MarketSnapshot, MarketType
from app.services.fs_bybit import BybitFsProvider, FsDataError
from app.services.fs_economics import common_step, evaluate, settlement_count
from app.services.fs_monitor import FsMonitor, identity_check
from app.services.fs_repository import FsRepository

NOW = datetime(2026, 10, 21, 1, 30, tzinfo=UTC)


def inputs():
    row = FsCandidate(
        symbol="ABCUSDT",
        base="ABC",
        observed_at=NOW,
        funding_pct=-1,
        interval_hours=4,
        next_funding_at=NOW + timedelta(hours=2.5),
        hourly_borrow_pct=0.001,
        product_borrowable=True,
        account_state="checked",
        account_max_loan=200,
        available_inventory=200,
        fee_source="account",
        spot_fee_pct=0.1,
        future_fee_pct=0.055,
        identity_state="verified",
        spot_volume_usdt=1e6,
        future_volume_usdt=2e6,
    )
    spot = {
        "symbol": "ABCUSDT",
        "baseCoin": "ABC",
        "quoteCoin": "USDT",
        "status": "Trading",
        "marginTrading": "utaOnly",
        "lotSizeFilter": {"basePrecision": "0.01", "minOrderQty": "0.01", "minOrderAmt": "5"},
    }
    future = {
        "symbol": "ABCUSDT",
        "baseCoin": "ABC",
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "status": "Trading",
        "contractType": "LinearPerpetual",
        "fundingInterval": 240,
        "lotSizeFilter": {
            "qtyStep": "0.1",
            "minOrderQty": "0.1",
            "minNotionalValue": "5",
            "maxMktOrderQty": "10000",
        },
    }
    sb = {
        "s": "ABCUSDT",
        "ts": int(NOW.timestamp() * 1000),
        "b": [["10", "1000"]],
        "a": [["10.01", "1000"]],
    }
    fb = {
        "s": "ABCUSDT",
        "ts": int(NOW.timestamp() * 1000),
        "b": [["9.99", "1000"]],
        "a": [["10", "1000"]],
    }
    return row, {
        "spot_meta": spot,
        "future_meta": future,
        "spot_book": sb,
        "future_book": fb,
        "mark": 10,
        "now": NOW,
    }


def test_funding_events_are_discrete_not_fractional():
    next_at = NOW + timedelta(hours=2)
    assert settlement_count(NOW, next_at, 4, 1) == 0
    assert settlement_count(NOW, next_at, 4, 2) == 0
    assert settlement_count(NOW, next_at, 4, 2.01) == 1
    assert settlement_count(NOW, next_at, 4, 8) == 2
    assert settlement_count(NOW, next_at, 4, 24) == 6
    with pytest.raises(FsDataError):
        settlement_count(NOW, NOW, 4, 8)


def test_same_quantity_four_prices_and_costs():
    row, args = inputs()
    result = evaluate(row, FsSettings(basis_stress_pct=0.1), **args)
    assert result.stage == "cost_pass"
    assert result.quantity == 100
    assert result.borrow_required > 100  # first-interest deduction reserve
    holding = result.scenarios[1]
    assert holding.settlements == 2
    assert holding.funding_usdt == 20
    assert holding.round_trip_price_pnl_usdt == pytest.approx(-2)
    assert holding.fees_usdt == pytest.approx(3.10045)
    assert holding.net_usdt == pytest.approx(20 - holding.borrow_usdt - 3.10045 - 2 - 0.5)
    assert holding.stressed_net_usdt == pytest.approx(holding.net_usdt - 1)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"available_inventory": 0}, "insufficient_borrow_capacity"),
        ({"available_inventory": None}, "account_capacity_unknown"),
        ({"account_state": "permission_denied"}, "account_permission_denied"),
        ({"fee_source": "configured_assumption"}, "fees_unverified"),
        ({"identity_state": "unverified"}, "identity_unverified"),
        ({"hourly_borrow_pct": None}, "borrow_rate_unknown"),
        ({"product_borrowable": False}, "borrow_not_supported"),
        ({"funding_pct": 0.1}, "negative_funding_missing"),
        ({"next_funding_at": NOW}, "funding_schedule_unknown"),
        ({"spot_volume_usdt": None}, "low_or_unknown_volume"),
    ],
)
def test_missing_or_bad_evidence_never_passes(change, reason):
    row, args = inputs()
    result = evaluate(row.model_copy(update=change), FsSettings(), **args)
    assert result.stage != "cost_pass"
    assert reason in result.blockers


@pytest.mark.parametrize(
    "case,reason",
    [
        ("stale", "stale_orderbook"),
        ("future", "stale_orderbook"),
        ("thin", "insufficient_depth"),
        ("inverted", "invalid_orderbook"),
        ("quote", "unsupported_contract"),
        ("step", "quantity_step_unknown"),
        ("different_base", "unsupported_contract"),
    ],
)
def test_bad_book_or_metadata(case, reason):
    row, args = inputs()
    if case in {"stale", "future"}:
        args["spot_book"]["ts"] += (-31 if case == "stale" else 6) * 1000
    elif case == "thin":
        args["spot_book"]["a"] = [["10.01", "1"]]
    elif case == "inverted":
        args["spot_book"]["a"] = [["9", "1000"]]
    elif case == "quote":
        args["future_meta"]["settleCoin"] = "USDC"
    elif case == "step":
        args["future_meta"]["lotSizeFilter"]["qtyStep"] = "NaN"
    else:
        args["future_meta"]["baseCoin"] = "1000ABC"
    result = evaluate(row, FsSettings(), **args)
    assert result.stage == "blocked"
    assert reason in result.blockers
    assert not result.scenarios


def test_quantity_lcm_and_rounding():
    assert str(common_step("0.03", "0.02")) == "0.06"
    row, args = inputs()
    args["spot_meta"]["lotSizeFilter"]["basePrecision"] = "0.03"
    args["future_meta"]["lotSizeFilter"]["qtyStep"] = "0.02"
    result = evaluate(row, FsSettings(target_usdt=10), **args)
    assert result.quantity == 0.96


def market(kind):
    return MarketSnapshot(
        symbol="ABCUSDT",
        base="ABC",
        raw_symbol="ABCUSDT",
        exchange="bybit",
        market_type=kind,
        bid=10,
        ask=10.01,
        timestamp=NOW,
        identity_status="verified",
        unit_status="verified",
        asset_id="abc",
        binding_valid_until=NOW + timedelta(days=1),
    )


def test_identity_never_uses_ticker_alone():
    row, args = inputs()
    legs = [market(MarketType.SPOT), market(MarketType.FUTURE)]
    assert identity_check(row, legs, args["spot_book"], args["future_book"], NOW)[0] == "verified"
    assert row.valid_until <= NOW + timedelta(seconds=15)
    legs[1].asset_id = "other"
    assert identity_check(row, legs, args["spot_book"], args["future_book"], NOW)[0] == "conflict"
    assert identity_check(row, [], args["spot_book"], args["future_book"], NOW)[0] == "unverified"
    legs[1].symbol_alias_price_multiplier = 1000
    assert identity_check(row, legs, args["spot_book"], args["future_book"], NOW)[0] != "verified"


@pytest.mark.asyncio
async def test_provider_get_only_whitelist_and_signature():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"retCode": 0, "result": {}, "time": int(datetime.now(UTC).timestamp() * 1000)},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BybitFsProvider(client)
        creds = AccountConnectionSecretPayload(api_key="private-key", api_secret="private-secret")
        await provider.get("/v5/spot-margin-trade/max-borrowable", {"currency": "ABC"}, creds)
        assert calls[0].method == "GET"
        assert "X-BAPI-SIGN" in calls[0].headers
        assert "private" not in str(calls[0].url)
        with pytest.raises(FsDataError, match="endpoint_not_allowed"):
            await provider.get("/v5/account/borrow", {}, creds)
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_provider_redacts_exchange_messages():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"retCode": 10005, "retMsg": "secret-key-request", "result": {}}
            )
        )
    ) as client:
        with pytest.raises(FsDataError, match="^permission_denied$"):
            await BybitFsProvider(client).get("/v5/market/tickers")


@pytest.mark.asyncio
async def test_repository_retention_and_settings(tmp_path):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    try:
        await repo.save_settings(FsSettings(target_usdt=123))
        assert (await repo.settings()).target_usdt == 123
        for i in range(300):
            report = FsReport(observed_at=NOW + timedelta(minutes=5 * i))
            await repo.save(report, [("ABCUSDT", "discovered", []), ("DEFUSDT", "blocked", ["x"])])
        async with repo.db.execute("SELECT count(*) FROM fs_snapshots") as c:
            assert (await c.fetchone())[0] == 288
        async with repo.db.execute("SELECT count(*) FROM fs_events") as c:
            assert (await c.fetchone())[0] == 500
        assert len(await repo.history()) == 24
    finally:
        await repo.aclose()


@pytest.mark.asyncio
async def test_stale_report_and_failed_scan_do_not_reuse_success(tmp_path):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()

    class Broken:
        async def public_snapshot(self):
            raise FsDataError("query_timeout")

        async def aclose(self):
            pass

    monitor = FsMonitor(repo, None, SimpleNamespace(get_markets=list), Broken())
    row, _ = inputs()
    row.stage = "cost_pass"
    row.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    monitor.report = FsReport(observed_at=NOW, candidates=[row])
    try:
        assert (await monitor.current()).candidates[0].stage == "stale"
        await monitor.scan()
        assert monitor.report.error == "query_timeout"
        assert not monitor.report.candidates
        assert not monitor.scanning
    finally:
        await monitor.aclose()


def test_api_auth_validation_and_no_trade_endpoints(tmp_path):
    app = create_app(
        settings=Settings(
            database_url=f"sqlite:///{(tmp_path / 'radar.db').as_posix()}",
            dashboard_password="secret",
        ),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/fs-monitor/report").status_code == 401
        headers = {"X-Dashboard-Password": "secret"}
        for route in ("report", "history", "events", "settings"):
            assert client.get("/api/fs-monitor/" + route, headers=headers).status_code == 200
        assert (
            client.put(
                "/api/fs-monitor/settings", headers=headers, json={"target_usdt": -1}
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/fs-monitor/settings", headers=headers, json={"account_id": "missing"}
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/fs-monitor/settings", headers=headers, json={"enabled": False}
            ).status_code
            == 200
        )
        assert client.post("/api/fs-monitor/refresh", headers=headers).status_code == 202
        assert client.post("/api/fs-monitor/borrow", headers=headers).status_code == 404


def test_api_requires_nonempty_password(tmp_path):
    app = create_app(
        settings=Settings(
            database_url=f"sqlite:///{(tmp_path / 'radar.db').as_posix()}", dashboard_password=""
        ),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/fs-monitor/report").status_code == 503


@pytest.mark.asyncio
async def test_full_scan_with_account_and_config_change_discards_inflight_result(tmp_path):
    now = datetime.now(UTC)
    _, args = inputs()
    for name in ("spot_book", "future_book"):
        args[name]["ts"] = int(now.timestamp() * 1000)
    ticker = {
        "symbol": "ABCUSDT",
        "fundingRate": "-0.01",
        "fundingIntervalHour": "4",
        "nextFundingTime": int((now + timedelta(hours=2)).timestamp() * 1000),
        "markPrice": "10",
        "turnover24h": "2000000",
    }
    data = {
        "spots": {"ABCUSDT": args["spot_meta"]},
        "futures": {"ABCUSDT": args["future_meta"]},
        "future_tickers": {"ABCUSDT": ticker},
        "spot_tickers": {"ABCUSDT": {"turnover24h": "1000000"}},
        "rates": {
            "ABC": {"borrowable": True, "hourlyBorrowRate": "0.00001", "maxBorrowingAmount": "9999"}
        },
        "spot_at": now,
        "future_at": now,
        "margin_at": now,
    }
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    await repo.save_settings(FsSettings(account_id="selected"))
    provider = SimpleNamespace(
        public_snapshot=AsyncMock(return_value=data),
        current_instruments=AsyncMock(
            return_value=[(args["spot_meta"], now), (args["future_meta"], now)]
        ),
        books=AsyncMock(return_value=[(args["spot_book"], now), (args["future_book"], now)]),
        account_context=AsyncMock(
            return_value=({"ABC": {"borrowable": True, "hourlyBorrowRate": "0.00001"}}, now)
        ),
        account_quote=AsyncMock(
            return_value={
                "account_max_loan": 200,
                "available_inventory": 200,
                "inventory_at": now,
                "account_at": now,
                "spot_fee_pct": 0.1,
                "future_fee_pct": 0.055,
            }
        ),
        aclose=AsyncMock(),
    )
    legs = [market(MarketType.SPOT), market(MarketType.FUTURE)]
    for leg in legs:
        leg.binding_valid_until = now + timedelta(days=1)
    monitor = FsMonitor(repo, None, SimpleNamespace(get_markets=lambda: legs), provider)
    monitor.credentials = AsyncMock(
        return_value=(AccountConnectionSecretPayload(api_key="k", api_secret="s"), now)
    )
    try:
        await monitor.scan()
        assert not monitor.report.error
        assert monitor.report.candidates[0].stage == "cost_pass"
        assert monitor.report.candidates[0].interval_hours == 4
        assert (await monitor.current()).candidates[0].stage == "cost_pass"
        assert len(await repo.events()) == 1
        await monitor.scan()
        assert len(await repo.events()) == 1  # no repeated identical event
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait_snapshot():
            entered.set()
            await release.wait()
            return data

        provider.public_snapshot.side_effect = wait_snapshot
        task = asyncio.create_task(monitor.scan())
        await entered.wait()
        await monitor.update_settings(FsSettings(target_usdt=500, enabled=False))
        release.set()
        await task
        assert monitor.report.settings.target_usdt == 500
        assert not monitor.report.candidates
    finally:
        await monitor.aclose()


@pytest.mark.asyncio
async def test_account_quote_checks_inventory_timestamp_and_fee_symbol():
    provider = BybitFsProvider(SimpleNamespace())
    now = datetime.now(UTC)
    responses = {
        "/v5/spot-margin-trade/max-borrowable": {"currency": "ABC", "maxLoan": "100"},
        "/v5/spot-margin-trade/flexible-available-inventory": {
            "currency": "ABC",
            "availableInventory": "0",
            "updateTime": int(now.timestamp() * 1000),
        },
        "/v5/account/fee-rate": {"list": [{"symbol": "ABCUSDT", "takerFeeRate": "0.001"}]},
    }

    async def get(path, *a, **kw):
        return responses[path], now

    provider.get = get
    quote = await provider.account_quote("ABCUSDT", "ABC", object())
    assert quote["available_inventory"] == 0
    responses["/v5/spot-margin-trade/flexible-available-inventory"]["updateTime"] -= 31000
    with pytest.raises(FsDataError, match="stale_inventory"):
        await provider.account_quote("ABCUSDT", "ABC", object())
