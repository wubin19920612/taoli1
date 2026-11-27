import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tarfile
from contextlib import ExitStack
from datetime import timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryFile
from uuid import UUID

import aiosqlite

from app.core.config import get_settings
from app.models.mobile_inbox import InboxItem
from app.services.mobile_inbox import InboxError, MobileInbox

BEIJING = timezone(timedelta(hours=8))
MAX_RESULT_LENGTH = 2000


class InboxArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("收件箱命令或参数无效")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = InboxArgumentParser(prog="python -m app.tools.mobile_inbox")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--unsynced", action="store_true", required=True)
    mark = commands.add_parser("mark-synced")
    mark.add_argument("ids", type=UUID, nargs="+")
    status = commands.add_parser("set-status")
    status.add_argument("id", type=UUID)
    status.add_argument("status", choices=("new", "researched", "dropped"))
    status.add_argument("--result")
    purge = commands.add_parser("purge")
    purge.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    if args.command == "purge" and not 1 <= args.days <= 3650:
        raise ValueError("清理天数必须在 1 至 3650 之间")
    if args.command == "set-status" and len(args.result or "") > MAX_RESULT_LENGTH:
        parser.error("result too long")
    return args


def _note(item: InboxItem) -> str:
    timestamp = item.created_at.astimezone(BEIJING).isoformat(sep=" ", timespec="seconds")
    return (
        f"时间（北京时间）：{timestamp}\n"
        f"来源 App：{item.source_app or ''}\n"
        f"ID：{item.id}\n\n"
        f"## 备注\n\n{item.note or ''}\n\n"
        f"## 分享文字原文\n\n{item.text or ''}"
    )


def _add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    entry = tarfile.TarInfo(name)
    entry.size = len(content)
    archive.addfile(entry, BytesIO(content))


def _add_item(archive: tarfile.TarFile, item: InboxItem, images: list[Path]) -> bool:
    folder = item.created_at.astimezone(BEIJING).strftime("%Y%m%d-%H%M") + "-" + str(item.id)[:8]
    with ExitStack() as files:
        try:
            sources = [files.enter_context(path.open("rb")) for path in images]
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return False
        directory = tarfile.TarInfo(f"{folder}/")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o755
        archive.addfile(directory)
        _add_bytes(archive, f"{folder}/note.md", _note(item).encode("utf-8"))
        for path, source in zip(images, sources):
            entry = tarfile.TarInfo(f"{folder}/{path.name}")
            entry.size = os.fstat(source.fileno()).st_size
            archive.addfile(entry, source)
    return True


async def _export(inbox: MobileInbox) -> dict[str, int]:
    items = await inbox.unsynced()
    manifest = []
    with TemporaryFile() as spool:
        with tarfile.open(fileobj=spool, mode="w") as staged:
            for item, images in items:
                if _add_item(staged, item, images):
                    manifest.append(str(item.id))
        spool.seek(0)
        with tarfile.open(fileobj=spool, mode="r:") as staged, tarfile.open(
            fileobj=sys.stdout.buffer, mode="w|gz",
        ) as archive:
            _add_bytes(archive, "manifest.json", json.dumps(manifest).encode("utf-8"))
            for entry in staged:
                if entry.isfile():
                    with staged.extractfile(entry) as source:
                        archive.addfile(entry, source)
                else:
                    archive.addfile(entry)
    sys.stdout.buffer.flush()
    return {"exported": len(manifest), "skipped_missing": len(items) - len(manifest)}


async def _run(args: argparse.Namespace) -> dict:
    settings = get_settings()
    async with aiosqlite.connect(settings.sqlite_path, isolation_level=None) as db:
        db.row_factory = aiosqlite.Row
        inbox = MobileInbox(db, settings.mobile_inbox_dir)
        if args.command == "export":
            return await _export(inbox)
        if args.command == "mark-synced":
            return {"marked": await inbox.mark_synced(args.ids)}
        if args.command == "set-status":
            await inbox.set_status(args.id, args.status, args.result)
            return {"id": str(args.id), "status": args.status}
        return {"purged": await inbox.purge(args.days)}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    try:
        outcome = asyncio.run(_run(args))
    except (InboxError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, tarfile.TarError):
        print(json.dumps({"ok": False, "error": "无法读取或写入收件箱"}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **outcome}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
