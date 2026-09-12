from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.database import connect_database
from app.db.repositories import OilNewsRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.oil_news import (
    OilMarketSnapshot,
    OilNewsDirection,
    OilNewsItem,
    OilNewsSettings,
    OilNewsSeverity,
)
from app.services.oil_news import (
    OilNewsFeed,
    OilNewsMonitor,
    OilNewsProvider,
    OilNewsTranslator,
    build_oil_news_alert,
    classify_oil_news,
)

NOW = datetime(2026, 9, 11, 6, 0, tzinfo=UTC)


def _item(
    *,
    fingerprint: str = "fingerprint-1",
    title: str = "Tanker attacked near the Strait of Hormuz",
    direction: OilNewsDirection = OilNewsDirection.LONG,
    severity: OilNewsSeverity = OilNewsSeverity.HIGH,
) -> OilNewsItem:
    return OilNewsItem(
        fingerprint=fingerprint,
        external_id=f"external-{fingerprint}",
        source="Reuters",
        source_feed="test-feed",
        title=title,
        url=f"https://example.com/{fingerprint}",
        published_at=NOW,
        fetched_at=NOW,
        categories=["霍尔木兹/航运"],
        severity=severity,
        impact_score=78,
        direction=direction,
        confidence=0.9,
        horizon="6小时-3天",
        rationale=["油轮遇袭增加运输中断风险"],
        risk_note="若航运迅速恢复，多头逻辑可能失效。",
    )


def test_classifies_hormuz_deal_as_critical_short() -> None:
    result = classify_oil_news(
        title="Iran and Gulf states to meet in push for Hormuz deal",
        summary=None,
        source="Financial Times",
    )

    assert result is not None
    severity, score, direction, confidence, categories, rationale, horizon, _ = result
    assert severity == OilNewsSeverity.CRITICAL
    assert score >= 85
    assert direction == OilNewsDirection.SHORT
    assert confidence >= 0.9
    assert "霍尔木兹/航运" in categories
    assert any("霍尔木兹" in reason for reason in rationale)
    assert horizon == "6小时-3天"


def test_classifies_tanker_attack_as_high_long() -> None:
    result = classify_oil_news(
        title="Oil tanker attacked near Hormuz as shipping risks rise",
        summary=None,
        source="Reuters",
    )

    assert result is not None
    severity, _, direction, confidence, _, rationale, _, _ = result
    assert severity in {OilNewsSeverity.HIGH, OilNewsSeverity.CRITICAL}
    assert direction == OilNewsDirection.LONG
    assert confidence >= 0.9
    assert any("油轮" in reason for reason in rationale)


def test_classifies_rising_hormuz_supply_risk_as_long_not_output_increase() -> None:
    result = classify_oil_news(
        title="Brent nears $110 as U.S.-Iran war raises Hormuz supply risks",
        summary=None,
        source="Reuters",
    )

    assert result is not None
    severity, _, direction, confidence, _, rationale, _, _ = result
    assert severity in {OilNewsSeverity.HIGH, OilNewsSeverity.CRITICAL}
    assert direction == OilNewsDirection.LONG
    assert confidence >= 0.9
    assert "供应中断风险上升，推高原油风险溢价" in rationale
    assert "产量或出口增加，扩大国际市场供应" not in rationale


def test_classifies_pipeline_fire_as_high_long() -> None:
    result = classify_oil_news(
        title="Saudi Petroline pipeline ablaze after attack",
        summary=None,
        source="Reuters",
    )

    assert result is not None
    severity, _, direction, _, categories, rationale, _, _ = result
    assert severity in {OilNewsSeverity.HIGH, OilNewsSeverity.CRITICAL}
    assert direction == OilNewsDirection.LONG
    assert "供应中断" in categories
    assert "管道起火可能直接减少供应" in rationale


def test_ignores_unrelated_news_and_does_not_chase_price_recaps() -> None:
    assert classify_oil_news(
        title="Technology stocks rally after earnings",
        summary=None,
        source="Reuters",
    ) is None

    recap = classify_oil_news(
        title="Oil prices soar to a four-month high",
        summary=None,
        source="NBC News",
    )
    assert recap is not None
    severity, _, direction, _, _, _, _, _ = recap
    assert severity == OilNewsSeverity.LOW
    assert direction == OilNewsDirection.WATCH


