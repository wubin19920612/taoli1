"""Spawn-only Gate sockets. Public wire parsing never runs in the parent loop.

IPC quotes are (bid, bid_size, ask, ask_size, t_ms, update_id, connection_generation).
Futures sizes remain contracts on this wire; the adapter converts them to base units.
None is a tombstone, so malformed updates cannot leave an old book executable.
"""
from __future__ import annotations

import asyncio
import json
import math
import queue
from collections import deque
from itertools import islice
from time import monotonic, process_time, time

MAX_BATCH = 2000
BATCH_SECONDS = .2
RECONNECT_DELAYS = (1, 2, 5, 10, 30)
URLS = {"future": "wss://fx-ws.gateio.ws/v4/ws/usdt", "spot": "wss://api.gateio.ws/ws/v4/"}
CHANNELS = {"future": "futures", "spot": "spot"}


def _side(price, size):
    if isinstance(price, bool) or isinstance(size, bool):
        raise TypeError("boolean level")
    if price == "" or size == "":
        return None, None
    price, size = float(price), float(size)
    if not math.isfinite(price) or not math.isfinite(size) or price < 0 or size < 0:
        raise ValueError("invalid level")
    return (price, size) if price and size else (None, None)


class GateBboWorker:
    heartbeat_seconds = 30

    def __init__(self):
        self.subscriptions = {kind: set() for kind in URLS}
        self.quotes = {}
        self.dirty = {}
        self._versions = {}
        self._started_at = monotonic()
        self.states = {kind: {"connected": False, "last_message_at": None, "generation": 0,
            "subscribed": 0, "messages": 0, "reconnects": 0, "last_error": None} for kind in URLS}
        self._counts = {kind: deque(maxlen=301) for kind in URLS}
        self._changed = {kind: asyncio.Event() for kind in URLS}
        self._sent = {kind: set() for kind in URLS}

    def set_subscriptions(self, selected):
        for kind in URLS:
            # Parent configuration bounds each side at 1000, also defend the wire boundary.
            desired = set(sorted({s for s in selected.get(kind, [])
                                  if isinstance(s, str) and s.endswith("_USDT")})[:1000])
            for symbol in self.subscriptions[kind] - desired:
                key = kind, symbol
                self.quotes.pop(key, None)
                self.dirty.pop(key, None)  # Parent prunes removed subscriptions itself.
                self._versions.pop(key, None)
            if desired != self.subscriptions[kind]:
                self.subscriptions[kind] = desired
                self._changed[kind].set()

    def handle_message(self, kind, raw):
        try:
            message = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            return
        if not isinstance(message, dict):
            return
        channel = message.get("channel", "")
        if channel not in {f"{CHANNELS[kind]}.book_ticker", f"{CHANNELS[kind]}.pong"}:
            return
        if message.get("error"):
            # Subscription errors fail the connection closed, including cached books.
            raise ValueError("Gate subscription error")
        now = monotonic()
        self.states[kind]["last_message_at"] = now
        self.states[kind]["messages"] += 1
        counts = self._counts[kind]
        second = int(now)
        if counts and counts[-1][0] == second:
            counts[-1] = second, counts[-1][1] + 1
        else:
            counts.append((second, 1))
        if message.get("event") != "update":
            return
        result = message.get("result")
        if not isinstance(result, dict):
            return
        symbol = result.get("s")
        if not isinstance(symbol, str) or symbol not in self.subscriptions[kind]:
            return
        key = kind, symbol
        previous = self._versions.get(key)
        try:
            stamp, update = result["t"], result["u"]
            if type(stamp) is not int or not 946_684_800_000 <= stamp <= 4_102_444_800_000:
                raise ValueError("invalid timestamp")
            if type(update) is not int or update < 0:
                raise ValueError("invalid update ID")
            if previous is not None and (update <= previous[0] or stamp < previous[1]):
                return
            self._versions[key] = update, stamp
            bid, bid_size = _side(result["b"], result["B"])
            ask, ask_size = _side(result["a"], result["A"])
            quote = (bid, bid_size, ask, ask_size, stamp, update, self.states[kind]["generation"])
        except (ValueError, TypeError, KeyError, OverflowError):
            quote = None
        self.quotes[key] = self.dirty[key] = quote

    def publish(self, outgoing):
        changes = dict(islice(self.dirty.items(), MAX_BATCH))
        cutoff = int(monotonic()) - 300
        window = max(.001, min(300, monotonic() - self._started_at))
        states = {kind: {**state, "messages_per_second": sum(
            count for second, count in self._counts[kind] if second > cutoff) / window}
            for kind, state in self.states.items()}
        packet = {"quotes": changes, "connections": states, "cpu_seconds": process_time()}
        try:
            outgoing.put_nowait(packet)
        except queue.Full:
            return False
        for key in changes:
            self.dirty.pop(key, None)
        return True

    async def _receive(self, kind, websocket):
        while True:
            self.handle_message(kind, await websocket.recv())

    async def _send(self, kind, websocket):
        last_subscription = float("-inf")
        next_ping = monotonic() + self.heartbeat_seconds
        while True:
            self._changed[kind].clear()
            desired, sent = self.subscriptions[kind], self._sent[kind]
            if desired != sent:
                while (delay := BATCH_SECONDS - (monotonic() - last_subscription)) > 0:
                    await asyncio.sleep(delay)
                desired = self.subscriptions[kind]
                removed, added = sent - desired, desired - sent
                event, symbols = ("unsubscribe", removed) if removed else ("subscribe", added)
                batch = sorted(symbols)[:100]
                if batch:
                    await asyncio.wait_for(websocket.send(json.dumps({"time": int(time()),
                        "channel": f"{CHANNELS[kind]}.book_ticker", "event": event, "payload": batch})), 5)
                    last_subscription = monotonic()
                    if event == "unsubscribe":
                        sent.difference_update(batch)
                    else:
                        sent.update(batch)
                    self.states[kind]["subscribed"] = len(sent)
            if monotonic() >= next_ping:
                await asyncio.wait_for(websocket.send(json.dumps({
                    "time": int(time()), "channel": f"{CHANNELS[kind]}.ping"})), 5)
                next_ping = monotonic() + self.heartbeat_seconds
            if self.subscriptions[kind] != sent:
                continue
            try:
                await asyncio.wait_for(self._changed[kind].wait(), max(0, next_ping - monotonic()))
            except TimeoutError:
                pass

    async def _connection(self, kind):
        from websockets.asyncio.client import connect

        failures, attempted = 0, False
        state = self.states[kind]
        while True:
            while not self.subscriptions[kind]:
                self._changed[kind].clear()
                await self._changed[kind].wait()
            if attempted:
                state["reconnects"] += 1
            attempted = True
            started = None
            try:
                async with connect(URLS[kind], open_timeout=10, close_timeout=.3,
                                   ping_interval=20, ping_timeout=20, max_queue=16) as websocket:
                    started = monotonic()
                    state.update(connected=True, last_message_at=None, last_error=None,
                                 generation=state["generation"] + 1)
                    for key in [key for key in self.quotes if key[0] == kind]:
                        self.quotes.pop(key)
                        self._versions.pop(key, None)
                        self.dirty[key] = None
                    tasks = [asyncio.create_task(self._receive(kind, websocket)),
                             asyncio.create_task(self._send(kind, websocket))]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as exc:  # noqa: BLE001 - reconnect; expose error class in status.
                state["last_error"] = type(exc).__name__
            finally:
                state.update(connected=False, subscribed=0, last_message_at=None)
                self._sent[kind].clear()
            if started is not None and monotonic() - started >= 60:
                failures = 0
            delay = RECONNECT_DELAYS[min(failures, len(RECONNECT_DELAYS) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def run(self, incoming, outgoing):
        sockets = [asyncio.create_task(self._connection(kind)) for kind in URLS]
        next_batch = monotonic()
        try:
            while True:
                try:
                    command = incoming.get_nowait()
                except queue.Empty:
                    command = None
                if command == "stop":
                    return
                if isinstance(command, dict):
                    self.set_subscriptions(command)
                if monotonic() >= next_batch:
                    self.publish(outgoing)
                    next_batch = monotonic() + BATCH_SECONDS
                for task in sockets:
                    if task.done():
                        task.result()
                        raise RuntimeError("Gate socket task exited")
                await asyncio.sleep(.02)
        finally:
            for task in sockets:
                task.cancel()
            await asyncio.gather(*sockets, return_exceptions=True)


def child_entry(incoming, outgoing):
    # Closing unused endpoints makes a killed/partial peer produce EOF rather than
    # leaving the other end waiting indefinitely for the rest of a pickle frame.
    incoming._writer.close()
    outgoing._reader.close()
    outgoing.cancel_join_thread()
    try:
        asyncio.run(GateBboWorker().run(incoming, outgoing))
    finally:
        incoming.close()
        outgoing.close()
