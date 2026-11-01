import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

import app.main as main
from app.core import config
from app.models.alert import AlertRule
from app.models.settings import AlertMessageTemplateSettings, LivePilotSettings
from app.services.alert_delivery_store import AlertDeliveryStore
from app.services.alert_engine import AlertMatch
from app.services.opportunity_trade_availability import OpportunityTradeAvailabilityReport
from app.services.snapshot_store import SnapshotStore
from test_alert_loop import (
    AllMatchesAlertEngine,
    FakeAstroAlertService,
    FakeEventRepo,
    FakeFeishuNotifier,
    FakeRuleRepo,
    FakeSettingsRepo,
    opportunity,
)


def isolation_app(*, count=2, concurrency=3, live_pilot=False):
    stop = asyncio.Event()
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        alert_match_concurrency=concurrency,
        alert_diagnostic_timeout_seconds=20,
        alert_orderbook_timeout_seconds=15,
        dashboard_password="timing-password-sentinel",
        astro_api_key="timing-api-key-sentinel",
        feishu_webhook_url="timing-webhook-sentinel",
    )
    rule = AlertRule(
        id="isolation-rule", name="isolation", types=["FF"],
        min_open_spread_pct=0.5, min_fee_adjusted_open_pct=0.25,
        consecutive_hits=1,
    )
    items = [opportunity().model_copy(update={
        "id": f"opp-{i}", "symbol": f"COIN{i}USDT",
        "funding_rate_buy_pct": 0.0,
        "funding_rate_sell_pct": 0.02 + i * 0.01,
        "net_funding_pct": 0.02 + i * 0.01,
    }) for i in range(count)]
    store = SnapshotStore()
    store.set_opportunities(items)
    app.state.snapshot_store = store
    app.state.alert_rule_repo = FakeRuleRepo([rule])
    app.state.alert_event_repo = FakeEventRepo(stop)
    app.state.alert_delivery_store = AsyncMock(spec=AlertDeliveryStore)
    app.state.settings_repo = FakeSettingsRepo(live_pilot_settings=LivePilotSettings(
        enabled=live_pilot, max_symbols=1, min_next_funding_edge_pct=0.01,
    ))
    app.state.alert_engine = AllMatchesAlertEngine(rule)
    app.state.feishu_notifier = FakeFeishuNotifier()
    app.state.astro_alert_service = FakeAstroAlertService()
    return app, stop


@pytest.fixture(autouse=True)
def capture_alert_timing():
    # Production keeps this logger off the root handlers; let caplog see it here.
    timing_logger = logging.getLogger("alert_timing")
    assert timing_logger.level == logging.INFO
    assert timing_logger.propagate is False
    assert timing_logger.handlers
    timing_logger.propagate = True
    try:
        yield
    finally:
        timing_logger.propagate = False


def timing_rows(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records
            if record.getMessage().startswith('{"alert_timing":')]


def same_symbol_rule_matches(app, monkeypatch):
    first, other = app.state.snapshot_store.get_opportunities()
    rule = app.state.alert_rule_repo.rules[0]
    pilot_rule = rule.model_copy(update={"id": "pilot-rule"})
    pilot = AlertMatch(pilot_rule, first, [])
    normal = AlertMatch(rule, first, [])
    unrelated = AlertMatch(rule, other, [])
    app.state.alert_engine.evaluate = Mock(return_value=[unrelated, normal, pilot])
    monkeypatch.setattr(main, "select_live_pilot_matches", lambda *args: [pilot])
    return pilot, unrelated, normal


