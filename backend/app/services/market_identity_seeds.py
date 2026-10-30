"""Bounded attestations captured during the PRL investigation on 2026-10-15.

They expire on fixed dates. Restarting the app cannot renew an old attestation.
Fresh official catalog checks can renew evidence-backed metadata separately.
"""

from datetime import UTC, datetime, timedelta

from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey


def reviewed_bindings():
    checked = datetime(2026, 10, 15, 1, 46, tzinfo=UTC)
    until = checked + timedelta(days=7)
    rows = []
    cases = [
        (
            "bitget",
            "future",
            "PRLUSDT",
            "crypto.perle",
            "Perle",
            "USDT",
            None,
            "https://www.bitget.com/zh-CN/support/articles/12560603881229",
            "Perle Solana mint PERLEQKUNUp1dgFZ8EvyXHdN9d6ZQqfGxALDvfs6pDs; official mix specification PRLUSDT/baseCoin PRL/USDT",
        ),
        (
            "bitget",
            "spot",
            "PRLUSDT",
            "crypto.perle",
            "Perle",
            "USDT",
            None,
            "https://www.bitget.com/zh-CN/support/articles/12560603881229",
            "Perle Solana mint PERLEQKUNUp1dgFZ8EvyXHdN9d6ZQqfGxALDvfs6pDs; spot PRL/USDT",
        ),
        (
            "bybit",
            "future",
            "PRLUSDT",
            "crypto.perle",
            "Perle",
            "USDT",
            None,
            "https://announcements.bybit.com/zh-MY/article/new-listing-prlusdt-perpetual-contract-in-innovation-zone-with-up-to-25x-leverage-blt5c4784cb046e26e6/",
            "Official underlying Perle; live instruments-info fullName Perle, baseCoin PRL, LinearPerpetual, USDT settlement",
        ),
        (
            "binance",
            "future",
            "PRLUSDT",
            "crypto.perle",
            "Perle",
            "USDT",
            1,
            "https://www.binance.com/en/support/announcement/detail/0116dd83010043bd95c37626e2277bbe",
            "Official underlying Perle, Solana AI project, PRLUSDT linear contract and USDT settlement",
        ),
        (
            "gate",
            "future",
            "PRL_USDT",
            "crypto.perle",
            "Perle",
            "USDT",
            10,
            "https://api.gateio.ws/api/v4/futures/usdt/contracts/PRL_USDT",
            "Official currencies PRL/Perle Solana mint agrees with Bitget; direct contract, quanto_multiplier 10, per-base-unit USDT price",
        ),
        (
            "gate",
            "spot",
            "PRL_USDT",
            "crypto.perle",
            "Perle",
            "USDT",
            None,
            "https://api.gateio.ws/api/v4/spot/currencies",
            "PRL/Perle Solana mint PERLEQKUNUp1dgFZ8EvyXHdN9d6ZQqfGxALDvfs6pDs; PRL_USDT spot pair",
        ),
        (
            "hyperliquid",
            "future",
            "io:PRL",
            "crypto.pearl-research",
            "Pearl",
            "USDC",
            1,
            "https://api.hyperliquid.xyz/info",
            "Exact io:PRL annotation displayName PEARL, keywords prl/pearl; io meta collateralToken 0, separate from Perle",
        ),
    ]
    for exchange, kind, raw, asset, name, quote, size, url, summary in cases:
        evidence = IdentityEvidence(
            source_type="official_metadata" if "api." in url else "official_announcement",
            url=url,
            summary=summary,
            fetched_at=checked,
        )
        if exchange == "hyperliquid":
            evidence.query = {"type": "perpAnnotation", "coin": "io:PRL"}
            evidence.response_hash = (
                "21240c71b5ac5a3b0585e65c0af92cf27078dc43a6c573744d85e910332fecd2"
            )
        rows.append(
            MarketBinding(
                market=MarketKey(exchange=exchange, market_type=kind, raw_symbol=raw),
                asset_id=asset,
                display_name=name,
                identity_status="verified",
                unit_status="verified",
                quote_currency=quote,
                settlement_currency=quote if kind == "future" else None,
                contract_kind="linear" if kind == "future" else "spot",
                contract_size=size,
                verified_at=checked,
                valid_until=until,
                evidence=[evidence],
            )
        )
    return rows
