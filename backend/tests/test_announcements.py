from datetime import UTC, datetime

import pytest

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.services.announcements import (
    AnnouncementMonitor,
    BitgetAnnouncementProvider,
    BybitAnnouncementProvider,
    OKXAnnouncementProvider,
    build_announcement_alert_message,
    classify_announcement,
)


BASE_TIME = datetime(2026, 5, 30, 8, 0, tzinfo=UTC)


def announcement(
    *,
    exchange: str = "okx",
    announcement_id: str = "ann-1",
    kind: AnnouncementKind = AnnouncementKind.LISTING,
    title: str = "OKX to list TEST for spot trading",
    url: str = "https://www.okx.com/help/test",
    source: str = "test-source",
    category: str = "announcements-new-listings",
) -> ExchangeAnnouncement:
    return ExchangeAnnouncement(
        exchange=exchange,
        announcement_id=announcement_id,
        kind=kind,
        title=title,
        url=url,
        source=source,
        category=category,
        published_at=BASE_TIME,
        fetched_at=BASE_TIME,
    )


def test_classify_announcement_uses_category_and_title_fallbacks() -> None:
    assert classify_announcement("Something ordinary", "announcements-new-listings") == AnnouncementKind.LISTING
    assert classify_announcement("Delisting of DOGUSDT Perpetual Contract") == AnnouncementKind.DELISTING
    assert classify_announcement("Bitget Spot Cross Margin adds GENIUS/USDT") == AnnouncementKind.LISTING
    assert classify_announcement("Proof of reserves updated") == AnnouncementKind.OTHER


def test_announcement_alert_message_is_readable() -> None:
    message = build_announcement_alert_message(
        announcement(
            exchange="bybit",
            title="Delisting of DOGUSDT Perpetual Contract",
            kind=AnnouncementKind.DELISTING,
            category="delistings",
            url="https://announcements.bybit.com/en-US/article/test/",
        )
    )

    assert message == "\n".join(
        [
            "[BYBIT] 下币公告",
            "时间: 2026-05-30 16:00:00 UTC+8",
            "标题: Delisting of DOGUSDT Perpetual Contract",
            "分类: delistings",
            "链接: https://announcements.bybit.com/en-US/article/test/",
        ]
    )


@pytest.mark.asyncio
async def test_repository_deduplicates_and_filters_announcements() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        first = announcement()

        inserted = await repo.create_if_new(first)
        duplicate = await repo.create_if_new(first.model_copy(update={"id": "different"}))

        assert inserted == first
        assert duplicate is None
        rows = await repo.list(exchange="OKX", kind=AnnouncementKind.LISTING, limit=10)
        assert rows == [first]

        await repo.update_alert_status(first.id, "sent")
        rows = await repo.list(limit=10)
        assert rows[0].alert_status == "sent"
        assert await repo.has_any() is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_settings_repository_round_trips_announcement_settings() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = SettingsRepository(db)

        assert await repo.get_announcement_settings() == AnnouncementSettings()

        settings = AnnouncementSettings(
            enabled=True,
            poll_interval_seconds=120,
            record_exchanges=["OKX", "okx", "bybit"],
            alert_exchanges=["BYBIT"],
            bootstrap_alerts_enabled=True,
        )
        saved = await repo.set_announcement_settings(settings)
        loaded = await repo.get_announcement_settings()

        assert saved.record_exchanges == ["okx", "bybit"]
        assert loaded.record_exchanges == ["okx", "bybit"]
        assert loaded.alert_exchanges == ["bybit"]
        assert loaded.bootstrap_alerts_enabled is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_records_without_bootstrap_alerts_by_default() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append)
        settings = AnnouncementSettings(record_exchanges=["okx"], alert_exchanges=["okx"])

        created = await monitor.process([announcement()], settings, bootstrap=True)

        assert len(created) == 1
        assert created[0].alert_status == "muted"
        assert alerts == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_alerts_new_configured_exchange_announcements() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append)
        settings = AnnouncementSettings(record_exchanges=["okx"], alert_exchanges=["okx"])

        created = await monitor.process([announcement()], settings, bootstrap=False)
        duplicate = await monitor.process([announcement()], settings, bootstrap=False)

        assert len(created) == 1
        assert created[0].alert_status == "sent"
        assert duplicate == []
        assert len(alerts) == 1
        assert "[OKX] 上币公告" in alerts[0]
        rows = await repo.list(limit=10)
        assert rows[0].alert_status == "sent"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_monitor_records_but_mutes_unalerted_exchanges() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append)
        settings = AnnouncementSettings(record_exchanges=["okx"], alert_exchanges=["bybit"])

        created = await monitor.process([announcement()], settings, bootstrap=False)

        assert len(created) == 1
        assert created[0].alert_status == "muted"
        assert alerts == []
    finally:
        await db.close()