@pytest.mark.parametrize(
    ("title", "source", "expected_direction"),
    [
        (
            "OPEC further lowers 2026 global oil demand growth forecast",
            "Reuters",
            OilNewsDirection.SHORT,
        ),
        (
            "Saudis tell OPEC that output slumped again to lowest since 1990",
            "Bloomberg",
            OilNewsDirection.LONG,
        ),
        (
            "U.S. crude oil inventories rose sharply last week",
            "Reuters",
            OilNewsDirection.SHORT,
        ),
        (
            "EIA reports another draw in U.S. crude oil stocks",
            "Reuters",
            OilNewsDirection.LONG,
        ),
    ],
)
def test_classifies_directional_supply_and_demand_combinations(
    title: str,
    source: str,
    expected_direction: OilNewsDirection,
) -> None:
    result = classify_oil_news(title=title, summary=None, source=source)

    assert result is not None
    severity, _, direction, confidence, _, _, _, _ = result
    assert severity in {OilNewsSeverity.HIGH, OilNewsSeverity.CRITICAL}
    assert direction == expected_direction
    assert confidence >= 0.9


def test_rss_parser_strips_google_publisher_suffix() -> None:
    xml = """
    <rss><channel>
      <item>
        <title>Iran states push for Hormuz deal - Financial Times</title>
        <link>https://news.google.com/articles/1</link>
        <pubDate>Fri, 11 Sep 2026 04:00:00 GMT</pubDate>
        <description>Officials will meet on shipping arrangements.</description>
        <source>Financial Times</source>
      </item>
      <item>
        <title>Iran states push for Hormuz deal - Financial Times</title>
        <link>https://news.google.com/articles/1</link>
        <pubDate>Fri, 11 Sep 2026 04:00:00 GMT</pubDate>
        <source>Financial Times</source>
      </item>
    </channel></rss>
    """
    provider = OilNewsProvider(feeds=(), now_fn=lambda: NOW)
    rows = provider._parse_feed(xml, OilNewsFeed("test", "https://example.com/rss"))

    assert len(rows) == 2
    assert rows[0].title == "Iran states push for Hormuz deal"
    assert rows[0].source == "Financial Times"
    assert rows[0].direction == OilNewsDirection.SHORT
    assert rows[0].published_at == datetime(2026, 9, 11, 4, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_repository_deduplicates_same_headline_across_feeds() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OilNewsRepository(db)
        first = _item()
        first.title_zh = "霍尔木兹海峡附近一艘油轮遇袭"
        first.summary_zh = "运输中断风险上升。"
        second = _item()
        second.source_feed = "another-feed"
        second.external_id = "another-external-id"

        assert await repo.create_if_new(first) is first
        assert await repo.create_if_new(second) is None
        rows = await repo.list()
        assert len(rows) == 1
        assert rows[0].title == first.title
        assert rows[0].title_zh == first.title_zh
        assert rows[0].summary_zh == first.summary_zh
    finally:
        await db.close()


class FakeProvider:
    def __init__(self, batches: list[list[OilNewsItem]]):
        self.batches = batches
        self.index = 0

    async def fetch(self):
        batch = self.batches[min(self.index, len(self.batches) - 1)]
        self.index += 1
        return batch, []

    async def fetch_market_snapshot(self):
        return OilMarketSnapshot(
            price=98.4,
            change_1h_pct=-1.2,
            observed_at=NOW,
        )

    async def aclose(self):
        return None


class FlakySender:
    def __init__(self):
        self.calls = 0

    async def __call__(self, _message: str) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary notifier failure")


class BlockingSender:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, _message: str) -> None:
        self.calls += 1
        self.started.set()
        await self.release.wait()


class FakeTranslator:
    def __init__(self):
        self.calls = 0

    async def translate_item(self, _item: OilNewsItem) -> tuple[str, str | None]:
        self.calls += 1
        return "霍尔木兹海峡附近一艘油轮遇袭", "运输中断风险正在上升。"

    async def aclose(self) -> None:
        return None


class FlakyTranslator(FakeTranslator):
    async def translate_item(self, item: OilNewsItem) -> tuple[str, str | None]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary translation failure")
        return "霍尔木兹海峡附近一艘油轮遇袭", None


