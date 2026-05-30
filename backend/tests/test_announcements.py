from datetime import UTC, datetime

import pytest

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository, SettingsRepository
from app.db.schema import initialize_schema
from app.models.announcement import AnnouncementKind, AnnouncementSettings, ExchangeAnnouncement
from app.services.announcements import (
    AnnouncementMonitor,
    BinanceAnnouncementProvider,
    BitgetAnnouncementProvider,
    BybitAnnouncementProvider,
    GateAnnouncementProvider,
    HyperliquidAnnouncementProvider,
    OKXAnnouncementProvider,
    build_announcement_alert_message,
    build_announcement_event_reminder_message,
    classify_announcement,
    extract_event_time,
    infer_market_type,
    infer_symbols,
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
        symbols=["TEST"],
        market_type="spot",
        event_time=BASE_TIME.replace(hour=9),
        summary="listing: symbols=TEST; market=spot; event_time=2026-05-30T09:00:00+00:00",
        published_at=BASE_TIME,
        fetched_at=BASE_TIME,
        event_reminder_status="pending",
    )


def test_classify_announcement_uses_category_and_title_fallbacks() -> None:
    assert classify_announcement("Something ordinary", "announcements-new-listings") == AnnouncementKind.LISTING
    assert classify_announcement("Delisting of DOGUSDT Perpetual Contract") == AnnouncementKind.DELISTING
    assert classify_announcement("Bitget Spot Cross Margin adds GENIUS/USDT") == AnnouncementKind.LISTING
    assert classify_announcement("Initial Listing: Gate to List QAIT (QAIT) for Spot", "newspotlistings") == AnnouncementKind.LISTING
    assert classify_announcement("Binance Futures Will Launch QNTXUSDT USDⓈ-Margined Perpetual Contract") == AnnouncementKind.LISTING
    assert classify_announcement("Proof of reserves updated") == AnnouncementKind.OTHER


def test_announcement_metadata_parsers_extract_symbols_market_and_time() -> None:
    title = "Binance Futures Will Launch QNTXUSDT USDⓈ-Margined Perpetual Contract on 2026-05-30 12:00 (UTC)"

    assert infer_symbols(title) == ["QNTXUSDT"]
    assert infer_market_type(title) == "futures"
    assert infer_symbols("Binance Alpha Will Remove DIGI, K, SKI") == ["DIGI", "K", "SKI"]
    assert infer_symbols("Pre-IPO Trading for QNTXUSDT Perpetual Futures (USDT-M)") == ["QNTXUSDT"]
    assert infer_symbols("Pre-Market Trading for QNTXUSDT Perpetual Futures (QNTX)") == ["QNTXUSDT"]
    assert infer_market_type("Bitget Spot Cross Margin adds GENIUS/USDT") == "spot margin"
    assert extract_event_time(title) == datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    assert extract_event_time("Trading starts on May 30, 2026 at 12:05 UTC") == datetime(2026, 5, 30, 12, 5, tzinfo=UTC)
    assert extract_event_time("The subscription period is from 2026-05-11 00:00 UTC to 2026-05-14 00:00 UTC.") is None


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
            "公告时间: 2026-05-30 16:00:00 UTC+8",
            "币种: TEST",
            "市场: spot",
            "事件时间: 2026-05-30 17:00:00 UTC+8",
            "标题: Delisting of DOGUSDT Perpetual Contract",
            "分类: delistings",
            "链接: https://announcements.bybit.com/en-US/article/test/",
        ]
    )


