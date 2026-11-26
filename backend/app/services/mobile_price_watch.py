"""Phone spot quotes, in-memory price history and durable alert transitions."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from math import isfinite
from uuid import UUID

from app.exchanges.base import utc_now
from app.models.mobile import (
    PriceWatchAlert, PriceWatchConfig, PriceWatchQuote, PriceWatchQuotes, PriceWatchUpdate,
)
from app.services.quote_age import quote_age_fields

logger = logging.getLogger(__name__)
EXCHANGES = {"binance": "币安", "okx": "OKX", "bybit": "Bybit", "bitget": "Bitget", "gate": "Gate"}
SAMPLE_INTERVAL = timedelta(seconds=10)
HOUR = timedelta(hours=1)


def _config(raw: str | None) -> PriceWatchConfig:
    if raw is None:
        return PriceWatchConfig(symbols=[], alerts=[])
    return PriceWatchConfig.model_validate_json(raw)


def _merge(current: PriceWatchConfig, update: PriceWatchUpdate, initially_triggered: set[UUID]) -> PriceWatchConfig:
    previous = {alert.id: alert for alert in current.alerts}
    alerts = []
    for item in update.alerts:
        old = previous.get(item.id)
        if old is None or old.condition() != item.condition():
            alert = PriceWatchAlert(**item.model_dump())
        else:
            alert = old.model_copy()
            alert.rearm = item.rearm
            # A trigger that wins a race with this POST owns the newer enabled=false.
            just_triggered = old.state == "triggered" and item.id not in initially_triggered
            if "enabled" in item.model_fields_set and not just_triggered:
                alert.enabled = item.enabled
                if item.enabled and old.state == "triggered":
                    alert.state = "armed"
        alerts.append(alert)
    return PriceWatchConfig(symbols=update.symbols, alerts=alerts)


class PriceHistory:
    def __init__(self):
        self.points: dict[str, deque[tuple[datetime, float]]] = {}

    def retain(self, symbols):
        self.points = {s: points for s, points in self.points.items() if s in symbols}

    def sample(self, quote: PriceWatchQuote, now: datetime):
        points = self.points.setdefault(quote.symbol, deque())
        # Keep one boundary sample for the hour lookup when ticks drift.
        cutoff = now - HOUR
        while len(points) >= 2 and points[1][0] <= cutoff:
            points.popleft()
        if points and points[0][0] < cutoff - SAMPLE_INTERVAL:
            points.popleft()
        if quote.price is not None and not quote.stale and (not points or now - points[-1][0] >= SAMPLE_INTERVAL):
            points.append((now, quote.price))

    def change(self, symbol, price, now, minutes):
        target = now - timedelta(minutes=minutes)
        for stamp, base in reversed(self.points.get(symbol, ())):
            if stamp <= target:
                if stamp < now - HOUR - SAMPLE_INTERVAL:
                    break
                # Decimal inputs preserve inclusive thresholds such as 100 -> 100.1.
                start = Decimal(str(base))
                result = float((Decimal(str(price)) - start) * 100 / start)
                return result if isfinite(result) else None
        return None


def _price_text(value):
    if value >= 1000:
        return f"{value:,.0f}"
    if value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{value:.6g}"


def _evaluate(alert, quote, now, history):
    price = quote.price
    if not alert.enabled or quote.stale or price is None:
        return None
    if alert.state == "rearm_wait":
        threshold = Decimal(str(alert.price))
        current = Decimal(str(price))
        if (alert.kind == "above" and current <= threshold * Decimal("0.999")) or (
            alert.kind == "below" and current >= threshold * Decimal("1.001")
        ):
            alert.state = "armed"
        return None
    if alert.state == "cooldown" and now >= alert.cooldown_until:
        alert.state = "armed"
        alert.cooldown_until = None
    if alert.state != "armed":
        return None

    if alert.kind == "change":
        change = history.change(alert.symbol, price, now, alert.window_minutes)
        if change is None or abs(change) < alert.change_pct:
            return None
        if (alert.direction == "up" and change < 0) or (alert.direction == "down" and change > 0):
            return None
        title = f"{alert.symbol} {alert.window_minutes} 分钟{'涨' if change > 0 else '跌'} {abs(change):.6g}%"
    else:
        if (alert.kind == "above" and price < alert.price) or (alert.kind == "below" and price > alert.price):
            return None
        title = f"{alert.symbol} {'上穿' if alert.kind == 'above' else '下穿'} {_price_text(alert.price)}"

    alert.last_triggered_at = now
    alert.last_trigger_price = price
    if not alert.rearm:
        alert.state = "triggered"
        alert.enabled = False
    elif alert.kind == "change":
        alert.state = "cooldown"
        alert.cooldown_until = now + timedelta(minutes=alert.window_minutes)
    else:
        alert.state = "rearm_wait"
    body = f"现价 {_price_text(price)} · {EXCHANGES[quote.exchange]}现货"
    return title, body


class MobilePriceWatch:
    def __init__(self, repo, store, load_risk, push, clock=utc_now):
        self.repo = repo
        self.store = store
        self.load_risk = load_risk
        self.push = push
        self.clock = clock
        self.history = PriceHistory()
        self.lock = asyncio.Lock()
        self._fire_revision = 0
        self._last_fire_revision: dict[UUID, int] = {}

    async def get(self):
        async with self.lock:
            return _config(await self.repo.read())

    async def save(self, update: PriceWatchUpdate):
        received_revision = self._fire_revision
        async with self.lock:
            raw = await self.repo.read()
            current = _config(raw)
            # A full-list save may have waited behind a firing tick on this connection.
            initially_triggered = {
                alert.id for alert in current.alerts
                if alert.state == "triggered" and self._last_fire_revision.get(alert.id, 0) <= received_revision
            }
            while True:
                result = _merge(current, update, initially_triggered)
                if await self.repo.replace(raw, result.model_dump_json(), []):
                    self.history.retain(result.symbols)
                    return result
                raw = await self.repo.read()
                current = _config(raw)

    def _quotes(self, symbols, now, stale_after):
        wanted = set(symbols)
        books = {}
        for market in self.store.get_all_markets():
            if market.base not in wanted or market.market_type != "spot" or market.exchange not in EXCHANGES:
                continue
            raw = market.raw_symbol.upper().replace("-", "").replace("_", "")
            if (market.quote != "USDT" or raw != market.base + "USDT"
                    or market.symbol_alias_price_multiplier != 1
                    or not 0 < market.bid <= market.ask or not isfinite(market.ask)):
                continue
            books[market.base, market.exchange] = market
        quotes = []
        for symbol in symbols:
            market = next((books[symbol, ex] for ex in EXCHANGES if (symbol, ex) in books), None)
            if market is None:
                quotes.append(PriceWatchQuote(symbol=symbol, error="暂无行情"))
                continue
            age = quote_age_fields(market.timestamp, None, now=now)["age_ms"] / 1000
            price = market.bid + (market.ask - market.bid) / 2
            quotes.append(PriceWatchQuote(
                symbol=symbol, exchange=market.exchange, market_type="spot", bid=market.bid,
                ask=market.ask, price=price, age_seconds=max(age, 0), stale=age > stale_after,
                change_5m_pct=self.history.change(symbol, price, now, 5),
                change_15m_pct=self.history.change(symbol, price, now, 15),
                change_1h_pct=self.history.change(symbol, price, now, 60),
            ))
        return quotes

    async def quotes(self, symbols=None):
        if symbols is None:
            symbols = (await self.get()).symbols
        risk = await self.load_risk()
        now = self.clock()
        return PriceWatchQuotes(observed_at=now, quotes=self._quotes(symbols, now, risk.stale_after_seconds))

    async def tick(self):
        async with self.lock:
            while True:
                raw = await self.repo.read()
                config = _config(raw)
                self.history.retain(config.symbols)
                if not config.symbols:
                    return
                risk = await self.load_risk()
                now = self.clock()
                quotes = {q.symbol: q for q in self._quotes(config.symbols, now, risk.stale_after_seconds)}
                for quote in quotes.values():
                    self.history.sample(quote, now)
                messages = []
                events = []
                for alert in config.alerts:
                    message = _evaluate(alert, quotes[alert.symbol], now, self.history)
                    if message:
                        messages.append((alert.id, *message))
                        title, body = message
                        events.append({"created_at": now.timestamp(), "title": title, "body": body, "symbol": alert.symbol})
                payload = config.model_dump_json()
                if payload == raw:
                    return
                if await self.repo.replace(raw, payload, events):
                    fired = {alert_id for alert_id, _, _ in messages}
                    self._fire_revision += 1
                    self._last_fire_revision = {
                        a.id: self._fire_revision if a.id in fired else self._last_fire_revision.get(a.id, 0)
                        for a in config.alerts if a.state == "triggered"
                    }
                    break
        for message in messages:
            try:
                await self.push.queue_price_watch(*message)
            except Exception:
                logger.warning("币价提醒未能加入推送队列")

    async def run(self, stop_event):
        loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            start = loop.time()
            try:
                await self.tick()
            except Exception:
                logger.exception("币价提醒检查失败")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(0, 5 - (loop.time() - start)))
            except TimeoutError:
                pass
