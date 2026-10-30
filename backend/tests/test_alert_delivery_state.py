import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from test_alert_engine import opportunity
from test_alert_loop import FakeFeishuNotifier, FakeRuleRepo, FakeSettingsRepo

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import AlertEventRepository
from app.db.schema import initialize_schema
from app.main import _restore_alert_delivery_state, _run_alert_loop, create_app
from app.models.alert import AlertRule
from app.services.alert_delivery_store import AlertDeliveryStore
from app.services.alert_engine import AlertEngine
from app.services.snapshot_store import SnapshotStore


@pytest.fixture
async def delivery_store(tmp_path):
    db = await connect_database(str(tmp_path / "delivery.db"))
    await initialize_schema(db)
    try:
        yield AlertDeliveryStore(db)
    finally:
        await db.close()


async def test_sent_cooldown_survives_database_reopen(tmp_path):
    path = str(tmp_path / "restart.db")
    now = datetime.now(UTC)
    rule = AlertRule(id="rule", name="restart", consecutive_hits=1, cooldown_seconds=300)
    opp = opportunity()
    engine = AlertEngine()
    match, = engine.evaluate([opp], [rule], now=now)
    engine.record_delivery_result(match, "sent", now)
    key = f"{rule.id}:{opp.id}"
    state = engine.export_state(key)
    db = await connect_database(path)
    try:
        await initialize_schema(db)
        store = AlertDeliveryStore(db)
        await store.mark_pending(key, now)
        await store.record(key, state.last_sent_at, state.retry_after, state.retry_attempts)
        # A second connection observes writes without any extra commit from the caller.
        reader = await connect_database(path)
        try:
            assert (await AlertDeliveryStore(reader).load_all())[0].pending_since is None
        finally:
            await reader.close()
    finally:
        await db.close()
    db = await connect_database(path)
    try:
        await initialize_schema(db)  # Repeated initialization preserves existing state.
        restarted = AlertEngine()
        assert restarted.restore(await AlertDeliveryStore(db).load_all(), now) == []
        assert restarted.evaluate([opp], [rule], now=now + timedelta(seconds=299)) == []
        assert len(restarted.evaluate([opp], [rule], now=now + timedelta(seconds=300))) == 1
    finally:
        await db.close()


@pytest.mark.parametrize("status", ["failed", "muted"])
async def test_retry_deadline_and_attempt_count_survive_restart(delivery_store, status):
    now = datetime.now(UTC)
    rule = AlertRule(id="rule", name="retry", consecutive_hits=1, cooldown_seconds=600)
    opp = opportunity()
    engine = AlertEngine()
    match, = engine.evaluate([opp], [rule], now=now)
    engine.record_delivery_result(match, status, now)
    key = f"{rule.id}:{opp.id}"
    state = engine.export_state(key)
    await delivery_store.record(key, state.last_sent_at, state.retry_after, state.retry_attempts)
    restarted = AlertEngine()
    assert restarted.restore(await delivery_store.load_all(), now) == []
    assert restarted.export_state(key) == state
    assert restarted.evaluate([opp], [rule], now=now + timedelta(seconds=59)) == []
    match, = restarted.evaluate([opp], [rule], now=now + timedelta(seconds=60))
    restarted.record_delivery_result(match, status, now + timedelta(seconds=60))
    state = restarted.export_state(key)
    assert state.retry_attempts == 2
    assert state.retry_after == now + timedelta(seconds=180)
    assert state.last_sent_at is None


async def test_pending_crash_is_recovered_by_real_app_startup_once(tmp_path):
    path = str(tmp_path / "startup.db")
    now = datetime.now(UTC)
    rule = AlertRule(id="rule", name="pending", consecutive_hits=3, cooldown_seconds=300)
    # Preserve an opaque opportunity ID, including its venue/DEX separators.
    opp = opportunity().model_copy(update={"id": "FF:BTC:hyperliquid:xyz:okx"})
    key = f"{rule.id}:{opp.id}"
    db = await connect_database(path)
    try:
        await initialize_schema(db)
        await AlertDeliveryStore(db).mark_pending(key, now)
    finally:
        await db.close()

    for _ in range(2):
        app = create_app(settings=Settings(database_url=f"sqlite:///{path}"),
                         start_background_workers=False)
        async with app.router.lifespan_context(app):
            engine = app.state.alert_engine
            assert engine._hits == {}
            assert engine._observations == {}
            for seconds in (1, 2, 3, 299):
                assert engine.evaluate([opp], [rule], now=now + timedelta(seconds=seconds)) == []
            assert len(engine.evaluate([opp], [rule], now=now + timedelta(seconds=300))) == 1
            events = await app.state.alert_event_repo.list()
            assert len(events) == 1
            assert events[0].status == "unknown"
            assert events[0].rule_id == rule.id
            assert events[0].opportunity_id == opp.id
            assert "上次发送过程中进程退出，结果未知，按已发送处理，不重发" in events[0].message
            await app.state.alert_rule_repo.upsert(rule)
            # Even with a matching rule, the API must preserve the recovery message.
            history = AsyncMock()
            app.state.history_repo = history
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost",
            ) as client:
                response = await client.get("/api/alerts/events")
            assert response.status_code == 200
            assert response.json()[0]["status"] == "unknown"
            assert response.json()[0]["message"] == events[0].message
            history.list_before.assert_not_awaited()
            state, = await app.state.alert_delivery_store.load_all()
            assert state.pending_since is None
            assert state.last_sent_at == now


