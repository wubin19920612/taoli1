"""Bounded spawn IPC. The parent owns presentation only, never a shadow ledger."""
import asyncio
import multiprocessing
import pickle
import queue
import threading
from weakref import ref
from time import monotonic, perf_counter, thread_time_ns
from array import array

from app.services.execution_routes import ExecutionRoutes, ShadowErrorLog
from app.services.shadow_ipc import child_entry, market_values, op_values, directory_values, rss_mb, CAPTURE_CHUNK_SIZE
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
        self._shutdown_deadline = None
        self._dispose_lock = asyncio.Lock()
        self._frozen_markets = ()
        self._frozen_ops = ()
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
        self._round_sequence = 0
        self._needs_resync = True
        self.resync_count = 0
        self._sent_full = False
        self._directory = []
        self._directory_sources = []
        self._quotes = {}
        self._threshold_ids = ()
        self._catalog_version = None
        self._capture_task = None
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
        # No shared Event: a killed child may permanently own its condition lock.
        # Stop travels through the parent's nonblocking queue producer instead.
        self._stop = None
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
                if message[0] == "statistics" and isinstance(message[2], bytes):
                    kind, token, payload, status = message
                    message = kind, token, pickle.loads(payload), status
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
        if kind in {"capture", "round"} and (token != self._serial or self._context is None):
            return
        if kind == "capture":
            self._capture_task = asyncio.create_task(self._timed_capture(self._capture_reply(token, result), result[0]))
            return
        if kind == "round":
            if status.pop("resync_required", False):
                self._needs_resync = True
            else:
                self._needs_resync = status.get("mirror_sequence") != self._round_sequence
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
            generation, version, originals = snapshot.generation, snapshot.opportunities_version, self._frozen_ops
            capture_ms, cpu_ms = sum(segments), sum(cpus)
            try:
                if self.store.opportunities_version != version:
                    self.skipped_publications += 1
                elif self.store.generation == generation and result is not None:
                    # Unrelated rows retain object identity, order and count.
                    display, cache = [], {}
                    for op in originals:
                        values_dict = op.__dict__
                        op_id = values_dict["id"]
                        fields = result.get(op_id)
                        if fields is None:
                            display.append(op)
                            continue
                        previous = self._display_cache.get(op_id)
                        if previous is not None and previous[1] == fields and (
                                previous[0] is op or previous[0].__dict__ == values_dict):
                            entry = previous
                        else:
                            entry = (op, fields, op.model_copy(update=fields))
                        cache[op_id] = entry
                        display.append(entry[2])
                    self._display_cache = cache
                    if not self.store.set_opportunities_if_version(display, version):
                        self.skipped_publications += 1
            except Exception:
                self.error("publish")
            # The child acknowledged every shard. Do not keep the producer's
            # previous model generation alive while the next batch is built.
            self._frozen_markets = self._frozen_ops = ()
            del originals, snapshot, context
            publish_ms = (perf_counter() - resumed) * 1000
            self._published.update(main_cpu_ms=cpu_ms, capture_ms=capture_ms, publish_ms=publish_ms,
                last_duration_ms=capture_ms + publish_ms,
                capture_segments_ms=tuple(segments), capture_phases_ms=dict(self._capture_phases),
                event_loop_max_segment_ms=max(*segments, publish_ms),
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
        # Reserve before the first cooperative yield. Cancellation of a caller
        # never releases this slot while capture/IPC still owns the snapshot.
        self._active = asyncio.get_running_loop().create_future()
        self._serial += 1
        self._busy_since = monotonic()
        self._context = (snapshot, [], [])
        self._capture_phases = dict.fromkeys(("thresholds", "directory", "quotes"), 0.0)
        self._capture_phase = "thresholds"
        # PM 46: published models and their list fields are immutable by contract.
        # Capture only references before returning/yielding; producers replace
        # whole batches. All shards (including their lengths) use these tuples.
        self._segment_started, self._segment_cpu = perf_counter(), thread_time_ns()
        try:
            self._frozen_markets = tuple(snapshot.markets)
            self._frozen_ops = tuple(snapshot.opportunities)
            self._frozen_catalog = (self.catalog.updated_at, id(self.catalog.entries), self.catalog.entries)
        except Exception:
            self._capture_failed()
            return self._active
        finally:
            self._record_segment()
        self._capture_task = asyncio.create_task(self._timed_capture(self._capture_start(self._serial), "thresholds"))
        return self._active

    def _encode(self, name, data):
        return pickle.dumps(data, protocol=5)

    def _record_segment(self):
        if self._context is not None:
            elapsed = (perf_counter() - self._segment_started) * 1000
            self._context[1].append(elapsed)
            self._capture_phases[self._capture_phase] += elapsed
            self._context[2].append((thread_time_ns() - self._segment_cpu) / 1e6)

    async def _timed_capture(self, work, phase):
        # Include loop/setup/cleanup overhead, not just projection calls. Only
        # cooperative await time and child IPC wait are excluded from occupancy.
        self._capture_phase = phase
        self._segment_started, self._segment_cpu = perf_counter(), thread_time_ns()
        try:
            await work
        finally:
            self._record_segment()

    async def _yield_capture(self):
        self._record_segment()
        await asyncio.sleep(0)
        self._segment_started, self._segment_cpu = perf_counter(), thread_time_ns()

    async def _slice(self, action):
        return action()

    async def _capture_start(self, token):
        try:
            snapshot = self._context[0]
            full = self._needs_resync
            if full:
                def reset_mirrors():
                    self._directory.clear()
                    self._directory_sources.clear()
                    self._quotes.clear()
                await self._slice(reset_mirrors)
                await self._yield_capture()
            ids, spreads = [], array("d")
            for offset in range(0, len(self._frozen_ops), CAPTURE_CHUNK_SIZE):
                def capture():
                    for op in self._frozen_ops[offset:offset + CAPTURE_CHUNK_SIZE]:
                        data = op.__dict__
                        ids.append(data["id"])
                        spreads.append(data["open_spread_pct"])
                await self._slice(capture)
                await self._yield_capture()
            def dispatch():
                id_table = tuple(ids)
                changed_ids = id_table if full or id_table != self._threshold_ids else None
                self._threshold_ids = id_table
                version = self._frozen_catalog[:2]
                entries = self._frozen_catalog[2] if full or version != self._catalog_version else None
                self._catalog_version = version
                self._round_sequence += 1
                payload = self._encode("round", (self._round_sequence, full, snapshot.published_at,
                                                  changed_ids, spreads, entries))
                self._incoming.put_nowait(("round", token, payload))
                if full:
                    self.resync_count += int(self._sent_full)
                    self._sent_full = True
            await self._slice(dispatch)
        except Exception:
            self._capture_failed()

    def _capture_failed(self):
        self.error("capture")
        self._needs_resync = True
        self._transport_error = "transport"

    async def _capture_reply(self, token, request):
        if token != self._serial or self._context is None:
            return
        kind, indices = request
        self._capture_phase = kind
        try:
            chunks = []
            if kind == "directory":
                # Replacements can change metadata at the same quote timestamp.
                # Compare immutable leaves; only changed rows allocate tuples.
                markets, directory, sources = self._frozen_markets, self._directory, self._directory_sources
                for offset in range(0, len(markets), CAPTURE_CHUNK_SIZE):
                    def capture_directory():
                        delta = []
                        for i in range(offset, min(offset + CAPTURE_CHUNK_SIZE, len(markets))):
                            model = markets[i]
                            # Weak refs permit the identity fast path under PM 46
                            # without retaining an entire previous model batch or
                            # confusing a reused id with the old object.
                            if i < len(sources) and sources[i]() is model:
                                continue
                            source = ref(model)
                            if i < len(sources):
                                sources[i] = source
                            else:
                                sources.append(source)
                            d = model.__dict__
                            old = directory[i] if i < len(directory) else None
                            if old is None or not (
                                d["exchange"] == old[0] and d["market_type"] == old[1]
                                and d["raw_symbol"] == old[2] and d["dex"] == old[3]
                                and d["symbol_alias_price_multiplier"] == old[4]
                                and d["contract_size_multiplier"] == old[5]
                                and d["asset_id"] == old[6] and d["symbol"] == old[7]):
                                row = directory_values(d)
                                delta.append((i, row))
                                self._quotes.pop(i, None)
                                if old is None:
                                    directory.append(row)
                                else:
                                    directory[i] = row
                        if delta:
                            chunks.append(self._encode("directory_delta", delta))
                    await self._slice(capture_directory)
                    await self._yield_capture()
                op_chunks = []
                for offset in range(0, len(indices), CAPTURE_CHUNK_SIZE):
                    def capture_ops():
                        rows = []
                        for i in indices[offset:offset + CAPTURE_CHUNK_SIZE]:
                            values = op_values(self._frozen_ops[i].__dict__)
                            rows.append(values[:-3] + (tuple(values[-3]), tuple(values[-2]), tuple(values[-1])))
                        op_chunks.append(self._encode("op_rows", rows))
                    await self._slice(capture_ops)
                    await self._yield_capture()
                def dispatch():
                    size = len(self._frozen_markets)
                    for i in range(size, len(self._directory)):
                        self._quotes.pop(i, None)
                    del self._directory[size:]
                    del self._directory_sources[size:]
                    self._incoming.put_nowait((kind, token, pickle.dumps((size, chunks, op_chunks), protocol=5)))
                await self._slice(dispatch)
            elif kind == "quotes":
                indices = range(len(self._frozen_markets)) if indices is None else indices
                for offset in range(0, len(indices), CAPTURE_CHUNK_SIZE):
                    def capture_quotes():
                        delta = []
                        for i in indices[offset:offset + CAPTURE_CHUNK_SIZE]:
                            values = market_values(self._frozen_markets[i].__dict__)
                            # Wire/cache leaves are tuples; no per-object copying
                            # occurs in the synchronous reference capture above.
                            values = values[:-1] + (tuple(values[-1]),)
                            if self._quotes.get(i) != values:
                                self._quotes[i] = values
                                delta.append((i, values))
                        if delta:
                            chunks.append(self._encode("quote_delta", delta))
                    await self._slice(capture_quotes)
                    await self._yield_capture()
                await self._slice(lambda: self._incoming.put_nowait((kind, token, pickle.dumps(chunks, protocol=5))))
            else:
                raise ValueError("invalid shadow capture request")
        except Exception:
            self._capture_failed()

    async def consume(self, snapshot):
        task = await self.submit(snapshot)
        if task is not None:
            await asyncio.shield(task)

    async def statistics(self, since, until, dims, include_fake, strategy_version):
        # Statistics share the same single-flight slot. A waiting API owns no
        # queued compute; incoming market rounds skip while statistics runs.
        while self._active is not None and not self._active.done():
            await asyncio.shield(self._active)
        if self._closing or self._disabled or not self._published["ready"]:
            raise RuntimeError("shadow worker unavailable")
        self._serial += 1
        self._incoming.put_nowait(("statistics", self._serial,
            self._encode("statistics", (since, until, dims, include_fake, strategy_version))))
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
        async with self._dispose_lock:
            await self._dispose_owned(force)

    async def _dispose_owned(self, force=False):
        if self._capture_task is not None and not self._capture_task.done():
            self._capture_task.cancel()
            await asyncio.gather(self._capture_task, return_exceptions=True)
        self._needs_resync = True
        process = self._process
        # Normal fault recovery has the same bounded terminate/kill ladder.
        deadline = self._shutdown_deadline or monotonic() + 4.5
        def remaining(cap):
            return max(0, min(cap, deadline - monotonic()))
        async def join(target, cap):
            timeout = remaining(cap)
            if timeout <= 0:
                return
            try:
                # Bound executor queueing too, not just the blocking join call.
                await asyncio.wait_for(asyncio.to_thread(target.join, timeout), timeout)
            except TimeoutError:
                pass
        if process is not None and process.pid is not None:
            if force and process.is_alive():
                try:
                    process.terminate()
                except OSError:
                    self.error("shutdown_terminate")
            await join(process, 2)
            if process.is_alive():
                try:
                    process.kill()
                except OSError:
                    self.error("shutdown_kill")
                await join(process, 2)
            if process.is_alive():
                self.error("shutdown_kill_timeout")
            else:
                process.close()
        if self._reader_stop is not None:
            self._reader_stop.set()
        if self._reader is not None:
            await join(self._reader, .5)
        for channel in (self._incoming, self._outgoing):
            if channel is not None:
                channel.cancel_join_thread()
                channel.close()
        self._process = self._incoming = self._outgoing = self._stop = None
        self._reader = self._reader_stop = None
        self._epoch += 1
        self._published["worker_pid"] = None
        self._display_cache.clear()
        self._directory.clear()
        self._directory_sources.clear()
        self._quotes.clear()
        self._threshold_ids = ()
        self._frozen_markets = self._frozen_ops = ()

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
                "child_restarts": self.child_restarts, "resync_count": self.resync_count}

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
            self._shutdown_deadline = monotonic() + SHUTDOWN_SECONDS + 4.5
            self._close_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._close_task)
            except Exception:
                self.error("shutdown")
            raise
        except Exception:
            self.error("shutdown")

    async def _shutdown(self):
        deadline = self._shutdown_deadline - 4.5
        # Accepted capture may still be yielding before sending its first frame.
        # Let it drain within the same shutdown deadline before asking the idle
        # child to exit; otherwise the stop flag can race with that first frame.
        try:
            if self._supervisor is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(self._supervisor), max(0, deadline - monotonic()))
                except TimeoutError:
                    self._supervisor.cancel()
                    await asyncio.gather(self._supervisor, return_exceptions=True)
                    raise
            while self._active is not None and not self._active.done() and monotonic() < deadline:
                await asyncio.sleep(.01)
            if self._incoming is not None:
                try:
                    self._incoming.put_nowait(("stop", 0, b""))
                except (queue.Full, OSError, ValueError):
                    self.error("shutdown_notify")
            while self._process is not None and self._process.is_alive() and monotonic() < deadline:
                await asyncio.sleep(.02)
            if self._process is not None and self._process.is_alive():
                self.error("shutdown_timeout")
        except (Exception, asyncio.CancelledError):
            self.error("shutdown_wait")
        finally:
            try:
                await self._dispose(force=True)
            except Exception:
                self.error("shutdown_dispose")
            finally:
                self._published["ready"] = False
                self._context = None
                if self._active is not None and not self._active.done():
                    self._active.set_result(None)
