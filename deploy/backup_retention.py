#!/usr/bin/env python3
"""Retain bounded local SQLite restore points after a successful deployment."""

from __future__ import annotations

import argparse
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path


AUTOMATIC_NAME = re.compile(r"radar-before-[0-9a-f]{12}-\d{8}T\d{6}Z\.db\Z")
SQUEEZE_ROUTE_AUTOMATIC_NAME = re.compile(
    r"squeeze-route-before-[0-9a-f]{12}-\d{8}T\d{6}Z\.db\Z"
)
LEGACY_NAME = re.compile(r"radar-[A-Za-z0-9._-]+\.db\Z")


@dataclass(frozen=True)
class BackupFile:
    path: Path
    size: int
    modified_at: datetime
    inode: int
    modified_ns: int


def list_backups(
    directory: Path, include_legacy: bool, prefix: str = "radar",
) -> list[BackupFile]:
    pattern = (
        SQUEEZE_ROUTE_AUTOMATIC_NAME if prefix == "squeeze-route"
        else LEGACY_NAME if include_legacy else AUTOMATIC_NAME
    )
    backups: list[BackupFile] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not pattern.fullmatch(entry.name) or not entry.is_file(follow_symlinks=False):
                continue
            file_stat = (directory / entry.name).stat(follow_symlinks=False)
            backups.append(
                BackupFile(
                    path=directory / entry.name,
                    size=file_stat.st_size,
                    modified_at=datetime.fromtimestamp(file_stat.st_mtime, timezone.utc),
                    inode=file_stat.st_ino,
                    modified_ns=file_stat.st_mtime_ns,
                )
            )
    return sorted(backups, key=lambda backup: (backup.modified_at, backup.path.name), reverse=True)


def select_retained(backups: list[BackupFile], now: datetime) -> set[Path]:
    retained = {backup.path for backup in backups[:3]}
    daily_dates: set[date] = set()
    weekly_dates: set[tuple[int, int]] = set()
    for backup in backups:
        age_days = (now.date() - backup.modified_at.date()).days
        if age_days < 0:
            retained.add(backup.path)
        elif age_days < 7 and backup.modified_at.date() not in daily_dates:
            retained.add(backup.path)
            daily_dates.add(backup.modified_at.date())
        elif 7 <= age_days < 35:
            week = backup.modified_at.isocalendar()[:2]
            if week not in weekly_dates and len(weekly_dates) < 4:
                retained.add(backup.path)
                weekly_dates.add(week)
    return retained


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--prefix", choices=("radar", "squeeze-route"), default="radar")
    parser.add_argument("--include-legacy", action="store_true")
    parser.add_argument("--protect", action="append", default=[], metavar="BASENAME")
    parser.add_argument("--expect-delete-count", type=int)
    parser.add_argument("--apply", action="store_true", help="Delete selected files; default is a preview")
    args = parser.parse_args()
    if args.prefix == "squeeze-route" and args.include_legacy:
        parser.error("--include-legacy is only available for radar backups")

    directory = args.backup_dir
    if directory.is_symlink() or not directory.is_dir():
        parser.error("backup directory must be an existing non-symlink directory")
    backups = list_backups(directory, args.include_legacy, args.prefix)
    retained = select_retained(backups, datetime.now(timezone.utc))
    protected = set(args.protect)
    unknown = protected - {backup.path.name for backup in backups}
    if unknown:
        parser.error(f"protected backup is not eligible: {', '.join(sorted(unknown))}")
    retained.update(backup.path for backup in backups if backup.path.name in protected)
    obsolete = [backup for backup in backups if backup.path not in retained]
    if args.expect_delete_count is not None and len(obsolete) != args.expect_delete_count:
        parser.error(f"deletion count changed: expected {args.expect_delete_count}, found {len(obsolete)}")
    for backup in backups:
        if backup.path in retained:
            print(f"KEEP {backup.path.name} ({backup.size} bytes)")
    for backup in obsolete:
        if args.apply:
            current = backup.path.stat(follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or (
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            ) != (backup.inode, backup.size, backup.modified_ns):
                raise RuntimeError(f"backup changed during cleanup: {backup.path.name}")
            backup.path.unlink()
        print(f"{'DELETE' if args.apply else 'WOULD DELETE'} {backup.path.name} ({backup.size} bytes)")
    print(
        f"{'Deleted' if args.apply else 'Would delete'} {len(obsolete)} of {len(backups)} "
        f"eligible backups; bytes={sum(backup.size for backup in obsolete)}; "
        f"retained={len(retained)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
