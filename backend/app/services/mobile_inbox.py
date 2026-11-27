import asyncio
import json
import logging
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time_ns
from uuid import UUID, uuid4

import aiosqlite
from aiohttp import BodyPartReader, MultipartReader
from aiohttp.helpers import parse_mimetype
from aiohttp.http_exceptions import BadHttpMessage
from multidict import CIMultiDict
from pydantic import ValidationError

from app.models.mobile_inbox import (
    FORM_LIMITS,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    InboxItem,
    InboxStatus,
    InboxUpload,
)

IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
CHUNK_SIZE = 64 * 1024
MAX_BODY_BYTES = MAX_IMAGE_BYTES * MAX_IMAGES + 256 * 1024
INVALID_FORM = "收件箱参数无效，请检查分享编号、内容和图片数量"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
QUOTA = 120
QUOTA_WINDOW_US = 3600 * 1_000_000
ORPHAN_AGE_NS = 600 * 1_000_000_000
logger = logging.getLogger(__name__)


class InboxError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


class _MultipartStream:
    def __init__(self, body: AsyncIterator[bytes]):
        self.body = body
        self.pending = memoryview(b"")
        self.buffer = bytearray()
        self.finished = False
        self.received = 0
        self.header_budget = 0

    async def _fill(self, size: int) -> None:
        while not self.pending and not self.finished:
            chunk = await anext(self.body, None)
            if chunk is None:
                self.finished = True
                return
            self.received += len(chunk)
            if self.received > MAX_BODY_BYTES:
                raise InboxError(413, "分享内容过大")
            self.pending = memoryview(chunk)
        self.buffer.extend(self.pending[:size])
        self.pending = self.pending[size:]

    async def read(self, size: int) -> bytes:
        while len(self.buffer) < size and not self.finished:
            await self._fill(size - len(self.buffer))
        return self._take(size)

    async def readline(self, max_line_length: int = 8192) -> bytes:
        limit = min(self.header_budget, max_line_length)
        while True:
            end = self.buffer.find(b"\n") + 1
            if end > limit or (not end and len(self.buffer) > limit):
                raise InboxError(422, INVALID_FORM)
            if end or self.finished:
                size = end or len(self.buffer)
                self.header_budget -= size
                return self._take(size)
            await self._fill(limit + 1 - len(self.buffer))

    def _take(self, size: int) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def unread_data(self, data: bytes) -> None:
        self.buffer[:0] = data

    def at_eof(self) -> bool:
        return self.finished and not self.buffer


async def _peek_client_id(stream: _MultipartStream, boundary: str) -> UUID:
    line = await stream.readline(max_line_length=FORM_LIMITS["client_id"] + 2)
    delimiter = await stream.readline()
    marker = b"--" + boundary.encode("ascii")
    if not line.endswith(b"\r\n") or delimiter.rstrip(b" \t\r\n") not in (marker, marker + b"--"):
        raise InboxError(422, INVALID_FORM)
    value = line[:-2].decode("ascii")
    client_id = UUID(value)
    if str(client_id) != value.lower():
        raise InboxError(422, INVALID_FORM)
    stream.unread_data(line + delimiter)
    return client_id


async def _parts(content_type: str, body: AsyncIterator[bytes]) -> AsyncIterator[
    tuple[BodyPartReader, str, str | None, UUID | None]
]:
    mime = parse_mimetype(content_type)
    boundary = mime.parameters.get("boundary", "")
    if (mime.type, mime.subtype) != ("multipart", "form-data") or not re.fullmatch(
        r"[A-Za-z0-9'()+_,./:=? -]{1,70}", boundary,
    ) or boundary.endswith(" "):
        raise InboxError(422, INVALID_FORM)
    stream = _MultipartStream(body)
    try:
        reader = MultipartReader(CIMultiDict({"Content-Type": content_type}), stream)
    except (BadHttpMessage, LookupError, ValueError) as exc:
        raise InboxError(422, INVALID_FORM) from exc
    while True:
        stream.header_budget = 16 * 1024
        try:
            part = await reader.next()
            if part is None:
                return
            if not isinstance(part, BodyPartReader) or (name := part.name) is None:
                raise InboxError(422, INVALID_FORM)
            filename = part.filename
            encoding = part.headers.get("Content-Transfer-Encoding", "binary").lower()
            if encoding not in {"binary", "8bit", "7bit"}:
                raise InboxError(422, INVALID_FORM)
            client_id = await _peek_client_id(stream, boundary) if name == "client_id" and filename is None else None
        except (AssertionError, BadHttpMessage, LookupError, RuntimeError, ValueError) as exc:
            raise InboxError(422, INVALID_FORM) from exc
        yield part, name, filename, client_id