def test_announcement_event_reminder_message_is_readable() -> None:
    message = build_announcement_event_reminder_message(
        announcement(),
        minutes_before=60,
        now=BASE_TIME,
    )

    assert message == "\n".join(
        [
            "[OKX] 上币快到时间提醒",
            "提醒窗口: 提前 60 分钟",
            "事件时间: 2026-05-30 17:00:00 UTC+8",
            "剩余: 约 60 分钟",
            "币种: TEST",
            "市场: spot",
            "标题: OKX to list TEST for spot trading",
            "链接: https://www.okx.com/help/test",
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
        assert rows[0].symbols == ["TEST"]
        assert rows[0].market_type == "spot"
        assert rows[0].event_time == BASE_TIME.replace(hour=9)

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

        defaults = await repo.get_announcement_settings()
        assert defaults.record_exchanges == ["binance", "okx", "bybit", "gate", "bitget", "hyperliquid"]

        settings = AnnouncementSettings(
            enabled=True,
            poll_interval_seconds=120,
            record_exchanges=["OKX", "okx", "bybit"],
            alert_exchanges=["BYBIT"],
            bootstrap_alerts_enabled=True,
            event_reminders_enabled=False,
            event_reminder_minutes_before=45,
        )
        saved = await repo.set_announcement_settings(settings)
        loaded = await repo.get_announcement_settings()

        assert saved.record_exchanges == ["okx", "bybit"]
        assert loaded.record_exchanges == ["okx", "bybit"]
        assert loaded.alert_exchanges == ["bybit"]
        assert loaded.bootstrap_alerts_enabled is True
        assert loaded.event_reminders_enabled is False
        assert loaded.event_reminder_minutes_before == 45
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
async def test_monitor_sends_due_event_reminders_once() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        monitor = AnnouncementMonitor(repo, alert_sender=alerts.append, now_fn=lambda: BASE_TIME)
        settings = AnnouncementSettings(
            record_exchanges=["okx"],
            alert_exchanges=["okx"],
            event_reminders_enabled=True,
            event_reminder_minutes_before=60,
            bootstrap_alerts_enabled=True,
        )
        row = announcement()
        await monitor.process([row], settings, bootstrap=False)

        reminded = await monitor.process_due_event_reminders(settings)
        second = await monitor.process_due_event_reminders(settings)

        assert len(reminded) == 1
        assert second == []
        assert any("快到时间提醒" in item for item in alerts)
        rows = await repo.list(limit=10)
        assert rows[0].event_reminder_status == "sent"
        assert rows[0].event_reminder_sent_at == BASE_TIME
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


def test_binance_provider_parses_listing_and_delisting_catalogs() -> None:
    provider = BinanceAnnouncementProvider(client=None)
    listing_payload = {
        "data": {
            "catalogs": [
                {
                    "catalogName": "New Cryptocurrency Listing",
                    "articles": [
                        {
                            "id": 275489,
                            "code": "3bdaff694bde45ccb443709336c8686d",
                            "title": "Binance Futures Will Launch QNTXUSDT USDⓈ-Margined Perpetual Contract",
                            "releaseDate": 1780038006968,
                        }
                    ],
                }
            ]
        }
    }
    delisting_payload = {
        "data": {
            "catalogs": [
                {
                    "catalogName": "Delisting",
                    "articles": [
                        {
                            "id": 275485,
                            "code": "318ddd21e0ee4e3690633b5ccd5e41d9",
                            "title": "Binance Alpha Will Remove DIGI, K, SKI",
                            "releaseDate": 1780037000000,
                        }
                    ],
                }
            ]
        }
    }

    rows = [
        *provider._parse_payload(listing_payload, "48", "New Cryptocurrency Listing"),
        *provider._parse_payload(delisting_payload, "161", "Delisting"),
    ]

    assert [row.exchange for row in rows] == ["binance", "binance"]
    assert [row.kind for row in rows] == [AnnouncementKind.LISTING, AnnouncementKind.DELISTING]
    assert rows[0].symbols == ["QNTXUSDT"]
    assert rows[0].market_type == "futures"
    assert rows[0].url == "https://www.binance.com/en/support/announcement/3bdaff694bde45ccb443709336c8686d"
    assert rows[0].published_at.isoformat() == "2026-05-29T07:00:06.968000+00:00"


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


def test_gate_provider_parses_next_data_listing_and_delisting_pages() -> None:
    provider = GateAnnouncementProvider(client=None)
    listing_html = """
    <script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">
    {"props":{"pageProps":{"listData":{"list":[
      {"id":51434,"title":"Initial Listing: Gate to List QAIT (QAIT) for Spot and Convert Trading","brief":"QAIT Spot Trading Start Time: May 28, 2026, 15:20 (UTC)","tags":"QAIT","url":"/announcements/article/51434","release_timestamp":"1779980455"},
      {"id":51430,"title":"Gate Launchpool Project #363","url":"/announcements/article/51430","release_timestamp":"1779980000"}
    ]}}}}
    </script>
    """
    delisting_html = """
    <script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">
    {"props":{"pageProps":{"listData":{"list":[
      {"id":51413,"title":"Gate Completes Delisting and Buyback of 15 Coins, Including GOVI and MISSION","url":"/announcements/article/51413","release_timestamp":"1779880455"}
    ]}}}}
    </script>
    """

    rows = [
        *provider._parse_page(listing_html, "newspotlistings"),
        *provider._parse_page(delisting_html, "delisted"),
    ]

    assert [row.exchange for row in rows] == ["gate", "gate"]
    assert [row.kind for row in rows] == [AnnouncementKind.LISTING, AnnouncementKind.DELISTING]
    assert rows[0].symbols == ["QAIT"]
    assert rows[0].market_type == "spot/convert"
    assert rows[0].url == "https://www.gate.com/announcements/article/51434"
    assert rows[0].published_at.isoformat() == "2026-05-28T15:00:55+00:00"
    assert rows[0].event_time.isoformat() == "2026-05-28T15:20:00+00:00"


@pytest.mark.asyncio
async def test_hyperliquid_provider_records_meta_universe_changes() -> None:
    db = await connect_database(":memory:")
    try:
        await initialize_schema(db)
        repo = AnnouncementRepository(db)
        await repo.set_provider_state(
            "hyperliquid:meta-universe",
            {"symbols": {"BTC": False, "OLD": False, "DOGE": False}},
        )
        provider = HyperliquidAnnouncementProvider(
            client=None,
            repository=repo,
            now_fn=lambda: BASE_TIME,
        )

        rows = await provider._parse_payload(
            {
                "universe": [
                    {"name": "BTC"},
                    {"name": "NEW"},
                    {"name": "DOGE", "isDelisted": True},
                ]
            }
        )

        assert [(row.kind, row.title) for row in rows] == [
            (AnnouncementKind.LISTING, "Hyperliquid listed NEW perpetual market"),
            (AnnouncementKind.DELISTING, "Hyperliquid delisted DOGE perpetual market"),
            (AnnouncementKind.DELISTING, "Hyperliquid delisted OLD perpetual market"),
        ]
        state = await repo.get_provider_state("hyperliquid:meta-universe")
        assert state == {"symbols": {"BTC": False, "DOGE": True, "NEW": False}}
    finally:
        await db.close()
