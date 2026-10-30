"""Bounded, reviewed Bybit FS targets; never infer identity from a ticker join."""

from datetime import UTC, datetime, timedelta

from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey

REVIEWED_AT = datetime(2026, 10, 22, 9, 20, tzinfo=UTC)
TARGETS = {
    "MEWUSDT": (
        "MEW",
        "cat in a dogs world",
        "crypto.contract.2c65b00983a0362471a59c8f",
        "https://www.bybit.com/en/learn/memes/what-is-mew-cat-coin",
    ),
    "BMTUSDT": (
        "BMT",
        "Bubblemaps",
        "crypto.contract.c09a22c42528e5740792aac9",
        "https://www.bybit.com/en/learn/crypto/what-is-bubblemaps-bmt",
    ),
}


def spec_url(symbol, category):
    return f"https://api.bybit.com/v5/market/instruments-info?category={category}&symbol={symbol}"


SPEC_URLS = [spec_url(s, c) for s in TARGETS for c in ("spot", "linear")]


def reviewed_fs_bindings(payloads, now):
    from app.services.market_identity_catalog import _evidence

    until = REVIEWED_AT + timedelta(days=7)
    if now >= until or now < REVIEWED_AT:
        return []
    result = []
    for symbol, (base, name, asset, article) in TARGETS.items():
        specs = {}
        for category in ("spot", "linear"):
            payload = payloads.get(spec_url(symbol, category)) or {}
            rows = payload.get("result", {}).get("list", [])
            if payload.get("retCode") == 0 and len(rows) == 1 and rows[0].get("symbol") == symbol:
                specs[category] = rows[0]
        if len(specs) != 2:
            continue
        spot, future = specs["spot"], specs["linear"]
        if (
            any(
                p.get("baseCoin") != base
                or p.get("quoteCoin") != "USDT"
                or p.get("status") != "Trading"
                for p in specs.values()
            )
            or future.get("fullName") != name
            or future.get("contractType") != "LinearPerpetual"
            or future.get("settleCoin") != "USDT"
            or future.get("isPreListing") is not False
            or str(spot.get("xstockMultiplier", "1")) != "1"
        ):
            continue
        evidence = [
            IdentityEvidence(
                source_type="official_announcement",
                url=article,
                fetched_at=REVIEWED_AT,
                summary=f"人工复核 Bybit 官方文章明确 {name} 的 {base}/USDT 现货及 {symbol} 永续；仅限这两个精确市场，不按同名代码推广。",
            )
        ] + [
            _evidence(
                spec_url(symbol, category),
                product,
                now,
                "当前精确市场的基础币、USDT 报价、交易状态与单位规格",
            )
            for category, product in specs.items()
        ]
        for category, kind in (("spot", "spot"), ("linear", "future")):
            result.append(
                MarketBinding(
                    market=MarketKey(exchange="bybit", market_type=kind, raw_symbol=symbol),
                    asset_id=asset,
                    display_name=name,
                    identity_status="verified",
                    unit_status="verified",
                    quote_currency="USDT",
                    settlement_currency="USDT" if kind == "future" else None,
                    contract_kind="linear" if kind == "future" else "spot",
                    contract_size=1,
                    verified_at=REVIEWED_AT,
                    valid_until=until,
                    evidence=evidence,
                )
            )
    return result
