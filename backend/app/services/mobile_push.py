"""Detect notification edges on the same card and quote publication used by the phone."""

import asyncio
import logging
import re
from dataclasses import dataclass
from time import monotonic

from app.models.mobile import MobileCard
from app.services.fcm import PushMessage, SendResult

logger = logging.getLogger(__name__)


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
        self.stale_recipients = set()
        self.big_spreads = {}

    def queue_big_spread(self, title, body, symbol):
        if self.fcm.enabled and self.previous is not None:
            # The journal has the opportunity's symbol and rendered legs, but no
            # Astro card ID. Preserve distinct venues/DEXes even for the same symbol.
            route = re.search(r"^(.+) 买 \S+ → (.+) 卖 \S+$", body, re.MULTILINE)
            identity = (symbol or title, route.groups() if route else ())
            self.big_spreads.setdefault(identity, PushMessage("big_spread", title, body[:720]))

    async def _send(self, device, message):
        result = await self.fcm.send(device["token"], message)
        if not result.sent or device["last_error"]:
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
        message = PushMessage("test", "手机推送测试", "这是一条普通测试通知")
        if device is None:
            return await self.fcm.send(token, message)
        return await self._send(device, message)

    async def _notify(self, device, message, now, identity=None):
        key = (device["token"], message.kind, identity or message.card_id)
        if device.get("retired") or key in self.cooldowns:
            return False
        self.cooldowns[key] = now + 600
        result = await self._send(device, message)
        device["retired"] = result.invalid
        return result.sent

    async def scan(self):
        now = self.clock()
        self.test_after = {k: t for k, t in self.test_after.items() if t > now}
        self.cooldowns = {k: t for k, t in self.cooldowns.items() if t > now}
        devices = await self.repo.scan()
        if not devices or not self.fcm.enabled:
            self.previous = None
            self.big_spreads.clear()
            self.stale_since = None
            self.stale_announced = False
            self.stale_recipients.clear()
            return

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
        spreads, self.big_spreads = self.big_spreads, {}
        for device in devices:
            kinds = device["kinds"]
            for key, card in cards.items():
                if key in before:
                    for msg in _events(
                        card, before[key], self.previous[key], device["threshold_pp"], kinds
                    ):
                        await self._notify(device, msg, now)
            if kinds["big_spread"]:
                for identity, msg in spreads.items():
                    await self._notify(device, msg, now, identity=identity)
            recipient = (device["token"], device["session_hash"])
            if kinds["stale"] and stale_edge:
                msg = PushMessage(
                    "stale", "行情数据断流", "手机概览数据已连续过期至少 60 秒", channel="urgent"
                )
                if await self._notify(device, msg, now):
                    self.stale_recipients.add(recipient)
            if (
                kinds["stale"]
                and recovered
                and recipient in self.stale_recipients
                and not device.get("retired")
            ):
                # Pair the alarm with its recovery even within the alarm cooldown.
                await self._send(
                    device, PushMessage("stale", "行情数据已恢复", "手机概览数据已更新")
                )
        if recovered:
            self.stale_recipients.clear()

    async def run(self, stop_event):
        while not stop_event.is_set():
            start = self.clock()
            try:
                await self.scan()
            except Exception:  # noqa: BLE001 - isolate scan errors and keep sensitive inputs out of logs.
                logger.warning("手机推送扫描失败")
            elapsed = self.clock() - start
            if elapsed > 1:
                logger.warning("手机推送扫描耗时 %.2f 秒", elapsed)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(0, 10 - elapsed))
            except TimeoutError:
                pass
