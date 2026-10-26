"""Bounded, synchronous estimates. Output contains static labels and numbers only.

Sizes describe reachable Python allocations, not RSS or allocator arenas. One
visited set attributes shared allocations to the first target visited. The
object budget caps distinct IDs; traversal visits are bounded by time. Large
containers extrapolate sampled descendants, so their sizes are estimates even
when traversal completes without truncation.
"""

import asyncio
import functools
import gc
import sys
import time
import types
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import chain


@dataclass(frozen=True)
class CensusTarget:
    name: str
    getter: Callable[[], object | None]


@dataclass
class _Budget:
    max_objects: int
    deadline: float
    visits: int = 0


@dataclass
class _Measurement:
    size: float = 0
    objects: int = 0
    truncated: bool = False
    failed: bool = False
    sampled: bool = False
    sample_size: int = 0


_SAMPLE_THRESHOLD = 2000
_SAMPLE_MODULUS = 16
_CONTAINERS = (dict, types.MappingProxyType, list, tuple, set, frozenset, deque)


_OPAQUE = (
    type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType,
    types.MethodType, types.CodeType, types.FrameType, types.CoroutineType,
    types.GeneratorType, types.AsyncGeneratorType, asyncio.Future,
)


def _children(obj: object) -> Iterator[object]:
    # Lazy iterators avoid an unbudgeted copy of a wide container. Do not follow
    # functions, frames or tasks into unrelated globals, clients or closures.
    if type(obj) in (str, bytes, bytearray, int, float, bool, complex, type(None), range):
        return
    if isinstance(obj, (dict, types.MappingProxyType)):
        yield from chain.from_iterable(obj.items())
    elif isinstance(obj, _CONTAINERS):
        yield from obj
    elif not isinstance(obj, _OPAQUE):
        try:
            attributes = object.__getattribute__(obj, "__dict__")
        except AttributeError:
            pass
        else:
            yield attributes
        # Member descriptors include inherited/private slots and Pydantic's
        # extra/private/fields_set storage. Never evaluate arbitrary properties.
        for cls in type(obj).__mro__:
            for descriptor in vars(cls).values():
                if isinstance(descriptor, types.MemberDescriptorType):
                    try:
                        yield descriptor.__get__(obj, type(obj))
                    except AttributeError:
                        pass


def _sampled_children(
    obj: object, count: int, result: _Measurement, deadline: float,
) -> tuple[Iterator[object], float]:
    # The same object must be selected in every container, regardless of length
    # or position, so sampled descendants share attribution across targets.
    # Retain only selected entries; count them before assigning their weight.
    mapping = isinstance(obj, (dict, types.MappingProxyType))
    source = iter(obj.items() if mapping else obj)
    first = next(source)
    selected = []
    for element in chain((first,), source):
        # Hash scans can be wide even when nothing is selected. Apply the same
        # per-target/global deadline as the subsequent depth measurement.
        if time.monotonic() >= deadline:
            result.truncated = True
            break
        identity = id(element[1] if mapping else element)
        if (((identity >> 4) * 2654435761) & 0xFFFFFFFF) % _SAMPLE_MODULUS == 0:
            selected.append(element)
    if not selected and not result.truncated:
        selected.append(first)
    result.sample_size += len(selected)
    children = chain.from_iterable(selected) if mapping else iter(selected)
    return children, count / len(selected) if selected else 1.0


