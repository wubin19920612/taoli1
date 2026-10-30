import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from test_astro_alerts import opportunity

from app.core.config import Settings
from app.db.database import connect_database
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.settings import AstroCardSettings, LivePilotSettings
from app.services.astro_alerts import AstroAlertService, _same_route
from app.services.astro_card_attempts import AstroCardAttemptRepository, route_key
from app.services.astro_client import AstroClientError, AstroSdkClient, AstroSdkConfig


@pytest.fixture
async def repository(tmp_path):
    db = await connect_database(str(tmp_path / "attempts.db"))
    await initialize_schema(db)
    try:
        yield AstroCardAttemptRepository(db)
    finally:
        await db.close()


class Client:
    def __init__(self):
        self.pairs = []
        self.submitted = []
        self.error = None
        self.list_calls = 0
        self.before_add = None

    async def list_pairs(self):
        self.list_calls += 1
        return list(self.pairs)

    async def list_pairs_cached(self):
        pytest.fail("Creation must use realtime lists")

    async def add_pair(self, planned):
        self.submitted.append(dict(planned))
        if self.before_add:
            await self.before_add(planned)
        if self.error:
            raise self.error
        self.pairs.append(dict(planned))
        return {"code": 0}


def service(client, repository, *, both=False):
    return AstroAlertService(
        client,
        Settings(astro_alert_auto_create=True, astro_manual_card_create=True, astro_dry_run_only=False),
        attempts=repository,
        card_settings=AstroCardSettings(card_variant="both" if both else "non_gc"),
        live_pilot_settings=LivePilotSettings(enabled=True),
        hl_gc_catalog=SimpleNamespace(supports_opportunity=AsyncMock(return_value=False)),
        add_restart_delay_seconds=0,
    )


async def trigger(subject, source, opp=None):
    opp = opp or opportunity()
    if source == "preadd":
        return await subject.handle_preadd(opp, subject.card_settings)
    return await getattr(subject, f"handle_{'manual_create' if source == 'manual' else source}")(opp)


@pytest.mark.asyncio
async def test_twenty_mixed_calls_serialize_each_variant(repository):
    client = Client()
    subject = service(client, repository, both=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(planned):
        entered.set()
        await release.wait()

    client.before_add = pause
    calls = [asyncio.create_task(trigger(subject, source))
             for source in ["alert", "manual", "preadd", "live_pilot"] * 5]
    await asyncio.wait_for(entered.wait(), 2)
    assert client.list_calls == 1
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*calls), 5)
    assert sum(result.status == "created" for result in results) == 1
    assert all(result.action == "existing" for result in results if result.status != "created")
    assert client.list_calls == 20
    assert len(client.submitted) == 2
    assert all(count == 1 for count in Counter(map(route_key, client.submitted)).values())
    assert {row["state"] for row in await repository.list_attempts()} == {"created"}


@pytest.mark.asyncio
async def test_different_names_make_progress_while_other_add_waits(repository):
    client = Client()
    subject = service(client, repository)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(planned):
        if planned["name"] == "BTC":
            entered.set()
            await release.wait()

    client.before_add = pause
    first = asyncio.create_task(subject.handle_alert(opportunity()))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = await asyncio.wait_for(subject.handle_alert(opportunity().model_copy(update={"symbol": "ETHUSDT"})), 2)
        assert second.status == "created"
        assert not first.done()
    finally:
        release.set()
        await first


@pytest.mark.asyncio
async def test_same_name_lock_includes_restart_wait(repository, monkeypatch):
    client = Client()
    subject = service(client, repository)
    subject.add_restart_delay_seconds = 3
    sleeping, release, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def restart_wait(seconds):
        assert seconds == 3
        sleeping.set()
        await release.wait()

    async def manual():
        queued.set()
        return await subject.handle_manual_create(opportunity())

    monkeypatch.setattr("app.services.astro_alerts.asyncio.sleep", restart_wait)
    first = asyncio.create_task(subject.handle_alert(opportunity()))
    await asyncio.wait_for(sleeping.wait(), 2)
    second = asyncio.create_task(manual())
    try:
        await asyncio.wait_for(queued.wait(), 2)
        assert client.list_calls == 1
        assert not first.done() and not second.done()
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert [result.action for result in results] == ["add", "existing"]
    assert client.list_calls == 2 and len(client.submitted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["alert", "live_pilot", "preadd"])
