import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_fs_monitor import inputs

from app.db.database import connect_database
from app.models.fs_monitor import FsReport, FsSettings
from app.models.market import MarketType
from app.models.settings import RiskSettings
from app.services.fs_astro import FsAstroBridge, card_plan
from app.services.fs_economics import evaluate
from app.services.fs_identity import REVIEWED_AT, TARGETS, reviewed_fs_bindings, spec_url
from app.services.fs_monitor import FsMonitor
from app.services.fs_repository import FsRepository


def signal():
    row, args = inputs()
    now = datetime.now(UTC)
    row.account_state = "not_configured"
    row.account_max_loan = row.available_inventory = None
    row.fee_source = "configured_assumption"
    row.next_funding_at = now + timedelta(hours=2)
    args["now"] = now
    for key in ("spot_book", "future_book"):
        args[key]["ts"] = int(now.timestamp() * 1000)
    return evaluate(row, FsSettings(), **args)


def test_public_signal_is_not_account_cost_pass_and_maps_to_disabled_fs():
    row = signal()
    assert row.signal_ready and row.stage == "discovered"
    pair = card_plan(row)
    assert (pair["name"], pair["type"], pair["buyEx"], pair["sellEx"]) == (
        "ABC",
        "FS",
        "bybit",
        "bybit",
    )
    assert pair["status"] is False and pair["disableOpen"] is True
    assert pair["leverage"] == "1"


@pytest.mark.parametrize(
    "change",
    [
        {"account_state": "permission_denied"},
        {"available_inventory": 0},
        {"identity_state": "conflict", "blockers": ["identity_conflict"]},
        {"identity_state": "unverified"},
        {"funding_pct": 0},
        {"account_state": "query_failed"},
    ],
)
def test_negative_evidence_blocks_signal(change):
    row, args = inputs()
    result = evaluate(row.model_copy(update=change), FsSettings(), **args)
    assert not result.signal_ready and result.stage != "cost_pass"


@pytest.mark.asyncio
async def test_astro_duplicate_and_uncertain_write_survive_restart(tmp_path):
    path = str(tmp_path / "fs.db")
    repo = FsRepository(await connect_database(path))
    await repo.initialize()
    row = signal()
    client = SimpleNamespace(list_pairs=AsyncMock(return_value=[]), add_pair=AsyncMock())
    client.list_pairs_cached = AsyncMock(side_effect=AssertionError("Use the realtime list"))
    fresh = AsyncMock(side_effect=signal)
    bridge = FsAstroBridge(client, repo, AsyncMock(return_value=RiskSettings()), dry_run_only=False)
    try:
        assert await bridge.process(row, fresh, lambda: True) == "created"
        client.list_pairs.assert_awaited_once()
        client.list_pairs_cached.assert_not_awaited()
        assert client.add_pair.await_count == 1
        assert await bridge.process(row, fresh, lambda: True) == "recorded"
        client.list_pairs.return_value = [card_plan(row)]
        assert await bridge.process(row, fresh, lambda: True) == "existing"
        assert client.add_pair.await_count == 1
        # Simulate an interrupted network write. The durable pending marker remains.
        await repo.record_astro_attempt("ABCUSDT", "pending")
    finally:
        await repo.aclose()
    repo = FsRepository(await connect_database(path))
    await repo.initialize()
    try:
        client.list_pairs.return_value = []
        bridge = FsAstroBridge(
            client, repo, AsyncMock(return_value=RiskSettings()), dry_run_only=False
        )
        assert await bridge.process(row, fresh, lambda: True) == "uncertain"
        client.list_pairs.return_value = [card_plan(row)]
        assert await bridge.process(row, fresh, lambda: True) == "existing"
        assert await repo.astro_attempt(row.symbol) == "reconciled"
        assert client.add_pair.await_count == 1
    finally:
        await repo.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["list_failure", "expired", "changed", "identity", "not_ready", "dry_run", "add_failure"],
)
async def test_astro_fail_closed(tmp_path, case):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    row = signal()
    fresh = signal()
    client = SimpleNamespace(list_pairs=AsyncMock(return_value=[]), add_pair=AsyncMock())
    if case == "list_failure":
        client.list_pairs.side_effect = RuntimeError("private-vendor-detail")
    if case == "expired":
        fresh.valid_until = datetime.now(UTC) - timedelta(seconds=1)
    if case == "identity":
        fresh.identity_details = [{"revision": 99}]
    if case == "not_ready":
        fresh.signal_ready = False
    if case == "add_failure":
        client.add_pair.side_effect = RuntimeError("private-vendor-detail")
    bridge = FsAstroBridge(
        client, repo, AsyncMock(return_value=RiskSettings()), dry_run_only=case == "dry_run"
    )
    try:
        result = await bridge.process(row, AsyncMock(return_value=fresh), lambda: case != "changed")
        assert result != "created" and "private" not in result
        if case == "add_failure":
            assert await repo.astro_attempt(row.symbol) == "pending"
            assert (
                await bridge.process(row, AsyncMock(return_value=fresh), lambda: True)
                == "uncertain"
            )
            assert client.add_pair.await_count == 1
        else:
            client.add_pair.assert_not_awaited()
    finally:
        await repo.aclose()


