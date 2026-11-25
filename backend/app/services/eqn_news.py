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
    def __init__(self, repo: SettingsRepository, notifier: FeishuNotifier):
        self.repo = repo
        self.notifier = notifier
        self.etag: str | None = None

    async def _deliver(self, text: str) -> None:
        if self.notifier.config.webhook_url:
            try:
                await self.notifier.send_text(text)
                return  # send_text records and queues a successful send.
            except Exception as exc:
                logger.warning("方程式新闻飞书发送失败 (%s)，继续手机推送", type(exc).__name__)
        await self.notifier.record_mobile_text(text)

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
            return None
        if response.status_code == 429:
            delay = _retry_delay(response.headers.get("Retry-After", ""))
            logger.warning("方程式新闻限流，%s 秒后重试", delay)
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
                messages.append((no, f"【方程式·{EQN_NEWS_CHANNELS[key]}】{zh[:500]}\n{url}"))

        if len(messages) > SEND_LIMIT:
            logger.warning("方程式新闻单轮超过 %s 条，丢弃较早的 %s 条", SEND_LIMIT, len(messages) - SEND_LIMIT)
        # Claim each article before external delivery. If interrupted, a restart
        # can continue with the next article without replaying a sent message.
        for no, text in messages[-SEND_LIMIT:]:
            updated = {key: latest if last is None else max(no, last) for key, last in cursors.items()}
            if not await self.repo.replace_eqn_news_cursors(cursors, updated):
                return None
            cursors = updated
            await self._deliver(text)

        updated = {key: max(latest, last or 0) for key, last in cursors.items()}
        if updated != cursors and not await self.repo.replace_eqn_news_cursors(cursors, updated):
            return None
        self.etag = response.headers.get("etag")
        return None

    async def run(self, stop_event: asyncio.Event) -> None:
        async with httpx.AsyncClient(timeout=8) as client:
            while not stop_event.is_set():
                started = monotonic()
                backoff = None
                try:
                    backoff = await self.poll_once(client)
                except Exception as exc:
                    logger.warning("方程式新闻轮询失败 (%s)", type(exc).__name__)
                delay = backoff if backoff is not None else max(0, started + POLL_SECONDS - monotonic())
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