async def test_timeout_blocks_automatic_retry_and_reconciles(repository, source):
    client = Client()
    client.error = TimeoutError("add timed out")
    subject = service(client, repository)
    result = await trigger(subject, source)
    planned = client.submitted[0]
    row = await repository.get(route_key(planned))
    assert result.action == "uncertain" and "结果未知" in result.message
    assert row["state"] == "unknown" and row["source"] == source
    client.error = None
    # Recreating the service demonstrates that the barrier survives its lifetime.
    subject = service(client, repository)
    skipped = await trigger(subject, source)
    assert (skipped.status, skipped.action) == ("skipped", "uncertain")
    assert row["updated_at"] in skipped.message
    assert len(client.submitted) == 1
    client.pairs.append(planned)
    existing = await trigger(subject, source)
    assert existing.action == "existing"
    assert (await repository.get(route_key(planned)))["state"] == "reconciled"
    assert len(client.submitted) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "unknown"])
async def test_manual_retry_warns_and_records_source(repository, state):
    client = Client()
    subject = service(client, repository)
    client.error = TimeoutError()
    await subject.handle_alert(opportunity())
    key = route_key(client.submitted[0])
    await repository.mark(key, state, "prior attempt")
    client.error = None
    result = await subject.handle_manual_create(opportunity())
    assert result.status == "created"
    assert "上次提交结果未知，可能产生重复卡" in result.warnings
    row = await repository.get(key)
    assert row["state"] == "created" and row["source"] == "manual"
    assert len(client.submitted) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("state,age,expected", [
    ("unknown", 25, "created"), ("unknown", 23, "skipped"), ("pending", 25, "skipped"),
])
async def test_only_unknown_older_than_24_hours_expires(repository, state, age, expected):
    client = Client()
    subject = service(client, repository)
    client.error = TimeoutError()
    await subject.handle_alert(opportunity())
    await repository.db.execute("UPDATE astro_card_attempts SET state=?, updated_at=?", (
        state, (datetime.now(UTC) - timedelta(hours=age)).isoformat(),
    ))
    await repository.db.commit()
    client.error = None
    result = await subject.handle_alert(opportunity())
    assert result.status == expected
    assert len(client.submitted) == (2 if expected == "created" else 1)
    if expected == "created":
        assert "已超过 24 小时" in result.message


@pytest.mark.asyncio
async def test_different_hl_dex_keys_do_not_share_unknown_barrier(repository):
    client = Client()
    subject = service(client, repository)
    xyz = opportunity().model_copy(update={"buy_exchange": "hyperliquid", "buy_raw_symbol": "xyz:BTC"})
    para = xyz.model_copy(update={"buy_raw_symbol": "para:BTC"})
    client.error = TimeoutError()
    assert (await subject.handle_alert(xyz)).action == "uncertain"
    client.error = None
    assert (await subject.handle_alert(para)).status == "created"
    first, second = client.submitted
    assert first["name"] == second["name"]
    assert route_key(first) != route_key(second)
    assert not _same_route(first, second)
    assert {row["state"] for row in await repository.list_attempts()} == {"unknown", "created"}