async def test_restore_restarts_hit_and_observation_accumulation(delivery_store):
    now = datetime.now(UTC)
    rule = AlertRule(id="rule", name="hits", consecutive_hits=3, cooldown_seconds=1)
    opp = opportunity()
    key = f"{rule.id}:{opp.id}"
    engine = AlertEngine()
    for _ in range(3):
        engine.evaluate([opp], [rule], now=now)
    await delivery_store.mark_pending(key, now)
    assert engine.restore(await delivery_store.load_all(), now) == [key]
    assert engine._hits == {}
    assert engine._observations == {}
    assert engine.evaluate([opp], [rule], now=now + timedelta(seconds=2)) == []
    assert engine.evaluate([opp], [rule], now=now + timedelta(seconds=3)) == []
    match, = engine.evaluate([opp], [rule], now=now + timedelta(seconds=4))
    assert len(match.observations) == 3


def recovery_app(store):
    app = FastAPI()
    app.state.alert_delivery_store = store
    app.state.alert_engine = AlertEngine()
    app.state.alert_event_repo = AlertEventRepository(store.db)
    return app


@pytest.mark.parametrize("failure", ["event", "record"])
async def test_recovery_write_failure_is_retryable_without_duplicate_event(delivery_store, failure, caplog):
    now = datetime.now(UTC)
    await delivery_store.mark_pending("rule:opp", now)
    await delivery_store.mark_pending("rule:other", now)
    app = recovery_app(delivery_store)
    target = app.state.alert_event_repo if failure == "event" else delivery_store
    method = "create" if failure == "event" else "record"
    original = getattr(target, method)
    calls = 0

    async def fail_first(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected recovery write failure")
        return await original(*args)

    setattr(target, method, fail_first)
    await _restore_alert_delivery_state(app)
    assert "injected recovery write failure" in caplog.text
    states = {state.key: state for state in await delivery_store.load_all()}
    assert states["rule:opp"].pending_since == now
    assert states["rule:other"].pending_since is None
    setattr(target, method, original)
    await _restore_alert_delivery_state(app)
    assert len(await app.state.alert_event_repo.list()) == 2
    assert all(state.pending_since is None for state in await delivery_store.load_all())


async def test_restore_load_failure_does_not_silently_start_empty(delivery_store):
    app = recovery_app(delivery_store)
    delivery_store.load_all = AsyncMock(side_effect=RuntimeError("load failed"))
    with pytest.raises(RuntimeError, match="load failed"):
        await _restore_alert_delivery_state(app)


async def test_prune_keeps_pending_and_boundary_and_recent_rows(delivery_store):
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    for key in ("expired", "boundary", "recent", "pending"):
        await delivery_store.record(key, now, None, 0)
    await delivery_store.mark_pending("pending", now - timedelta(days=10))
    await delivery_store.db.execute(
        "UPDATE alert_delivery_state SET updated_at = ? WHERE key = 'expired'",
        ((cutoff - timedelta(seconds=1)).isoformat(),),
    )
    await delivery_store.db.execute(
        "UPDATE alert_delivery_state SET updated_at = ? WHERE key = 'boundary'",
        (cutoff.isoformat(),),
    )
    await delivery_store.db.commit()
    await delivery_store.prune(cutoff)
    assert {state.key for state in await delivery_store.load_all()} == {"boundary", "recent", "pending"}


async def test_mark_pending_preserves_previous_state_until_record(delivery_store):
    now = datetime.now(UTC)
    await delivery_store.record("key", now, now + timedelta(seconds=60), 2)
    await delivery_store.mark_pending("key", now + timedelta(seconds=61))
    state, = await delivery_store.load_all()
    assert state.last_sent_at == now
    assert state.retry_after == now + timedelta(seconds=60)
    assert state.retry_attempts == 2
    assert state.pending_since == now + timedelta(seconds=61)
    await delivery_store.record("key", now + timedelta(seconds=62), None, 0)
    state, = await delivery_store.load_all()
    assert state.pending_since is None
    assert state.retry_after is None
    assert state.retry_attempts == 0


def loop_app(store, stop):
    app = recovery_app(store)
    rule = AlertRule(id="rule", name="loop", consecutive_hits=1)
    opportunities = [opportunity().model_copy(update={"id": f"opp-{i}"}) for i in (1, 2)]
    snapshots = SnapshotStore()
    snapshots.set_opportunities(opportunities)
    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.snapshot_store = snapshots
    app.state.settings_repo = FakeSettingsRepo()
    app.state.feishu_live_send_enabled = True
    app.state.feishu_notifier = FakeFeishuNotifier()
    repo = app.state.alert_event_repo
    original_create = repo.create

    async def create(event):
        result = await original_create(event)
        if event.opportunity_id == "opp-2":
            stop.set()
        return result

    repo.create = create
    return app


@pytest.mark.parametrize("failure", ["event", "pending", "record", "prune"])
async def test_loop_continues_after_individual_database_failure(delivery_store, failure, caplog):
    stop = asyncio.Event()
    app = loop_app(delivery_store, stop)
    target = app.state.alert_event_repo if failure == "event" else delivery_store
    method = {"event": "create", "pending": "mark_pending"}.get(failure, failure)
    original = getattr(target, method)
    calls = 0

    async def fail_first(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(f"injected {failure} failure")
        return await original(*args)

    setattr(target, method, fail_first)
    notifier = app.state.feishu_notifier
    original_send = notifier.send_alert

    async def check_pending(rule, opp, **kwargs):
        states = {state.key: state for state in await delivery_store.load_all()}
        assert states[f"{rule.id}:{opp.id}"].pending_since is not None
        await original_send(rule, opp, **kwargs)

    notifier.send_alert = check_pending
    await asyncio.wait_for(_run_alert_loop(app, 60, stop), timeout=2)
    assert f"injected {failure} failure" in caplog.text
    assert "alert loop failed" not in caplog.text
    states = {state.key: state for state in await delivery_store.load_all()}
    assert states["rule:opp-2"].last_sent_at is not None
    assert states["rule:opp-2"].pending_since is None
    events = await app.state.alert_event_repo.list()
    assert any(event.opportunity_id == "opp-2" and event.status == "sent" for event in events)
    assert len(notifier.sent_texts) == (1 if failure == "pending" else 2)
    if failure == "pending":
        assert "rule:opp-1" not in states
    elif failure == "record":
        assert states["rule:opp-1"].pending_since is not None
    else:
        assert states["rule:opp-1"].last_sent_at is not None
        assert states["rule:opp-1"].pending_since is None


@pytest.mark.parametrize("status", ["failed", "muted"])
async def test_loop_persists_failed_and_muted_backoff(delivery_store, status):
    stop = asyncio.Event()
    app = loop_app(delivery_store, stop)
    if status == "muted":
        app.state.feishu_live_send_enabled = False
    else:
        app.state.feishu_notifier.send_alert = AsyncMock(side_effect=RuntimeError("offline failure"))
    await asyncio.wait_for(_run_alert_loop(app, 60, stop), timeout=2)
    states = await delivery_store.load_all()
    assert len(states) == 2
    for state in states:
        assert state.last_sent_at is None
        assert state.retry_attempts == 1
        assert state.retry_after > datetime.now(UTC)
        assert state.pending_since is None
    assert {event.status for event in await app.state.alert_event_repo.list()} == {status}


async def test_loop_prunes_once_per_day_and_keeps_long_cooldowns(delivery_store):
    stop = asyncio.Event()
    app = loop_app(delivery_store, stop)
    app.state.snapshot_store.set_opportunities([])
    rule = AlertRule(name="long cooldown", cooldown_seconds=10 * 86400)
    calls = 0

    async def rules():
        nonlocal calls
        calls += 1
        if calls == 3:
            stop.set()
        return [rule]

    app.state.alert_rule_repo.list = rules
    delivery_store.prune = AsyncMock(wraps=delivery_store.prune)
    before = datetime.now(UTC)
    await asyncio.wait_for(_run_alert_loop(app, 0.001, stop), timeout=2)
    after = datetime.now(UTC)
    delivery_store.prune.assert_awaited_once()
    cutoff, = delivery_store.prune.await_args.args
    assert before - timedelta(days=11) <= cutoff <= after - timedelta(days=11)
