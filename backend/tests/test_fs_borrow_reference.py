from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from test_fs_monitor import inputs

from app.models.fs_monitor import FsCandidate, FsSettings
from app.services.fs_economics import evaluate
from app.services.fs_public import PublicFsProvider, public_borrow_details


@pytest.mark.asyncio
async def test_okx_custom_quota_overrides_basic_without_guessing_account_tier():
    payload = [
        {
            "basic": [
                {"ccy": "MEW", "quota": "14000000", "rate": "0.00024"},
                {"ccy": "USDT", "quota": "99999999", "rate": "0.9"},
                {"ccy": "MISSING", "quota": "999", "rate": "0.9"},
            ],
            "configCcyList": [{"ccy": "USDT", "rate": "0.00048"}, {"ccy": "MISSING"}],
            "config": [
                {"ccy": "USDT", "level": "VIP 1", "stgyType": "0", "quota": "3000000"},
                {"ccy": "USDT", "level": "Lv1", "stgyType": "1", "quota": "7000000"},
                {"ccy": "USDT", "level": "Lv1", "stgyType": "0", "quota": "1000000"},
            ],
            "regular": [{"level": "Lv1", "loanQuotaCoef": "5"}],
        }
    ]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": "0", "data": payload})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicFsProvider("okx", SimpleNamespace(get_markets=list), client)
        rates, at = await provider.borrow_reference()
    normal = public_borrow_details("okx", rates["MEW"], at)
    custom = public_borrow_details("okx", rates["USDT"], at)
    missing = public_borrow_details("okx", rates["MISSING"], at)
    assert normal.reference_limit == 14000000 and normal.scope == "okx_basic"
    assert custom.reference_limit == 1000000 and custom.scope == "okx_lv1"
    assert rates["USDT"]["hourlyBorrowRate"] == pytest.approx(0.00002)
    assert missing.reference_limit is None and rates["MISSING"]["hourlyBorrowRate"] is None
    assert all(r.method == "GET" and "OK-ACCESS-KEY" not in r.headers for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exchange,body,minimum",
    [
        (
            "bitget",
            {
                "code": "00000",
                "data": [
                    {
                        "baseCoin": "ZIL",
                        "quoteCoin": "USDT",
                        "isCrossBorrowable": True,
                        "status": "1",
                        "userMinBorrow": "0.00000001",
                        "maxTradeAmount": "999999",
                    }
                ],
            },
            1e-8,
        ),
        (
            "gate",
            [
                {
                    "currency_pair": "ZIL_USDT",
                    "status": "enabled",
                    "base_min_borrow_amount": "135",
                    "quote_min_borrow_amount": "1",
                }
            ],
            135,
        ),
    ],
)
async def test_minimum_is_base_borrow_quantity_not_trade_limit_or_quote(exchange, body, minimum):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        provider = PublicFsProvider(exchange, SimpleNamespace(get_markets=list), client)
        rates, at = await provider.borrow_reference()
    ref = public_borrow_details(exchange, rates["ZIL"], at)
    assert ref.minimum_borrow == minimum
    assert ref.reference_limit is None
    assert rates["ZIL"]["borrowable"] is True


@pytest.mark.asyncio
async def test_binance_public_margin_flag_never_becomes_borrow_support_or_inventory():
    body = {
        "symbols": [
            {
                "symbol": "BMTUSDT",
                "baseAsset": "BMT",
                "quoteAsset": "USDT",
                "isMarginTradingAllowed": True,
            },
            {
                "symbol": "BMTBTC",
                "baseAsset": "BMT",
                "quoteAsset": "BTC",
                "isMarginTradingAllowed": False,
            },
        ]
    }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = PublicFsProvider("binance", SimpleNamespace(get_markets=list), client)
        rates, at = await provider.borrow_reference()
        await provider.binance_specs(False)  # reuse the same paced metadata request
    ref = public_borrow_details("binance", rates["BMT"], at)
    assert ref.margin_supported is True
    assert ref.reference_limit is None and ref.minimum_borrow is None
    assert "borrowable" not in rates["BMT"] and "hourlyBorrowRate" not in rates["BMT"]
    assert len(requests) == 1 and requests[0].url.path == "/api/v3/exchangeInfo"
    assert requests[0].method == "GET" and "X-MBX-APIKEY" not in requests[0].headers


@pytest.mark.parametrize(
    "raw,expected",
    [("0", 0), ("262000", 262000), ("", None), ("NaN", None), ("-1", None), (True, None)],
)
def test_public_limit_zero_missing_and_invalid_are_distinct(raw, expected):
    ref = public_borrow_details("bybit", {"maxBorrowingAmount": raw}, datetime.now(UTC))
    assert ref.reference_limit == expected


def test_query_failure_cannot_relabel_cached_public_amount_as_fresh():
    ref = public_borrow_details(
        "bybit", {"maxBorrowingAmount": "999"}, datetime.now(UTC), failed=True
    )
    assert ref.status == "query_failed" and ref.observed_at is None and ref.reference_limit is None
    assert public_borrow_details("gate", {}, datetime.now(UTC)).status == "not_listed"


@pytest.mark.parametrize(
    "account_state,inventory",
    [("not_configured", None), ("permission_denied", None), ("checked", 0)],
)
def test_public_reference_never_satisfies_account_capacity(account_state, inventory):
    row, args = inputs()
    row.account_state = account_state
    row.account_max_loan, row.available_inventory = None if inventory is None else 200, inventory
    row.public_borrow = public_borrow_details(
        "bybit", {"maxBorrowingAmount": "999999"}, args["now"]
    )
    result = evaluate(row, FsSettings(basis_stress_pct=0.1), **args)
    assert result.stage not in {"quota_sufficient", "cost_pass"}
    assert result.available_inventory == inventory
    if account_state != "not_configured":
        assert not result.signal_ready
    # New optional reference also round-trips old persisted history without migrations.
    old = result.model_dump(exclude={"public_borrow"})
    assert FsCandidate.model_validate(old).public_borrow is None