@pytest.mark.asyncio
async def test_same_symbol_waits_for_live_pilot_while_other_symbol_completes(monkeypatch):
    app, stop = isolation_app(live_pilot=True)
    pilot, unrelated, normal = same_symbol_rule_matches(app, monkeypatch)
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    started = []
    original_process = main._process_alert_match

    async def process(app, match, *args):
        started.append(match)
        result = await original_process(app, match, *args)
        if match is unrelated:
            completed.set()
        return result

    original_pilot = app.state.astro_alert_service.handle_live_pilot

    async def handle_pilot(item):
        entered.set()
        await release.wait()
        return await original_pilot(item)

    monkeypatch.setattr(main, "_process_alert_match", process)
    app.state.astro_alert_service.handle_live_pilot = handle_pilot
    task = asyncio.create_task(main._run_alert_loop(app, 60, stop))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(completed.wait(), 1)
        assert started == [pilot, unrelated]
        assert [e.opportunity_id for e in app.state.alert_event_repo.events] == ["opp-1"]
        assert not task.done()
        release.set()
        await asyncio.wait_for(task, 1)
        assert started == [pilot, unrelated, normal]
        assert [e.rule_id for e in app.state.alert_event_repo.events
                if e.opportunity_id == "opp-0"] == ["pilot-rule", "isolation-rule"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_serial_concurrency_preserves_interleaved_symbol_order(monkeypatch):
    app, stop = isolation_app(concurrency=1, live_pilot=True)
    ordered = same_symbol_rule_matches(app, monkeypatch)
    await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    assert [(e.rule_id, e.opportunity_id) for e in app.state.alert_event_repo.events] == [
        (m.rule.id, m.opportunity.id) for m in ordered
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,status,first_calls", [
    ("pending", "pending_write_failed", ["pending"]),
    ("record", "state_write_failed", ["pending", "send", "record", "event"]),
    ("event", "event_write_failed", ["pending", "send", "record", "event"]),
])
async def test_write_failure_marks_round_unhealthy_and_continues_same_symbol(
    monkeypatch, caplog, failure, status, first_calls,
):
    app, stop = isolation_app()
    items = app.state.snapshot_store.get_opportunities()
    app.state.snapshot_store.set_opportunities([
        item.model_copy(update={"symbol": "SAMEUSDT"}) for item in items
    ])
    calls = []

    def record_call(stage, key):
        calls.append((stage, key))
        if stage == failure and key == "isolation-rule:opp-0":
            raise RuntimeError(f"{stage} write failed")

    async def pending(key, *args):
        record_call("pending", key)

    async def record(key, *args):
        record_call("record", key)

    original_create = app.state.alert_event_repo.create

    async def create(event):
        record_call("event", f"{event.rule_id}:{event.opportunity_id}")
        return await original_create(event)

    original_send = app.state.feishu_notifier.send_alert

    async def send(rule, item, **kwargs):
        record_call("send", f"{rule.id}:{item.id}")
        return await original_send(rule, item, **kwargs)

    app.state.alert_delivery_store.mark_pending.side_effect = pending
    app.state.alert_delivery_store.record.side_effect = record
    app.state.alert_event_repo.create = create
    app.state.feishu_notifier.send_alert = send
    heartbeat = Mock()
    monkeypatch.setattr(main, "beat", heartbeat)
    with caplog.at_level(logging.INFO, logger="alert_timing"):
        await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    heartbeat.assert_called_once_with("alert-loop", ok=False)
    rows = timing_rows(caplog)
    assert len(rows) == 2
    assert {row["key"]: row["status"] for row in rows} == {
        "isolation-rule:opp-0": status, "isolation-rule:opp-1": "sent",
    }
    assert calls == [
        *[(stage, "isolation-rule:opp-0") for stage in first_calls],
        *[(stage, "isolation-rule:opp-1") for stage in ["pending", "send", "record", "event"]],
    ]
    events = app.state.alert_event_repo.events
    assert [e.opportunity_id for e in events] == (
        ["opp-0", "opp-1"] if failure == "record" else ["opp-1"]
    )
    assert all(e.status == "sent" for e in events)
    assert app.state.alert_engine.delivery_statuses == (
        ["sent"] if failure == "pending" else ["sent", "sent"]
    )
    assert len(app.state.feishu_notifier.sent_texts) == (1 if failure == "pending" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["diag", "orderbook"])