async def _chunks(part: BodyPartReader) -> AsyncIterator[bytes]:
    while not part.at_eof():
        try:
            chunk = await part.read_chunk(CHUNK_SIZE)
        except (AssertionError, ValueError) as exc:
            raise InboxError(422, INVALID_FORM) from exc
        if chunk:
            yield chunk


async def _form_value(name: str, part: BodyPartReader) -> str | None:
    data = bytearray()
    oversized = False
    async for chunk in _chunks(part):
        if oversized or len(data) + len(chunk) > FORM_LIMITS[name] * 4:
            if name != "source_app":
                raise InboxError(422, INVALID_FORM)
            oversized = True
        else:
            data.extend(chunk)
    if name == "source_app":
        return data.decode("ascii") if not oversized and re.fullmatch(rb"[A-Za-z0-9._]{1,200}", data) else None
    try:
        value = data.decode("utf-8")
        if len(value) > FORM_LIMITS[name]:
            raise ValueError
    except ValueError as exc:
        raise InboxError(422, INVALID_FORM) from exc
    return value


def _extension(header: bytes) -> str:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "webp"
    raise InboxError(415, "仅支持 PNG、JPEG 或 WebP 图片")


async def _write_image(part: BodyPartReader, path: Path) -> str:
    size = 0
    header = bytearray()
    extension = None
    with path.open("xb") as target:
        async for chunk in _chunks(part):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise InboxError(413, "单张图片不能超过 15 MB")
            if extension is None:
                header.extend(chunk[:12 - len(header)])
                if len(header) == 12:
                    extension = _extension(header)
            target.write(chunk)
    extension = extension or _extension(header)
    final_path = path.with_suffix(f".{extension}")
    path.rename(final_path)
    return final_path.name


