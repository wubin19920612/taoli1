"""Bounded spawn IPC. The parent owns presentation only, never a shadow ledger."""
import asyncio
import multiprocessing
import pickle
import queue
import threading
from time import monotonic, perf_counter, thread_time_ns
from operator import attrgetter

from app.services.execution_routes import ExecutionRoutes, ShadowErrorLog
from app.services.shadow_ipc import child_entry, market_values, op_values, directory_values, threshold_values, rss_mb
from app.services.snapshot_consumers import PublishedSnapshot

RESTART_BASE_SECONDS = 5
MAX_RESTARTS = 3
WORK_TIMEOUT_SECONDS = 60
SHUTDOWN_SECONDS = 10


class ShadowRuntime:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.error_log = ShadowErrorLog(clock=lambda: monotonic())
        self.catalog = ExecutionRoutes(settings.native_route_exchanges, settings.crossex_catalog_refresh_seconds,
                                       on_error=self.error)
        self._process = self._incoming = self._outgoing = self._stop = None
        self._reader = self._reader_stop = self._supervisor = None
        self._ready = asyncio.Event()
        self._active = None
        self._context = None
        self._serial = 0
        self._epoch = 0
        self._closing = self._disabled = False
        self._close_task = None
        self._busy_since = None
        self._rss_exceeded = False
        self._transport_error = None
        self._restart_at = None
        self.child_restarts = self.skipped_rounds = self.skipped_publications = 0
        self._held = (frozenset(), frozenset())
        self._published = dict(enabled=settings.shadow_enabled, ready=False, open_count=0, dropped=0,
            dropped_by_reason=dict.fromkeys(("capacity", "liquidity", "market_type", "symbol"), 0),
            baseline_skipped=0, pending_writes=0, scorer_dropped=0, scorer_price_states=0,
            recomputed_positions=0, worker_phases_ms={}, last_duration_ms=None,
            worker_pid=None, child_rss_mb=None, main_cpu_ms=None)
        self._display_cache = {}
        self._wire_cache = {}
        self._child_errors = {}
        self._child_error_totals = {}

    @property
    def errors(self):
        return self.status()["errors"]

    def error(self, category="consumer"):
        self.error_log.record(category)

    async def initialize(self, shared_db=None):
        if not self.settings.shadow_enabled or self._closing or self._disabled:
            return
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(self._supervise())
            try:
                self._spawn()
            except Exception:
                await self._failed("spawn")
        try:
            await asyncio.wait_for(self._ready.wait(), WORK_TIMEOUT_SECONDS)
        except TimeoutError:
            self.error("initialize_timeout")

    def _spawn(self):
        ctx = multiprocessing.get_context("spawn")
        self._epoch += 1
        epoch = self._epoch
        self._ready.clear()
        self._rss_exceeded = False
        self._transport_error = None
        self._incoming, self._outgoing = ctx.Queue(maxsize=1), ctx.Queue(maxsize=1)
        self._stop = ctx.Event()
        # Whitelist configuration: credentials and unrelated app config never cross IPC.
        config = {name: getattr(self.settings, name) for name in (
            "shadow_enabled", "shadow_min_open_pct", "shadow_max_open", "shadow_slippage_bps",
            "shadow_min_liquidity_usd", "native_route_exchanges", "crossex_catalog_refresh_seconds", "sqlite_path")}
        if config["sqlite_path"] == ":memory:":
            raise ValueError("spawn shadow requires a file-backed SQLite database")
        self._process = ctx.Process(target=child_entry, args=(config, self._incoming, self._outgoing, self._stop),
                                    name="shadow-ledger", daemon=True)
        self._process.start()
        # Queue has no public directional endpoint API. Close unused local pipe
        # ends after spawn so a killed peer yields EOF/BrokenPipe, including when
        # it dies halfway through a frame (get(timeout) alone cannot bound that).
        self._incoming._reader.close()
        self._outgoing._writer.close()
        self._incoming._ignore_epipe = True
        self._published.update(worker_pid=self._process.pid, ready=False)
        self._busy_since = monotonic()
        self._reader_stop = threading.Event()
        loop = asyncio.get_running_loop()
        self._reader = threading.Thread(target=self._read_results,
            args=(self._outgoing, self._reader_stop, loop, epoch), name="shadow-results", daemon=True)
        self._reader.start()

    def _read_results(self, outgoing, stopped, loop, epoch):
        # Only queue I/O and deserialization here. Never calculate or touch state.
        while not stopped.is_set():
            try:
                message = outgoing.get(timeout=.1)
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError):
                return
            except Exception:
                try:
                    loop.call_soon_threadsafe(self._transport_failed, epoch)
                except RuntimeError:
                    pass
                return
            try:
                loop.call_soon_threadsafe(self._receive, epoch, message)
            except RuntimeError:
                return

    def _transport_failed(self, epoch):
        if epoch == self._epoch:
            self._transport_error = "receive"

    def _receive(self, epoch, message):
        try:
            self._receive_message(epoch, message)
        except Exception:
            self._transport_failed(epoch)

    def _receive_message(self, epoch, message):
        if epoch != self._epoch:
            return
        kind, token, result, status = message
        if kind == "capture":
            self._capture_reply(token, result)
            return
        self._held = status.pop("held", self._held)
        self._child_errors = status.pop("error_counts", {})
        self._published.update(status)
        if (status.get("child_rss_mb") or 0) > self.settings.shadow_child_max_rss_mb:
            self._rss_exceeded = True
        if kind == "ready":
            if status.get("ready"):
                self._published.pop("error", None)
            self._busy_since = None
            self._ready.set()
            return
        if kind == "closed":
            return
        if token != self._serial or self._active is None:
            return
        if kind == "round":
            resumed = perf_counter()
            context, self._context = self._context, None
            snapshot, segments, cpus = context
            generation, version, originals = snapshot.generation, snapshot.opportunities_version, snapshot.opportunities
            capture_ms, cpu_ms = sum(segments), sum(cpus)
            try:
                if self.store.opportunities_version != version:
                    self.skipped_publications += 1
                elif self.store.generation == generation and result is not None:
                    # Unrelated rows retain object identity, order and count.
                    display, cache = [], {}
                    for op in originals:
                        fields = result.get(op.id)
                        if fields is None:
                            display.append(op)
                            continue
                        previous = self._display_cache.get(op.id)
                        values = tuple(op.__dict__.values())
                        if previous is not None and previous[:2] == (values, fields):
                            entry = previous
                        else:
                            entry = (values, fields, op.model_copy(update=fields))
                        cache[op.id] = entry
                        display.append(entry[2])
                    self._display_cache = cache
                    if not self.store.set_opportunities_if_version(display, version):
                        self.skipped_publications += 1
            except Exception:
                self.error("publish")
            del originals, snapshot, context
            publish_ms = (perf_counter() - resumed) * 1000
            self._published.update(main_cpu_ms=cpu_ms, capture_ms=capture_ms, publish_ms=publish_ms,
                last_duration_ms=capture_ms + publish_ms,
                capture_segments_ms=tuple(segments), event_loop_max_segment_ms=max(*segments, publish_ms),
                catalog_updated_at=self.catalog.updated_at, catalog_entries=len(self.catalog.entries))
        self._busy_since = None
        if not self._active.done():
            self._active.set_result(result)

    async def submit(self, snapshot):
        if not self.settings.shadow_enabled:
            return None
        if (self._closing or self._disabled or not self._published["ready"]
                or self._process is None or not self._process.is_alive()
                or (self._active is not None and not self._active.done())):
            self.skipped_rounds += 1
            return None
        started, cpu = perf_counter(), thread_time_ns()
        try:
            # Stage 1 contains only id/spread. Selection runs exclusively in the
            # child, which requests exact directory/quote indices in stages 2/3.
            thresholds = tuple(map(threshold_values, map(attrgetter("__dict__"), snapshot.opportunities)))
            encoded_thresholds = self._encode("thresholds", thresholds)
            payload = pickle.dumps((snapshot.published_at, encoded_thresholds, self.catalog.entries), protocol=5)
            self._serial += 1
            self._incoming.put_nowait(("round", self._serial, payload))
            capture_ms = (perf_counter() - started) * 1000
            cpu_ms = (thread_time_ns() - cpu) / 1e6
            self._context = (snapshot, [capture_ms], [cpu_ms])
            self._active = asyncio.get_running_loop().create_future()
            self._busy_since = monotonic()
            return self._active
        except queue.Full:
            self.skipped_rounds += 1
        except Exception:
            self.error("capture")
        return None

    def _encode(self, name, data):
        previous = self._wire_cache.get(name)
        if previous is not None and previous[0] == data:
            return previous[1]
        encoded = pickle.dumps(data, protocol=5)
        self._wire_cache[name] = (data, encoded)
        return encoded

    def _capture_reply(self, token, request):
        if token != self._serial or self._context is None:
            return
        started, cpu = perf_counter(), thread_time_ns()
        snapshot, segments, cpus = self._context
        kind, indices = request
        try:
            if kind == "directory":
                data = (tuple(map(directory_values, map(attrgetter("__dict__"), snapshot.markets))),
                        tuple(values[:-3] + tuple(tuple(v) for v in values[-3:])
                              for values in (op_values(snapshot.opportunities[i].__dict__) for i in indices)))
            elif kind == "quotes":
                markets = snapshot.markets if indices is None else (snapshot.markets[i] for i in indices)
                data = tuple(map(market_values, map(attrgetter("__dict__"), markets)))
            else:
                raise ValueError("invalid shadow capture request")
            payload = self._encode(kind, data) if kind == "directory" else pickle.dumps(data, protocol=5)
            self._incoming.put_nowait((kind, token, payload))
        except Exception:
            self.error("capture")
            self._transport_error = "transport"
        finally:
            segments.append((perf_counter() - started) * 1000)
            cpus.append((thread_time_ns() - cpu) / 1e6)

    async def consume(self, snapshot):
        task = await self.submit(snapshot)
        if task is not None:
            await asyncio.shield(task)

    async def statistics(self, rows, dims, include_fake):
        # No bootstrap or worker is needed for the empty result identity.
        # API readiness is checked by read_committed before reaching this method.
        if not rows:
            return []
        # Statistics share the same single-flight slot. A waiting API owns no
        # queued compute; incoming market rounds skip while statistics runs.
        while self._active is not None and not self._active.done():
            await asyncio.shield(self._active)
        if self._closing or self._disabled or not self._published["ready"]:
            raise RuntimeError("shadow worker unavailable")
        self._serial += 1
        self._incoming.put_nowait(("statistics", self._serial, pickle.dumps((rows, dims, include_fake), protocol=5)))
        self._active = asyncio.get_running_loop().create_future()
        self._busy_since = monotonic()
        result = await asyncio.shield(self._active)
        if result is None:
            raise RuntimeError("shadow statistics failed")
        return result

    async def _supervise(self):
        while not self._closing:
            await asyncio.sleep(.1)
            if self._disabled:
                return
            if self._closing:
                continue
            if self._restart_at is not None:
                if monotonic() >= self._restart_at:
                    self._restart_at = None
                    self.child_restarts += 1
                    try:
                        self._spawn()
                    except Exception:
                        await self._failed("spawn")
                continue
            process = self._process
            if process is None:
                continue
            if self._transport_error is not None:
                await self._failed(self._transport_error)
                continue
            if not process.is_alive():
                await self._failed("crash")
                continue
            try:
                resident = rss_mb(process.pid)
                self._published["child_rss_mb"] = resident
            except OSError:
                if not process.is_alive():
                    await self._failed("crash")
                else:
                    await self._failed("rss_probe", permanent=True)
                continue
            if self._rss_exceeded or resident > self.settings.shadow_child_max_rss_mb:
                await self._failed("rss_limit", permanent=True)
            elif self._busy_since is not None and monotonic() - self._busy_since > WORK_TIMEOUT_SECONDS:
                await self._failed("timeout")

    async def _failed(self, reason, permanent=False):
        self.error(reason)
        self._epoch += 1  # Discard already queued callbacks from the failed child.
        self._published.update(ready=False, error=reason)
        self._disabled = permanent or self.child_restarts >= MAX_RESTARTS
        await self._dispose(force=True)
        self._published["ready"] = False
        for key, value in self._child_errors.items():
            self._child_error_totals[key] = self._child_error_totals.get(key, 0) + value
        self._child_errors = {}
        self._context = None
        self._busy_since = None
        if self._active is not None and not self._active.done():
            self._active.set_result(None)
        if not self._disabled and not self._closing:
            self._restart_at = monotonic() + RESTART_BASE_SECONDS * 2 ** self.child_restarts
        self._ready.set()

    async def _dispose(self, force=False):
        process = self._process
        if process is not None and process.pid is not None:
            if force and process.is_alive():
                process.terminate()
            while process.is_alive():
                await asyncio.sleep(.02)
            process.join(timeout=0)
            process.close()
        if self._reader_stop is not None:
            self._reader_stop.set()
        if self._reader is not None:
            await asyncio.to_thread(self._reader.join, .5)
        for channel in (self._incoming, self._outgoing):
            if channel is not None:
                channel.cancel_join_thread()
                channel.close()
        self._process = self._incoming = self._outgoing = None
        self._reader = None
        self._epoch += 1
        self._published["worker_pid"] = None
        self._display_cache.clear()
        self._wire_cache.clear()

    def status(self):
        counts = dict(self._child_error_totals)
        for source in (self._child_errors, self.error_log.counts()):
            for key, value in source.items():
                counts[key] = counts.get(key, 0) + value
        return {"catalog_updated_at": None, "catalog_entries": 0, **self._published,
                "dropped_by_reason": dict(self._published["dropped_by_reason"]),
                "enabled": self.settings.shadow_enabled, "disabled": self._disabled,
                "skipped_rounds": self.skipped_rounds, "skipped_publications": self.skipped_publications,
                "errors": sum(counts.values()), "error_counts": counts, "catalog_errors": self.catalog.errors,
                "child_restarts": self.child_restarts}

    async def run_inline_observer(self, stop):
        if not self.settings.shadow_enabled:
            return
        generation = 0
        while not stop.is_set():
            if self.store.generation > generation:
                generation = self.store.generation
                await self.submit(PublishedSnapshot(generation, self.store.published_at,
                    tuple(self.store.get_markets()), tuple(self.store.get_opportunities()),
                    opportunities_version=self.store.opportunities_version))
            try:
                await asyncio.wait_for(stop.wait(), .1)
            except TimeoutError:
                pass

    async def aclose(self):
        self._closing = True
        if not self.settings.shadow_enabled:
            return
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _shutdown(self):
        if self._supervisor is not None:
            await self._supervisor
        if self._stop is not None:
            self._stop.set()
        deadline = monotonic() + SHUTDOWN_SECONDS
        while self._process is not None and self._process.is_alive() and monotonic() < deadline:
            await asyncio.sleep(.02)
        if self._process is not None and self._process.is_alive():
            self.error("shutdown_timeout")
        await self._dispose(force=True)
        self._published["ready"] = False
        self._context = None
        if self._active is not None and not self._active.done():
            self._active.set_result(None)