async def test_slow_match_does_not_block_other_match(monkeypatch, stage):
    app, stop = isolation_app()
    blocked, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    app.state.trade_availability_service = object()

    async def stage_call(_service, item, *args, **kwargs):
        if item.id == "opp-0":
            blocked.set()
            await release.wait()
        if stage == "diag":
            return OpportunityTradeAvailabilityReport(text="diagnostic ok", opening_restricted=False)
        return None

    target = ("build_opportunity_trade_availability_report" if stage == "diag"
              else "_order_book_validation_failure")
    if stage == "orderbook":
        del app.state.trade_availability_service
    monkeypatch.setattr(main, target, stage_call)
    original_create = app.state.alert_event_repo.create

    async def create(event):
        result = await original_create(event)
        if event.opportunity_id == "opp-1":
            completed.set()
        return result

    app.state.alert_event_repo.create = create
    task = asyncio.create_task(main._run_alert_loop(app, 60, stop))
    try:
        await asyncio.wait_for(blocked.wait(), 1)
        await asyncio.wait_for(completed.wait(), 1)
        assert not release.is_set()
        assert not task.done()  # The round still owns the slow match, even after stop.
        assert [e.opportunity_id for e in app.state.alert_event_repo.events] == ["opp-1"]
        release.set()
        await asyncio.wait_for(task, 1)
        assert len(app.state.alert_event_repo.events) == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_diagnostic_timeout_delivers_restricted_rating(monkeypatch, caplog):
    app, stop = isolation_app(count=1)
    app.state.settings.alert_diagnostic_timeout_seconds = 0.01
    app.state.trade_availability_service = object()
    async def hang(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "build_opportunity_trade_availability_report", hang)
    with caplog.at_level(logging.INFO, logger="alert_timing"):
        await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    event = app.state.alert_event_repo.events[0]
    assert event.status == "sent"
    assert "诊断：未知（超时）" in event.message
    assert event.message.startswith("【需评估】")
    assert "最新信号或建卡校验未通过" in event.message
    assert app.state.feishu_notifier.sent_texts == [event.message]
    assert timing_rows(caplog)[0]["timeouts"] == ["diag"]


@pytest.mark.asyncio
@pytest.mark.parametrize("live_pilot", [False, True])
@pytest.mark.parametrize("compact", [False, True])
async def test_orderbook_timeout_skips_all_card_creation(monkeypatch, live_pilot, compact, caplog):
    app, stop = isolation_app(count=1, live_pilot=live_pilot)
    app.state.settings.alert_orderbook_timeout_seconds = 0.01
    app.state.settings_repo.alert_template = AlertMessageTemplateSettings(
        format="compact" if compact else "detailed", suppress_when_card_conditions_fail=True,
    )

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "_order_book_validation_failure", hang)
    with caplog.at_level(logging.INFO, logger="alert_timing"):
        await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    event = app.state.alert_event_repo.events[0]
    assert event.status == "muted"
    assert not app.state.astro_alert_service.calls
    assert not app.state.astro_alert_service.live_pilot_calls
    if compact:
        assert "卡片：未创建（订单簿校验超时）" in event.message
    else:
        assert "Astro: 订单簿校验超时" in event.message
    row = timing_rows(caplog)[0]
    assert row["timeouts"] == ["orderbook"]
    assert row["astro_ms"] is None
    assert row["feishu_ms"] is None


@pytest.mark.asyncio
async def test_unhandled_match_exception_isolated_and_heartbeat_unhealthy(monkeypatch, caplog):
    app, stop = isolation_app()
    original_find = main._find_latest_opportunity

    def find(app, key):
        if key == "opp-0":
            raise RuntimeError("isolated match error")
        return original_find(app, key)

    monkeypatch.setattr(main, "_find_latest_opportunity", find)
    heartbeat = Mock()
    monkeypatch.setattr(main, "beat", heartbeat)
    with caplog.at_level(logging.INFO, logger="alert_timing"):
        await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    assert [e.opportunity_id for e in app.state.alert_event_repo.events] == ["opp-1"]
    heartbeat.assert_called_once_with("alert-loop", ok=False)
    assert "alert match processing failed: isolation-rule:opp-0" in caplog.text
    assert {r["key"]: r["status"] for r in timing_rows(caplog)} == {
        "isolation-rule:opp-0": "failed", "isolation-rule:opp-1": "sent",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("child_cancel", [False, True])
async def test_cancel_propagates_and_drains_inflight_matches(monkeypatch, child_cancel):
    app, stop = isolation_app()
    app.state.trade_availability_service = object()
    both_started = asyncio.Event()
    active = set()

    async def report(_service, item):
        active.add(item.id)
        if len(active) == 2:
            both_started.set()
        try:
            await both_started.wait()
            if child_cancel and item.id == "opp-0":
                raise asyncio.CancelledError
            await asyncio.Event().wait()
        finally:
            active.remove(item.id)

    monkeypatch.setattr(main, "build_opportunity_trade_availability_report", report)
    task = asyncio.create_task(main._run_alert_loop(app, 60, stop))
    try:
        await asyncio.wait_for(both_started.wait(), 1)
        if not child_cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert not active
        assert not app.state.alert_event_repo.events
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 3])
async def test_live_pilot_starts_first_and_success_heartbeat(monkeypatch, concurrency):
    app, stop = isolation_app(concurrency=concurrency, live_pilot=True)
    started = []
    original_find = main._find_latest_opportunity

    def find(app, key):
        started.append(key)
        return original_find(app, key)

    monkeypatch.setattr(main, "_find_latest_opportunity", find)
    heartbeat = Mock()
    monkeypatch.setattr(main, "beat", heartbeat)
    await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    assert started == ["opp-1", "opp-0"]
    assert app.state.astro_alert_service.live_pilot_calls == ["opp-1"]
    if concurrency == 1:
        assert [e.opportunity_id for e in app.state.alert_event_repo.events] == started
    heartbeat.assert_called_once_with("alert-loop", ok=True)


