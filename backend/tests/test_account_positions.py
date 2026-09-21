from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.account_position import (
    AccountPosition,
    AccountPositionAccountState,
    AccountPositionFreshness,
    AccountPositionIdentity,
    account_position_id,
)
from app.models.market import MarketType
from app.services.account_positions import (
    AccountPositionPermissionError,
    AccountPositionService,
    GateAccountPositionProvider,
)


NOW = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)


def position(
    *,
    account_id: str = "gate:primary",
    account_label: str = "主账户",
    exchange: str = "gate",
    raw_symbol: str = "BTC_USDT",
    symbol: str = "BTCUSDT",
    side: str = "long",
    dex: str | None = None,
) -> AccountPosition:
    identity = {
        "account_id": account_id,
        "account_label": account_label,
        "exchange": exchange,
        "market_type": MarketType.FUTURE,
        "raw_symbol": raw_symbol,
        "symbol": symbol,
        "side": side,
        "dex": dex,
    }
    return AccountPosition(
        id=account_position_id(
            account_id=account_id,
            exchange=exchange,
            market_type=MarketType.FUTURE,
            raw_symbol=raw_symbol,
            side=side,
            dex=dex,
        ),
        **identity,
        quantity=0.25,
        quantity_unit="BTC",
        contract_quantity=250,
        contract_multiplier=0.001,
        entry_price=60_000,
        mark_price=61_000,
        notional_usdt=15_250,
        unrealized_pnl_usdt=250,
        roi_pct=8.2,
        leverage=5,
        price_basis="交易所标记价",
        updated_at=NOW,
    )


class FakeProvider:
    def __init__(
        self,
        exchange: str,
        account_id: str,
        account_label: str,
        positions: list[AccountPosition],
        *,
        configured: bool = True,
    ):
        self.exchange = exchange
        self.account_id = account_id
        self.account_label = account_label
        self.positions = positions
        self.configured = configured
        self.error: Exception | None = None

    async def fetch_positions(self) -> list[AccountPosition]:
        if self.error is not None:
            raise self.error
        return self.positions


def test_position_identity_keeps_account_market_side_and_hyperliquid_dex() -> None:
    identities = {
        account_position_id(
            account_id="gate:primary",
            exchange="gate",
            market_type="future",
            raw_symbol="BTC_USDT",
            side="long",
            dex=None,
        ),
        account_position_id(
            account_id="gate:secondary",
            exchange="gate",
            market_type="future",
            raw_symbol="BTC_USDT",
            side="long",
            dex=None,
        ),
        account_position_id(
            account_id="gate:primary",
            exchange="gate",
            market_type="future",
            raw_symbol="BTC_USDT",
            side="short",
            dex=None,
        ),
        account_position_id(
            account_id="hl:primary",
            exchange="hyperliquid",
            market_type="future",
            raw_symbol="BTC",
            side="long",
            dex="main",
        ),
        account_position_id(
            account_id="hl:primary",
            exchange="hyperliquid",
            market_type="future",
            raw_symbol="xyz:BTC",
            side="long",
            dex="xyz",
        ),
    }
    assert len(identities) == 5

    with pytest.raises(ValueError, match="explicit DEX"):
        AccountPositionIdentity(
            id=account_position_id(
                account_id="hl:primary",
                exchange="hyperliquid",
                market_type="future",
                raw_symbol="BTC",
                side="long",
                dex=None,
            ),
            account_id="hl:primary",
            account_label="主账户",
            exchange="hyperliquid",
            market_type="future",
            raw_symbol="BTC",
            symbol="BTCUSDT",
            side="long",
            dex=None,
        )


