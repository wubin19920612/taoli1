import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.schema import initialize_schema
from app.main import create_app
from app.models.hyperliquid_trade_status import (
    HyperliquidActionState,
    HyperliquidMarketTradeStatus,
    HyperliquidTradeActionStatus,
    HyperliquidTradeStatusResult,
    HyperliquidTradeStatusWatch,
)
from app.services.hyperliquid_trade_status import (
    HyperliquidTradeStatusError,
    HyperliquidTradeStatusMonitor,
    HyperliquidTradeStatusService,
    HyperliquidTradeStatusWatchRepository,
)


def _payloads(*, capped: bool) -> dict[str, object]:
    return {
        "metaAndAssetCtxs": [
            {
                "universe": [
                    {
                        "name": "ZETA",
                        "szDecimals": 1,
                        "maxLeverage": 3,
                        "marginTableId": 3,
                    }
                ]
            },
            [
                {
                    "funding": "0.000075",
                    "openInterest": "34225314.8",
                    "dayNtlVlm": "1183442.41",
                    "oraclePx": "0.06726",
                    "markPx": "0.06726",
                    "midPx": "0.0674",
                }
            ],
        ],
        "perpsAtOpenInterestCap": ["ZETA"] if capped else [],
        "l2Book": {
            "coin": "ZETA",
            "time": 1789966333633,
            "levels": [
                [{"px": "0.06673", "sz": "5994.3"}, {"px": "0.06650", "sz": "100"}],
                [{"px": "0.06675", "sz": "764.1"}, {"px": "0.06700", "sz": "100"}],
            ],
        },
    }


def _mock_client(*, capped: bool) -> httpx.AsyncClient:
    payloads = _payloads(capped=capped)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=payloads[body["type"]])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_hyperliquid_trade_status_uses_official_oi_cap_and_keeps_reduce_only_separate() -> None:
    client = _mock_client(capped=True)
    service = HyperliquidTradeStatusService(client)

    result = await service.fetch_status("ZETAUSDT", dex="main", raw_symbol="ZETA")

    assert len(result.markets) == 1
    market = result.markets[0]
    assert market.dex == "main"
    assert market.raw_symbol == "ZETA"
    assert market.at_open_interest_cap is True
    assert market.buy_open.state == HyperliquidActionState.BLOCKED
    assert market.buy_open.reason_code == "OPEN_INTEREST_CAP"
    assert market.sell_open.state == HyperliquidActionState.BLOCKED
    assert market.buy_reduce_only.state == HyperliquidActionState.AVAILABLE
    assert "平空" in market.buy_reduce_only.reason
    assert market.sell_reduce_only.state == HyperliquidActionState.AVAILABLE
    assert market.bid_depth_01pct_usdt == pytest.approx(0.06673 * 5994.3)
    assert market.ask_depth_01pct_usdt == pytest.approx(0.06675 * 764.1)
    assert "平多" in market.sell_reduce_only.reason
    assert market.best_bid == pytest.approx(0.06673)
    assert market.best_ask == pytest.approx(0.06675)
    assert market.bid_depth_1pct_usdt == pytest.approx(0.06673 * 5994.3 + 0.06650 * 100)
    assert market.ask_depth_1pct_usdt == pytest.approx(0.06675 * 764.1 + 0.06700 * 100)
    assert market.open_interest_usdt == pytest.approx(34225314.8 * 0.06726)
    assert market.funding_rate_pct == pytest.approx(0.0075)
    assert market.funding_interval_hours == 1
    assert market.fees_included is False
    await client.aclose()


@pytest.mark.asyncio
async def test_hyperliquid_trade_status_marks_opening_available_after_cap_clears() -> None:
    client = _mock_client(capped=False)
    service = HyperliquidTradeStatusService(client)

    result = await service.fetch_status("ZETA", dex="main", raw_symbol="ZETA")

    market = result.markets[0]
    assert market.at_open_interest_cap is False
    assert market.buy_open.state == HyperliquidActionState.AVAILABLE
    assert market.buy_open.executable_price == pytest.approx(0.06675)
    assert market.sell_open.state == HyperliquidActionState.AVAILABLE
    assert market.sell_open.executable_price == pytest.approx(0.06673)
    await client.aclose()