def test_okx_provider_parses_listing_and_delisting_payloads() -> None:
    provider = OKXAnnouncementProvider(client=None)
    listing_payload = {
        "code": "0",
        "data": [
            {
                "details": [
                    {
                        "title": "OKX to list TRUMP (OFFICIAL TRUMP) for spot trading",
                        "url": "https://www.okx.com/help/okx-to-list-trump-official-trump-for-spot-trading",
                        "pTime": "1737255602487",
                        "annType": "announcements-new-listings",
                    }
                ]
            }
        ],
    }
    delisting_payload = {
        "code": "0",
        "data": [
            {
                "details": [
                    {
                        "title": "OKX to delist several perpetual futures",
                        "url": "https://www.okx.com/help/okx-to-delist-several-perpetual-futures",
                        "pTime": "1737601200000",
                        "annType": "announcements-delistings",
                    }
                ]
            }
        ],
    }

    rows = [
        *provider._parse_payload(listing_payload, "announcements-new-listings"),
        *provider._parse_payload(delisting_payload, "announcements-delistings"),
    ]

    assert [row.exchange for row in rows] == ["okx", "okx"]
    assert [row.kind for row in rows] == [AnnouncementKind.LISTING, AnnouncementKind.DELISTING]
    assert rows[0].published_at.isoformat() == "2025-01-19T03:00:02.487000+00:00"


def test_bybit_provider_parses_announcement_payload() -> None:
    provider = BybitAnnouncementProvider(client=None)
    listing_payload = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "title": "New listing: WDCUSDT Perpetual Contract, with up to 10x leverage",
                    "url": "https://announcements.bybit.com/en-US/article/new-listing/",
                    "publishTime": 1779962014000,
                }
            ]
        },
    }
    delisting_payload = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "title": "Delisting of DOGUSDT Perpetual Contract",
                    "url": "https://announcements.bybit.com/en-US/article/delisting/",
                    "publishTime": 1779952014000,
                },
            ]
        },
    }

    rows = [
        *provider._parse_payload(listing_payload, "new_crypto"),
        *provider._parse_payload(delisting_payload, "delistings"),
    ]

    assert [row.exchange for row in rows] == ["bybit", "bybit"]
    assert [row.kind for row in rows] == [AnnouncementKind.LISTING, AnnouncementKind.DELISTING]
    assert rows[0].announcement_id == "new-listing"


def test_bitget_provider_parses_and_classifies_payload() -> None:
    provider = BitgetAnnouncementProvider(client=None)
    payload = {
        "code": "00000",
        "data": [
            {
                "annId": "12560603884670",
                "annTitle": "Bitget Spot Cross Margin adds FARTCOIN/USDT",
                "annUrl": "https://www.bitget.com/en/support/articles/12560603884670",
                "cTime": "1780052400000",
                "annType": "coin_listings",
                "annSubType": "margin",
            },
            {
                "annId": "12560603884671",
                "annTitle": "Bitget will delist TEST/USDT",
                "annUrl": "https://www.bitget.com/en/support/articles/12560603884671",
                "cTime": "1780052400000",
                "annType": "latest_news",
            },
        ],
    }

    rows = provider._parse_payload(payload, "coin_listings")

    assert [row.exchange for row in rows] == ["bitget", "bitget"]
    assert [row.kind for row in rows] == [AnnouncementKind.LISTING, AnnouncementKind.DELISTING]
    assert rows[0].category == "coin_listings:margin"
