import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from test_mobile_api import mobile_app  # noqa: F401 - production-auth app fixture
from test_mobile_pins import login

from app.db.repositories import SettingsRepository
from app.models.mobile import EQN_NEWS_CHANNELS, EqnNewsSettings
from app.services import eqn_news
from app.services.eqn_news import EqnNewsMonitor
from app.services.feishu import FeishuConfig, FeishuNotifier


PATH = "/api/mobile/eqn-news/settings"


async def test_eqn_settings_require_session_and_accept_mobile_without_unlock(mobile_app):
    app, client = mobile_app
    assert (await client.get(PATH)).status_code == 401
    assert (await client.post(PATH, json={"channels": []})).status_code == 401
    await login(client, "mobile_readonly", int(time.time() // 30))
    expected = [{"key": key, "name_zh": name} for key, name in EQN_NEWS_CHANNELS.items()]
    assert len(expected) == 10
    assert (await client.get(PATH)).json() == {"channels": [], "available": expected}
    response = await client.post(PATH, json={"channels": ["oi", "main", "oi"]})
    assert response.json() == {"channels": ["oi", "main"]}
    for payload in ({}, {"channels": "main"}, {"channels": [1]}, {"channels": ["oi", "alpha"]},
                    {"channels": ["Main"]}, {"channels": [], "extra": True}):
        assert (await client.post(PATH, json=payload)).status_code == 422
    assert (await client.get(PATH)).json() == {"channels": ["oi", "main"], "available": expected}
    assert await SettingsRepository(app.state.settings_repo.db).get_eqn_news_cursors() == {
        "oi": None, "main": None,
    }


def article(no, *channels, zh=None):
    return {"no": no, "channels": list(channels), "zh": zh or str(no), "url": f"https://example.org/{no}"}


def page(*items, tag="v1"):
    return httpx.Response(200, json={"items": items}, headers={"ETag": tag})


@asynccontextmanager
async def feed(*responses):
    requests = []
    pending = deque(responses)

    def dispatch(request):
        requests.append(request)
        assert str(request.url) == "https://api.eqn.news/public/v1/news?channel=all&limit=50"
        assert request.method == "GET" and request.headers["User-Agent"] == "taoli1/1.0"
        return pending.popleft()

    async with httpx.AsyncClient(transport=httpx.MockTransport(dispatch)) as client:
        yield client, requests
    assert not pending


async def subscribe(repo, *channels):
    await repo.set_eqn_news_settings(EqnNewsSettings(channels=list(channels)))


async def drain(monitor):
    worker = asyncio.create_task(monitor._deliver_pending())
    try:
        await asyncio.wait_for(monitor._pending.join(), 2)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_eqn_poll_filters_baselines_restarts_and_formats(mobile_app):
    app, _ = mobile_app
    repo = app.state.settings_repo
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.record_mobile_text = AsyncMock()
    monitor = EqnNewsMonitor(repo, notifier)
    first = article(1, "main")
    included = article(3, "main", "oi", zh="文" * 501) | {"source_url": "https://example.org/source"}
    excluded = [article(i, "main", key) for i, key in enumerate(("alpha", "binance", "tier2", "tier3", "korea"), 4)]
    async with feed(
        page(first), page(first, article(2, "elon"), included, included, *excluded),
        page(article(9, "oi"), article(10, "main"), tag="v2"),
        page(article(9, "oi"), article(10, "main"), article(11, "oi", "main")),
        page(article(12, "main")),
    ) as (client, requests):
        await monitor.poll_once(client)
        assert not requests
        await subscribe(repo, "main")
        await monitor.poll_once(client)
        await drain(monitor)
        notifier.record_mobile_text.assert_not_awaited()
        await monitor.poll_once(client)
        await drain(monitor)
        notifier.record_mobile_text.assert_awaited_once_with("【方程式·方程式新闻】" + "文" * 500 + "\nhttps://example.org/source")
        await subscribe(repo, "oi", "main")
        await monitor.poll_once(client)
        await drain(monitor)
        assert "If-None-Match" not in requests[-1].headers
        restarted = EqnNewsMonitor(SettingsRepository(repo.db), notifier)
        await restarted.poll_once(client)
        await drain(restarted)
        # Disable then re-enable between polls, including before a restart.
        await subscribe(repo)
        await subscribe(repo, "main")
        await restarted.poll_once(client)
        await drain(restarted)
        assert [call.args[0] for call in notifier.record_mobile_text.await_args_list[1:]] == [
            "【方程式·方程式新闻】10\nhttps://example.org/10", "【方程式·OI价格异动】11\nhttps://example.org/11",
        ]


async def test_eqn_limit_etag_and_retry_after(mobile_app, caplog):
    app, _ = mobile_app
    repo = app.state.settings_repo
    await subscribe(repo, "hl")
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.record_mobile_text = AsyncMock()
    monitor = EqnNewsMonitor(repo, notifier)
    batch = page(*(article(i, "hl") for i in range(25, 0, -1)), tag="v2")
    async with feed(page(), httpx.Response(304), httpx.Response(429, headers={"Retry-After": "73"}),
                    batch, batch) as (client, requests):
        for delay in [None, None, 73, None, None]:
            assert await monitor.poll_once(client) == delay
        assert [r.headers.get("If-None-Match") for r in requests] == [None, "v1", "v1", "v1", "v2"]
    await drain(monitor)
    assert [call.args[0].splitlines()[0] for call in notifier.record_mobile_text.await_args_list] == [
        f"【方程式·Hyperliquid】{i}" for i in range(6, 26)
    ]
    assert "丢弃较早的 5 条" in caplog.text


async def test_eqn_inflight_response_cannot_overwrite_unsubscribe(mobile_app):
    app, _ = mobile_app
    repo = app.state.settings_repo
    await subscribe(repo, "main")
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.record_mobile_text = AsyncMock()
    monitor = EqnNewsMonitor(repo, notifier)
    async with feed(page(article(1, "main"))) as (client, _):
        await monitor.poll_once(client)

    async def unsubscribe_while_fetching(request):
        await subscribe(repo, "oi")
        return page(article(2, "main"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(unsubscribe_while_fetching)) as client:
        await monitor.poll_once(client)
    await drain(monitor)
    notifier.record_mobile_text.assert_not_awaited()
    assert await repo.get_eqn_news_cursors() == {"oi": None}


@pytest.mark.parametrize("webhook_status", [None, 200, 503])
async def test_eqn_delivers_one_normal_mobile_record_with_or_without_feishu(mobile_app, webhook_status):
    app, _ = mobile_app
    repo = app.state.settings_repo
    await subscribe(repo, "tradfi")
    posted = []

    def webhook(request):
        posted.append(json.loads(request.content)["content"]["text"])
        return httpx.Response(webhook_status, json={"code": 0})

    url = "https://example.org/webhook" if webhook_status else ""
    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as fake:
        notifier = FeishuNotifier(FeishuConfig(webhook_url=url), fake)
        notifier.event_repo = app.state.mobile_alert_repo
        notifier.mobile_push = Mock()
        monitor = EqnNewsMonitor(repo, notifier)
        async with feed(page(), page(article(7, "tradfi", zh="公告与资金费"))) as (client, _):
            await monitor.poll_once(client)
            await monitor.poll_once(client)
            await drain(monitor)
        await notifier.aclose()
    assert posted == (["【方程式·方程式财经】公告与资金费\nhttps://example.org/7"] if webhook_status else [])
    event, = await notifier.event_repo.list(0, 10)
    assert (event["kind"], event["level"]) == ("eqn_news", "normal")
    notifier.mobile_push.queue_feishu.assert_called_once_with("eqn_news", event["title"], event["body"], urgent=False)


@pytest.mark.parametrize("journal", ["error", "timeout", "missing"])
async def test_eqn_phone_push_survives_unavailable_journal(journal):
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.mobile_push = Mock()
    timed_out = asyncio.Event()

    async def record(**event):
        if journal == "error":
            raise RuntimeError("unavailable")
        try:
            await asyncio.Event().wait()
        finally:
            timed_out.set()

    if journal != "missing":
        notifier.event_repo = Mock(record=record)
    text = "【方程式·方程式新闻】消息"
    await asyncio.wait_for(EqnNewsMonitor(None, notifier)._deliver(text), 2)
    notifier.mobile_push.queue_feishu.assert_called_once_with("eqn_news", text, text, urgent=False)
    assert timed_out.is_set() == (journal == "timeout")


async def test_eqn_feishu_deadline_falls_back_to_phone(monkeypatch):
    notifier = FeishuNotifier(FeishuConfig(webhook_url="https://example.org"))
    notifier.mobile_push = Mock()
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    notifier.send_text = AsyncMock(side_effect=hang)
    assert eqn_news.DELIVERY_TIMEOUT_SECONDS == 10
    monkeypatch.setattr(eqn_news, "DELIVERY_TIMEOUT_SECONDS", 0.01)
    text = "【方程式·方程式新闻】消息"
    await asyncio.wait_for(EqnNewsMonitor(None, notifier)._deliver(text), 1)
    notifier.send_text.assert_awaited_once()
    notifier.mobile_push.queue_feishu.assert_called_once_with("eqn_news", text, text, urgent=False)


async def test_eqn_bounded_queue_preserves_old_messages_and_retries_when_full(mobile_app, caplog):
    app, _ = mobile_app
    repo = app.state.settings_repo
    await subscribe(repo, "main")
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.record_mobile_text = AsyncMock()
    monitor = EqnNewsMonitor(repo, notifier)
    assert monitor._pending.maxsize == 200
    monitor._pending = asyncio.Queue(maxsize=4)
    waiting_batch = page(article(6, "main"), tag="waiting")
    async with feed(
        page(), page(article(1, "main"), article(2, "main")),
        page(*(article(i, "main") for i in (3, 4, 5)), tag="accepted"),
        waiting_batch, waiting_batch, waiting_batch,
    ) as (client, requests):
        for _ in range(5):
            await monitor.poll_once(client)
        assert await repo.get_eqn_news_cursors() == {"main": 5}
        assert monitor.etag == "accepted"
        assert monitor._pending.qsize() == 4
        assert caplog.text.count("队列已满") == 1
        assert "丢弃新批次中最旧的 1 条" in caplog.text
        notifier.record_mobile_text.assert_not_awaited()
        await drain(monitor)
        await monitor.poll_once(client)
        assert requests[-1].headers["If-None-Match"] == "accepted"
        assert await repo.get_eqn_news_cursors() == {"main": 6}
    await drain(monitor)
    assert [call.args[0].splitlines()[0] for call in notifier.record_mobile_text.await_args_list] == [
        f"【方程式·方程式新闻】{i}" for i in (1, 2, 4, 5, 6)
    ]


async def test_eqn_rejected_cursor_claim_leaves_queued_messages_intact(mobile_app, monkeypatch, caplog):
    app, _ = mobile_app
    repo = app.state.settings_repo
    await subscribe(repo, "main")
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.record_mobile_text = AsyncMock()
    monitor = EqnNewsMonitor(repo, notifier)
    monitor._pending = asyncio.Queue(maxsize=2)
    async with feed(page(), page(article(1, "main"), tag="accepted"),
                    page(article(2, "main"), article(3, "main"), tag="rejected")) as (client, _):
        await monitor.poll_once(client)
        await monitor.poll_once(client)
        reject = AsyncMock(return_value=False)
        monkeypatch.setattr(repo, "replace_eqn_news_cursors", reject)
        await monitor.poll_once(client)
    reject.assert_awaited_once_with({"main": 1}, {"main": 3})
    assert await repo.get_eqn_news_cursors() == {"main": 1}
    assert monitor.etag == "accepted"
    assert "队列已满" not in caplog.text
    await drain(monitor)
    notifier.record_mobile_text.assert_awaited_once_with("【方程式·方程式新闻】1\nhttps://example.org/1")


async def test_eqn_overflow_warning_resets_below_half_without_emptying(caplog):
    repo = Mock(replace_eqn_news_cursors=AsyncMock(return_value=True))
    monitor = EqnNewsMonitor(repo, None)
    monitor._pending = asyncio.Queue(maxsize=4)
    halfway, below_half, resume = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release = asyncio.Event()

    async def deliver(text):
        if text == "2":
            halfway.set()
            await resume.wait()
        if text == "3":
            below_half.set()
            await release.wait()

    monitor._deliver = deliver
    assert await monitor._claim_batch({"main": 0}, {"main": 4}, ["1", "2", "3", "4"])
    assert not await monitor._claim_batch({"main": 4}, {"main": 5}, ["5"])
    worker = asyncio.create_task(monitor._deliver_pending())
    try:
        await asyncio.wait_for(halfway.wait(), 2)
        assert monitor._pending.qsize() == 2 and monitor._overflow_warned
        resume.set()
        await asyncio.wait_for(below_half.wait(), 2)
        assert monitor._pending.qsize() == 1 and not monitor._overflow_warned
        assert await monitor._claim_batch({"main": 4}, {"main": 8}, ["5", "6", "7", "8"])
        assert caplog.text.count("队列已满") == 2
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_eqn_normal_shutdown_drains_pending_and_inflight_delivery():
    stop = asyncio.Event()
    delivering, release = asyncio.Event(), asyncio.Event()
    delivered = []
    monitor = EqnNewsMonitor(None, None)

    async def poll(stop_event):
        for text in ("1", "2"):
            monitor._pending.put_nowait(text)
        await stop_event.wait()

    async def deliver(text):
        delivering.set()
        await release.wait()
        delivered.append(text)

    monitor._poll = poll
    monitor._deliver = deliver
    runner = asyncio.create_task(monitor.run(stop))
    try:
        await asyncio.wait_for(delivering.wait(), 2)
        stop.set()
        await asyncio.sleep(0)
        assert not runner.done()
    finally:
        release.set()
        stop.set()
        await asyncio.wait_for(runner, 2)
    assert delivered == ["1", "2"]
    assert monitor._pending.empty()


async def test_eqn_shutdown_finishes_in_progress_cursor_claim_and_delivers():
    claiming, release = asyncio.Event(), asyncio.Event()

    async def claim(previous, updated):
        claiming.set()
        await release.wait()
        return True

    repo = Mock(get_eqn_news_cursors=AsyncMock(return_value={"main": 0}),
                replace_eqn_news_cursors=AsyncMock(side_effect=claim))
    notifier = FeishuNotifier(FeishuConfig(webhook_url=""))
    notifier.record_mobile_text = AsyncMock()
    monitor = EqnNewsMonitor(repo, notifier)

    async with feed(page(article(1, "main"))) as (client, _):
        async def poll(stop_event):
            await monitor.poll_once(client)

        monitor._poll = poll
        runner = asyncio.create_task(monitor.run(asyncio.Event()))
        try:
            await asyncio.wait_for(claiming.wait(), 2)
            runner.cancel()
            await asyncio.sleep(0)
            assert not runner.done()
            notifier.record_mobile_text.assert_not_awaited()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(runner, 2)
    repo.replace_eqn_news_cursors.assert_awaited_once_with({"main": 0}, {"main": 1})
    notifier.record_mobile_text.assert_awaited_once_with("【方程式·方程式新闻】1\nhttps://example.org/1")


async def test_eqn_poll_continues_during_delivery_and_run_cancels_both(mobile_app, monkeypatch, caplog):
    app, _ = mobile_app
    repo = app.state.settings_repo
    await subscribe(repo, "main")
    notifier = FeishuNotifier(FeishuConfig(webhook_url="https://example.org"))
    delivering, fetching = asyncio.Event(), asyncio.Event()
    send_cancelled, fetch_cancelled = asyncio.Event(), asyncio.Event()

    async def slow_send(*args, **kwargs):
        delivering.set()
        try:
            await asyncio.Event().wait()
        finally:
            send_cancelled.set()

    responses = deque([page(), page(article(1, "main")), page(article(2, "main"))])

    async def dispatch(request):
        if len(responses) == 1:
            await delivering.wait()
        if responses:
            return responses.popleft()
        fetching.set()
        try:
            await asyncio.Event().wait()
        finally:
            fetch_cancelled.set()

    notifier.send_text = slow_send
    client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
    monkeypatch.setattr(eqn_news.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(eqn_news, "POLL_SECONDS", 0)
    assert eqn_news.DRAIN_TIMEOUT_SECONDS == 5
    monkeypatch.setattr(eqn_news, "DRAIN_TIMEOUT_SECONDS", 0.05)
    monitor = EqnNewsMonitor(repo, notifier)
    runner = asyncio.create_task(monitor.run(asyncio.Event()))
    try:
        await asyncio.wait_for(fetching.wait(), 2)
        assert await repo.get_eqn_news_cursors() == {"main": 2}
        assert monitor._pending.qsize() == 1
    finally:
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(runner, 1)
    assert send_cancelled.is_set() and fetch_cancelled.is_set()
    assert "投递超过关闭期限" in caplog.text


async def test_eqn_fetch_failures_are_summarized_and_reset_on_recovery(monkeypatch, caplog):
    clock = 0
    stop = asyncio.Event()
    sequence = iter([(0, 503), (10, 503), (20, 429), (30, 429), (599, 503), (600, 503),
                     (601, 304), (602, 503), (603, 200)])

    def dispatch(request):
        nonlocal clock
        clock, status = next(sequence)
        if status == 200:
            stop.set()
            return page()
        return httpx.Response(status)

    repo = Mock(get_eqn_news_cursors=AsyncMock(return_value={"main": 0}))
    monitor = EqnNewsMonitor(repo, None)
    client = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
    monkeypatch.setattr(eqn_news.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(eqn_news, "POLL_SECONDS", 0)
    monkeypatch.setattr(eqn_news, "monotonic", lambda: clock)
    with caplog.at_level("INFO", logger=eqn_news.__name__):
        await asyncio.wait_for(monitor.run(stop), 2)
    assert [r.getMessage() for r in caplog.records if r.name == eqn_news.__name__] == [
        "方程式新闻轮询失败 (HTTP 503)，本期失败 1 次",
        "方程式新闻轮询失败 (HTTP 429)，本期失败 1 次",
        "方程式新闻轮询失败 (HTTP 503)，本期失败 3 次",
        "方程式新闻抓取恢复",
        "方程式新闻轮询失败 (HTTP 503)，本期失败 1 次",
        "方程式新闻抓取恢复",
    ]