@pytest.mark.asyncio
async def test_monitor_bootstraps_silently_then_alerts_new_major_news_once() -> None:
    db = await connect_database(":memory:")
    sent: list[str] = []
    try:
        await initialize_schema(db)
        repo = OilNewsRepository(db)
        bootstrap = _item(fingerprint="bootstrap")
        next_item = _item(
            fingerprint="next",
            title="Shipping halted after tanker attacked near Hormuz",
            severity=OilNewsSeverity.CRITICAL,
        )
        provider = FakeProvider([[bootstrap], [bootstrap, next_item], [bootstrap, next_item]])
        monitor = OilNewsMonitor(repo, provider, alert_sender=sent.append, now_fn=lambda: NOW)
        settings = OilNewsSettings(bootstrap_alerts_enabled=False)

        first = await monitor.poll(settings)
        second = await monitor.poll(settings)
        third = await monitor.poll(settings)

        assert first.inserted_count == 1
        assert first.alerted_count == 0
        assert second.inserted_count == 1
        assert second.alerted_count == 1
        assert third.inserted_count == 0
        assert third.alerted_count == 0
        assert len(sent) == 1
        assert "做多倾向" in sent[0]
        assert "CLUSDT 98.40" in sent[0]
        rows = await repo.list()
        assert next(row for row in rows if row.fingerprint == "next").alert_status == "sent"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settings_repository_persists_oil_news_configuration() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SettingsRepository(db)
        settings = OilNewsSettings(
            poll_interval_seconds=600,
            alert_min_severity=OilNewsSeverity.CRITICAL,
        )
        await repo.set_oil_news_settings(settings)

        assert await repo.get_oil_news_settings() == settings
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_retries_a_failed_notification_without_inserting_a_duplicate() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OilNewsRepository(db)
        item = _item(severity=OilNewsSeverity.CRITICAL)
        provider = FakeProvider([[item], [item]])
        sender = FlakySender()
        monitor = OilNewsMonitor(repo, provider, alert_sender=sender, now_fn=lambda: NOW)
        settings = OilNewsSettings(bootstrap_alerts_enabled=True)

        first = await monitor.poll(settings)
        second = await monitor.poll(settings)

        assert first.inserted_count == 1
        assert first.alerted_count == 0
        assert second.inserted_count == 0
        assert second.alerted_count == 1
        assert sender.calls == 2
        rows = await repo.list()
        assert len(rows) == 1
        assert rows[0].alert_status == "sent"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_sends_english_immediately_when_title_translation_fails() -> None:
    db = await connect_database(":memory:")
    sent: list[str] = []
    try:
        await initialize_schema(db)
        repo = OilNewsRepository(db)
        item = _item(severity=OilNewsSeverity.CRITICAL)
        item.summary = "Shipping disruption risks are rising."
        provider = FakeProvider([[item], [item]])
        translator = FlakyTranslator()
        monitor = OilNewsMonitor(
            repo,
            provider,
            alert_sender=sent.append,
            translator=translator,
            now_fn=lambda: NOW,
        )
        settings = OilNewsSettings(bootstrap_alerts_enabled=True)

        first = await monitor.poll(settings)
        second = await monitor.poll(settings)

        assert first.alerted_count == 1
        assert second.alerted_count == 0
        assert translator.calls == 1
        assert len(sent) == 1
        assert f"英文标题：{item.title}" in sent[0]
        stored = (await repo.list())[0]
        assert stored.title_zh is None
        assert stored.alert_status == "sent"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_serializes_manual_and_background_polls_to_avoid_duplicate_alerts() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = OilNewsRepository(db)
        provider = FakeProvider([[_item(severity=OilNewsSeverity.CRITICAL)]])
        sender = BlockingSender()
        monitor = OilNewsMonitor(repo, provider, alert_sender=sender, now_fn=lambda: NOW)
        settings = OilNewsSettings(bootstrap_alerts_enabled=True)

        first_poll = asyncio.create_task(monitor.poll(settings))
        await asyncio.wait_for(sender.started.wait(), timeout=1)
        second_poll = asyncio.create_task(monitor.poll(settings))
        await asyncio.sleep(0)

        assert sender.calls == 1
        sender.release.set()
        first, second = await asyncio.gather(first_poll, second_poll)
        assert first.alerted_count + second.alerted_count == 1
        assert sender.calls == 1
    finally:
        await db.close()


def test_alert_message_includes_direction_evidence_and_reversal_condition() -> None:
    item = _item()
    item.title_zh = "霍尔木兹海峡附近一艘油轮遇袭"
    item.summary_zh = "运输中断风险正在上升。"
    item.market = OilMarketSnapshot(price=101.25, change_1h_pct=2.4, observed_at=NOW)
    message = build_oil_news_alert(item)

    assert "中文标题：霍尔木兹海峡附近一艘油轮遇袭" in message
    assert "中文摘要：运输中断风险正在上升。" in message
    assert f"英文标题：{item.title}" in message
    assert "做多倾向" in message
    assert "置信度 90%" in message
    assert "油轮遇袭" in message
    assert "时间：2026-09-11 14:00:00 UTC+8" in message
    assert "CLUSDT 101.25，近1小时 +2.40%（行情确认）" in message
    assert "反转条件" in message


