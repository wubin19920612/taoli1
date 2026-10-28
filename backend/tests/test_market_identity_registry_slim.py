"""M2-4b: offline equivalence and size checks against the registry from 4ad88b7."""

import gc
import time
import weakref
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from app.models.market import MarketSnapshot, MarketType
from app.models.market_identity import IdentityEvidence, MarketBinding, MarketKey, utc
from app.runtime.cache_census import deep_size
from app.services.market_identity import MarketIdentityRegistry


class LegacyMarketIdentityRegistry:
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


NOW = datetime(2026, 10, 28, 2, tzinfo=UTC)
UNTIL = NOW + timedelta(days=1)


def market(raw="PRLUSDT", **updates):
    values = {
        "symbol": "PRLUSDT", "base": "PRL", "exchange": "bitget",
        "market_type": MarketType.FUTURE, "raw_symbol": raw,
        "bid": 100.0, "ask": 101.0, "bid_size": 2.0, "ask_size": 3.0,
        "mark_price": 100.5, "index_price": 100.25, "timestamp": NOW,
    }
    values.update(updates)
    return MarketSnapshot(**values)


def evidence(count=1):
    return [
        IdentityEvidence(
            source_type="official_specification", url=f"https://example.test/spec/{index}",
            summary=f"Official contract specification {index}", fetched_at=NOW,
            response_hash=f"response-{index}", query={"market": "PRLUSDT"},
        )
        for index in range(count)
    ]


def binding(row, **updates):
    values = {
        "market": MarketKey.from_snapshot(row), "asset_id": "perle", "display_name": "Perle",
        "identity_status": "verified", "unit_status": "verified", "quote_currency": "USDT",
        "verified_at": NOW - timedelta(days=1), "valid_until": UNTIL, "evidence": evidence(),
        "revision": 7,
    }
    values.update(updates)
    return MarketBinding(**values)


def equivalence_cases():
    rows = {
        name: market(name + "USDT")
        for name in (
            "pending", "verified", "expired", "conflict", "multiplier", "contract_mismatch",
            "valuation", "settle_usdt", "settle_usdc", "spec_a", "spec_b", "naive_validity",
            "null_prices", "null_sizes", "alias", "upstream", "book", "missing",
        )
    }
    rows["contract_mismatch"].contract_size_multiplier = 2
    rows["alias"].symbol_alias_price_multiplier = 0.01
    rows["upstream"].upstream_timestamp = NOW
    rows["book"].book_timestamp = NOW
    rows["book"].quote_time_source = "exchange_book"
    # Nullable bid/ask can occur in unvalidated model_copy updates in the pipeline.
    rows["null_prices"] = rows["null_prices"].model_copy(
        update={"bid": None, "ask": None, "mark_price": None, "index_price": None}
    )
    rows["null_sizes"].bid_size = rows["null_sizes"].ask_size = None
    bindings = [
        MarketBinding(market=MarketKey.from_snapshot(rows["pending"])),
        binding(rows["verified"], unit_status="pending"),
        binding(rows["expired"], identity_status="expired", unit_status="pending"),
        binding(rows["conflict"], identity_status="conflict", unit_status="pending"),
        binding(rows["multiplier"], price_multiplier=0.001),
        binding(rows["contract_mismatch"], contract_size=1),
        binding(
            rows["valuation"], asset_kind="valuation", contract_kind="valuation",
            tracking_definition="Price = company valuation in USD / 1,000,000,000",
        ),
        binding(rows["settle_usdt"], settlement_currency="USDT"),
        binding(rows["settle_usdc"], settlement_currency="USDC"),
        binding(rows["spec_a"], spec_fingerprint="spec-a"),
        binding(rows["spec_b"], spec_fingerprint="spec-b"),
        binding(rows["naive_validity"], valid_until=UNTIL.replace(tzinfo=None)),
        *[binding(rows[name], price_multiplier=0.001)
          for name in ("null_prices", "null_sizes", "alias", "upstream", "book")],
    ]
    # Identical normalized tickers must still distinguish raw markets and DEXes.
    for dex in ("io", "xyz"):
        row = market(f"{dex}:PRL", exchange="hyperliquid", dex=dex)
        rows[dex] = row
        bindings.append(binding(row, asset_id=f"asset-{dex}"))
    rows["unbound_dex"] = market("other:PRL", exchange="hyperliquid", dex="other")
    rows["invalid_key"] = market("io:PRL", exchange="hyperliquid", dex="xyz")
    return bindings, rows


def assert_same_snapshot(actual, expected):
    assert actual.model_dump() == expected.model_dump()
    # Equality alone accepts 1 == 1.0 and datetime values need to stay datetime.
    for name in MarketSnapshot.model_fields:
        assert type(getattr(actual, name)) is type(getattr(expected, name)), name


@pytest.mark.parametrize("mode", ["enforce", "shadow"])
@pytest.mark.parametrize("now", [
    NOW, UNTIL, UNTIL + timedelta(microseconds=1),
    NOW.astimezone(timezone(timedelta(hours=9))), NOW.replace(tzinfo=None),
], ids=["before_expiry", "at_expiry", "after_expiry", "offset_now", "naive_now"])
def test_annotate_and_annotate_all_match_frozen_legacy(mode, now):
    bindings, rows = equivalence_cases()
    old = LegacyMarketIdentityRegistry(bindings, mode=mode)
    new = MarketIdentityRegistry(bindings, mode=mode)
    assert old.mode == new.mode == mode
    for row in rows.values():
        original = row.model_dump()
        actual, expected = new.annotate(row, now), old.annotate(row, now)
        assert_same_snapshot(actual, expected)
        assert_same_snapshot(new.annotate(actual, now), old.annotate(expected, now))
        assert row.model_dump() == original
    for actual, expected in zip(
        new.annotate_all(iter(rows.values()), now), old.annotate_all(iter(rows.values()), now),
        strict=True,
    ):
        assert_same_snapshot(actual, expected)
    assert new.get(MarketKey.from_snapshot(rows["missing"])) is None
    for first, second in (("settle_usdt", "settle_usdc"), ("spec_a", "spec_b")):
        a = new.annotate(rows[first], now).binding_fingerprint
        b = new.annotate(rows[second], now).binding_fingerprint
        assert a != b
    assert new.annotate(rows["invalid_key"], now).identity_status == "conflict"
    assert new.annotate(rows["contract_mismatch"], now).identity_status == "conflict"
    assert new.annotate(rows["verified"], now).identity_status == (
        "verified" if utc(now) < UNTIL else "expired"
    )


