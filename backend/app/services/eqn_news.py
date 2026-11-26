"""Poll subscribed Equation News channels and deliver previously unseen articles."""

import asyncio
import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import monotonic

import httpx

from app.db.repositories import SettingsRepository
from app.models.mobile import EQN_NEWS_CHANNELS
from app.services.announcement_eqn import CHANNELS as ANNOUNCEMENT_CHANNELS
from app.services.feishu import FeishuNotifier

logger = logging.getLogger(__name__)
NEWS_URL = "https://api.eqn.news/public/v1/news"
POLL_SECONDS = 10
SEND_LIMIT = 20
DELIVERY_TIMEOUT_SECONDS = 10
DRAIN_TIMEOUT_SECONDS = 5
FAILURE_REPORT_SECONDS = 600


def _retry_delay(header: str) -> float:
    try:
        seconds = int(header)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return POLL_SECONDS
    return max(POLL_SECONDS, seconds)


def _articles(payload) -> dict[int, dict]:
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
    if isinstance(payload, dict):
        payload = payload.get("items")
    if not isinstance(payload, list):
        raise ValueError("Missing eqn.news items")
    articles = {}
    for item in payload:
        if isinstance(item, dict) and type(item.get("no")) is int and item["no"] > 0:
            articles.setdefault(item["no"], item)
    return articles


class EqnNewsMonitor:
    """Deliver at most once. Claim the batch before making it available for delivery.

    A crash after the claim can lose queued or in-flight news. Overflow only
    drops older articles from the incoming batch when there is room to claim it.
    """

    def __init__(self, repo: SettingsRepository, notifier: FeishuNotifier):
        self.repo = repo
        self.notifier = notifier
        self.etag: str | None = None
        self._pending: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._claim_lock = asyncio.Lock()
        self._overflow_warned = False
        self._failures: dict[str, tuple[float, int]] = {}

    def _fetch_failed(self, reason: str) -> None:
        now = monotonic()
        previous = self._failures.get(reason)
        count = previous[1] + 1 if previous else 1
        if previous is None or now - previous[0] >= FAILURE_REPORT_SECONDS:
            logger.warning("方程式新闻轮询失败 (%s)，本期失败 %s 次", reason, count)
            self._failures[reason] = (now, 0)
        else:
            self._failures[reason] = (previous[0], count)

    def _fetch_recovered(self) -> None:
        if self._failures:
            logger.info("方程式新闻抓取恢复")
            self._failures.clear()

    async def _deliver(self, text: str) -> None:
        if self.notifier.config.webhook_url:
            try:
                async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):
                    await self.notifier.send_text(text, record_mobile=False)
            except Exception as exc:
                logger.warning("方程式新闻飞书发送失败 (%s)，继续手机推送", type(exc).__name__)
        await self.notifier.record_mobile_text(text)

    async def _deliver_pending(self) -> None:
        while True:
            text = await self._pending.get()
            if self._pending.qsize() * 2 < self._pending.maxsize:
                self._overflow_warned = False
            try:
                await self._deliver(text)
            except Exception as exc:
                logger.warning("方程式新闻投递失败 (%s)", type(exc).__name__)
            finally:
                self._pending.task_done()

    async def _claim_batch(
        self, cursors: dict[str, int | None], updated: dict[str, int], messages: list[str],
    ) -> bool:
        async with self._claim_lock:
            if messages and self._pending.full():
                if not self._overflow_warned:
                    logger.warning("方程式新闻投递队列已满，本轮暂缓提交 %s 条", len(messages))
                    self._overflow_warned = True
                return False
            if not await self.repo.replace_eqn_news_cursors(cursors, updated):
                return False
            free = self._pending.maxsize - self._pending.qsize()
            dropped = max(0, len(messages) - free)
            for text in messages[dropped:]:
                self._pending.put_nowait(text)
            if dropped and not self._overflow_warned:
                logger.warning("方程式新闻投递队列已满，丢弃新批次中最旧的 %s 条", dropped)
                self._overflow_warned = True
            return True

    async def poll_once(self, client: httpx.AsyncClient) -> float | None:
        """Return the required backoff after a rate-limited response."""
        cursors = await self.repo.get_eqn_news_cursors()
        if not cursors:
            self.etag = None
            return None
        headers = {"User-Agent": "taoli1/1.0"}
        if self.etag and None not in cursors.values():
            headers["If-None-Match"] = self.etag
        response = await client.get(NEWS_URL, params={"channel": "all", "limit": 50}, headers=headers)
        if response.status_code == 304:
            self._fetch_recovered()
            return None
        if response.status_code == 429:
            delay = _retry_delay(response.headers.get("Retry-After", ""))
            self._fetch_failed("HTTP 429")
            return delay
        response.raise_for_status()
        articles = _articles(response.json())
        latest = max(articles, default=0)
        messages = []
        for no, item in sorted(articles.items()):
            channels = item.get("channels")
            if not isinstance(channels, list) or any(key in channels for key in ANNOUNCEMENT_CHANNELS):
                continue
            key = next((key for key, last in cursors.items()
                        if last is not None and no > last and key in channels), None)
            zh = item.get("zh")
            if key and isinstance(zh, str) and zh.strip():
                url = item.get("source_url") or item.get("url") or ""
                messages.append(f"【方程式·{EQN_NEWS_CHANNELS[key]}】{zh[:500]}\n{url}")

        if len(messages) > SEND_LIMIT:
            logger.warning("方程式新闻单轮超过 %s 条，丢弃较早的 %s 条", SEND_LIMIT, len(messages) - SEND_LIMIT)
        updated = {key: max(latest, last or 0) for key, last in cursors.items()}
        if updated != cursors:
            # Complete the cursor claim and enqueue together if shutdown interrupts polling.
            claim = asyncio.create_task(self._claim_batch(cursors, updated, messages[-SEND_LIMIT:]))
            try:
                accepted = await asyncio.shield(claim)
            except asyncio.CancelledError:
                await claim
                raise
            if not accepted:
                return None
        self.etag = response.headers.get("etag")
        self._fetch_recovered()
        return None

    async def run(self, stop_event: asyncio.Event) -> None:
        async with asyncio.TaskGroup() as group:
            delivery = group.create_task(self._deliver_pending())
            try:
                await self._poll(stop_event)
            finally:
                try:
                    async with asyncio.timeout(DRAIN_TIMEOUT_SECONDS):
                        await self._pending.join()
                except TimeoutError:
                    logger.warning("方程式新闻投递超过关闭期限，取消未完成投递")
                finally:
                    delivery.cancel()

    async def _poll(self, stop_event: asyncio.Event) -> None:
        async with httpx.AsyncClient(timeout=8) as client:
            while not stop_event.is_set():
                started = monotonic()
                backoff = None
                try:
                    backoff = await self.poll_once(client)
                except httpx.HTTPStatusError as exc:
                    self._fetch_failed(f"HTTP {exc.response.status_code}")
                except Exception as exc:
                    self._fetch_failed(type(exc).__name__)
                delay = backoff if backoff is not None else max(0, started + POLL_SECONDS - monotonic())
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
