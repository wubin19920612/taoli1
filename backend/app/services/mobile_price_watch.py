"""Phone spot quotes, in-memory price history and durable alert transitions."""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from math import isfinite

from app.exchanges.base import utc_now
from app.models.mobile import (
    PriceWatchAlert, PriceWatchConfig, PriceWatchQuote, PriceWatchQuotes, PriceWatchUpdate,
)
from app.services.quote_age import quote_age_fields

logger = logging.getLogger(__name__)
EXCHANGES = {"binance": "币安", "okx": "OKX", "bybit": "Bybit", "bitget": "Bitget", "gate": "Gate"}
SAMPLE_INTERVAL = timedelta(seconds=10)
WINDOW_TOLERANCE = timedelta(seconds=30)
HOUR = timedelta(hours=1)


class PriceWatchRevisionConflict(Exception):
    """The client edited a configuration that has since changed."""


def _config(raw: str | None) -> PriceWatchConfig:
    if raw is None:
        return PriceWatchConfig(symbols=[], alerts=[])
    return PriceWatchConfig.model_validate_json(raw)


def _merge(current: PriceWatchConfig, update: PriceWatchUpdate) -> PriceWatchConfig:
    previous = {alert.id: alert for alert in current.alerts}
    alerts = []
    for item in update.alerts:
        old = previous.get(item.id)
        if old is None or old.condition() != item.condition():
            alert = PriceWatchAlert(**item.model_dump())
        else:
            alert = old.model_copy()
            alert.rearm = item.rearm
            if "enabled" in item.model_fields_set:
                alert.enabled = item.enabled
                if item.enabled and not old.enabled and old.state == "triggered":
                    alert.state = "armed"
        alerts.append(alert)
    return PriceWatchConfig(symbols=update.symbols, alerts=alerts, revision=current.revision + 1)


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
        if points and points[0][0] < cutoff - WINDOW_TOLERANCE:
            points.popleft()
        if quote.price is not None and not quote.stale and (not points or now - points[-1][0] >= SAMPLE_INTERVAL):
            points.append((now, quote.price))

    def change(self, symbol, price, now, minutes):
        target = now - timedelta(minutes=minutes)
        for stamp, base in reversed(self.points.get(symbol, ())):
            if stamp <= target:
                if stamp < target - WINDOW_TOLERANCE:
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
    if alert.state == "cooldown" and now >= alert.cooldown_until:
        alert.state = "armed"
        alert.cooldown_until = None
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

    async def get(self):
        async with self.lock:
            return _config(await self.repo.read())

    async def save(self, update: PriceWatchUpdate):
        async with self.lock:
            raw = await self.repo.read()
            current = _config(raw)
            if current.revision != update.revision:
                raise PriceWatchRevisionConflict
            result = _merge(current, update)
            async with self.repo.replacing(raw, result.model_dump_json(), []) as changed:
                if not changed:
                    raise PriceWatchRevisionConflict
            self.history.retain(result.symbols)
            return result

    def _quotes(self, symbols, now, stale_after):
        books = {}
        for market in self.store.spot_markets_for(symbols):
            if market.exchange not in EXCHANGES:
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
                state_changed = False
                for alert in config.alerts:
                    previous_state = alert.state
                    message = _evaluate(alert, quotes[alert.symbol], now, self.history)
                    if alert.state != previous_state:
                        state_changed = True
                    if message:
                        messages.append((alert.id, *message))
                        title, body = message
                        events.append({"created_at": now.timestamp(), "title": title, "body": body, "symbol": alert.symbol})
                if not state_changed and not messages:
                    return
                config.revision += 1
                devices = await self.push.price_watch_devices() if messages else []
                committed = asyncio.get_running_loop().create_future()
                try:
                    async with self.repo.replacing(raw, config.model_dump_json(), events) as changed:
                        if not changed:
                            continue
                        if messages:
                            self.push.queue_price_watch(devices, messages, committed)
                    committed.set_result(True)
                    return
                except asyncio.QueueFull:
                    logger.warning("币价提醒推送队列已满，下轮重试")
                    return
                finally:
                    if not committed.done():
                        committed.set_result(False)

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