@pytest.mark.asyncio
async def test_account_position_aggregation_isolates_failures_and_marks_cached_data_stale() -> None:
    working = FakeProvider("gate", "gate:primary", "主账户", [position()])
    denied = FakeProvider("okx", "okx:primary", "只读账户", [])
    denied.error = AccountPositionPermissionError("sensitive upstream response")
    missing = FakeProvider("bybit", "bybit:primary", "默认账户", [], configured=False)
    service = AccountPositionService([working, denied, missing])

    first = await service.snapshot()

    assert len(first.positions) == 1
    assert first.accounts[0].state == AccountPositionAccountState.OK
    assert first.accounts[1].state == AccountPositionAccountState.PERMISSION_DENIED
    assert first.accounts[1].message == "凭据无持仓读取权限或已失效"
    assert first.accounts[2].state == AccountPositionAccountState.NOT_CONFIGURED

    working.error = RuntimeError("API key must never be returned")
    second = await service.snapshot()

    assert len(second.positions) == 1
    assert second.positions[0].freshness == AccountPositionFreshness.STALE
    assert second.accounts[0].state == AccountPositionAccountState.STALE
    assert "最近一次成功快照" in second.accounts[0].message
    assert "API key" not in second.accounts[0].message


class FakeGateClient:
    has_credentials = True

    async def list_positions(self, settle: str) -> list[dict]:
        assert settle == "usdt"
        return [
            {
                "contract": "BTC_USDT",
                "size": "20",
                "mode": "dual_long",
                "entry_price": "60000",
                "mark_price": "61000",
                "value": "1220",
                "unrealised_pnl": "20",
                "margin": "244",
                "leverage": "5",
            },
            {
                "contract": "BTC_USDT",
                "size": "8",
                "mode": "dual_short",
                "entry_price": "62000",
                "mark_price": "61000",
                "value": "488",
                "unrealised_pnl": "8",
                "margin": "97.6",
                "leverage": "5",
            },
            {"contract": "ETH_USDT", "size": "0"},
        ]

    async def list_contracts(self, settle: str) -> list[dict]:
        assert settle == "usdt"
        return [{"name": "BTC_USDT", "quanto_multiplier": "0.001"}]


@pytest.mark.asyncio
async def test_gate_provider_parses_real_open_positions_and_dual_sides() -> None:
    provider = GateAccountPositionProvider(
        FakeGateClient(),  # type: ignore[arg-type]
        account_id="primary",
        account_label="主账户",
    )

    positions = await provider.fetch_positions()

    assert [item.side for item in positions] == ["long", "short"]
    assert len({item.id for item in positions}) == 2
    assert positions[0].raw_symbol == "BTC_USDT"
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].quantity == pytest.approx(0.02)
    assert positions[0].contract_quantity == pytest.approx(20)
    assert positions[0].contract_multiplier == pytest.approx(0.001)
    assert positions[0].roi_pct == pytest.approx(20 / 244 * 100)
    assert positions[0].estimated_fields == ["roi_pct"]


def test_hidden_position_identity_persists_across_app_restart(tmp_path) -> None:
    database_path = tmp_path / "floating-position-settings.db"
    app_settings = Settings(
        dashboard_password="secret",
        database_url=f"sqlite:///{database_path.as_posix()}",
    )
    hidden = AccountPositionIdentity.model_validate(position().model_dump()).model_dump(mode="json")
    with TestClient(create_app(settings=app_settings)) as client:
        saved = client.post(
            "/api/settings/floating-watch/positions",
            headers={"X-Dashboard-Password": "secret"},
            json={"action": "add", "position": hidden},
        )
        assert saved.status_code == 200
        assert saved.json()["hidden_positions"] == [hidden]

    with TestClient(create_app(settings=app_settings)) as client:
        restored = client.get("/api/settings/floating-watch").json()
        assert restored["hidden_positions"] == [hidden]
        removed = client.post(
            "/api/settings/floating-watch/positions",
            headers={"X-Dashboard-Password": "secret"},
            json={"action": "remove", "position": hidden},
        )
        assert removed.json()["hidden_positions"] == []


def test_account_position_api_does_not_report_unconfigured_account_as_empty(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GATE_API_KEY", raising=False)
    monkeypatch.delenv("GATE_API_SECRET", raising=False)
    app = create_app(settings=Settings(database_url="sqlite:///:memory:"))

    with TestClient(app) as client:
        response = client.get("/api/account-positions")

    assert response.status_code == 200
    assert response.json()["positions"] == []
    assert response.json()["accounts"][0]["configured"] is False
    assert response.json()["accounts"][0]["state"] == "not_configured"
    assert response.json()["accounts"][0]["message"] == "尚未配置账户凭据"