def _timestamp(value: datetime) -> int:
    delta = value.replace(tzinfo=value.tzinfo or UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _date(value: int) -> datetime:
    return EPOCH + timedelta(microseconds=value)


def _item(row: aiosqlite.Row) -> InboxItem:
    fields = dict(row)
    fields.update(
        created_at=_date(row["created_at"]),
        synced_at=None if row["synced_at"] is None else _date(row["synced_at"]),
        image_count=len(json.loads(row["images"])),
    )
    return InboxItem.model_validate(fields)


class MobileInbox:
    def __init__(self, db: aiosqlite.Connection, root: str):
        self.db = db
        self.root = Path(root).resolve()
        self._client_locks: dict[UUID, tuple[asyncio.Lock, int]] = {}

    async def cleanup_startup(self) -> None:
        cutoff = time_ns() - ORPHAN_AGE_NS
        removed = 0
        try:
            paths = list(self.root.iterdir())
        except FileNotFoundError:
            paths = []
        except OSError:
            logger.warning("Could not scan mobile inbox directories", exc_info=True)
            paths = []
        for path in paths:
            try:
                if path.is_symlink() or not path.is_dir():
                    continue
                if not path.name.startswith(".upload-"):
                    try:
                        item_id = UUID(path.name)
                    except ValueError:
                        continue
                    if str(item_id) != path.name or path.stat().st_mtime_ns >= cutoff:
                        continue
                    if await self.db.execute_fetchall("SELECT 1 FROM mobile_inbox_items WHERE id = ?", (path.name,)):
                        continue
                shutil.rmtree(path)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning("Could not remove leftover mobile inbox directory", exc_info=True)
        logger.info("Mobile inbox startup cleanup: removed %d directories", removed)

    @asynccontextmanager
    async def _lock_client(self, client_id: UUID) -> AsyncIterator[None]:
        if client_id in self._client_locks:
            lock, users = self._client_locks[client_id]
        else:
            lock, users = asyncio.Lock(), 0
        self._client_locks[client_id] = lock, users + 1
        try:
            async with lock:
                yield
        finally:
            _, users = self._client_locks[client_id]
            if users == 1:
                del self._client_locks[client_id]
            else:
                self._client_locks[client_id] = lock, users - 1

    async def receive(self, content_type: str, body: AsyncIterator[bytes], session: str) -> InboxItem:
        fields = {}
        images = []
        staging = None
        async with AsyncExitStack() as cleanup:
            async for part, name, filename, client_id in _parts(content_type, body):
                if name == "images":
                    if "client_id" not in fields or filename is None or len(images) == MAX_IMAGES:
                        raise InboxError(422, INVALID_FORM)
                    if staging is None:
                        self.root.mkdir(parents=True, exist_ok=True)
                        staging = Path(cleanup.enter_context(TemporaryDirectory(prefix=".upload-", dir=self.root)))
                    images.append(await _write_image(part, staging / f"{len(images) + 1}.tmp"))
                elif name in FORM_LIMITS and name not in fields and filename is None:
                    if client_id is not None:
                        await cleanup.enter_async_context(self._lock_client(client_id))
                        existing = await self._by_client_id(client_id)
                        if existing:
                            return _item(existing)
                        await self._check_quota(session)
                    fields[name] = await _form_value(name, part)
                else:
                    raise InboxError(422, INVALID_FORM)
            try:
                upload = InboxUpload(**fields, image_count=len(images))
            except ValidationError as exc:
                raise InboxError(422, INVALID_FORM) from exc
            saving = asyncio.create_task(self._save(upload, images, staging, session))
            try:
                return await asyncio.shield(saving)
            except asyncio.CancelledError:
                await saving
                raise

    async def _check_quota(self, session: str) -> None:
        [(count,)] = await self.db.execute_fetchall(
            "SELECT COUNT(*) FROM mobile_inbox_items WHERE session_hash = ? AND created_at > ?",
            (session, time_ns() // 1000 - QUOTA_WINDOW_US),
        )
        if count >= QUOTA:
            raise InboxError(429, f"每个会话每小时最多可保存 {QUOTA} 条")

    async def _by_client_id(self, client_id: UUID) -> aiosqlite.Row | None:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM mobile_inbox_items WHERE client_id = ?", (str(client_id),),
        )
        return rows[0] if rows else None

    async def _save(self, upload: InboxUpload, images: list[str], staging: Path | None,
                    session: str) -> InboxItem:
        item_id = uuid4()
        stored = self._directory(item_id)
        if staging is not None:
            staging.rename(stored)
        rows = []
        now = time_ns() // 1000
        try:
            rows = await self.db.execute_fetchall(
                """
                INSERT INTO mobile_inbox_items (id, client_id, session_hash, created_at, text, note, source_app, images)
                SELECT ?, ?, ?, MAX(?, COALESCE((SELECT MAX(created_at) FROM mobile_inbox_items), 0) + 1), ?, ?, ?, ?
                WHERE (SELECT COUNT(*) FROM mobile_inbox_items WHERE session_hash = ? AND created_at > ?) < ?
                ON CONFLICT(client_id) DO NOTHING
                RETURNING *
                """,
                (str(item_id), str(upload.client_id), session, now, upload.text, upload.note, upload.source_app,
                 json.dumps(images), session, now - QUOTA_WINDOW_US, QUOTA),
            )
        finally:
            if staging is not None and not rows:
                self._discard_files(item_id)
        if not rows:
            existing = await self._by_client_id(upload.client_id)
            if existing is not None:
                return _item(existing)
            raise InboxError(429, f"每个会话每小时最多可保存 {QUOTA} 条")
        return _item(rows[0])

    async def list_items(self, limit: int, before: datetime | None) -> list[InboxItem]:
        if before is None:
            rows = await self.db.execute_fetchall(
                "SELECT * FROM mobile_inbox_items ORDER BY created_at DESC LIMIT ?", (limit,),
            )
        else:
            rows = await self.db.execute_fetchall(
                "SELECT * FROM mobile_inbox_items WHERE created_at < ? ORDER BY created_at DESC LIMIT ?",
                (_timestamp(before), limit),
            )
        return [_item(row) for row in rows]

    def _directory(self, item_id: UUID) -> Path:
        return self._local_path(self.root / str(item_id))

    @staticmethod
    def _local_path(path: Path) -> Path:
        try:
            if path.resolve() == path:
                return path
        except (OSError, RuntimeError):
            pass
        raise InboxError(404, "收件箱文件不存在")

    def _image_path(self, item_id: UUID, filename: str) -> Path:
        if not re.fullmatch(r"[1-9]\.(?:png|jpg|webp)", filename):
            raise InboxError(404, "收件箱图片不存在")
        return self._local_path(self._directory(item_id) / filename)

    async def _get(self, item_id: UUID) -> aiosqlite.Row:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM mobile_inbox_items WHERE id = ?", (str(item_id),),
        )
        if not rows:
            raise InboxError(404, "收件箱条目不存在")
        return rows[0]

    async def image(self, item_id: UUID, number: int) -> tuple[Path, str]:
        row = await self._get(item_id)
        images = json.loads(row["images"])
        if not 1 <= number <= len(images):
            raise InboxError(404, "收件箱图片不存在")
        if row["files_purged_at"] is not None:
            raise InboxError(410, "收件箱图片已清理")
        path = self._image_path(item_id, images[number - 1])
        if not path.is_file():
            raise InboxError(410, "收件箱图片已清理")
        return path, IMAGE_TYPES[path.suffix[1:]]

    def _remove_files(self, item_id: UUID) -> None:
        path = self._directory(item_id)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            if path.exists():
                raise

    def _discard_files(self, item_id: UUID) -> None:
        try:
            self._remove_files(item_id)
        except (InboxError, OSError):
            logger.warning("Could not remove mobile inbox files for %s", item_id, exc_info=True)

    async def delete(self, item_id: UUID) -> None:
        async with self.db.execute("DELETE FROM mobile_inbox_items WHERE id = ?", (str(item_id),)) as cursor:
            if not cursor.rowcount:
                raise InboxError(404, "收件箱条目不存在")
        self._discard_files(item_id)

    async def unsynced(self) -> list[tuple[InboxItem, list[Path]]]:
        rows = await self.db.execute_fetchall(
            "SELECT * FROM mobile_inbox_items WHERE synced_at IS NULL ORDER BY created_at",
        )
        return [(_item(row), [self._image_path(UUID(row["id"]), name) for name in json.loads(row["images"])])
                for row in rows]

    async def mark_synced(self, item_ids: list[UUID]) -> int:
        now = time_ns() // 1000
        async with self.db.executemany(
            "UPDATE mobile_inbox_items SET synced_at = ? WHERE id = ? AND synced_at IS NULL",
            [(now, str(item_id)) for item_id in item_ids],
        ) as cursor:
            return cursor.rowcount

    async def set_status(self, item_id: UUID, status: InboxStatus, result: str | None) -> None:
        async with self.db.execute(
            "UPDATE mobile_inbox_items SET status = ?, result = COALESCE(?, result) WHERE id = ?",
            (status, result, str(item_id)),
        ) as cursor:
            if not cursor.rowcount:
                raise InboxError(404, "收件箱条目不存在")

    async def purge(self, days: int) -> int:
        now = time_ns() // 1000
        rows = await self.db.execute_fetchall(
            "SELECT id FROM mobile_inbox_items WHERE synced_at < ? AND files_purged_at IS NULL",
            (now - days * 86400 * 1_000_000,),
        )
        purged = 0
        for row in rows:
            async with self.db.execute(
                "UPDATE mobile_inbox_items SET files_purged_at = ? WHERE id = ? AND files_purged_at IS NULL",
                (now, row["id"]),
            ) as cursor:
                changed = cursor.rowcount
            if changed:
                purged += changed
                self._discard_files(UUID(row["id"]))
        return purged