@pytest.mark.asyncio
async def test_mymemory_translator_translates_title_and_nonduplicate_summary() -> None:
    requested: list[str] = []
    translations = {
        "Tanker attacked near Hormuz": "霍尔木兹海峡附近一艘油轮遇袭",
        "Shipping disruption risks are rising.": "航运中断风险正在上升。",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        source = request.url.params["q"]
        requested.append(source)
        return httpx.Response(
            200,
            json={
                "responseStatus": 200,
                "responseData": {"translatedText": translations[source]},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = OilNewsTranslator(client)
        item = _item(title="Tanker attacked near Hormuz")
        item.summary = "Shipping disruption risks are rising."

        title_zh, summary_zh = await translator.translate_item(item)

    assert title_zh == "霍尔木兹海峡附近一艘油轮遇袭"
    assert summary_zh == "航运中断风险正在上升。"
    assert requested == [item.title, item.summary]


@pytest.mark.asyncio
async def test_translator_uses_google_when_mymemory_is_unavailable() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "api.mymemory.translated.net":
            return httpx.Response(429, text="quota exceeded")
        return httpx.Response(200, json=["供应风险加大，布伦特原油价格上涨"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        translator = OilNewsTranslator(client)
        translated = await translator.translate_text(
            "Brent crude rises as supply risks grow"
        )

    assert translated == "供应风险加大，布伦特原油价格上涨"
    assert requested_hosts == [
        "api.mymemory.translated.net",
        "clients5.google.com",
    ]


@pytest.mark.asyncio
async def test_summary_translation_failure_does_not_block_chinese_title_alert() -> None:
    db = await connect_database(":memory:")
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        source = request.url.params["q"]
        if source == "Tanker attacked near Hormuz":
            return httpx.Response(
                200,
                json={
                    "responseStatus": 200,
                    "responseData": {"translatedText": "霍尔木兹海峡附近一艘油轮遇袭"},
                },
            )
        return httpx.Response(503, text="translator unavailable")

    try:
        await initialize_schema(db)
        repo = OilNewsRepository(db)
        item = _item(
            title="Tanker attacked near Hormuz",
            severity=OilNewsSeverity.CRITICAL,
        )
        item.summary = "Shipping disruption risks are rising."
        provider = FakeProvider([[item]])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            monitor = OilNewsMonitor(
                repo,
                provider,
                alert_sender=sent.append,
                translator=OilNewsTranslator(client),
                now_fn=lambda: NOW,
            )

            result = await monitor.poll(OilNewsSettings(bootstrap_alerts_enabled=True))

        assert result.alerted_count == 1
        assert len(sent) == 1
        assert "中文标题：霍尔木兹海峡附近一艘油轮遇袭" in sent[0]
        assert f"英文标题：{item.title}" in sent[0]
        assert "中文摘要：" not in sent[0]
        stored = (await repo.list())[0]
        assert stored.title_zh == "霍尔木兹海峡附近一艘油轮遇袭"
        assert stored.summary_zh is None
        assert stored.alert_status == "sent"
    finally:
        await db.close()


def test_unconfirmed_claim_reduces_direction_confidence() -> None:
    confirmed = classify_oil_news(
        title="Oil tanker attacked near Hormuz",
        summary=None,
        source="Reuters",
    )
    unconfirmed = classify_oil_news(
        title="Claims that oil tanker attacked near Hormuz",
        summary=None,
        source="Unknown source",
    )

    assert confirmed is not None and unconfirmed is not None
    assert unconfirmed[3] < confirmed[3]
    assert any("未确认" in reason for reason in unconfirmed[5])


def test_oil_news_api_exposes_records_and_password_protected_settings() -> None:
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:", dashboard_password="secret"),
        start_background_workers=False,
    )
    with TestClient(app) as client:
        assert client.get("/api/oil-news").json() == []
        response = client.get("/api/settings/oil-news")
        assert response.status_code == 200
        assert response.json()["alert_min_severity"] == "high"

        unauthorized = client.put("/api/settings/oil-news", json=response.json())
        assert unauthorized.status_code == 401
        updated = {**response.json(), "poll_interval_seconds": 600}
        authorized = client.put(
            "/api/settings/oil-news",
            json=updated,
            headers={"X-Dashboard-Password": "secret"},
        )
        assert authorized.status_code == 200
        assert authorized.json()["poll_interval_seconds"] == 600
