from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_fs_astro import signal
from test_fs_monitor import inputs, market

from app.db.database import connect_database
from app.models.fs_monitor import FsCandidate, FsSettings
from app.models.market import MarketType
from app.models.settings import RiskSettings
from app.services.fs_astro import FsAstroBridge, card_plan
from app.services.fs_bybit import FsDataError
from app.services.fs_monitor import FsMonitor, identity_check
from app.services.fs_public import (
    EXCHANGES,
    PublicFsProvider,
    daily_to_hourly,
    raw_symbols,
    route_key,
)
from app.services.fs_repository import FsRepository


@pytest.mark.parametrize("exchange", EXCHANGES)
def test_native_fs_mapping_uses_exact_exchange_and_disabled_card(exchange):
    row = signal()
    row.exchange = exchange
    row.spot_raw_symbol, row.future_raw_symbol = raw_symbols(exchange, row.base)
    pair = card_plan(row)
    assert pair["buyEx"] == pair["sellEx"] == exchange
    assert pair["type"] == "FS" and pair["name"] == row.base
    assert pair["status"] is False and pair["disableOpen"] is True


@pytest.mark.asyncio
async def test_durable_duplicates_are_per_exchange_and_legacy_bybit_survives(tmp_path):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    client = SimpleNamespace(list_pairs=AsyncMock(return_value=[]), add_pair=AsyncMock())
    bridge = FsAstroBridge(client, repo, AsyncMock(return_value=RiskSettings()), dry_run_only=False)
    try:
        await repo.record_astro_attempt("ABCUSDT", "pending")
        for exchange in EXCHANGES:
            row = signal()
            row.exchange = exchange
            row.spot_raw_symbol, row.future_raw_symbol = raw_symbols(exchange, row.base)
            expected = "uncertain" if exchange == "bybit" else "created"
            assert await bridge.process(row, AsyncMock(return_value=row), lambda: True) == expected
            if exchange != "bybit":
                assert (
                    await bridge.process(row, AsyncMock(return_value=row), lambda: True)
                    == "recorded"
                )
        assert client.add_pair.await_count == 4
        row = signal().model_copy(update={"exchange": "gate"})
        client.list_pairs.return_value = [card_plan(row)]
        assert await bridge.process(row, AsyncMock(return_value=row), lambda: True) == "existing"
    finally:
        await repo.aclose()


@pytest.mark.asyncio
async def test_recheck_cannot_switch_exchange(tmp_path):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    client = SimpleNamespace(list_pairs=AsyncMock(return_value=[]), add_pair=AsyncMock())
    bridge = FsAstroBridge(client, repo, AsyncMock(return_value=RiskSettings()), dry_run_only=False)
    try:
        fresh = signal().model_copy(update={"exchange": "okx"})
        assert (
            await bridge.process(signal(), AsyncMock(return_value=fresh), lambda: True)
            == "signal_changed"
        )
        client.add_pair.assert_not_awaited()
    finally:
        await repo.aclose()


def test_okx_contract_units_need_registry_and_metadata_agreement():
    row, args = inputs()
    row.exchange, row.contract_multiplier = "okx", 0.1
    row.spot_raw_symbol, row.future_raw_symbol = "ABC-USDT", "ABC-USDT-SWAP"
    legs = [market(MarketType.SPOT), market(MarketType.FUTURE)]
    for leg, raw in zip(legs, (row.spot_raw_symbol, row.future_raw_symbol)):
        leg.exchange, leg.raw_symbol = "okx", raw
    legs[1].contract_size_multiplier = 0.1
    assert (
        identity_check(row, legs, args["spot_book"], args["future_book"], args["now"])[0]
        == "verified"
    )
    legs[1].contract_size_multiplier = 1
    assert (
        identity_check(row, legs, args["spot_book"], args["future_book"], args["now"])[0]
        == "unverified"
    )
    assert daily_to_hourly("0.00024") == pytest.approx(0.00001)
    assert daily_to_hourly("bad") is None