def _measure(
    obj: object, budget: _Budget, shared: set[int], *, deadline: float | None = None,
) -> _Measurement:
    result = _Measurement()
    deadline = budget.deadline if deadline is None else min(deadline, budget.deadline)
    # Each frame carries its extrapolation factor. A sampled container's own
    # shallow size is counted before applying its factor to descendants only.
    stack = [(iter((obj,)), 1.0)]
    while stack:
        # Check even when iterating duplicates or exhausted child iterators.
        if time.monotonic() >= deadline:
            result.truncated = True
            break
        try:
            children, weight = stack[-1]
            current = next(children)
        except StopIteration:
            stack.pop()
            continue
        except Exception:  # noqa: BLE001 - preserve partial attribution on a bad child.
            result.truncated = result.failed = True
            break
        budget.visits += 1
        identity = id(current)
        if identity in shared:
            continue
        # Duplicates spend no object budget. Stop before inserting a new ID;
        # len(shared) is the actual memory bound, across all targets.
        if len(shared) >= budget.max_objects:
            result.truncated = True
            break
        try:
            amount = sys.getsizeof(current)
            shared.add(identity)
            result.size += amount * weight
            result.objects += 1
            if isinstance(current, _CONTAINERS) and len(current) > _SAMPLE_THRESHOLD:
                count = len(current)
                result.sampled = True
                children, factor = _sampled_children(current, count, result, deadline)
                stack.append((children, weight * factor))
            else:
                stack.append((iter(_children(current)), weight))
        except Exception:  # noqa: BLE001 - return only numeric evidence.
            result.truncated = result.failed = True
            break
    return result


def deep_size(obj: object, *, max_objects: int, deadline: float) -> tuple[int, int, bool]:
    """Return estimated bytes, distinct objects, truncation; never recurse."""
    result = _measure(obj, _Budget(max_objects, deadline), set())
    return round(result.size), result.objects, result.truncated


def _read_cgroup_file(path: str) -> str:
    with open(path, encoding="ascii") as source:
        return source.read().strip()


def read_cgroup_headroom_bytes() -> int | None:
    """Read the cgroup v2 limit and usage; unavailable/unlimited means no gate."""
    try:
        maximum = _read_cgroup_file("/sys/fs/cgroup/memory.max")
        if maximum.strip() == "max":
            return None
        maximum = int(maximum)
        current = int(_read_cgroup_file("/sys/fs/cgroup/memory.current"))
    except (OSError, UnicodeError, ValueError):
        return None
    if maximum <= 0 or current < 0:
        return None
    return maximum - current


def run_census(
    targets: list[CensusTarget], *, max_objects: int = 600_000,
    budget_seconds: float = 3.0, target_seconds: float = 0.5,
) -> dict:
    started = time.perf_counter()
    headroom = read_cgroup_headroom_bytes()
    # Reserve bookkeeping (IDs + set capacity/growth) and a further 64 MiB.
    # Check before constructing the visited set or invoking any target getter.
    if headroom is not None and headroom < max_objects * 80 + 64 * 1024 ** 2:
        return {"census_skipped": "low_headroom", "headroom_mb": headroom / 1024 ** 2,
                "visited_objects": 0, "visits": 0,
                "budget_seconds": budget_seconds, "target_seconds": target_seconds}
    budget = _Budget(max_objects, time.monotonic() + budget_seconds)
    shared: set[int] = set()
    roots = []  # Prevent id reuse for containers returned temporarily by getters.
    rows = []
    errors = 0
    for target in targets:
        row = {
            "name": target.name, "entries": None, "unique_kb": 0.0,
            "objects": 0, "truncated": False,
        }
        rows.append(row)
        now = time.monotonic()
        if now >= budget.deadline:
            row["truncated"] = True
            continue
        deadline = min(budget.deadline, now + target_seconds)
        try:
            obj = target.getter()
            if obj is None:
                row["missing"] = True
                continue
            roots.append(obj)
            if isinstance(obj, functools._lru_cache_wrapper):
                row.update(entries=obj.cache_info().currsize, unique_kb=None)
                continue
            if isinstance(obj, _CONTAINERS):
                row["entries"] = len(obj)
            measurement = _measure(obj, budget, shared, deadline=deadline)
            errors += int(measurement.failed)
            row.update(unique_kb=measurement.size / 1024, objects=measurement.objects,
                       truncated=measurement.truncated)
            if measurement.sampled:
                row.update(sampled=True, sample_size=measurement.sample_size)
        except Exception:  # noqa: BLE001 - never serialize arbitrary exception data.
            errors += 1
            row["truncated"] = True
        finally:
            # Do not retain the last target through the next getter or probe sleep.
            obj = None
    rows.sort(key=lambda row: row["unique_kb"] or 0, reverse=True)
    # Include release of the potentially large bookkeeping set in blocking time.
    visited_objects = len(shared)
    shared.clear()
    roots.clear()
    result = {"cache_census": rows, "census_ms": (time.perf_counter() - started) * 1000,
              "visited_objects": visited_objects, "visits": budget.visits,
              "budget_seconds": budget_seconds, "target_seconds": target_seconds}
    if errors:
        result["census_errors"] = errors
    return result


