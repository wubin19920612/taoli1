"""Bounded spawn supervision and a read-only quote cache for Gate collection."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import multiprocessing
import queue
import threading
from time import monotonic, time

from app.exchanges.gate_bbo import MAX_BATCH, RECONNECT_DELAYS, child_entry
from app.services.shadow_ipc import rss_mb

logger = logging.getLogger("gate_bbo")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


def select_subscriptions(markets, previous, settings):
    """Exact Gate wire IDs; normalized symbols are used only for the cross-venue filter."""
    others = {m.symbol for m in markets if m.exchange != "gate"}
    candidates = {"spot": {}, "future": {}}
    for market in markets:
        volume = market.volume_24h_usdt
        if (market.exchange == "gate" and market.symbol in others
                and market.market_type in candidates and market.raw_symbol.endswith("_USDT")
                and volume is not None and math.isfinite(volume)
                and volume >= settings.gate_bbo_min_volume_usd):
            candidates[market.market_type][market.raw_symbol] = volume
    selected = {}
    for kind, cap in (("spot", settings.gate_bbo_max_spot), ("future", settings.gate_bbo_max_futures)):
        retained = set(previous.get(kind, ()))
        selected[kind] = sorted(candidates[kind], key=lambda symbol: (
            symbol not in retained, -candidates[kind][symbol], symbol))[:cap]
    return selected


class GateBboRuntime:
    refresh_seconds = 600
    stats_seconds = 300

    def __init__(self, settings, store=None):
        self.settings, self.store = settings, store
        self._process = self._incoming = self._outgoing = None
        self._reader = self._reader_stop = self._supervisor = None
        self._quotes = {}
        self._connections = {}
        self._subscriptions = {"spot": [], "future": []}
        self._allowed = frozenset()
        self._subscription_pending = True
        self._epoch = 0
        self._closing = False
        self._close_task = None
        self._restart_at = None
        self._failures = 0
        self._started_at = None
        self._last_packet_at = None
        self._refresh_at = 0
        self._rss_at = 0
        self._log_at = monotonic() + self.stats_seconds
        self._rss = self._cpu = None
        self._transport_error = False
        self._dispose_lock = asyncio.Lock()
        self._mail_lock = threading.Lock()
        self._mail = None
        self._mail_scheduled = False
        self.restarts = self.batches = self.batch_size = 0
        self.merge_hits = self.merge_fallbacks = 0
        self.merge_errors = 0
        self.last_merge_error = None
        self._merge_error_log_at = 0
        self.last_error = None

    def _alive(self):
        return self._process is not None and self._process.is_alive()

    def get_fresh(self, kind, raw_symbol):
        if (not self.settings.gate_bbo_enabled or self._closing or not self._alive()
                or (str(kind), raw_symbol) not in self._allowed):
            return None
        state = self._connections.get(str(kind), {})
        received = state.get("last_message_at")
        if (not state.get("connected") or received is None
                or not 0 <= monotonic() - received <= 5):
            return None
        quote = self._quotes.get((str(kind), raw_symbol))
        if quote is None or quote[6] != state.get("generation"):
            return None
        bid, bid_size, ask, ask_size, stamp, _, _ = quote
        if not -2 <= time() - stamp / 1000 <= self.settings.gate_bbo_quote_max_age_seconds:
            return None
        if (any(value is None or not math.isfinite(value) or value <= 0
                for value in (bid, bid_size, ask, ask_size)) or bid >= ask):
            return None
        return quote

    def record_merge(self, hit):
        if hit:
            self.merge_hits += 1
        else:
            self.merge_fallbacks += 1

    def record_merge_error(self, stage, exc):
        # Keep independent of record_merge: that instrumentation may be the
        # failing operation. Counters include suppressed logs; never log payloads.
        self.merge_errors += 1
        self.last_merge_error = {"stage": stage, "type": type(exc).__name__}
        now = monotonic()
        if now >= self._merge_error_log_at:
            self._merge_error_log_at = now + 60
            logger.warning(json.dumps({"gate_bbo": {
                "merge_errors": self.merge_errors, "last_merge_error": self.last_merge_error,
            }}, separators=(",", ":")))

    def set_subscriptions(self, selected):
        self._subscriptions = {kind: list(selected.get(kind, ()))[:cap] for kind, cap in (
            ("spot", self.settings.gate_bbo_max_spot), ("future", self.settings.gate_bbo_max_futures))}
        with self._mail_lock:
            self._allowed = frozenset((kind, symbol) for kind, symbols in self._subscriptions.items() for symbol in symbols)
            if self._mail is not None:
                self._mail["quotes"] = {key: value for key, value in self._mail["quotes"].items() if key in self._allowed}
        self._quotes = {key: value for key, value in self._quotes.items() if key in self._allowed}
        self._subscription_pending = True

    def refresh_subscriptions(self):
        if self.store is None or monotonic() < self._refresh_at:
            return
        markets = self.store.get_all_markets()
        # Retry until the first publication; do not wait ten minutes on an empty startup store.
        self._refresh_at = monotonic() + (self.refresh_seconds if markets else 1)
        if markets:
            self.set_subscriptions(select_subscriptions(markets, self._subscriptions, self.settings))

    async def start(self):
        if self._closing or self._supervisor is not None:
            return
        # Keep logging fallback counters even with sockets disabled.
        if self.settings.gate_bbo_enabled:
            self.refresh_subscriptions()
            try:
                self._spawn()
            except Exception:  # noqa: BLE001 - spawn errors enter bounded recovery.
                await self._failed("spawn")
        self._supervisor = asyncio.create_task(self._supervise(), name="gate-bbo-supervisor")

    def _spawn(self):
        ctx = multiprocessing.get_context("spawn")
        self._epoch += 1
        self._incoming, self._outgoing = ctx.Queue(maxsize=1), ctx.Queue(maxsize=1)
        self._process = ctx.Process(target=child_entry, args=(self._incoming, self._outgoing),
                                    name="gate-bbo", daemon=True)
        self._process.start()
        self._incoming._reader.close()
        self._outgoing._writer.close()
        self._incoming._ignore_epipe = True
        self._started_at = monotonic()
        self._last_packet_at = None
        self._transport_error = False
        self._subscription_pending = True
        self._reader_stop = threading.Event()
        self._reader = threading.Thread(target=self._read_results,
            args=(self._outgoing, self._reader_stop, asyncio.get_running_loop(), self._epoch),
            name="gate-bbo-results", daemon=True)
        self._reader.start()

    def _read_results(self, outgoing, stopped, loop, epoch):
        while not stopped.is_set():
            try:
                packet = outgoing.get(timeout=.1)
                if len(packet["quotes"]) > MAX_BATCH:
                    raise ValueError("oversized Gate IPC batch")
            except queue.Empty:
                continue
            except (EOFError, OSError, ValueError, KeyError, TypeError):
                try:
                    loop.call_soon_threadsafe(self._transport_failed, epoch)
                except RuntimeError:
                    pass
                return
            # One queued loop callback, even when collection stalls. Merge deltas
            # rather than dropping a batch: quiet symbols may never send again.
            with self._mail_lock:
                if epoch != self._epoch or stopped.is_set():
                    return
                if self._mail is None:
                    self._mail = {**packet, "batch_count": 1, "batch_size": len(packet["quotes"])}
                else:
                    self._mail["quotes"].update(packet["quotes"])
                    self._mail.update(connections=packet["connections"], cpu_seconds=packet["cpu_seconds"],
                                      batch_count=self._mail["batch_count"] + 1,
                                      batch_size=len(packet["quotes"]))
                # Membership changes can temporarily add old IDs to the mailbox.
                # Bound it without dropping current subscription deltas.
                self._mail["quotes"] = {key: value for key, value in self._mail["quotes"].items() if key in self._allowed}
                if not self._mail_scheduled:
                    self._mail_scheduled = True
                    try:
                        loop.call_soon_threadsafe(self._drain_mail, epoch)
                    except RuntimeError:
                        return

    def _transport_failed(self, epoch):
        if epoch == self._epoch and not self._closing:
            self._transport_error = True
            self._connections.clear()

    def _drain_mail(self, epoch):
        if epoch != self._epoch:
            return
        with self._mail_lock:
            packet, self._mail = self._mail, None
            self._mail_scheduled = False
        if packet is not None and not self._closing:
            self._quotes.update(packet["quotes"])
            self._connections = packet["connections"]
            self._cpu = packet["cpu_seconds"]
            self.batch_size = packet["batch_size"]
            self.batches += packet["batch_count"]
            self._last_packet_at = monotonic()

    async def _supervise(self):
        while not self._closing:
            await self._tick()
            await asyncio.sleep(.1)

    async def _tick(self):
        now = monotonic()
        if now >= self._log_at:
            logger.info(json.dumps({"gate_bbo": self.status()}, separators=(",", ":"), allow_nan=False))
            self._log_at = now + self.stats_seconds
        if self._closing or not self.settings.gate_bbo_enabled:
            return
        self.refresh_subscriptions()
        if self._restart_at is not None:
            if now >= self._restart_at:
                self._restart_at = None
                self.restarts += 1
                try:
                    self._spawn()
                except Exception:  # noqa: BLE001 - spawn errors enter bounded recovery.
                    await self._failed("spawn")
            return
        if not self._alive():
            await self._failed("crash")
            return
        if self._transport_error:
            await self._failed("transport")
            return
        # A wedged child cannot keep quotes live just because the PID exists.
        if now - (self._last_packet_at or self._started_at) > 10:
            await self._failed("ipc_timeout")
            return
        if now >= self._rss_at:
            try:
                self._rss = rss_mb(self._process.pid)
            except OSError:
                await self._failed("rss_probe")
                return
            self._rss_at = now + 1
            if self._rss > self.settings.gate_bbo_child_max_rss_mb:
                await self._failed("rss_limit")
                return
        if self._last_packet_at is not None and now - self._started_at >= 60:
            self._failures = 0
        if self._subscription_pending:
            try:
                self._incoming.put_nowait(self._subscriptions)
                self._subscription_pending = False
            except queue.Full:
                pass
            except (OSError, ValueError):
                await self._failed("transport")

    async def _failed(self, reason):
        self.last_error = reason
        self._invalidate()
        await self._dispose(monotonic() + 1.5)
        delay = RECONNECT_DELAYS[min(self._failures, len(RECONNECT_DELAYS) - 1)]
        self._failures += 1
        self._restart_at = monotonic() + delay

    def _invalidate(self):
        with self._mail_lock:
            self._epoch += 1
            self._mail = None
            self._mail_scheduled = False
        self._quotes.clear()
        self._connections.clear()

    async def _dispose(self, deadline):
        async with self._dispose_lock:
            async def join(target, cap):
                budget = min(cap, max(0, deadline - monotonic()))
                if budget:
                    try:
                        await asyncio.wait_for(asyncio.to_thread(target.join, budget), budget)
                    except TimeoutError:
                        pass
            process = self._process
            if self._reader_stop is not None:
                self._reader_stop.set()
            if process is not None and process.pid is not None:
                if process.is_alive():
                    try:
                        process.terminate()
                    except OSError:
                        pass
                await join(process, .4)
                if process.is_alive():
                    process.kill()
                    await join(process, .4)
                if process.is_alive():
                    self.last_error = "kill_timeout"
                    raise RuntimeError("Gate child did not exit")
                process.close()
            self._process = None
            if self._reader is not None:
                await join(self._reader, .15)
            for channel in (self._incoming, self._outgoing):
                if channel is not None:
                    channel.cancel_join_thread()
                    channel.close()
            self._incoming = self._outgoing = self._reader = self._reader_stop = None

    async def aclose(self):
        if self._close_task is None:
            self._closing = True
            self._invalidate()
            self._close_task = asyncio.create_task(self._shutdown(monotonic() + 1.8))
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._close_task)
            raise

    async def _shutdown(self, deadline):
        if self._supervisor is not None:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
        if self._incoming is not None:
            try:
                self._incoming.put_nowait("stop")
            except (queue.Full, OSError, ValueError):
                pass
        grace = min(deadline - 1, monotonic() + .7)
        while self._alive() and monotonic() < grace:
            await asyncio.sleep(.02)
        await self._dispose(deadline)

    def status(self):
        now = monotonic()
        alive = self._alive()
        connections = {}
        for kind in ("spot", "future"):
            state = self._connections.get(kind, {})
            received = state.get("last_message_at")
            connections[kind] = {
                "connected": bool(alive and state.get("connected")),
                "live": bool(alive and state.get("connected") and received is not None and 0 <= now - received <= 5),
                "subscribed": state.get("subscribed", 0) if alive else 0,
                "requested": len(self._subscriptions[kind]),
                "messages": state.get("messages", 0),
                "messages_per_second": state.get("messages_per_second", 0),
                "reconnects": state.get("reconnects", 0),
                "last_error": state.get("last_error"),
            }
        return {"enabled": self.settings.gate_bbo_enabled,
            "batch_quotes_executable": self.settings.gate_batch_quotes_executable,
            "connections": connections, "pid": self._process.pid if alive else None,
            "rss_mb": self._rss, "cpu_seconds": self._cpu, "restarts": self.restarts,
            "batches": self.batches, "batch_size": self.batch_size, "merge_hits": self.merge_hits,
            "merge_fallbacks": self.merge_fallbacks, "last_error": self.last_error,
            "merge_errors": self.merge_errors, "last_merge_error": self.last_merge_error,
            "restart_in_seconds": max(0, self._restart_at - now) if self._restart_at is not None else None}
