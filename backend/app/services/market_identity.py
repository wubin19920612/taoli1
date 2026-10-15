from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

from app.models.market import MarketSnapshot
from app.models.market_identity import MarketBinding, MarketKey, utc


class MarketIdentityRegistry:
    """Atomically replaceable exact-market bindings; no network or DB in readers."""

    def __init__(self, bindings=(), *, mode="enforce"):
        self.mode = mode
        self.replace(bindings)

    def replace(self, bindings):
        self.bindings = MappingProxyType({b.market.key: b.model_copy(deep=True) for b in bindings})

    def get(self, market: MarketKey) -> MarketBinding | None:
        return self.bindings.get(market.key)

    def annotate(self, row: MarketSnapshot, now: datetime | None = None) -> MarketSnapshot:
        now = utc(now or datetime.now(UTC))
        try:
            key = MarketKey.from_snapshot(row)
        except ValueError:
            return row.model_copy(
                update={
                    "identity_enforced": self.mode == "enforce",
                    "identity_status": "conflict",
                    "asset_id": None,
                }
            )
        binding = self.get(key)
        common = {
            "identity_enforced": self.mode == "enforce",
            "identity_checked": True,
            "quote_time_source": row.quote_time_source
            if row.book_timestamp
            else "upstream"
            if row.upstream_timestamp
            else "received",
        }
        if binding is None:
            return row.model_copy(
                update={
                    **common,
                    "asset_id": None,
                    "asset_name": None,
                    "identity_status": "pending",
                    "binding_revision": 0,
                    "unit_status": "pending",
                }
            )
        status = binding.identity_status
        if status == "verified" and binding.valid_until and utc(binding.valid_until) <= now:
            status = "expired"
        # A changed, observable contract face value invalidates the old attestation.
        if (
            binding.contract_size is not None
            and row.contract_size_multiplier is not None
            and binding.contract_size != row.contract_size_multiplier
        ):
            status = "conflict"
        updates = {
            **common,
            "asset_id": binding.asset_id,
            "asset_name": binding.display_name,
            "asset_kind": binding.asset_kind if status == "verified" else row.asset_kind,
            "identity_status": status,
            "unit_status": binding.unit_status,
            "binding_revision": binding.revision,
            "binding_valid_until": binding.valid_until,
            "binding_fingerprint": binding.fingerprint,
            "original_quote_currency": binding.quote_currency,
            "contract_kind": binding.contract_kind,
            "tracking_definition": binding.tracking_definition,
        }
        # Existing alias conversion is already reflected in prices/sizes. Apply
        # only the ratio between the attested conversion and the existing one.
        if status == "verified" and binding.unit_status == "verified":
            ratio = binding.price_multiplier / row.symbol_alias_price_multiplier
            for field in ("bid", "ask", "mark_price", "index_price"):
                value = getattr(row, field)
                if value is not None:
                    updates[field] = value * ratio
            for field in ("bid_size", "ask_size"):
                value = getattr(row, field)
                if value is not None:
                    updates[field] = value / ratio
            updates["symbol_alias_price_multiplier"] = binding.price_multiplier
        return row.model_copy(update=updates)

    def annotate_all(self, markets, now=None):
        return [self.annotate(row, now) for row in markets]
