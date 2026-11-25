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
        notifier.record_mobile_text.assert_not_awaited()
        await monitor.poll_once(client)
        notifier.record_mobile_text.assert_awaited_once_with("【方程式·方程式新闻】" + "文" * 500 + "\nhttps://example.org/source")
        await subscribe(repo, "oi", "main")
        await monitor.poll_once(client)
        assert "If-None-Match" not in requests[-1].headers
        restarted = EqnNewsMonitor(SettingsRepository(repo.db), notifier)
        await restarted.poll_once(client)
        # Disable then re-enable between polls, including before a restart.
        await subscribe(repo)
        await subscribe(repo, "main")
        await restarted.poll_once(client)
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
        await notifier.aclose()
    assert posted == (["【方程式·方程式财经】公告与资金费\nhttps://example.org/7"] if webhook_status else [])
    event, = await notifier.event_repo.list(0, 10)
    assert (event["kind"], event["level"]) == ("eqn_news", "normal")
    notifier.mobile_push.queue_feishu.assert_called_once_with("eqn_news", event["title"], event["body"], urgent=False)