def test_route_key_tracks_exact_market_type_direction_and_each_hl_leg():
    planned = {"name": "BTC", "type": "FF", "buyEx": "hl", "sellEx": "gc-hl"}
    key = route_key(planned)
    assert key == route_key({**planned, "aHlDex": " MAIN ", "bHlDex": "main", "status": True})
    for field, value in (("name", "ETH"), ("type", "SF"), ("buyEx", "gc-hl"),
                         ("sellEx", "hl"), ("aHlDex", "xyz"), ("bHlDex", "para")):
        assert route_key({**planned, field: value}) != key
    remote = {**planned, "aHlDex": "main", "aEffectiveHlDex": "xyz"}
    assert _same_route(remote, {**planned, "aHlDex": " XYZ "})
    assert not _same_route(remote, planned)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,state", [
    (AstroClientError("rejected", 400, has_response_body=True), "failed"),
    (AstroClientError("no body", 400), "unknown"),
    (AstroClientError("server error", 503, has_response_body=True), "unknown"),
    (httpx.ConnectError("network error"), "unknown"),
    (ValueError("unexpected response"), "unknown"),
])
async def test_rejection_vs_uncertainty_and_retry(repository, error, state):
    client = Client()
    client.error = error
    subject = service(client, repository)
    result = await subject.handle_alert(opportunity())
    assert result.action == ("add" if state == "failed" else "uncertain")
    assert (await repository.get(route_key(client.submitted[0])))["state"] == state
    client.error = None
    retried = await subject.handle_alert(opportunity())
    assert retried.status == ("created" if state == "failed" else "skipped")
    assert len(client.submitted) == (2 if state == "failed" else 1)


@pytest.mark.asyncio
async def test_pending_is_committed_before_add_and_cancellation_propagates(repository, tmp_path):
    client = Client()
    subject = service(client, repository)
    entered = asyncio.Event()

    async def pause(planned):
        reader = await connect_database(str(tmp_path / "attempts.db"))
        try:
            row = await AstroCardAttemptRepository(reader).get(route_key(planned))
            assert row["state"] == "pending"
            assert json.loads(row["request_json"]) == planned
        finally:
            await reader.close()
        entered.set()
        await asyncio.Event().wait()

    client.before_add = pause
    task = asyncio.create_task(subject.handle_alert(opportunity()))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await repository.get(route_key(client.submitted[0])))["state"] == "unknown"
    assert (await subject.handle_alert(opportunity())).action == "uncertain"


@pytest.mark.asyncio
async def test_failed_pending_commit_prevents_external_write(repository, monkeypatch):
    client = Client()
    subject = service(client, repository)
    monkeypatch.setattr(repository.db, "commit", AsyncMock(side_effect=RuntimeError("disk full")))
    result = await subject.handle_alert(opportunity())
    assert result.status == "failed" and "未提交远端" in result.message
    assert not client.submitted
    assert await repository.list_attempts() == []


@pytest.mark.asyncio
async def test_outcome_write_failure_keeps_committed_pending_barrier(repository, monkeypatch):
    client = Client()
    subject = service(client, repository)
    client.error = TimeoutError("write may have reached remote")
    monkeypatch.setattr(repository, "mark", AsyncMock(side_effect=RuntimeError("storage unavailable")))
    result = await subject.handle_alert(opportunity())
    assert result.action == "uncertain"
    assert (await repository.get(route_key(client.submitted[0])))["state"] == "pending"
    client.error = None
    assert (await subject.handle_alert(opportunity())).action == "uncertain"
    assert len(client.submitted) == 1


@pytest.mark.asyncio
async def test_created_confirmation_failure_is_uncertain(repository, monkeypatch):
    client = Client()
    subject = service(client, repository)
    mark = repository.mark

    async def fail_created(key, state, message):
        if state == "created":
            raise RuntimeError("confirmation failed")
        await mark(key, state, message)

    monkeypatch.setattr(repository, "mark", fail_created)
    result = await subject.handle_alert(opportunity())
    assert result.action == "uncertain"
    assert (await repository.get(route_key(client.submitted[0])))["state"] == "unknown"
    assert (await subject.handle_alert(opportunity())).action == "existing"
    assert (await repository.get(route_key(client.submitted[0])))["state"] == "reconciled"


@pytest.mark.asyncio
async def test_missing_repository_fails_closed():
    client = Client()
    result = await service(client, None).handle_alert(opportunity())
    assert result.status == "failed"
    assert not client.submitted