@pytest.mark.asyncio
async def test_concurrent_auto_checks_add_only_once(tmp_path):
    repo = FsRepository(await connect_database(str(tmp_path / "fs.db")))
    await repo.initialize()
    client = SimpleNamespace(list_pairs=AsyncMock(return_value=[]), add_pair=AsyncMock())
    bridge = FsAstroBridge(client, repo, AsyncMock(return_value=RiskSettings()), dry_run_only=False)
    try:
        results = await asyncio.gather(
            *[
                bridge.process(signal(), AsyncMock(side_effect=signal), lambda: True)
                for _ in range(3)
            ]
        )
        assert results.count("created") == 1 and client.add_pair.await_count == 1
    finally:
        await repo.aclose()


def test_refresh_cadence_subtracts_scan_time_without_relaxing_expiry():
    monitor = FsMonitor(None, None, None, provider=object())
    monitor.report.candidates = [signal()]
    monitor._last_duration = 8
    assert monitor.refresh_delay(FsSettings()) == 2
    monitor._last_duration = 20
    assert monitor.refresh_delay(FsSettings()) == 1
    monitor.report.candidates = []
    assert monitor.refresh_delay(FsSettings()) == 40


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["account_changed", "account_disabled", "identity_changed", "expired"]
)
async def test_current_downgrades_both_cost_and_signal(case):
    from test_fs_monitor import market

    row = signal()
    row.stage = "cost_pass"
    now = datetime.now(UTC)
    legs = [market(MarketType.SPOT), market(MarketType.FUTURE)]
    for leg in legs:
        leg.binding_valid_until = now + timedelta(days=1)
    row.identity_details = [
        {
            "market_type": m.market_type.value,
            "revision": m.binding_revision,
            "fingerprint": m.binding_fingerprint,
        }
        for m in legs
    ]
    settings = FsSettings(account_id="selected" if case.startswith("account") else None)
    monitor = FsMonitor(
        SimpleNamespace(settings=AsyncMock(return_value=settings)),
        None,
        SimpleNamespace(get_markets=lambda: legs),
        provider=object(),
    )
    monitor._account_stamp = now
    monitor.credentials = AsyncMock(return_value=(object(), now + timedelta(seconds=1)))
    if case == "account_disabled":
        from app.services.fs_bybit import FsDataError

        monitor.credentials.side_effect = FsDataError("account_unavailable")
    if case == "identity_changed":
        legs[0].identity_status = "conflict"
    if case == "expired":
        row.valid_until = now - timedelta(seconds=1)
    monitor.report = FsReport(observed_at=now, candidates=[row])
    current = await monitor.current()
    assert current.candidates[0].stage == "stale"
    assert not current.candidates[0].signal_ready


def specs():
    data = {}
    for symbol, (base, name, _, _) in TARGETS.items():
        for category in ("spot", "linear"):
            product = {"symbol": symbol, "baseCoin": base, "quoteCoin": "USDT", "status": "Trading"}
            if category == "linear":
                product.update(
                    fullName=name,
                    contractType="LinearPerpetual",
                    settleCoin="USDT",
                    isPreListing=False,
                )
            data[spec_url(symbol, category)] = {"retCode": 0, "result": {"list": [product]}}
    return data


def test_reviewed_identity_requires_both_exact_specs_and_expires_without_renewal():
    data = specs()
    rows = reviewed_fs_bindings(data, REVIEWED_AT + timedelta(hours=1))
    assert len(rows) == 4
    assert all(len(r.evidence) == 3 and r.contract_size == 1 for r in rows)
    assert len({r.asset_id for r in rows}) == 2
    assert not reviewed_fs_bindings(data, REVIEWED_AT + timedelta(days=7))
    data[spec_url("MEWUSDT", "linear")]["result"]["list"][0]["fullName"] = "Other same-ticker coin"
    assert all(r.market.raw_symbol != "MEWUSDT" for r in reviewed_fs_bindings(data, REVIEWED_AT))
    data.pop(spec_url("BMTUSDT", "spot"))
    assert not reviewed_fs_bindings(data, REVIEWED_AT)
