"""Detect notification edges on the same card and quote publication used by the phone."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4
from weakref import WeakValueDictionary

from app.models.mobile import MobileCard
from app.services.data_filters import normalize_symbol
from app.services.fcm import PushMessage, SendResult

logger = logging.getLogger(__name__)
SCAN_INTERVAL = 10
QUEUE_LIMIT = 200
FEISHU_SCAN_LIMIT = 50
HANDLED_TOKEN_LIMIT = 500
SEND_CONCURRENCY = 4


@dataclass
class StaleAlarm:
    sent: bool = False
    recovered_device: dict | None = None


@dataclass
class BigSpreadPush:
    message: PushMessage


@dataclass
class FeishuPush:
    message: PushMessage
    handled_tokens: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PendingPush:
    device: dict
    notification: PushMessage | BigSpreadPush
    stale_alarm: StaleAlarm | None = None
    from_feishu: bool = False


def _can_drop_feishu(notification: PushMessage | BigSpreadPush) -> bool:
    return (
        isinstance(notification, PushMessage)
        and notification.channel != "urgent"
        and notification.kind not in {"big_spread", "leg_alert", "stale"}
    )


class PushQueue(asyncio.Queue[PendingPush]):
    def replace_oldest_feishu(self, pending: PendingPush) -> bool:
        for index, queued in enumerate(self._queue):
            if queued.from_feishu and _can_drop_feishu(queued.notification):
                # Exchanging one queued item keeps the unfinished task count unchanged.
                del self._queue[index]
                self._queue.append(pending)
                return True
        return False


@dataclass(frozen=True, slots=True)
class CardSnapshot:
    open_gap: float | None
    close_gap: float | None
    qty_a: float
    qty_b: float
    state: str
    imbalance: bool

    @classmethod
    def read(cls, card: MobileCard):
        return cls(
            card.gap_to_open
            if not card.holding and card.open_auto and card.state != "paused"
            else None,
            card.gap_to_close if card.holding and card.close_auto else None,
            card.qty_a,
            card.qty_b,
            card.state,
            card.leg_imbalance,
        )


def _near(gap, threshold):
    return gap is not None and -threshold <= gap < 0


def _quantities(card):
    def leg(exchange, dex, raw, qty):
        market = " ".join(filter(None, (exchange, dex, raw))) if dex else exchange
        return f"{market} {qty:g}"

    buy = leg(card.buy_ex, card.dex_a, card.raw_symbol_a, card.qty_a)
    sell = leg(card.sell_ex, card.dex_b, card.raw_symbol_b, card.qty_b)
    return f"{buy} / {sell}"


def _events(card, before, after, threshold, kinds):
    def message(kind, title, body, channel="normal"):
        return PushMessage(kind, f"{card.name} {title}", body, card.id, card.name, channel)

    for kind, label, gap, last, spread, condition in (
        (
            "near_open",
            "开仓",
            after.open_gap,
            before.open_gap,
            card.open_spread_now,
            card.open_condition,
        ),
        (
            "near_close",
            "清仓",
            after.close_gap,
            before.close_gap,
            card.close_spread_now,
            card.close_condition,
        ),
    ):
        if kinds[kind] and _near(gap, threshold) and not _near(last, threshold):
            yield message(
                kind,
                f"接近{label}",
                f"{label}价差 {spread:.2f}%，条件 {condition:.2f}%，差 {abs(gap):.2f}pp",
            )

    delta_a, delta_b = after.qty_a - before.qty_a, after.qty_b - before.qty_b
    held_before = bool(before.qty_a or before.qty_b)
    if kinds["fill"] and (delta_a or delta_b):
        if not held_before:
            direction = "开仓"
        elif not card.holding:
            direction = "平仓"
        elif min(delta_a, delta_b) >= 0:
            direction = "加仓"
        elif max(delta_a, delta_b) <= 0:
            direction = "减仓"
        else:
            direction = "两腿调仓"
        yield message("fill", f"{direction}成交 {delta_a:+g} / {delta_b:+g}", _quantities(card))

    if kinds["leg_alert"] and card.holding:
        reasons = []
        if after.imbalance and not before.imbalance:
            reasons.append("持仓失衡" if after.qty_a and after.qty_b else "单腿持仓")
        if after.state in {"paused", "close_disabled"} and (
            before.state != after.state or not held_before
        ):
            reasons.append("持仓已暂停" if after.state == "paused" else "持仓已禁平")
        if reasons:
            yield message("leg_alert", "、".join(reasons), _quantities(card), "urgent")


class MobilePushService:
    def __init__(self, repo, fcm, load_summary, clock=monotonic):
        self.repo = repo
        self.fcm = fcm
        self.load_summary = load_summary
        self.clock = clock
        self.previous = None
        self.cooldowns = {}
        self.test_after = {}
        self.stale_since = None
        self.stale_announced = False
        self.stale_recipients = {}
        self.big_spreads: dict[str, BigSpreadPush] = {}
        self._pending_spreads: WeakValueDictionary[str, BigSpreadPush] = WeakValueDictionary()
        self._feishu_messages: dict[str, FeishuPush] = {}
        self._queue = PushQueue(maxsize=QUEUE_LIMIT)
        self._send_slots = asyncio.Semaphore(SEND_CONCURRENCY)

    def queue_big_spread(self, title, body, symbol):
        if not self.fcm.enabled or self.previous is None:
            return
        route = re.search(r"^(.+) 买 \S+ → (.+) 卖 \S+$", body, re.MULTILINE)
        title_symbol = re.match(r"^【大价差】\s*(\S+)", title)
        if title_symbol:
            identity = normalize_symbol(title_symbol[1])
        else:
            title_parts = title.split(maxsplit=1)
            identity = normalize_symbol(symbol.strip() or (title_parts[0] if title_parts else ""))
            if symbol:
                identity = identity.removesuffix("USDT") or identity
        if route:
            # The rendered legs include Hyperliquid DEX and raw market names.
            legs = [" ".join(leg.split()) for leg in route.groups()]
            identity = f"{identity}:{legs[0]}→{legs[1]}"

        # Feishu's phone record has no symbol and keeps only the headline,
        # route and card line from the full big-spread notification.
        lines = body.splitlines()
        urgent = not symbol and title_symbol and route and len(lines) == 3 and (
            lines[-1].startswith("卡片：")
        )
        message = PushMessage(
            "big_spread", title, body[:720], channel="urgent" if urgent else "normal",
            identity=identity,
        )
        pending = self._pending_spreads.get(message.tag)
        if pending is not None:
            # All queued recipients share the same record until they start sending.
            if urgent:
                pending.message = message
            return
        if len(self.big_spreads) == QUEUE_LIMIT:
            del self.big_spreads[next(iter(self.big_spreads))]
            logger.warning("手机推送队列已满，丢弃最旧的大价差提醒")
        pending = BigSpreadPush(message)
        self.big_spreads[message.tag] = pending
        self._pending_spreads[message.tag] = pending

    def queue_feishu(self, kind: str, title: str, body: str, urgent: bool) -> None:
        if not self.fcm.enabled:
            return
        # The news poller deduplicates by article number. Distinct articles
        # can share the same (truncated) headline within the device cooldown.
        identity = uuid4().hex if kind == "eqn_news" else title
        message = PushMessage(
            kind, title, body[:720], channel="urgent" if urgent else "normal", identity=identity,
        )
        pending = self._feishu_messages.get(message.tag)
        if pending is not None:
            if urgent:
                pending.message = message
            return
        if len(self._feishu_messages) == QUEUE_LIMIT:
            oldest = next(
                (tag for tag, push in self._feishu_messages.items() if _can_drop_feishu(push.message)),
                None,
            )
            if oldest is None:
                logger.warning("手机推送队列已满，丢弃新飞书提醒")
                return
            del self._feishu_messages[oldest]
            logger.warning("手机推送队列已满，丢弃最旧的飞书提醒")
        self._feishu_messages[message.tag] = FeishuPush(message)

    def _scan_feishu(self, devices, now):
        active_tokens = {device["token_hash"] for device in devices}
        remaining = FEISHU_SCAN_LIMIT
        while self._feishu_messages and remaining:
            tag = next(iter(self._feishu_messages))
            pending = self._feishu_messages[tag]
            message = pending.message
            pending.handled_tokens.intersection_update(active_tokens)
            for device in devices:
                token_hash = device["token_hash"]
                if (
                    token_hash in pending.handled_tokens
                    or not device["kinds"].get(message.kind, message.kind == "eqn_news")
                    or (token_hash, tag) in self.cooldowns
                ):
                    continue
                if len(pending.handled_tokens) >= HANDLED_TOKEN_LIMIT:
                    logger.warning("手机推送单条飞书提醒已达设备上限，丢弃剩余投递")
                    break
                if not remaining:
                    return
                self._notify(device, message, now, from_feishu=True)
                pending.handled_tokens.add(token_hash)
                remaining -= 1
            del self._feishu_messages[tag]

    async def _send(self, token, notification, device=None):
        async with self._send_slots:
            message = notification.message if isinstance(notification, BigSpreadPush) else notification
            result = await self.fcm.send(token, message)
        if device is not None and (not result.sent or device["last_error"]):
            await self.repo.record_result(
                device, None if result.sent else result.reason, result.invalid
            )
        return result

    async def test(self, token, session_hash):
        now = self.clock()
        if now < self.test_after.get(token, 0):
            return SendResult(False, "每个设备每分钟最多测试一次")
        self.test_after[token] = now + 60
        device = await self.repo.get(token, session_hash)
        message = PushMessage("test", "手机推送测试", "这是一条普通测试通知", identity="device")
        return await self._send(token, message, device)

    async def send_urgent(self, *, kind, title, body, card_id, card_name):
        if not self.fcm.enabled:
            return
        message = PushMessage(kind, title, body, card_id, card_name, channel="urgent")
        devices = await self.repo.scan()
        results = await asyncio.gather(
            *(self._send(device["token"], message, device) for device in devices), return_exceptions=True,
        )
        if any(isinstance(result, Exception) for result in results):
            logger.warning("部分设备的紧急推送发送失败")

    def _enqueue(self, pending: PendingPush) -> bool:
        if self._queue.full():
            if pending.from_feishu:
                if self._queue.replace_oldest_feishu(pending):
                    logger.warning("手机推送发送队列已满，丢弃最旧的飞书提醒")
                    return True
                logger.warning("手机推送发送队列已满，丢弃新飞书提醒")
                return False
            self._queue.get_nowait()
            self._queue.task_done()
            logger.warning("手机推送队列已满，丢弃最旧的待发送通知")
        self._queue.put_nowait(pending)
        return True

    def _notify(self, device, notification, now, stale_alarm=None, *, from_feishu=False):
        message = notification.message if isinstance(notification, BigSpreadPush) else notification
        key = (device["token_hash"], message.tag)
        if key not in self.cooldowns and self._enqueue(
            PendingPush(device, notification, stale_alarm, from_feishu=from_feishu)
        ):
            self.cooldowns[key] = now + 600
            return True
        return False

    def _queue_recovery(self, device):
        message = PushMessage("stale", "行情数据已恢复", "手机概览数据已更新", identity="summary")
        self._enqueue(PendingPush(device, message))

    async def _send_queued(self):
        while True:
            pending = await self._queue.get()
            try:
                device = pending.device
                # A pending notification can outlive a registration or preference change.
                current = await self.repo.get(device["token"], device["session_hash"])
                if current is None or current["updated_at"] != device["updated_at"]:
                    continue
                result = await self._send(device["token"], pending.notification, current)
                if pending.stale_alarm is not None and result.sent:
                    pending.stale_alarm.sent = True
                    if pending.stale_alarm.recovered_device is not None:
                        self._queue_recovery(pending.stale_alarm.recovered_device)
            except Exception:  # noqa: BLE001 - never log device details or raw provider errors.
                logger.warning("手机推送发送任务失败")
            finally:
                self._queue.task_done()
                # Drop the sender's references before blocking on the empty queue.
                pending = None

    async def scan(self):
        now = self.clock()
        self.test_after = {k: t for k, t in self.test_after.items() if t > now}
        self.cooldowns = {k: t for k, t in self.cooldowns.items() if t > now}
        devices = await self.repo.scan()
        if not devices or not self.fcm.enabled:
            self.previous = None
            self.big_spreads.clear()
            self._pending_spreads.clear()
            self._feishu_messages.clear()
            self.stale_since = None
            self.stale_announced = False
            self.stale_recipients.clear()
            return

        self._scan_feishu(devices, now)

        summary = await self.load_summary()
        cards = {card.id: card for card in summary.cards if card.id}
        before = self.previous
        self.previous = {key: CardSnapshot.read(card) for key, card in cards.items()}
        if summary.stale and self.stale_since is None:
            self.stale_since = now
        if before is None:
            return
        stale_edge = summary.stale and not self.stale_announced and now - self.stale_since >= 60
        recovered = not summary.stale and self.stale_announced
        if not summary.stale:
            self.stale_since = None
        self.stale_announced = (self.stale_announced or stale_edge) and summary.stale
        spreads, self.big_spreads = self.big_spreads.values(), {}
        for device in devices:
            kinds = device["kinds"]
            for key, card in cards.items():
                if key in before:
                    for msg in _events(
                        card, before[key], self.previous[key], device["threshold_pp"], kinds
                    ):
                        self._notify(device, msg, now)
            if kinds["big_spread"]:
                for spread in spreads:
                    self._notify(device, spread, now)
            recipient = (device["token_hash"], device["session_hash"])
            if kinds["stale"] and stale_edge:
                msg = PushMessage(
                    "stale", "行情数据断流", "手机概览数据已连续过期至少 60 秒",
                    channel="urgent", identity="summary",
                )
                alarm = StaleAlarm()
                if self._notify(device, msg, now, stale_alarm=alarm):
                    self.stale_recipients[recipient] = alarm
            if kinds["stale"] and recovered and recipient in self.stale_recipients:
                # Pair the alarm with its recovery even within the alarm cooldown.
                alarm = self.stale_recipients[recipient]
                if alarm.sent:
                    self._queue_recovery(device)
                else:
                    alarm.recovered_device = device
        if recovered:
            self.stale_recipients.clear()

    async def run(self, stop_event):
        senders = [asyncio.create_task(self._send_queued()) for _ in range(SEND_CONCURRENCY)]
        try:
            while not stop_event.is_set():
                start = self.clock()
                try:
                    await self.scan()
                except Exception:  # noqa: BLE001 - isolate scan errors without logging device details.
                    logger.warning("手机推送扫描失败")
                elapsed = self.clock() - start
                if elapsed > 1:
                    logger.warning("手机推送扫描耗时 %.2f 秒", elapsed)
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=max(0, SCAN_INTERVAL - elapsed)
                    )
                except TimeoutError:
                    pass
        finally:
            for sender in senders:
                sender.cancel()
            await asyncio.gather(*senders, return_exceptions=True)