@pytest.mark.asyncio
async def test_partial_success_records_variants_separately(repository):
    client = Client()
    subject = service(client, repository, both=True)

    async def fail_second(planned):
        if planned["buyEx"].startswith("gc-"):
            raise TimeoutError("second variant")

    client.before_add = fail_second
    result = await subject.handle_alert(opportunity())
    assert result.action == "add_partial" and "结果未知" in result.message
    assert [(await repository.get(route_key(pair)))["state"] for pair in client.submitted] == ["created", "unknown"]


@pytest.mark.asyncio
async def test_unknown_first_variant_does_not_prevent_visible_sibling_reconciliation(repository):
    client = Client()
    subject = service(client, repository, both=True)
    base = {"name": "BTC", "type": "FF", "buyEx": "binance", "sellEx": "okx"}
    gc = {**base, "buyEx": "gc-binance", "sellEx": "gc-okx"}
    await repository.mark_pending(base, "alert", False)
    await repository.mark_pending(gc, "alert", False)
    client.pairs.append(gc)
    result = await subject.handle_alert(opportunity())
    assert result.action == "uncertain" and result.status == "skipped"
    assert not client.submitted
    assert (await repository.get(route_key(base)))["state"] == "pending"
    assert (await repository.get(route_key(gc)))["state"] == "reconciled"


@pytest.mark.asyncio
async def test_cleanup_preserves_unresolved_and_recent_records(repository):
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    for state in ("created", "failed", "reconciled", "pending", "unknown"):
        planned = {"name": state, "type": "FF", "buyEx": "binance", "sellEx": "okx"}
        await repository.mark_pending(planned, "alert", False)
        await repository.mark(route_key(planned), state, "old")
    await repository.db.execute("UPDATE astro_card_attempts SET updated_at=?", (old,))
    await repository.db.commit()
    # A new process/day is eligible for maintenance again.
    restarted = AstroCardAttemptRepository(repository.db)
    await restarted.mark_pending({"name": "NEW", "type": "FF", "buyEx": "binance", "sellEx": "okx"}, "manual", False)
    rows = await restarted.list_attempts()
    assert {row["pair_name"] for row in rows} == {"pending", "unknown", "NEW"}
    assert rows[0]["pair_name"] == "NEW"
    assert (await restarted.list_attempts(1, "unknown"))[0]["pair_name"] == "unknown"


def test_audit_endpoint_uses_global_password_and_filters():
    app = create_app(settings=Settings(database_url="sqlite:///:memory:", dashboard_password="test-password"),
                     start_background_workers=False)
    with TestClient(app) as client:
        repository = app.state.astro_alert_service.attempts
        planned = {"name": "BTC", "type": "FF", "buyEx": "binance", "sellEx": "okx"}
        client.portal.call(repository.mark_pending, planned, "manual", False)
        assert client.get("/api/astro/card-attempts").status_code == 401
        headers = {"X-Dashboard-Password": "test-password"}
        response = client.get("/api/astro/card-attempts?limit=1&state=pending", headers=headers)
        assert response.status_code == 200
        assert response.json()[0]["route_key"] == route_key(planned)
        assert response.json()[0]["open_enabled"] is False
        assert client.get("/api/astro/card-attempts?state=failed", headers=headers).json() == []
        assert len(client.get("/api/astro/card-attempts?state=", headers=headers).json()) == 1
        for query in ("limit=0", "limit=1001", "state=invalid"):
            assert client.get("/api/astro/card-attempts?" + query, headers=headers).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,rejected", [(400, "rejected", True), (429, "rate limited", True),
                                                (403, "", False), (503, "failure", False)])
async def test_sdk_preserves_response_evidence(status, body, rejected):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text=body)
    )) as transport:
        sdk = AstroSdkClient(AstroSdkConfig("https://astro.example", "admin", "test-only"), transport)
        with pytest.raises(AstroClientError) as caught:
            await sdk.add_pair({"name": "BTC"})
        assert caught.value.status_code == status
        assert caught.value.has_response_body == bool(body)
        assert caught.value.explicitly_rejected is rejected