def test_replace_isolates_input_mutations_and_releases_full_binding():
    row = market()
    source = binding(row, price_multiplier=0.001, contract_size=1)
    source_ref = weakref.ref(source)
    market_ref = weakref.ref(source.market)
    evidence_ref = weakref.ref(source.evidence[0])
    registry = MarketIdentityRegistry([source])
    expected = registry.annotate(row, NOW)
    source.asset_id = "changed"
    source.display_name = "Changed"
    source.asset_kind = "valuation"
    source.identity_status = "conflict"
    source.unit_status = "pending"
    source.quote_currency = "EUR"
    source.settlement_currency = "EUR"
    source.price_multiplier = 10
    source.contract_size = 99
    source.contract_kind = "valuation"
    source.tracking_definition = "Changed tracking definition"
    source.spec_fingerprint = "changed-spec"
    source.revision = 99
    source.verified_at = NOW
    source.valid_until = NOW - timedelta(days=1)
    source.evidence[0].query["market"] = "changed"
    source.evidence[0].summary = "Changed evidence"
    source.evidence.clear()
    object.__setattr__(source.market, "raw_symbol", "CHANGEDUSDT")
    assert_same_snapshot(registry.annotate(row, NOW), expected)
    del source
    gc.collect()
    assert source_ref() is market_ref() is evidence_ref() is None


def test_entries_are_frozen_slotted_and_snapshot_replacement_preserves_last_binding():
    row = market()
    first = binding(row)
    last = binding(row, asset_id="replacement", revision=8)
    registry = MarketIdentityRegistry([first, last])
    assert len(registry.bindings) == 1
    entry = registry.get(MarketKey.from_snapshot(row))
    assert entry.asset_id == "replacement"
    assert not hasattr(entry, "__dict__")
    assert not hasattr(entry, "evidence")
    with pytest.raises(FrozenInstanceError):
        entry.revision = 100
    with pytest.raises(TypeError):
        registry.bindings["extra"] = entry
    previous = registry.bindings
    registry.replace(iter([first]))
    assert entry is previous[MarketKey.from_snapshot(row).key]
    assert entry.revision == 8
    assert registry.annotate(row, NOW).binding_revision == 7
    registry.replace([])
    assert registry.annotate(row, NOW).identity_status == "pending"
    assert registry.annotate_all([], NOW) == []


def test_fingerprint_is_computed_once_per_entry_on_replace(monkeypatch):
    row = market()
    source = binding(row)
    fingerprint = MarketBinding.fingerprint.fget
    calls = []

    def counted(binding):
        calls.append(binding)
        return fingerprint(binding)

    monkeypatch.setattr(MarketBinding, "fingerprint", property(counted))
    registry = MarketIdentityRegistry([source])
    assert calls == [source]
    for _ in range(3):
        assert registry.annotate(row, NOW).binding_fingerprint == fingerprint(source)
    assert calls == [source]
    registry.replace([source])
    assert calls == [source, source]


def test_repeated_strings_are_shared_across_entries():
    # Separate parsed bindings should deduplicate equal strings, as DB loads do.
    payload = binding(market()).model_dump_json()
    sources = [MarketBinding.model_validate_json(payload) for _ in range(2)]
    for source in sources:
        source.display_name = (" " + source.display_name)[1:]
    object.__setattr__(sources[1].market, "raw_symbol", "OTHERUSDT")
    assert sources[0].display_name is not sources[1].display_name
    registry = MarketIdentityRegistry(sources)
    first, second = registry.bindings.values()
    for field in ("asset_id", "display_name", "quote_currency", "tracking_definition"):
        assert getattr(first, field) is getattr(second, field), field


def test_20000_verified_bindings_deep_size_drops_at_least_seventy_percent():
    template = binding(market(), evidence=evidence(5)).model_dump(mode="json")
    bindings = []
    for index in range(20_000):
        template["market"]["raw_symbol"] = f"PRL{index}USDT"
        bindings.append(MarketBinding.model_validate(template))
    assert all(len(source.evidence) == 5 for source in bindings)
    old = LegacyMarketIdentityRegistry(bindings)
    new = MarketIdentityRegistry(bindings)
    assert len(old.bindings) == len(new.bindings) == 20_000
    before, before_objects, before_truncated = deep_size(
        old.bindings, max_objects=2_000_000, deadline=time.monotonic() + 60,
    )
    after, after_objects, after_truncated = deep_size(
        new.bindings, max_objects=2_000_000, deadline=time.monotonic() + 60,
    )
    assert not before_truncated and not after_truncated
    print(f"M2_4B_REGISTRY before_bytes={before} after_bytes={after} "
          f"reduction_pct={(1 - after / before) * 100:.4f} "
          f"before_objects={before_objects} after_objects={after_objects}")
    assert after <= before * 0.30, (before, after)