@pytest.mark.asyncio
async def test_concurrency_bounded_and_round_does_not_overlap(monkeypatch):
    app, stop = isolation_app(count=6)
    app.state.trade_availability_service = object()
    release, full = asyncio.Event(), asyncio.Event()
    active = 0
    peak = 0
    started = []
    evaluate = Mock(wraps=app.state.alert_engine.evaluate)
    app.state.alert_engine.evaluate = evaluate

    async def report(_service, item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.append(item.id)
        if active == 3:
            full.set()
        try:
            await release.wait()
            return OpportunityTradeAvailabilityReport(text="ok", opening_restricted=False)
        finally:
            active -= 1

    monkeypatch.setattr(main, "build_opportunity_trade_availability_report", report)
    task = asyncio.create_task(main._run_alert_loop(app, 0.001, stop))
    try:
        await asyncio.wait_for(full.wait(), 1)
        await asyncio.sleep(0.02)
        assert started == ["opp-0", "opp-1", "opp-2"]
        assert evaluate.call_count == 1
        release.set()
        await asyncio.wait_for(task, 1)
        assert peak == 3
        assert len(app.state.alert_event_repo.events) == 6
        assert evaluate.call_count == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_latest_validation_uses_time_after_diagnostic(monkeypatch):
    app, stop = isolation_app(count=1)
    app.state.trade_availability_service = object()
    initial = datetime.now(UTC)
    current = initial
    # Cross the strict expiry boundary even if both real clock reads are identical.
    refreshed = initial + timedelta(
        seconds=app.state.alert_rule_repo.rules[0].max_data_age_seconds + 1,
    )

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current

    async def report(*args):
        nonlocal current
        current = refreshed
        return OpportunityTradeAvailabilityReport(text="ok", opening_restricted=False)

    monkeypatch.setattr(main, "datetime", Clock)
    monkeypatch.setattr(main, "build_opportunity_trade_availability_report", report)
    validate = Mock(wraps=main._latest_signal_validation_failure)
    monkeypatch.setattr(main, "_latest_signal_validation_failure", validate)
    evaluate = Mock(wraps=app.state.alert_engine.evaluate)
    app.state.alert_engine.evaluate = evaluate
    await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    assert evaluate.call_args.kwargs["now"] == initial
    assert validate.call_args.args[3] == refreshed
    assert "最新行情数据已过期" in app.state.alert_event_repo.events[0].message
    assert app.state.alert_event_repo.events[0].status == "muted"
    assert not app.state.astro_alert_service.calls


@pytest.mark.asyncio
async def test_all_three_database_writes_share_lock_but_external_calls_do_not(monkeypatch):
    app, stop = isolation_app(count=5)
    active = []
    writes = []

    async def write(kind, key):
        assert not active, f"overlapping writes: {active} and {kind}:{key}"
        active.append((kind, key))
        writes.append(("enter", kind, key))
        await asyncio.sleep(0)
        writes.append(("exit", kind, key))
        active.pop()

    async def pending(key, *args):
        await write("pending", key)

    async def record(key, *args):
        await write("record", key)

    original_create = app.state.alert_event_repo.create

    async def create(event):
        await write("event", f"isolation-rule:{event.opportunity_id}")
        return await original_create(event)

    app.state.alert_delivery_store.mark_pending.side_effect = pending
    app.state.alert_delivery_store.record.side_effect = record
    app.state.alert_event_repo.create = create
    # Holding all three notifier calls until they start proves send is outside the write lock.
    three_sends = asyncio.Event()
    sending = 0

    async def send(*args, **kwargs):
        nonlocal sending
        sending += 1
        if sending == 3:
            three_sends.set()
        await three_sends.wait()

    app.state.feishu_notifier.send_alert = send
    await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    assert len(app.state.alert_event_repo.events) == 5
    assert len(writes) == 30
    for i in range(0, len(writes), 2):
        assert writes[i][0] == "enter"
        assert writes[i + 1] == ("exit", *writes[i][1:])
    for i in range(5):
        assert [kind for action, kind, key in writes
                if action == "enter" and key == f"isolation-rule:opp-{i}"] == [
                    "pending", "record", "event",
                ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["astro", "feishu"])
async def test_mutating_stages_have_no_outer_deadline(monkeypatch, stage):
    app, stop = isolation_app(count=1)
    app.state.settings.alert_diagnostic_timeout_seconds = 0.001
    app.state.settings.alert_orderbook_timeout_seconds = 0.001
    entered, release = asyncio.Event(), asyncio.Event()
    service = app.state.astro_alert_service if stage == "astro" else app.state.feishu_notifier
    name = "handle_alert" if stage == "astro" else "send_alert"
    original = getattr(service, name)

    async def call(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, name, call)
    task = asyncio.create_task(main._run_alert_loop(app, 60, stop))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.03)
        assert not task.done()
        assert not app.state.alert_event_repo.events
        release.set()
        await asyncio.wait_for(task, 1)
        assert app.state.alert_event_repo.events[0].status == "sent"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_one_safe_timing_json_per_match_with_null_unexecuted_stages(caplog):
    app, stop = isolation_app()
    with caplog.at_level(logging.INFO, logger="alert_timing"):
        await asyncio.wait_for(main._run_alert_loop(app, 60, stop), 1)
    rows = timing_rows(caplog)
    assert len(rows) == 2
    assert {row["key"] for row in rows} == {"isolation-rule:opp-0", "isolation-rule:opp-1"}
    for row in rows:
        assert set(row) == {"alert_timing", "key", "symbol", "status", "diag_ms",
                            "orderbook_ms", "astro_ms", "feishu_ms", "total_ms", "timeouts"}
        assert row["alert_timing"] == 1
        assert row["status"] == "sent"
        assert row["diag_ms"] is None
        assert row["timeouts"] == []
        for stage in ("orderbook", "astro", "feishu"):
            assert 0 <= row[f"{stage}_ms"] <= row["total_ms"]
    assert "timing-password-sentinel" not in caplog.text
    assert "timing-api-key-sentinel" not in caplog.text
    assert "timing-webhook-sentinel" not in caplog.text


