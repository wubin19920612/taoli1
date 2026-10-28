"""Lossless, in-process storage for validated raw exchange snapshots."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from app.models.market import MarketSnapshot

_FIELDS = tuple(MarketSnapshot.model_fields)
_FIELD_BITS = tuple((name, 1 << index) for index, name in enumerate(_FIELDS))
# Include immutable containers because they can contain mutable descendants.
_CONTAINERS = (list, dict, set, tuple, frozenset, bytearray)


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
                values[name] = deepcopy(values[name])
            markets.append(MarketSnapshot.model_construct(
                _fields_set=field_sets[bitmap].copy(), **values,
            ))
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
                value = deepcopy(value)
            values.append(value)
        bitmap = sum(bit for name, bit in _FIELD_BITS if name in market.model_fields_set)
        rows.append((*values, bitmap))
    return CompactMarkets(
        rows=tuple(rows),
        mutable_fields=tuple(name for name in _FIELDS if name in mutable_fields),
    )
