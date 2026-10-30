"""Process-local series caches with one shared, access-ordered point budget.

Reads return shallow container copies; callers must assign after appending or
trimming. Point models are not copied. Diagnostics never refresh TTL or evict.
Operations are synchronous and intended for the service's single event loop.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, cast

REALTIME_CACHE_MAX_KEYS = 128
REALTIME_CACHE_MAX_TOTAL_POINTS = 200_000
REALTIME_CACHE_IDLE_TTL_SECONDS = 1_800
REALTIME_CACHE_MAX_EVICTION_MARKERS = 128

type SeriesValue = list[Any] | dict[str, list[Any]]


def _copy_series[T: SeriesValue](value: T) -> T:
    if isinstance(value, dict):
        return cast(T, {key: list(points) for key, points in value.items()})
    return cast(T, list(value))


def _point_count(value: SeriesValue) -> int:
    if isinstance(value, dict):
        return sum(len(points) for points in value.values())
    return len(value)


class RealtimeSeriesBudget:
    def __init__(
        self,
        max_points: int = REALTIME_CACHE_MAX_TOTAL_POINTS,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if max_points < 0:
            raise ValueError("max_points must be nonnegative")
        self.max_points = max_points
        self.clock = clock
        self.total_points = 0
        self._entries: OrderedDict[
            tuple[RealtimeSeriesCache, str], tuple[float, int]
        ] = OrderedDict()

    def _remove(self, cache: RealtimeSeriesCache, key: str, *, evicted: bool = True) -> None:
        _, points = self._entries.pop((cache, key))
        self.total_points -= points
        del cache._data[key]
        if evicted:
            cache._evicted[key] = None
            cache._evicted.move_to_end(key)
            if len(cache._evicted) > REALTIME_CACHE_MAX_EVICTION_MARKERS:
                cache._evicted.popitem(last=False)
                cache._forgotten_evictions = True

    def _expire(self, now: float) -> None:
        for (cache, key), (accessed_at, _) in list(self._entries.items()):
            if now - accessed_at >= cache.idle_ttl_seconds:
                self._remove(cache, key)

    def _touch(self, cache: RealtimeSeriesCache, key: str, now: float) -> None:
        _, points = self._entries[(cache, key)]
        self._entries[(cache, key)] = (now, points)
        self._entries.move_to_end((cache, key))
        cache._data.move_to_end(key)

    def _enforce(self, cache: RealtimeSeriesCache) -> None:
        while len(cache) > cache.max_keys:
            self._remove(cache, next(iter(cache._data)))
        while self.total_points > self.max_points:
            oldest_cache, oldest_key = next(iter(self._entries))
            self._remove(oldest_cache, oldest_key)


class RealtimeSeriesCache[T: SeriesValue]:
    def __init__(
        self,
        budget: RealtimeSeriesBudget,
        *,
        max_keys: int = REALTIME_CACHE_MAX_KEYS,
        idle_ttl_seconds: float = REALTIME_CACHE_IDLE_TTL_SECONDS,
    ) -> None:
        if max_keys < 0 or idle_ttl_seconds <= 0:
            raise ValueError("max_keys must be nonnegative and TTL must be positive")
        self.budget = budget
        self.max_keys = max_keys
        self.idle_ttl_seconds = idle_ttl_seconds
        self._data: OrderedDict[str, T] = OrderedDict()
        self._evicted: OrderedDict[str, None] = OrderedDict()
        self._forgotten_evictions = False

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key: str) -> T:
        now = self.budget.clock()
        self.budget._expire(now)
        value = self._data[key]
        self.budget._touch(self, key, now)
        return _copy_series(value)

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key: str, value: T) -> None:
        now = self.budget.clock()
        self.budget._expire(now)
        if key in self._data:
            self.budget._remove(self, key, evicted=False)
        self._evicted.pop(key, None)
        stored = _copy_series(value)
        points = _point_count(stored)
        self._data[key] = stored
        self.budget._entries[(self, key)] = (now, points)
        self.budget.total_points += points
        self.budget._enforce(self)

    def clear(self) -> None:
        for key in list(self._data):
            self.budget._remove(self, key, evicted=False)
        self._evicted.clear()
        self._forgotten_evictions = False

    def restart_status(self, key: str) -> Literal["evicted", "unknown"] | None:
        """Inspect a miss after get() has swept TTL; never retain unbounded keys.

        Once marker history overflows, an unknown miss might be an old eviction
        or first use. The caller must describe that uncertainty, not claim an
        eviction for every new key. A cache hit needs no restart notice.
        """
        if key in self._evicted:
            return "evicted"
        return "unknown" if self._forgotten_evictions else None

    def values(self):
        """Read-only diagnostics; no TTL/LRU side effects."""
        return self._data.values()

    def diagnostic_view(self) -> Mapping[str, T]:
        """Expose only this cache's values to census, not the shared budget graph."""
        return MappingProxyType(self._data)
