"""Lossless, in-process storage for validated raw exchange snapshots."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from app.models.market import MarketSnapshot

_FAST = (
    not MarketSnapshot.__private_attributes__
    and MarketSnapshot.__pydantic_post_init__ is None
    and MarketSnapshot.model_config.get("extra") != "allow"
)
_FIELDS = tuple(MarketSnapshot.model_fields)
_FIELD_BITS = tuple((name, 1 << index) for index, name in enumerate(_FIELDS))
# Include immutable containers because they can contain mutable descendants.
_CONTAINERS = (list, dict, set, tuple, frozenset, bytearray)
_IMMUTABLE_LEAVES = (str, int, float, bool, type(None))


def _copy_mutable(value: object) -> object:
    # Subclasses can carry mutable state of their own; keep their deepcopy semantics.
    if type(value) is list and all(type(item) in _IMMUTABLE_LEAVES for item in value):
        return list(value)
    return deepcopy(value)


@dataclass
class CompactMarkets:
    # Ordinary attributes keep the retained graph visible to cache_census.
    rows: tuple[tuple[object, ...], ...]
    mutable_fields: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.rows)

    def unpack(self) -> list[MarketSnapshot]:
        markets = []
        field_sets: dict[int, set[str]] = {}
        for row in self.rows:
            bitmap = row[-1]
            if bitmap not in field_sets:
                field_sets[bitmap] = {name for name, bit in _FIELD_BITS if bitmap & bit}
            values = dict(zip(_FIELDS, row))
            for name in self.mutable_fields:
                values[name] = _copy_mutable(values[name])
            fields_set = field_sets[bitmap].copy()
            if _FAST:
                # Match Pydantic 2.12 model_construct for these complete, trusted rows.
                m = MarketSnapshot.__new__(MarketSnapshot)
                object.__setattr__(m, "__dict__", values)
                object.__setattr__(m, "__pydantic_fields_set__", fields_set)
                object.__setattr__(m, "__pydantic_extra__", None)
                object.__setattr__(m, "__pydantic_private__", None)
            else:
                m = MarketSnapshot.model_construct(_fields_set=fields_set, **values)
            markets.append(m)
        return markets


def pack(markets: Sequence[MarketSnapshot]) -> CompactMarkets:
    rows = []
    mutable_fields = set()
    for market in markets:
        values = []
        for name in _FIELDS:
            value = getattr(market, name)
            if isinstance(value, _CONTAINERS):
                mutable_fields.add(name)
                value = _copy_mutable(value)
            values.append(value)
        bitmap = sum(bit for name, bit in _FIELD_BITS if name in market.model_fields_set)
        rows.append((*values, bitmap))
    return CompactMarkets(
        rows=tuple(rows),
        mutable_fields=tuple(name for name in _FIELDS if name in mutable_fields),
    )
