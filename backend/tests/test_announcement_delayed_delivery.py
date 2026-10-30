from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.services.announcements import (
    AnnouncementMonitor,
    BitgetAnnouncementProvider,
    build_announcement_alert_message,
)

NOW = datetime(2026, 10, 14, 4, 52, 18, tzinfo=UTC)


def copper_notice(age_minutes=85):
    return ExchangeAnnouncement(
        exchange="bitget", source="bitget-public-annoucements",
        announcement_id="12560603897463", kind=AnnouncementKind.OTHER,
        title="Bitget announcement on the temporary adjustment to the COPPERUSDT futures index",
        title_zh="【重要】Bitget 关于COPPERUSDT 合约指数临时调整公告",
        url="https://www.bitget.com/en/support/articles/12560603897463",
        published_at=NOW - timedelta(minutes=age_minutes), fetched_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("age_minutes", [0, 10, 85, 180])
async def test_delayed_or_equal_timestamp_notice_is_sent_once_after_restart(age_minutes):
    db = await connect_database(":memory:")
    alerts = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        # A newer article already exists: publication order is not discovery order.
        await repo.create_if_new(copper_notice(0).model_copy(update={
            "announcement_id": "newer-maintenance", "alert_status": "sent",
        }))
        settings = AnnouncementSettings(other_alerts_enabled=True, alert_max_age_minutes=180)
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: NOW)
        created = await monitor.process([copper_notice(age_minutes)] * 2, settings)
        assert [row.alert_status for row in created] == ["sent"]
        restarted = AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: NOW)
        assert await restarted.process([copper_notice(age_minutes)], settings) == []
        assert len(alerts) == 1
        assert "COPPERUSDT" in alerts[0]
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["bootstrap", "new_source", "expired", "disabled"])
async def test_delayed_delivery_preserves_history_and_notification_controls(case):
    db = await connect_database(":memory:")
    alerts = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        if case != "new_source":
            await repo.create_if_new(copper_notice(0).model_copy(update={
                "announcement_id": "baseline", "alert_status": "muted",
            }))
        settings = AnnouncementSettings(
            other_alerts_enabled=case != "disabled", alert_max_age_minutes=180,
        )
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: NOW)
        row = copper_notice(181 if case == "expired" else 85)
        created = await monitor.process([row], settings, bootstrap=case == "bootstrap")
        assert created[0].alert_status == "muted"
        assert alerts == []
    finally:
        await db.close()


def test_delayed_notice_displays_original_time_discovery_time_and_delay():
    message = build_announcement_alert_message(copper_notice())
    assert "公告时间: 2026-10-14 11:27:18 UTC+8" in message
    assert "首次发现: 2026-10-14 12:52:18 UTC+8" in message
    assert "85 分钟" in message
    assert "延迟" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "business", "malformed"])
async def test_bitget_failed_category_does_not_drop_copper_notice(failure):
    requests = []

    def respond(request):
        kind = request.url.params.get("annType", "")
        requests.append(kind)
        if kind == "coin_listings":
            if failure == "http":
                return httpx.Response(503)
            if failure == "business":
                return httpx.Response(200, json={"code": "40017", "msg": "bad parameter"})
            return httpx.Response(200, json={"code": "00000", "data": {}})
        rows = []
        if kind == "latest_news":
            rows = [{"annId": "12560603897463", "annTitle": copper_notice().title,
                     "annUrl": copper_notice().url, "annType": kind,
                     "cTime": str(int(copper_notice().published_at.timestamp() * 1000))}]
        return httpx.Response(200, json={"code": "00000", "data": rows})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        rows = await BitgetAnnouncementProvider(client=client, now_fn=lambda: NOW).fetch_headlines()
    assert [row.announcement_id for row in rows] == ["12560603897463"]
    assert {"product_updates", "maintenance_system_updates"} <= set(requests)


@pytest.mark.asyncio
async def test_bitget_all_failed_sources_are_not_reported_as_empty_success():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"code": "40017", "msg": "bad parameter"}),
    )) as client:
        with pytest.raises(ValueError, match="Bitget"):
            await BitgetAnnouncementProvider(client=client).fetch_headlines()
