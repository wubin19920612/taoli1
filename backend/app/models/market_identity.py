from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MARKET_KEY_CACHE_SIZE = 32768


@lru_cache(maxsize=MARKET_KEY_CACHE_SIZE)
def _encode_key(exchange, market_type, dex, raw_symbol) -> str:
    return json.dumps([exchange, market_type, dex, raw_symbol], separators=(",", ":"))


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MarketKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    exchange: str = Field(min_length=1, max_length=64)
    market_type: Literal["spot", "future"]
    dex: str | None = None
    raw_symbol: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def normalize(self):
        exchange = self.exchange.strip().lower()
        raw = self.raw_symbol.strip()
        dex = self.dex.strip().lower() if self.dex else None
        if exchange == "hyperliquid" and self.market_type == "future":
            prefix = raw.split(":", 1)[0].lower() if ":" in raw else "main"
            if dex is not None and prefix != dex:
                raise ValueError("DEX conflicts with raw market prefix")
            dex = prefix
        elif dex:
            raise ValueError("DEX is only valid for Hyperliquid perpetuals")
        if not exchange or not raw:
            raise ValueError("exact market identity is required")
        object.__setattr__(self, "exchange", exchange)
        object.__setattr__(self, "raw_symbol", raw)
        object.__setattr__(self, "dex", dex)
        return self

    @classmethod
    def from_snapshot(cls, row):
        return cls(
            exchange=row.exchange,
            market_type=row.market_type,
            dex=row.dex,
            raw_symbol=row.raw_symbol,
        )

    @property
    def key(self) -> str:
        # raw symbols can contain colons. This encoding is also used by the DB.
        return _encode_key(self.exchange, self.market_type, self.dex, self.raw_symbol)


class IdentityEvidence(BaseModel):
    source_type: Literal["official_announcement", "official_metadata", "official_specification"]
    url: str = Field(min_length=8, max_length=2048, pattern=r"^https://")
    summary: str = Field(min_length=3, max_length=4000)
    fetched_at: datetime
    response_hash: str | None = Field(default=None, max_length=128)
    query: dict[str, str] = Field(default_factory=dict)


class AssetIdentity(BaseModel):
    asset_id: str = Field(min_length=2, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=160)
    asset_kind: Literal["crypto", "equity", "index", "commodity", "valuation", "other"] = "crypto"
    identifiers: dict[str, str] = Field(default_factory=dict)


class MarketBinding(BaseModel):
    market: MarketKey
    asset_id: str | None = Field(default=None, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(default="待核验", min_length=1, max_length=160)
    asset_kind: Literal["crypto", "equity", "index", "commodity", "valuation", "other"] = "crypto"
    identity_status: Literal["pending", "verified", "conflict", "expired"] = "pending"
    unit_status: Literal["pending", "verified"] = "pending"
    quote_currency: str = Field(default="USDT", min_length=1, max_length=20)
    settlement_currency: str | None = Field(default=None, max_length=20)
    price_multiplier: float = Field(default=1, gt=0, allow_inf_nan=False)
    contract_size: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    contract_kind: Literal["spot", "linear", "inverse", "valuation"] = "linear"
    tracking_definition: str = Field(default="", max_length=2000)
    spec_fingerprint: str | None = Field(default=None, max_length=128)
    revision: int = Field(default=1, ge=1)
    verified_at: datetime | None = None
    valid_until: datetime | None = None
    evidence: list[IdentityEvidence] = Field(default_factory=list, max_length=20)

    @field_validator("quote_currency", "settlement_currency")
    @classmethod
    def normalize_currency(cls, value):
        return value.strip().upper() if value else value

    @model_validator(mode="after")
    def verified_has_evidence(self):
        if self.identity_status == "verified":
            if (
                not self.evidence
                or not self.asset_id
                or not self.verified_at
                or not self.valid_until
            ):
                raise ValueError("verified identity requires asset, evidence and validity dates")
            if utc(self.valid_until) <= utc(self.verified_at):
                raise ValueError("identity validity must end after verification")
        if self.unit_status == "verified" and (
            not self.evidence or self.identity_status != "verified"
        ):
            raise ValueError("verified units require verified identity evidence")
        if (
            self.unit_status == "verified"
            and self.contract_kind == "valuation"
            and not self.tracking_definition.strip()
        ):
            raise ValueError("valuation units require an explicit tracking definition")
        return self

    @property
    def fingerprint(self) -> str:
        values = [
            self.asset_id,
            self.quote_currency,
            self.settlement_currency,
            float(self.price_multiplier),
            float(self.contract_size) if self.contract_size is not None else None,
            self.contract_kind,
            self.tracking_definition,
            self.spec_fingerprint,
        ]
        return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()


class BindingMutation(BaseModel):
    binding: MarketBinding
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=2000)


class ValidationIssue(BaseModel):
    code: str
    message: str
    leg: Literal["buy", "sell", "pair"] = "pair"


class PairCapabilities(BaseModel):
    raw_market_view: bool = True
    reference_comparison: bool = True
    same_asset_opportunity: bool = False
    strategy_alert: bool = False
    automatic_card: bool = False
    manual_card: bool = False


class PairValidationResult(BaseModel):
    registry_revision: int = 0
    comparison_kind: Literal["same_asset", "custom_cross_asset"] = "same_asset"
    identity_relation: Literal["same", "different", "unresolved", "conflict"] = "unresolved"
    unit_status: Literal["verified", "unverified"] = "unverified"
    quote_status: Literal["executable", "reference", "invalid"] = "invalid"
    anomaly_status: Literal["normal", "pending_review", "passed", "failed"] = "normal"
    buy_market: MarketKey | None = None
    sell_market: MarketKey | None = None
    buy_asset_id: str | None = None
    sell_asset_id: str | None = None
    buy_revision: int = 0
    sell_revision: int = 0
    buy_spec_fingerprint: str | None = None
    sell_spec_fingerprint: str | None = None
    quote_fingerprint: str = ""
    blockers: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    capabilities: PairCapabilities = Field(default_factory=PairCapabilities)
    validated_at: datetime
    expires_at: datetime

    @property
    def reason_codes(self) -> list[str]:
        return list(dict.fromkeys(issue.code for issue in self.blockers))


class MarketValidationSettings(BaseModel):
    mode: Literal["shadow", "enforce"] = "shadow"
    max_leg_time_skew_seconds: float = Field(default=10, ge=0, le=60, allow_inf_nan=False)
    review_ttl_seconds: float = Field(default=15, gt=0, le=30, allow_inf_nan=False)
    review_concurrency: int = Field(default=3, ge=1, le=5)
    review_timeout_seconds: float = Field(default=5, gt=0, le=15, allow_inf_nan=False)
    review_failure_cooldown_seconds: float = Field(default=30, ge=5, le=300, allow_inf_nan=False)


class QuoteConversion(BaseModel):
    base: str
    quote: str = "USDT"
    rate: float = Field(gt=0, allow_inf_nan=False)
    source: str
    observed_at: datetime
    expires_at: datetime
