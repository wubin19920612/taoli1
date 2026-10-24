"""Parse public publisher pages without executing their embedded scripts."""

from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser


@dataclass(frozen=True)
class ChannelPost:
    post_id: str
    text: str
    published_at: datetime


class TelegramChannelParser(HTMLParser):
    def __init__(self, channel: str) -> None:
        super().__init__(convert_charrefs=True)
        self.channel = channel
        self.posts: list[ChannelPost] = []
        self._depth = 0
        self._message_depth: int | None = None
        self._text_depth: int | None = None
        self._post_id = ""
        self._parts: list[str] = []
        self._date: str | None = None
        self._media = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if tag == "div":
            self._depth += 1
            if "tgme_widget_message" in classes:
                prefix = self.channel + "/"
                identity = values.get("data-post") or ""
                post_id = identity.removeprefix(prefix)
                if identity.startswith(prefix) and post_id.isascii() and post_id.isdigit():
                    self._message_depth = self._depth
                    self._post_id = post_id
                    self._parts = []
                    self._date = None
                    self._media = False
            if self._message_depth is not None and "tgme_widget_message_text" in classes:
                self._text_depth = self._depth
        if self._message_depth is None:
            return
        if tag == "time":
            self._date = values.get("datetime")
        if any(c.startswith(("tgme_widget_message_photo", "tgme_widget_message_video",
                             "tgme_widget_message_document")) for c in classes):
            self._media = True
        if tag == "br" and self._text_depth is not None:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._depth == self._text_depth:
            self._text_depth = None
        if self._depth == self._message_depth:
            self._finish_post()
            self._message_depth = None
            self._text_depth = None
        self._depth = max(0, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._text_depth is not None:
            self._parts.append(data)

    def _finish_post(self) -> None:
        try:
            date = datetime.fromisoformat(self._date or "")
        except ValueError:
            return
        if date.tzinfo is None:
            return
        text = "\n".join(" ".join(line.split()) for line in "".join(self._parts).splitlines()).strip()
        if not text and self._media:
            text = "Hyperliquid 官方媒体公告（请查看原文）"
        if text:
            self.posts.append(ChannelPost(self._post_id, text, date))
