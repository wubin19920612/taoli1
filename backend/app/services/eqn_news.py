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
    """Deliver at most once. Enqueue a batch, then advance the persisted cursors.

    Delivery waits for the cursor claim to succeed. A crash after the claim can
    lose queued or in-flight news. Overflow drops the oldest queued articles.
    """

    def __init__(self, repo: SettingsRepository, notifier: FeishuNotifier):
        self.repo = repo
        self.notifier = notifier
        self.etag: str | None = None
        self._pending: asyncio.Queue[tuple[str, asyncio.Future[bool]]] = asyncio.Queue(maxsize=200)
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
            text, claimed = await self._pending.get()
            if self._pending.empty():
                self._overflow_warned = False
            try:
                if await asyncio.shield(claimed):
                    await self._deliver(text)
            except Exception as exc:
                logger.warning("方程式新闻投递失败 (%s)", type(exc).__name__)
            finally:
                self._pending.task_done()

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
            claimed = asyncio.get_running_loop().create_future()
            dropped = 0
            for text in messages[-SEND_LIMIT:]:
                if self._pending.full():
                    self._pending.get_nowait()
                    self._pending.task_done()
                    dropped += 1
                self._pending.put_nowait((text, claimed))
            if dropped and not self._overflow_warned:
                logger.warning("方程式新闻投递队列已满，丢弃最旧的 %s 条", dropped)
                self._overflow_warned = True
            accepted = False
            try:
                accepted = await self.repo.replace_eqn_news_cursors(cursors, updated)
            finally:
                claimed.set_result(accepted)
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
