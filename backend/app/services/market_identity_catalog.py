"""Fixed, public official providers. Never fetch a user-supplied evidence URL."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import httpx

from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey
from app.services.fs_identity import SPEC_URLS, reviewed_fs_bindings

GATE_COINS = "https://api.gateio.ws/api/v4/spot/currencies"
BITGET_COINS = "https://api.bitget.com/api/v2/spot/public/coins"
GATE_PAIRS = "https://api.gateio.ws/api/v4/spot/currency_pairs"
GATE_CONTRACTS = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
BITGET_PAIRS = "https://api.bitget.com/api/v2/spot/public/symbols"
BITGET_CONTRACTS = "https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES"
BYBIT_CONTRACTS = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"
HL_INFO = "https://api.hyperliquid.xyz/info"
BINANCE_SPEC = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_PRL_NOTICE = (
    "https://www.binance.com/en/support/announcement/detail/0116dd83010043bd95c37626e2277bbe"
)

NATIVES = {
    "BTC": ("Bitcoin", "crypto.bitcoin"),
    "ETH": ("Ethereum", "crypto.ethereum"),
    "SOL": ("Solana", "crypto.solana"),
    "DOGE": ("Dogecoin", "crypto.dogecoin"),
    "XRP": ("XRP", "crypto.xrp"),
}
CHAINS = {
    "BEP20": "BSC",
    "BSC": "BSC",
    "ERC20": "ETH",
    "ETH": "ETH",
    "SOL": "SOL",
    "SOLANA": "SOL",
    "TRC20": "TRX",
    "TRX": "TRX",
}
PERLE_ADDRESS = ("SOL", "PERLEQKUNUp1dgFZ8EvyXHdN9d6ZQqfGxALDvfs6pDs")
WORMHOLE_ADDRESS = ("SOL", "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ")


def _addresses(chains, *, bitget=False):
    result = set()
    for item in chains or []:
        chain = str(item.get("chain" if bitget else "name") or "").upper()
        address = str(item.get("contractAddress" if bitget else "addr") or "").strip()
        if not address or chain not in CHAINS:
            continue
        normalized = CHAINS[chain]
        if normalized in {"ETH", "BSC"}:
            if re.fullmatch(r"0x[0-9a-fA-F]{40}", address) is None:
                continue
            address = address.lower()
        elif (
            normalized == "SOL"
            and re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", address) is None
            or normalized == "TRX"
            and re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", address) is None
        ):
            continue
        result.add((normalized, address))
    return result


def _evidence(url, value, now, summary):
    return IdentityEvidence(
        source_type="official_metadata",
        url=url,
        summary=summary,
        response_hash=hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
        fetched_at=now,
    )


def catalog_bindings(payloads, *, now=None):
    """Link Gate and Bitget only via chain+address, or curated native identity.

    Full names remain evidence, never a general join key. Futures must also
    name that underlying asset in the venue's own product specification.
    """
    now = now or datetime.now(UTC)
    gate_coins = payloads.get(GATE_COINS, [])
    bitget_coins = (payloads.get(BITGET_COINS) or {}).get("data", [])
    identities = {}
    address_owners = {}
    for coin in gate_coins:
        if coin.get("delisted") or coin.get("trade_disabled"):
            continue
        if set(coin.get("category") or []).intersection(
            {"stocks", "stock", "rwa", "index", "commodities"}
        ):
            continue
        ticker, name = coin.get("currency"), coin.get("name")
        addresses = _addresses(coin.get("chains"))
        if ticker in NATIVES and str(name).casefold() == NATIVES[ticker][0].casefold():
            asset_id = NATIVES[ticker][1]
        elif addresses and name:
            primary = PERLE_ADDRESS if PERLE_ADDRESS in addresses else min(addresses)
            asset_id = (
                "crypto.perle"
                if primary == PERLE_ADDRESS
                else "crypto.wormhole"
                if WORMHOLE_ADDRESS in addresses
                else "crypto.contract."
                + hashlib.sha256(json.dumps(primary).encode()).hexdigest()[:24]
            )
        else:
            continue
        evidence = [
            _evidence(
                GATE_COINS,
                coin,
                now,
                f"Gate 官方资产 {ticker} / {name}，链与合约地址 {json.dumps(sorted(addresses))}",
            )
        ]
        identities[("gate", ticker)] = (asset_id, name, evidence)
        for address in addresses:
            address_owners.setdefault(address, set()).add((asset_id, name))
    for coin in bitget_coins:
        ticker = coin.get("coin")
        owners = set()
        for address in _addresses(coin.get("chains"), bitget=True):
            owners.update(address_owners.get(address, set()))
        if ticker in NATIVES:
            # Native-chain identity must be present, rather than a ticker-only
            # match to a wrapped token on a different chain.
            native_chain = ticker
            if any(
                CHAINS.get(c.get("chain"), c.get("chain")) == native_chain
                and not c.get("contractAddress")
                for c in coin.get("chains", [])
            ):
                owners.add((NATIVES[ticker][1], NATIVES[ticker][0]))
        if len(owners) != 1:
            continue
        asset_id, name = next(iter(owners))
        evidence = [
            _evidence(
                BITGET_COINS,
                coin,
                now,
                f"Bitget 官方链与合约地址 {json.dumps(sorted(_addresses(coin.get('chains'), bitget=True)))}；跨所匹配相同链与地址",
            )
        ]
        # Keep the matching source attestation as well as the target venue's.
        evidence.extend(
            next((v[2] for k, v in identities.items() if k[0] == "gate" and v[0] == asset_id), [])
        )
        identities[("bitget", ticker)] = (asset_id, name, evidence)
    result = []
    sources = [
        ("gate", "spot", GATE_PAIRS, payloads.get(GATE_PAIRS, [])),
        ("gate", "future", GATE_CONTRACTS, payloads.get(GATE_CONTRACTS, [])),
        ("bitget", "spot", BITGET_PAIRS, (payloads.get(BITGET_PAIRS) or {}).get("data", [])),
        (
            "bitget",
            "future",
            BITGET_CONTRACTS,
            (payloads.get(BITGET_CONTRACTS) or {}).get("data", []),
        ),
    ]
    for exchange, market_type, url, products in sources:
        for product in products:
            if exchange == "gate":
                raw = product.get("id" if market_type == "spot" else "name", "")
                base = product.get("base") if market_type == "spot" else raw.removesuffix("_USDT")
                quote = (
                    product.get("quote")
                    if market_type == "spot"
                    else "USDT"
                    if raw.endswith("_USDT")
                    else None
                )
                if (
                    product.get("in_delisting")
                    or product.get("type", "direct") != "direct"
                    or product.get("is_pre_market")
                ):
                    continue
            else:
                raw, base, quote = (
                    product.get("symbol"),
                    product.get("baseCoin"),
                    product.get("quoteCoin"),
                )
                if product.get("isRwa") == "YES":
                    continue
            identity = identities.get((exchange, base))
            if identity is None or quote != "USDT" or not raw:
                continue
            asset_id, name, evidence = identity
            size = (
                product.get("quanto_multiplier")
                if exchange == "gate" and market_type == "future"
                else None
            )
            result.append(
                MarketBinding(
                    market=MarketKey(exchange=exchange, market_type=market_type, raw_symbol=raw),
                    asset_id=asset_id,
                    display_name=name,
                    identity_status="verified",
                    unit_status="verified",
                    quote_currency=quote,
                    settlement_currency=quote if market_type == "future" else None,
                    contract_kind="linear" if market_type == "future" else "spot",
                    contract_size=float(size) if size else None,
                    verified_at=now,
                    valid_until=now + timedelta(days=7),
                    evidence=[
                        *evidence,
                        _evidence(
                            url,
                            product,
                            now,
                            f"官方产品规格明确底层 {base}、报价 {quote}，按单个底层资产报价",
                        ),
                    ],
                )
            )
    # Bybit explicitly identifies Perle in its listing announcement. The
    # instrument fullName must still agree; other same-ticker projects do not
    # inherit this reviewed exception.
    for product in (payloads.get(BYBIT_CONTRACTS) or {}).get("result", {}).get("list", []):
        base = product.get("baseCoin")
        native = ("Wormhole", "crypto.wormhole") if base == "W" else NATIVES.get(base)
        is_perle = base == "PRL" and product.get("fullName") == "Perle"
        if not is_perle and not (
            native and product.get("fullName", "").casefold() == native[0].casefold()
        ):
            continue
        if (
            product.get("quoteCoin") != "USDT"
            or product.get("contractType") != "LinearPerpetual"
            or product.get("status") != "Trading"
        ):
            continue
        result.append(
            MarketBinding(
                market=MarketKey(
                    exchange="bybit", market_type="future", raw_symbol=product["symbol"]
                ),
                asset_id="crypto.perle" if is_perle else native[1],
                display_name="Perle" if is_perle else native[0],
                identity_status="verified",
                unit_status="verified",
                quote_currency="USDT",
                settlement_currency=product.get("settleCoin"),
                verified_at=now,
                valid_until=now + timedelta(days=7),
                evidence=[
                    _evidence(
                        BYBIT_CONTRACTS,
                        product,
                        now,
                        "Bybit 官方 fullName、底层资产和线性合约规格核验",
                    )
                ],
            )
        )
    annotation, meta = payloads.get("hl_prl_annotation"), payloads.get("hl_io_meta")
    if (
        isinstance(annotation, dict)
        and annotation.get("displayName") == "PEARL"
        and isinstance(meta, list)
        and len(meta) == 2
    ):
        product = next(
            (
                p
                for p in meta[0].get("universe", [])
                if p.get("name") == "io:PRL" and not p.get("isDelisted")
            ),
            None,
        )
        if product and meta[0].get("collateralToken", 0) == 0:
            evidence = _evidence(
                HL_INFO,
                {"annotation": annotation, "asset": product},
                now,
                "精确 io:PRL 的官方标注为 PEARL，独立于 Perle，io 市场报价 USDC",
            )
            evidence.query = {"type": "perpAnnotation", "coin": "io:PRL"}
            result.append(
                MarketBinding(
                    market=MarketKey(
                        exchange="hyperliquid", market_type="future", raw_symbol="io:PRL", dex="io"
                    ),
                    asset_id="crypto.pearl-research",
                    display_name="Pearl",
                    identity_status="verified",
                    unit_status="verified",
                    quote_currency="USDC",
                    settlement_currency="USDC",
                    contract_size=1,
                    verified_at=now,
                    valid_until=now + timedelta(days=7),
                    evidence=[evidence],
                )
            )
    # These are bounded, explicitly reviewed bindings, not a rule that every
    # same-named native/stock ticker on every venue is the same asset.
    reviewed_at = datetime(2026, 10, 15, tzinfo=UTC)
    for product in (payloads.get(BINANCE_SPEC) or {}).get("symbols", []):
        base = product.get("baseAsset")
        if base not in {*NATIVES, "PRL", "W"} or product.get("symbol") != f"{base}USDT":
            continue
        if (
            product.get("status") != "TRADING"
            or product.get("contractType") != "PERPETUAL"
            or product.get("quoteAsset") != "USDT"
        ):
            continue
        name, asset = (
            ("Perle", "crypto.perle")
            if base == "PRL"
            else ("Wormhole", "crypto.wormhole")
            if base == "W"
            else NATIVES[base]
        )
        evidence = [
            _evidence(BINANCE_SPEC, product, now, f"经核验的 Binance {name} 单位与当前原始合约规格")
        ]
        if base in {"PRL", "W"}:
            evidence.append(
                IdentityEvidence(
                    source_type="official_announcement",
                    url=BINANCE_PRL_NOTICE
                    if base == "PRL"
                    else "https://www.binance.com/en/support/announcement/detail/299a0918dd7b4b31ae12aa83cff0d61c",
                    summary=f"官方公告明确底层资产 {name} ({base}) 与 USDT 结算合约",
                    fetched_at=reviewed_at,
                )
            )
        result.append(
            MarketBinding(
                market=MarketKey(
                    exchange="binance", market_type="future", raw_symbol=product["symbol"]
                ),
                asset_id=asset,
                display_name=name,
                identity_status="verified",
                unit_status="verified",
                quote_currency="USDT",
                settlement_currency="USDT",
                contract_size=1,
                verified_at=reviewed_at,
                valid_until=reviewed_at + timedelta(days=30),
                evidence=evidence,
            )
        )
    for product in (payloads.get("hl_main_meta") or {}).get("universe", []):
        base = product.get("name")
        if base not in {"BTC", "ETH"} or product.get("isDelisted"):
            continue
        name, asset = NATIVES[base]
        evidence = _evidence(
            HL_INFO,
            product,
            now,
            f"已复核的 HL 默认 DEX {base} / {name}，原生加密资产单位，USDC 计价；不适用于其他 DEX 同名代码",
        )
        evidence.query = {"type": "meta"}
        result.append(
            MarketBinding(
                market=MarketKey(
                    exchange="hyperliquid", market_type="future", raw_symbol=base, dex="main"
                ),
                asset_id=asset,
                display_name=name,
                identity_status="verified",
                unit_status="verified",
                quote_currency="USDC",
                settlement_currency="USDC",
                contract_size=1,
                verified_at=reviewed_at,
                valid_until=reviewed_at + timedelta(days=30),
                evidence=[evidence],
            )
        )
    result.extend(reviewed_fs_bindings(payloads, now))
    return result


class OfficialMarketIdentityCatalog:
    def __init__(self, service):
        self.service = service
        self.status = {"state": "not_started", "imported": 0, "errors": {}}

    async def refresh(self):
        urls = [
            GATE_COINS,
            BITGET_COINS,
            GATE_PAIRS,
            GATE_CONTRACTS,
            BITGET_PAIRS,
            BITGET_CONTRACTS,
            BYBIT_CONTRACTS,
            BINANCE_SPEC,
            *SPEC_URLS,
        ]
        semaphore = asyncio.Semaphore(3)
        async with httpx.AsyncClient(timeout=10) as client:

            async def get(url):
                async with semaphore:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()

            responses = await asyncio.gather(*(get(url) for url in urls), return_exceptions=True)

            async def hl(body):
                async with semaphore:
                    response = await client.post(HL_INFO, json=body)
                    response.raise_for_status()
                    return response.json()

            hl_results = await asyncio.gather(
                hl({"type": "perpAnnotation", "coin": "io:PRL"}),
                hl({"type": "metaAndAssetCtxs", "dex": "io"}),
                hl({"type": "meta"}),
                return_exceptions=True,
            )
        payloads = {
            url: response
            for url, response in zip(urls, responses)
            if not isinstance(response, BaseException)
        }
        payloads.update(
            {
                key: response
                for key, response in zip(
                    ("hl_prl_annotation", "hl_io_meta", "hl_main_meta"), hl_results
                )
                if not isinstance(response, BaseException)
            }
        )
        errors = {
            url: type(response).__name__
            for url, response in zip(urls, responses)
            if isinstance(response, BaseException)
        }
        errors.update(
            {
                key: type(response).__name__
                for key, response in zip(
                    ("hl_prl_annotation", "hl_io_meta", "hl_main_meta"), hl_results
                )
                if isinstance(response, BaseException)
            }
        )
        bindings = catalog_bindings(payloads)
        imported = await self.service.repository.import_catalog(bindings)
        if imported:
            await self.service.refresh()
        self.status = {
            "state": "degraded" if errors else "ok",
            "imported": imported,
            "errors": errors,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    async def run(self, stop):
        while not stop.is_set():
            try:
                await self.refresh()
            except Exception as exc:  # noqa: BLE001 - catalog failures cannot stop live collection.
                self.status = {"state": "failed", "error": type(exc).__name__}
            try:
                await asyncio.wait_for(stop.wait(), timeout=86400)
            except TimeoutError:
                pass
