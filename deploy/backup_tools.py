#!/usr/bin/env python3
"""Local backup inventory, capacity gate and isolated SQLite restore drill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

from backup_retention import AUTOMATIC_NAME, SQUEEZE_ROUTE_AUTOMATIC_NAME


KEY_TABLES = {
    "radar": ("app_settings", "opportunity_history", "alert_events"),
    "squeeze-route": ("squeeze_route_state", "squeeze_route_latest", "squeeze_route_events"),
}


def capacity_limit(value: str) -> int:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0:
            raise ValueError
        limit = int(amount * 1024**3)
        if limit < 1:
            raise ValueError
        return limit
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError("BACKUP_DIR_MAX_GB must be a finite positive GiB value (at least 1 byte)") from None


def check_directory(directory: Path, max_gb: str) -> None:
    """Count every entry recursively without following links or deleting files."""
    limit = capacity_limit(max_gb)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("backup directory must be an existing non-symlink directory")
    total = 0
    unmanaged: list[tuple[str, int]] = []

    def scan(parent: Path) -> None:
        nonlocal total
        with os.scandir(parent) as entries:
            for entry in entries:
                path = Path(entry.path)
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    scan(path)
                    continue
                total += metadata.st_size
                eligible = (
                    parent == directory
                    and stat.S_ISREG(metadata.st_mode)
                    and (AUTOMATIC_NAME.fullmatch(entry.name)
                         or SQUEEZE_ROUTE_AUTOMATIC_NAME.fullmatch(entry.name))
                )
                if not eligible:
                    unmanaged.append((str(path.relative_to(directory)), metadata.st_size))

    scan(directory)
    print(f"BACKUP_DIR total_bytes={total} limit_bytes={limit} unit=GiB")
    for name, size in sorted(unmanaged):
        print(f"UNMANAGED bytes={size} name={json.dumps(name, ensure_ascii=True)}")
    print(f"UNMANAGED files={len(unmanaged)} bytes={sum(size for _, size in unmanaged)} "
          "(outside automatic cleanup; links counted but never followed)")
    if total > limit:
        raise ValueError("backup directory exceeds BACKUP_DIR_MAX_GB; refusing deployment. "
                         "Review the inventory and retention preview; no files were deleted.")
    print("BACKUP_DIR_CHECK PASS")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_live_path(path: Path) -> None:
    resolved = path.resolve()
    for forbidden in (Path("/data").resolve(), Path("/var/lib/docker").resolve()):
        if resolved == forbidden or forbidden in resolved.parents:
            raise ValueError("live data/Docker paths are forbidden; export a standalone backup first")


def restore_temp_parent(backup: Path) -> Path:
    """Select without tempfile's write probe; validate before any file creation."""
    configured = next((os.environ[name] for name in ("TMPDIR", "TEMP", "TMP")
                       if os.environ.get(name)), None)
    candidates = [Path(configured)] if configured else [Path("/tmp"), Path("/var/tmp")]
    if not configured and os.name == "nt":
        candidates.append(Path(os.environ.get("SystemRoot", "C:/Windows")) / "Temp")
    backup_directories = (backup.resolve().parent, Path(__file__).resolve().parent.parent / "backups")
    for candidate in candidates:
        reject_live_path(candidate)
        resolved = candidate.resolve()
        for directory in backup_directories:
            directory = directory.resolve()
            if resolved == directory or directory in resolved.parents:
                raise ValueError("temporary paths inside backup directories are forbidden")
        # Only read metadata after the protected-path checks; never probe by writing.
        if resolved.is_dir():
            return resolved
    raise ValueError("no existing safe temporary directory; set TMPDIR explicitly")


def integrity_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchall()
    if result != [("ok",)]:
        raise ValueError(f"integrity_check={result}")


def table_counts(connection: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    counts = {}
    for table in tables:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            raise ValueError(f"missing key table: {table}")
        quoted = '"' + table.replace('"', '""') + '"'
        counts[table] = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
    return counts


def compare_counts(reference: sqlite3.Connection, restored: sqlite3.Connection,
                   tables: list[str]) -> None:
    expected = table_counts(reference, tables)
    actual = table_counts(restored, tables)
    for table in tables:
        print(f"TABLE {json.dumps(table)} backup={expected[table]} restored={actual[table]}")
    if actual != expected:
        raise ValueError("key table row counts differ")


def restore_drill(backup: Path, kind: str | None, tables: list[str] | None) -> None:
    reject_live_path(backup)
    temporary_parent = restore_temp_parent(backup)
    if backup.name in ("radar.db", "radar-squeeze-route.db"):
        raise ValueError("live database basenames are forbidden; choose an exported backup")
    if backup.is_symlink() or not backup.is_file():
        raise ValueError("backup must be an existing regular, non-symlink file")
    if any(Path(str(backup) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("backup has SQLite sidecars; choose a closed standalone snapshot")
    if not tables:
        if kind is None:
            if backup.name.startswith("squeeze-route-before-"):
                kind = "squeeze-route"
            elif backup.name.startswith("radar-before-"):
                kind = "radar"
            else:
                raise ValueError("set --kind radar|squeeze-route or explicit --table names")
        tables = list(KEY_TABLES[kind])
    original_sha = sha256(backup)
    with tempfile.TemporaryDirectory(prefix="taoli1-restore-drill-", dir=temporary_parent) as temporary:
        reference_path = Path(temporary) / "reference.db"
        restored_path = Path(temporary) / "restored.db"
        # The supplied path is only read as a file. All SQLite opens are in this
        # private temporary directory, never on the live volume or input file.
        shutil.copyfile(backup, reference_path)
        if sha256(reference_path) != original_sha:
            raise ValueError("backup changed while copying")
        with closing(sqlite3.connect(reference_path.as_uri() + "?mode=ro&immutable=1",
                                     uri=True)) as reference:
            reference.execute("PRAGMA cache_size=-2048")
            integrity_check(reference)
            with closing(sqlite3.connect(restored_path)) as restored:
                restored.execute("PRAGMA cache_size=-2048")
                reference.backup(restored, pages=-1)
                integrity_check(restored)
                compare_counts(reference, restored, tables)
        if sha256(backup) != original_sha:
            raise ValueError("input backup changed during drill")
        restored_sha = sha256(restored_path)
    print(f"RESTORE_DRILL PASS integrity_check=ok tables={len(tables)} "
          f"backup_sha256={original_sha} restored_sha256={restored_sha}")
    print("RESTORE_DRILL temporary files removed; input backup unchanged")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-dir", help="read-only inventory and capacity gate")
    check.add_argument("--backup-dir", type=Path, required=True)
    check.add_argument("--max-gb", default=os.environ.get("BACKUP_DIR_MAX_GB", "15"))
    drill = commands.add_parser("restore-drill", help="restore only into an auto-removed temp directory")
    drill.add_argument("backup", type=Path)
    drill.add_argument("--kind", choices=tuple(KEY_TABLES))
    drill.add_argument("--table", action="append", dest="tables", help="override default key tables; repeatable")
    args = parser.parse_args()
    try:
        if args.command == "check-dir":
            check_directory(args.backup_dir, args.max_gb)
        else:
            restore_drill(args.backup, args.kind, args.tables)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"{args.command} FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
