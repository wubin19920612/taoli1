"""One lifespan-owned socket; collection only reads the exact-coin memory cache.

Wire contract follows docs/hl-bbo-spec-2026-11-03.md. HIP-3 coins retain the
metadata name, as in HyperliquidAdapter's existing l2Book path. No REST calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import deque
from collections.abc import Iterable
from time import monotonic

from websockets.asyncio.client import connect as websocket_connect

logger = logging.getLogger("hl_bbo")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    # Like alert_timing: a plain JSON line even if root filters INFO.
    logger.addHandler(logging.StreamHandler())

BboQuote = tuple[float | None, float | None, float | None, float | None, int, float]


class HyperliquidBboStream:
    ws_url = "wss://api.hyperliquid.xyz/ws"
    send_interval_seconds = 1 / 20
    heartbeat_seconds = 50.0  # Leave margin before HL's 60-second idle limit.
    stats_interval_seconds = 300.0
    reconnect_delays = (1, 2, 5, 10, 30)
    # HL pushes bbo only on change, so a quiet market keeps its last book as long as
    # the connection itself is live; the per-quote cap bounds silently lost streams.
    quote_max_age_seconds = 120.0

    def __init__(self, *, max_coins: int = 450, max_age_seconds: float = 10.0):
        self.max_coins = max_coins
        self.max_age_seconds = max_age_seconds
        self._coins: set[str] = set()
        self._quotes: dict[str, BboQuote] = {}
        self._changed = asyncio.Event()
        self._subscribed: set[str] = set()
        self._connected = False
        self._reconnects = 0
        self._last_send = float("-inf")
        self._last_message_at = float("-inf")
        # Per-second buckets, not a timestamp per message; memory stays bounded.
        self._message_counts: deque[tuple[int, int]] = deque(maxlen=301)

    def set_coins(self, coins: Iterable[str]) -> None:
        candidates = {
            coin for coin in coins
            if isinstance(coin, str) and coin and not coin.startswith("@") and "/" not in coin
        }
        # Keep sent subscriptions first; metadata ordering must not churn a capped set.
        selected = set(sorted(
            candidates, key=lambda coin: (coin not in self._subscribed, coin),
        )[:self.max_coins])
        if selected == self._coins:
            return
        for coin in self._coins - selected:
            self._quotes.pop(coin, None)
        self._coins = selected
        self._changed.set()

    @staticmethod
    def _level(level: object) -> tuple[float | None, float | None]:
        if level is None:
            return None, None
        if not isinstance(level, dict):
            raise TypeError("invalid bbo level")
        price, size = float(level["px"]), float(level["sz"])
        if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
            raise ValueError("invalid bbo price/size")
        return price, size

    def handle_message(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("channel") != "bbo":
                return
            data = message.get("data")
            if not isinstance(data, dict):
                return
            coin = data.get("coin")
            if not isinstance(coin, str) or coin not in self._coins:
                return
            # A malformed update for a known market must not leave its old book executable.
            self._quotes.pop(coin, None)
            levels = data.get("bbo")
            if not isinstance(levels, list) or len(levels) != 2:
                return
            timestamp = data.get("time")
            # Milliseconds in 2000-01-01..2100-01-01, safe on supported platforms.
            if type(timestamp) is not int or not 946_684_800_000 <= timestamp <= 4_102_444_800_000:
                return
            bid, bid_size = self._level(levels[0])
            ask, ask_size = self._level(levels[1])
            received = monotonic()
            self._last_message_at = received
            self._quotes[coin] = (bid, bid_size, ask, ask_size, timestamp, received)
            second = int(received)
            if self._message_counts and self._message_counts[-1][0] == second:
                self._message_counts[-1] = (second, self._message_counts[-1][1] + 1)
            else:
                self._message_counts.append((second, 1))
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            # Bad JSON or malformed payloads must never terminate the receive loop.
            return

    def get_fresh(self, coin: str) -> BboQuote | None:
        quote = self._quotes.get(coin)
        now = monotonic()
        if (quote is None or not self._connected
                or not 0 <= now - self._last_message_at <= self.max_age_seconds
                or not 0 <= now - quote[5] <= self.quote_max_age_seconds):
            return None
        bid, bid_size, ask, ask_size, _, _ = quote
        if (bid is None or ask is None or not 0 < bid < ask
                or bid_size is None or ask_size is None or bid_size <= 0 or ask_size <= 0):
            return None
        return quote

    def stats(self) -> dict[str, int | bool]:
        cutoff = int(monotonic()) - 300
        return {
            "hl_bbo": 1,
            "connected": self._connected,
            "subscribed": len(self._subscribed),  # Successfully sent on this connection.
            "fresh_count": sum(self.get_fresh(coin) is not None for coin in self._coins),
            "msgs_last_5m": sum(count for second, count in self._message_counts if second > cutoff),
            "reconnects": self._reconnects,
        }

    async def _log_stats(self) -> None:
        while True:
            await asyncio.sleep(self.stats_interval_seconds)
            logger.info(json.dumps(self.stats(), separators=(",", ":")))

    async def _receive(self, websocket) -> None:
        while True:
            self.handle_message(await websocket.recv())

    async def _send(self, websocket, message: dict) -> None:
        await asyncio.wait_for(websocket.send(json.dumps(message)), timeout=10)
        self._last_send = monotonic()

    async def _send_subscriptions(self, websocket) -> None:
        while True:
            self._changed.clear()
            if self._subscribed != self._coins:
                # Windows timers can wake early. Recheck the monotonic deadline
                # instead of assuming one sleep enforces the send-rate ceiling.
                while (delay := self.send_interval_seconds - (monotonic() - self._last_send)) > 0:
                    await asyncio.sleep(delay)
                # Recompute after yielding: a newer metadata cycle may have replaced the set.
                removed = self._subscribed - self._coins
                added = self._coins - self._subscribed
                if removed:
                    coin = min(removed)
                    await self._send(websocket, {
                        "method": "unsubscribe", "subscription": {"type": "bbo", "coin": coin},
                    })
                    self._subscribed.remove(coin)
                elif added:
                    coin = min(added)
                    await self._send(websocket, {
                        "method": "subscribe", "subscription": {"type": "bbo", "coin": coin},
                    })
                    self._subscribed.add(coin)
                continue
            timeout = max(0, self._last_send + self.heartbeat_seconds - monotonic())
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=timeout)
            except TimeoutError:
                await self._send(websocket, {"method": "ping"})

    async def _connections(self) -> None:
        failures = 0
        attempted = False
        while True:
            while not self._coins:
                self._changed.clear()
                await self._changed.wait()
            if attempted:
                self._reconnects += 1
            attempted = True
            connected_at = None
            try:
                async with websocket_connect(
                    self.ws_url, open_timeout=10, close_timeout=3,
                    ping_interval=20, ping_timeout=20,
                ) as websocket:
                    # Books from a previous connection may have missed updates.
                    self._quotes.clear()
                    self._last_message_at = float("-inf")
                    self._connected = True
                    connected_at = monotonic()
                    receiver = asyncio.create_task(self._receive(websocket))
                    sender = asyncio.create_task(self._send_subscriptions(websocket))
                    tasks = (receiver, sender)
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:  # noqa: BLE001, S110 - periodic stats expose socket failures.
                pass
            finally:
                self._connected = False
                self._subscribed.clear()
            # A short-lived handshake must not reset backoff and cause a reconnect storm.
            if connected_at is not None and monotonic() - connected_at >= 60:
                failures = 0
            delay = self.reconnect_delays[min(failures, len(self.reconnect_delays) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def run(self, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        tasks = (
            asyncio.create_task(self._connections()),
            asyncio.create_task(self._log_stats()),
            asyncio.create_task(stop_event.wait()),
        )
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