# Explicit ownership paths: no discovery through cache keys, user names or object
# repr. Missing optional services remain visible. Read attributes afresh each run
# so replaced containers are never pinned by the registry's getter closures.
SERVICE_PATHS = (
    "snapshot_store._markets", "snapshot_store._all_markets",
    "snapshot_store._opportunities", "snapshot_store._exchange_errors",
    "market_collector._poll_states", "market_collector._exchange_snapshots",
    "market_collector._exchange_errors", "account_position_service._cache",
    "account_connection_service._provider_cache", "fs_monitor.report", "fs_monitor._states",
    "astro_hl_gc_catalog._cache", "instrument_market_cap_service._search_cache",
    "instrument_market_cap_service._market_cache", "instrument_market_cap_service._locks",
    "instrument_statistics_service._cache", "instrument_statistics_service._pending",
    "instrument_statistics_service._coverage", "trade_availability_service._cache",
    "market_validation.registry.bindings", "market_identity_catalog.status",
    "market_validation.books", "market_validation.reviews", "market_validation.failures",
    "market_validation.review_attempted", "market_validation.tasks",
    "market_validation.review_inflight", "market_validation._last_incident",
    "market_validation.discovered", "market_validation.collision_symbols",
    "market_validation.invalidated_ids", "market_validation.fx",
    "alert_engine._hits", "alert_engine._last_sent", "alert_engine._retry_after",
    "alert_engine._retry_attempts", "alert_engine._observations",
    "opportunity_radar_alert_engine._hits", "opportunity_radar_alert_engine._alerted_active",
    "opportunity_radar_alert_engine._last_sent", "phone_price_alert_engine._last_sent",
    "gate_twap_manager.jobs", "gate_twap_manager.tasks",
    "index_component_auto_watch._last_attempt_by_symbol",
    "index_component_monitor._latest_prices",
    "multi_exchange_premium_monitor._next_allowed", "multi_exchange_premium_monitor._locks",
    "multi_exchange_premium_monitor._request_at", "multi_exchange_premium_monitor._exchange_retry_at",
    "multi_exchange_premium_monitor.status", "negative_basis_monitor._last_run_at",
    "negative_basis_monitor._last_sent_at", "negative_basis_monitor._last_sent_level",
    "negative_basis_monitor._auto_candidates", "oil_news_monitor._translation_retry_after",
    "squeeze_monitor._verified_symbols", "service_controller._tasks",
    "squeeze_route_monitor._queue._queue", "squeeze_route_monitor.tracker",
    "squeeze_route_monitor.provider._route",
)

_QUERY_ATTRIBUTES = (
    "_hyperliquid_dex_details", "_hyperliquid_meta_contexts_by_dex",
    "_hyperliquid_coin_by_base", "_lighter_markets",
)


def _path(root: object, path: str) -> object | None:
    for attribute in path.split("."):
        root = getattr(root, attribute, None)
        if root is None:
            break
    return root


def _member(root: object, path: str, index: int, suffix: str) -> object | None:
    collection = _path(root, path)
    if isinstance(collection, dict):
        collection = collection.values()
    if collection is None:
        return None
    for offset, member in enumerate(collection):
        if offset == index:
            return _path(member, suffix)
    return None


def _task_local(state: object, task_name: str, function: str, variable: str) -> object | None:
    from app.models.announcement import ExchangeAnnouncement
    from app.models.history import OpportunityHistoryRow
    from app.services.snapshot_consumers import PublishedSnapshot

    supervisor = getattr(state, "task_supervisor", None)
    task = getattr(supervisor, "tasks", {}).get(task_name)
    coroutine = task.get_coro() if task is not None else None
    # CPython coroutine referents expose fast locals without materializing
    # frame.f_locals. On Python 3.12 that cached dict can itself pin an old
    # snapshot until the next inspection. Inspect only the known coroutine and
    # select domain objects by type; never recurse into its other referents.
    expected = {"snapshot": PublishedSnapshot, "rows": OpportunityHistoryRow,
                "announcements": ExchangeAnnouncement}[variable]
    for _ in range(32):
        if coroutine is None:
            return None
        code = getattr(coroutine, "cr_code", None)
        if code is not None and code.co_name == function:
            for value in gc.get_referents(coroutine):
                if variable == "snapshot" and isinstance(value, expected):
                    return value
                if variable != "snapshot" and isinstance(value, list):
                    if value and isinstance(value[0], expected):
                        return value
            return None
        coroutine = getattr(coroutine, "cr_await", None)
    return None