def _market(open_state: HyperliquidActionState) -> HyperliquidMarketTradeStatus:
    action = HyperliquidTradeActionStatus(
        state=open_state,
        reason_code="PUBLIC_MARKET_AVAILABLE",
        reason="test",
        executable_price=0.067,
        depth_1pct_usdt=1_000,
    )
    close_action = HyperliquidTradeActionStatus(
        state=HyperliquidActionState.CONDITIONAL,
        reason_code="REDUCE_ONLY_REQUIRES_POSITION",
        reason="test",
        executable_price=0.067,
        depth_1pct_usdt=1_000,
    )
    return HyperliquidMarketTradeStatus(
        symbol="ZETAUSDT",
        dex="main",
        raw_symbol="ZETA",
        observed_at=datetime.now(UTC),
        at_open_interest_cap=open_state != HyperliquidActionState.AVAILABLE,
        best_bid=0.0669,
        best_ask=0.067,
        bid_depth_1pct_usdt=1_000,
        ask_depth_1pct_usdt=1_000,
        volume_24h_usdt=1_000_000,
        funding_rate_pct=0.01,
        buy_open=action,
        sell_open=action,
        buy_reduce_only=close_action,
        sell_reduce_only=close_action,
    )


@pytest.mark.asyncio
async def test_monitor_notifies_only_when_cap_state_recovers() -> None:
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await initialize_schema(db)
    repository = HyperliquidTradeStatusWatchRepository(db)
    now = datetime.now(UTC)
    await repository.upsert(
        HyperliquidTradeStatusWatch(
            symbol="ZETAUSDT",
            dex="main",
            raw_symbol="ZETA",
            last_buy_state=HyperliquidActionState.BLOCKED,
            last_sell_state=HyperliquidActionState.BLOCKED,
            created_at=now,
            updated_at=now,
        )
    )
    service = AsyncMock()
    service.fetch_status.return_value = HyperliquidTradeStatusResult(
        query="ZETAUSDT",
        observed_at=now,
        markets=[_market(HyperliquidActionState.AVAILABLE)],
    )
    sender = AsyncMock()
    monitor = HyperliquidTradeStatusMonitor(repository, service, sender)

    first_events = await monitor.check_once()
    second_events = await monitor.check_once()

    assert first_events[0].recovered_sides == ["buy", "sell"]
    assert second_events == []
    sender.assert_awaited_once()
    assert "Hyperliquid 增仓恢复" in sender.await_args.args[0]
    saved = (await repository.list())[0]
    assert saved.last_buy_state == HyperliquidActionState.AVAILABLE
    assert saved.last_error is None
    await db.close()


def test_hyperliquid_trade_status_routes_return_diagnostics_and_persist_watch() -> None:
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:"),
        start_background_workers=False,
    )
    now = datetime.now(UTC)
    status = HyperliquidTradeStatusResult(
        query="ZETAUSDT",
        observed_at=now,
        markets=[_market(HyperliquidActionState.BLOCKED)],
    )
    service = AsyncMock()
    service.fetch_status.return_value = status

    with TestClient(app) as client:
        app.state.hyperliquid_trade_status_service = service
        response = client.get(
            "/api/hyperliquid/trade-status/ZETAUSDT?dex=main&raw_symbol=ZETA"
        )
        created = client.post(
            "/api/hyperliquid/trade-status/watches",
            json={"symbol": "ZETAUSDT", "dex": "main", "raw_symbol": "ZETA"},
        )
        watches = client.get("/api/hyperliquid/trade-status/watches/list")
        deleted = client.delete(
            f"/api/hyperliquid/trade-status/watches/{created.json()['id']}"
        )

    assert response.status_code == 200
    assert response.json()["markets"][0]["at_open_interest_cap"] is True
    assert created.status_code == 200
    assert created.json()["last_buy_state"] == "blocked"
    assert len(watches.json()) == 1
    assert deleted.status_code == 200


def test_hyperliquid_trade_status_route_maps_upstream_failure_to_bad_gateway() -> None:
    app = create_app(
        settings=Settings(database_url="sqlite:///:memory:"),
        start_background_workers=False,
    )
    service = AsyncMock()
    service.fetch_status.side_effect = HyperliquidTradeStatusError("upstream unavailable")

    with TestClient(app) as client:
        app.state.hyperliquid_trade_status_service = service
        response = client.get("/api/hyperliquid/trade-status/ZETAUSDT")

    assert response.status_code == 502
    assert response.json() == {"detail": "upstream unavailable"}