@pytest.mark.parametrize("field,env_name,default,upper", [
    ("alert_match_concurrency", "ALERT_MATCH_CONCURRENCY", 3, 8),
    ("alert_diagnostic_timeout_seconds", "ALERT_DIAGNOSTIC_TIMEOUT_SECONDS", 20, 120),
    ("alert_orderbook_timeout_seconds", "ALERT_ORDERBOOK_TIMEOUT_SECONDS", 15, 120),
])
def test_alert_isolation_settings_defaults_boundaries_and_invalid_fallback(
    monkeypatch, field, env_name, default, upper,
):
    monkeypatch.setattr(config, "find_dotenv", lambda **kwargs: "")
    monkeypatch.delenv(env_name, raising=False)
    try:
        config.get_settings.cache_clear()
        assert getattr(config.Settings(), field) == default
        assert getattr(config.get_settings(), field) == default
        for raw, expected in [("1", 1), (str(upper), upper), ("0", default),
                              (str(upper + 1), default), ("-1", default),
                              ("bad", default), ("", default), ("nan", default),
                              ("inf", default), ("-inf", default)]:
            monkeypatch.setenv(env_name, raw)
            config.get_settings.cache_clear()
            assert getattr(config.get_settings(), field) == expected
        if field == "alert_match_concurrency":
            monkeypatch.setenv(env_name, "1.5")
            config.get_settings.cache_clear()
            assert getattr(config.get_settings(), field) == default
        else:
            monkeypatch.setenv(env_name, "1.5")
            config.get_settings.cache_clear()
            assert getattr(config.get_settings(), field) == 1.5
    finally:
        config.get_settings.cache_clear()