@pytest.mark.asyncio
async def test_okx_rate_and_contract_book_conversion_are_public_only():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("interest-rate-loan-quota"):
            data = [{"basic": [{"ccy": "ABC", "rate": "0.00024"}]}]
        else:
            data = [
                {
                    "ts": "1792667678102",
                    "bids": [["10", "20", "0", "1"]],
                    "asks": [["11", "30", "0", "1"]],
                }
            ]
        return httpx.Response(200, json={"code": "0", "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicFsProvider("okx", SimpleNamespace(get_markets=list), client)
        rates, _ = await provider.borrow_reference()
        assert rates["ABC"]["hourlyBorrowRate"] == pytest.approx(0.00001)
        provider.cache["ABCUSDT"] = ({}, {"baseMultiplier": 0.1})
        spot, future = await provider.books("ABCUSDT")
        assert spot[0]["b"][0][1] == 20
        assert future[0]["b"][0][1] == 2
        assert future[0]["s"] == "ABC-USDT-SWAP"
    assert all(r.method == "GET" and "OK-ACCESS-KEY" not in r.headers for r in requests)


@pytest.mark.asyncio
async def test_discovery_preserves_source_time_and_excludes_alias_or_rtoken():
    now = datetime.now(UTC)
    legs = [market(MarketType.SPOT), market(MarketType.FUTURE)]
    for leg in legs:
        leg.exchange = "bitget"
        leg.timestamp = now - timedelta(minutes=10)
    alias = legs[0].model_copy(update={"raw_symbol": "RABCUSDT"})
    provider = PublicFsProvider("bitget", SimpleNamespace(get_markets=lambda: [*legs, alias]))
    provider.borrow_reference = AsyncMock(return_value=({}, now))
    try:
        data = await provider.public_snapshot()
        assert data["spots"]["ABCUSDT"]["symbol"] == "ABCUSDT"
        assert data["spot_tickers"]["ABCUSDT"]["source_at"] < now - timedelta(minutes=9)
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_round_robin_same_symbols_failure_isolation_and_filtering(tmp_path):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    now = datetime.now(UTC)
    snapshot = {
        "spots": {},
        "rates": {},
        "futures": {"ABCUSDT": {"quoteCoin": "USDT"}},
        "future_tickers": {
            "ABCUSDT": {"symbol": "ABCUSDT", "fundingRate": "-0.01", "fundingIntervalHour": 4}
        },
    }
    providers = {
        ex: SimpleNamespace(
            public_snapshot=AsyncMock(return_value=deepcopy(snapshot)), aclose=AsyncMock()
        )
        for ex in EXCHANGES
    }
    providers["gate"].public_snapshot.side_effect = FsDataError("query_timeout")
    mon = FsMonitor(
        repo,
        None,
        SimpleNamespace(get_markets=list),
        provider=providers["bybit"],
        providers=providers,
    )

    async def candidate(ticker, *args, exchange="bybit"):
        return FsCandidate(
            symbol=ticker["symbol"],
            base="ABC",
            exchange=exchange,
            observed_at=now,
            valid_until=now + timedelta(seconds=30),
        )

    mon._candidate = candidate
    try:
        await mon.scan()
        result = await mon.current()
        assert result.error is None
        assert result.exchange_status["gate"] == "query_timeout"
        assert len({route_key(r) for r in result.candidates}) == 4
        assert {r.exchange for r in result.candidates} == set(EXCHANGES) - {"gate"}
        events = await repo.events()
        assert len({r["symbol"] for r in events}) == 4
        await mon.update_settings(FsSettings(exchanges=["okx"]))
        await mon.scan()
        assert [r.exchange for r in (await mon.current()).candidates] == ["okx"]
    finally:
        await mon.aclose()


@pytest.mark.asyncio
async def test_rate_endpoint_failure_retains_candidates_but_never_assumes_zero():
    leg = market(MarketType.FUTURE).model_copy(
        update={"exchange": "okx", "raw_symbol": "ABC-USDT-SWAP"}
    )
    provider = PublicFsProvider("okx", SimpleNamespace(get_markets=lambda: [leg]))
    provider.borrow_reference = AsyncMock(side_effect=FsDataError("public_query_failed"))
    try:
        data = await provider.public_snapshot()
        assert data["rates"] == {} and data["borrow_error"] == "public_borrow_query_failed"
        assert "ABCUSDT" in data["future_tickers"]
    finally:
        await provider.aclose()