def build_census_targets(app_services: object) -> list[CensusTarget]:
    from app.core import config
    from app.services import market_sessions, pair_spread_query

    targets = [CensusTarget(path, lambda path=path: _path(app_services, path))
               for path in SERVICE_PATHS]
    for path in ("instrument_statistics_service.query_service", "multi_exchange_premium_monitor.service"):
        for attribute in _QUERY_ATTRIBUTES:
            full = f"{path}.{attribute}"
            targets.append(CensusTarget(full, lambda full=full: _path(app_services, full)))

    groups = (
        ("market_collector.adapters", ("_markets", "_cached", "_rtoken_spot_symbols",
                                       "market_errors", "_collateral_names")),
        ("trade_availability_service.adapters", ("_markets", "_cached", "_rtoken_spot_symbols",
                                                 "market_errors", "_collateral_names")),
        ("fs_monitor.providers", ("cache", "_metadata")),
        ("announcement_provider.providers", ("_localization._cache", "_localization._missing_until",
                                              "last_source_errors", "last_source_articles")),
    )
    for path, attributes in groups:
        collection = _path(app_services, path)
        # A missing optional group still has a stable placeholder in the report.
        for index in range(max(1, len(collection) if collection is not None else 0)):
            for attribute in attributes:
                targets.append(CensusTarget(
                    f"{path}.{index}.{attribute}",
                    lambda path=path, index=index, attribute=attribute:
                        _member(app_services, path, index, attribute),
                ))

    def research_cache():
        researcher = _path(app_services, "announcement_monitor.asset_researcher")
        return getattr(getattr(researcher, "__self__", researcher), "_cache", None)

    targets.append(CensusTarget("announcement_research._cache", research_cache))
    for label, task_name in (("history_snapshot", "history-recorder"), ("index_snapshot", "index-components")):
        def consumer_attribute(attribute, task_name=task_name):
            consumers = getattr(app_services, "snapshot_consumers", {})
            return _path(consumers.get(task_name), attribute)

        targets.extend((
            CensusTarget(f"{label}.slot", lambda get=consumer_attribute: get("slot._latest")),
            CensusTarget(f"{label}.durations", lambda get=consumer_attribute: get("_durations")),
            CensusTarget(f"{label}.active", lambda task_name=task_name:
                         _task_local(app_services, task_name, "run", "snapshot")),
        ))
    targets.extend((
        CensusTarget("history_recorder.rows", lambda:
                     _task_local(app_services, "history-recorder", "record", "rows")),
        CensusTarget("announcement_enrichment.batch", lambda: _task_local(
            app_services, "announcement-enrichment-loop", "run_announcement_enrichment_loop", "announcements",
        )),
    ))
    for attribute in ("_REALTIME_PAIR_SPREAD_CACHE", "_REALTIME_PAIR_FUNDING_CACHE",
                      "_REALTIME_PAIR_OPEN_INTEREST_CACHE", "_REALTIME_SYMBOL_SPREAD_CACHE"):
        targets.append(CensusTarget(f"pair_spread_query.{attribute}",
                                    lambda attribute=attribute: getattr(pair_spread_query, attribute, None)))
    targets.extend((
        CensusTarget("config.get_settings", lambda: config.get_settings),
        CensusTarget("market_sessions.us_stock_session_close", lambda: market_sessions.us_stock_session_close),
    ))
    # Shared snapshots belong to their primary stores/collector, including
    # adapter caches, before secondary services or snapshot consumers see them.
    priority = {"snapshot_store": 0, "market_collector": 1}
    targets.sort(key=lambda target: priority.get(target.name.partition(".")[0], 2))
    return targets
