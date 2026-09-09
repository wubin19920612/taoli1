from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from app.db.database import connect_database
from app.db.repositories import AnnouncementRepository
from app.db.schema import initialize_schema
from app.models.announcement import (
    AnnouncementAssetResearch,
    AnnouncementKind,
    AnnouncementSettings,
    ExchangeAnnouncement,
)
from app.services.announcement_research import (
    AnnouncementResearchService,
    _is_stock_context,
    _title_asset_hints,
)
from app.services.announcements import AnnouncementMonitor, build_announcement_alert_message


BASE_TIME = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)


def listing_announcement(
    *,
    exchange: str = "binance",
    title: str = "Binance will list TEST for spot trading",
    symbols: list[str] | None = None,
    market_type: str = "spot",
    category: str = "newspotlistings",
    asset_research: list[AnnouncementAssetResearch] | None = None,
) -> ExchangeAnnouncement:
    return ExchangeAnnouncement(
        exchange=exchange,
        announcement_id=f"research-{exchange}",
        kind=AnnouncementKind.LISTING,
        title=title,
        url="https://example.com/announcement",
        source="test-source",
        category=category,
        symbols=symbols or ["TEST"],
        market_type=market_type,
        asset_research=asset_research or [],
        published_at=BASE_TIME,
        fetched_at=BASE_TIME,
    )


def _search_html(symbol: str, name: str, snippet: str) -> str:
    return f"""
    <html>
      <a class="result__a" href="https://example.com/{symbol.lower()}">{name} ({symbol})</a>
      <div class="result__snippet">{snippet}</div>
    </html>
    """


def test_unknown_bitget_r_prefixed_coin_is_not_assumed_to_be_a_stock_rtoken() -> None:
    announcement = listing_announcement(
        exchange="bitget",
        title="Bitget lists RWAUSDT for spot trading",
        symbols=["RWAUSDT"],
        market_type="spot",
    )

    assert not _is_stock_context(announcement, "RWAUSDT")


@pytest.mark.asyncio
async def test_bitget_rtoken_metadata_outage_does_not_fall_back_to_crypto_guessing() -> None:
    requested_hosts: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host or "")
        if request.url.host == "api.bitget.com":
            raise httpx.ConnectError("metadata unavailable", request=request)
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AnnouncementResearchService(client=client, now_fn=lambda: BASE_TIME)
    try:
        results = await service.research(
            listing_announcement(
                exchange="bitget",
                title="Bitget lists RNEWUSDT for spot trading",
                symbols=["RNEWUSDT"],
                market_type="spot",
            )
        )
    finally:
        await service.aclose()
        await client.aclose()

    assert requested_hosts == ["api.bitget.com"]
    assert results[0].status == "not_found"
    assert results[0].asset_type == "unknown"


@pytest.mark.asyncio
async def test_researches_crypto_with_coingecko_and_persists_public_sources() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.coingecko.com" and request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "coins": [
                        {
                            "id": "test-network",
                            "name": "Test Network",
                            "symbol": "test",
                            "market_cap_rank": 400,
                        }
                    ],
                },
                request=request,
            )
        if request.url.host == "api.coingecko.com" and "/coins/test-network" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "description": {
                        "en": "Test Network is a decentralized protocol for secure data availability and settlement."
                    },
                    "categories": ["Layer 1"],
                    "links": {"homepage": ["https://test.network"]},
                },
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AnnouncementResearchService(client=client, now_fn=lambda: BASE_TIME)
    try:
        results = await service.research(
            listing_announcement(
                title="Binance will list Test Network (TEST) for spot trading",
                symbols=["TESTUSDT"],
            )
        )
    finally:
        await service.aclose()
        await client.aclose()

    assert len(results) == 1
    assert results[0].symbol == "TESTUSDT"
    assert results[0].canonical_symbol == "TEST"
    assert results[0].asset_type == "crypto"
    assert results[0].name == "Test Network"
    assert "data availability" in (results[0].business or "")
    assert [source.title for source in results[0].sources] == ["CoinGecko", "项目官网"]


@pytest.mark.asyncio
async def test_researches_bstock_announcement_as_stocks_without_calling_coingecko() -> None:
    requested_paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(str(request.url))
        if request.url.host == "query1.finance.yahoo.com":
            return httpx.Response(404, text="not found", request=request)
        if request.url.host == "html.duckduckgo.com":
            query = parse_qs(request.url.query.decode()).get("q", [""])[0]
            if "CRMB" in query:
                return httpx.Response(
                    200,
                    text=_search_html(
                        "CRMB",
                        "Salesforce",
                        "Salesforce provides customer relationship management software and cloud services for sales, service, marketing, and data teams.",
                    ),
                    request=request,
                )
            return httpx.Response(
                200,
                text=_search_html(
                    "HIMSB",
                    "Hims & Hers Health",
                    "Hims & Hers Health operates a digital health platform connecting consumers with online consultations, prescriptions, and wellness products.",
                ),
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AnnouncementResearchService(client=client, now_fn=lambda: BASE_TIME)
    announcement = listing_announcement(
        title=(
            "Binance Exchange Adds Salesforce (CRMB) and Hims & Hers Health (HIMSB) "
            "bStocks Trading Pairs on Binance Spot/Convert"
        ),
        symbols=["CRMB", "HIMSB"],
        market_type="spot/convert",
        category="48:New Cryptocurrency Listing",
    )
    try:
        results = await service.research(announcement)
    finally:
        await service.aclose()
        await client.aclose()

    assert _title_asset_hints(announcement.title) == {
        "CRMB": "Salesforce",
        "HIMSB": "Hims & Hers Health",
    }
    assert [(item.symbol, item.asset_type, item.name) for item in results] == [
        ("CRMB", "stock", "Salesforce"),
        ("HIMSB", "stock", "Hims & Hers Health"),
    ]
    assert all(item.status == "partial" for item in results)
    assert all("coingecko.com" not in url for url in requested_paths)
    assert any("Salesforce provides" in (item.business or "") for item in results)
    assert any("Hims & Hers Health operates" in (item.business or "") for item in results)


@pytest.mark.asyncio
async def test_bitget_rtoken_research_uses_standard_stock_symbol() -> None:
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "api.bitget.com":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "symbol": "RSOXLUSDT",
                            "baseCoin": "rSOXL",
                            "areaSymbol": "yes",
                        }
                    ]
                },
                request=request,
            )
        if request.url.host == "query1.finance.yahoo.com":
            return httpx.Response(
                200,
                json={
                    "chart": {
                        "result": [
                            {
                                "meta": {
                                    "instrumentType": "EQUITY",
                                    "longName": "Direxion Daily Semiconductor Bull 3X Shares",
                                }
                            }
                        ]
                    }
                },
                request=request,
            )
        if request.url.host == "html.duckduckgo.com":
            return httpx.Response(
                200,
                text=_search_html(
                    "SOXL",
                    "Direxion Daily Semiconductor Bull 3X Shares",
                    "SOXL is an exchange traded fund designed to provide daily leveraged exposure to the semiconductor sector.",
                ),
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = AnnouncementResearchService(client=client, now_fn=lambda: BASE_TIME)
    try:
        results = await service.research(
            listing_announcement(
                exchange="bitget",
                title="Bitget lists RSOXLUSDT for spot trading",
                symbols=["RSOXLUSDT"],
                market_type="spot",
                category="coin_listings",
            )
        )
    finally:
        await service.aclose()
        await client.aclose()

    assert len(results) == 1
    assert results[0].symbol == "RSOXLUSDT"
    assert results[0].canonical_symbol == "SOXL"
    assert results[0].asset_type == "stock"
    assert results[0].name == "Direxion Daily Semiconductor Bull 3X Shares"
    assert any("/SOXL?" in url for url in requested_urls)
    assert all("/RSOXL" not in url for url in requested_urls if "query1.finance.yahoo.com" in url)


@pytest.mark.asyncio
async def test_legacy_announcement_schema_gets_asset_research_column() -> None:
    db = await connect_database(":memory:")
    try:
        await db.executescript(
            """
            CREATE TABLE exchange_announcements (
              id TEXT PRIMARY KEY,
              exchange TEXT NOT NULL,
              announcement_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              url TEXT NOT NULL,
              source TEXT NOT NULL,
              category TEXT,
              published_at TEXT NOT NULL,
              fetched_at TEXT NOT NULL,
              alert_status TEXT NOT NULL,
              UNIQUE(exchange, source, announcement_id)
            );
            """
        )
        await db.execute(
            """
            INSERT INTO exchange_announcements (
              id, exchange, announcement_id, kind, title, url, source,
              published_at, fetched_at, alert_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-announcement",
                "binance",
                "legacy-1",
                "listing",
                "Binance will list TEST",
                "https://example.com/legacy",
                "legacy-source",
                BASE_TIME.isoformat(),
                BASE_TIME.isoformat(),
                "sent",
            ),
        )
        await db.commit()

        await initialize_schema(db)

        columns = await (await db.execute("PRAGMA table_info(exchange_announcements)")).fetchall()
        assert "asset_research_json" in {row["name"] for row in columns}
        repository = AnnouncementRepository(db)
        row = await repository.get_by_identity(
            exchange="BINANCE",
            source="legacy-source",
            announcement_id="legacy-1",
        )
    finally:
        await db.close()

    assert row is not None
    assert row.asset_research == []


@pytest.mark.asyncio
async def test_research_failure_does_not_block_announcement_alert_or_persistence() -> None:
    db = await connect_database(":memory:")
    alerts: list[str] = []

    async def failing_research(_: ExchangeAnnouncement) -> list:
        raise RuntimeError("public search unavailable")

    try:
        await initialize_schema(db)
        repository = AnnouncementRepository(db)
        monitor = AnnouncementMonitor(
            repository,
            alert_sender=alerts.append,
            asset_researcher=failing_research,
        )
        created = await monitor.process(
            [listing_announcement(exchange="okx")],
            AnnouncementSettings(record_exchanges=["okx"]),
            bootstrap=False,
        )
        rows = await repository.list(limit=10)
    finally:
        await db.close()

    assert len(created) == 1
    assert created[0].asset_research == []
    assert len(alerts) == 1
    assert "[OKX] 上币公告" in alerts[0]
    assert rows[0].asset_research == []


@pytest.mark.asyncio
async def test_retries_persisted_not_found_research_on_a_later_poll() -> None:
    db = await connect_database(":memory:")
    calls = 0

    async def research(_: ExchangeAnnouncement) -> list[AnnouncementAssetResearch]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [
                AnnouncementAssetResearch(
                    symbol="TEST",
                    canonical_symbol="TEST",
                    status="not_found",
                    searched_at=BASE_TIME,
                )
            ]
        return [
            AnnouncementAssetResearch(
                symbol="TEST",
                canonical_symbol="TEST",
                asset_type="crypto",
                name="Test Network",
                summary="Test Network，公开项目介绍。",
                business="A public test network.",
                status="found",
                searched_at=BASE_TIME,
            )
        ]

    try:
        await initialize_schema(db)
        repository = AnnouncementRepository(db)
        monitor = AnnouncementMonitor(repository, asset_researcher=research)
        settings = AnnouncementSettings(record_exchanges=["binance"])

        first = await monitor.process(
            [listing_announcement()],
            settings,
            bootstrap=False,
        )
        second = await monitor.process(
            [listing_announcement()],
            settings,
            bootstrap=False,
        )
        rows = await repository.list(limit=10)
    finally:
        await db.close()

    assert len(first) == 1
    assert second == []
    assert calls == 2
    assert rows[0].asset_research[0].status == "found"


def test_stock_and_crypto_announcement_alert_labels_are_readable() -> None:
    stock_message = build_announcement_alert_message(
        listing_announcement(
            exchange="bitget",
            title="Bitget lists RSOXLUSDT for spot trading",
            symbols=["RSOXLUSDT"],
            market_type="spot",
            category="coin_listings",
            asset_research=[
                {
                    "symbol": "RSOXLUSDT",
                    "canonical_symbol": "SOXL",
                    "asset_type": "stock",
                    "name": "Direxion Daily Semiconductor Bull 3X Shares",
                    "status": "found",
                    "searched_at": BASE_TIME,
                }
            ],
        )
    )
    crypto_message = build_announcement_alert_message(
        listing_announcement(
            title="Binance will list TEST for spot trading",
            symbols=["TESTUSDT"],
            market_type="spot",
        )
    )

    assert "标的: RSOXLUSDT" in stock_message
    assert "市场: 股票现货" in stock_message
    assert "币种: TESTUSDT" in crypto_message
    assert "市场: 现货" in crypto_message
