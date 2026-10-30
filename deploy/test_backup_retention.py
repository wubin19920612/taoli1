from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backup_retention import list_backups, select_retained


SCRIPT = Path(__file__).with_name("backup_retention.py")


class BackupRetentionTests(unittest.TestCase):
    def test_keeps_recent_daily_and_weekly_restore_points(self) -> None:
        now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for age_days, hour in [
                (0, 10), (0, 9), (0, 8), (0, 7), (1, 10), (1, 9),
                (4, 10), (4, 9), (9, 10), (9, 9), (16, 10), (40, 10),
            ]:
                timestamp = now - timedelta(days=age_days, hours=12 - hour)
                name = f"radar-before-abcdef123456-{timestamp:%Y%m%dT%H%M%SZ}.db"
                path = directory / name
                path.write_bytes(b"backup")
                os.utime(path, (timestamp.timestamp(), timestamp.timestamp()))
            backups = list_backups(directory, include_legacy=False)
            retained = select_retained(backups, now)
            retained_names = {path.name for path in retained}
            self.assertEqual(len(retained), 7)
            self.assertIn("radar-before-abcdef123456-20260924T080000Z.db", retained_names)
            self.assertIn("radar-before-abcdef123456-20260915T100000Z.db", retained_names)
            self.assertNotIn("radar-before-abcdef123456-20260815T100000Z.db", retained_names)

    def test_preview_and_apply_touch_only_eligible_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index in range(5):
                (directory / f"radar-before-abcdef123456-20260924T12000{index}Z.db").write_bytes(
                    f"backup{index}".encode()
                )
            manual = directory / "radar-pre-change.db"
            manual.write_bytes(b"manual")
            sidecar = directory / "radar-before-abcdef123456-20260924T120000Z.db-wal"
            sidecar.write_bytes(b"wal")
            outside = directory.parent / "outside-backup.db"
            symlink = directory / "radar-before-abcdef123456-20260924T120005Z.db"
            try:
                symlink.symlink_to(outside)
            except OSError:
                symlink = None

            command = [sys.executable, str(SCRIPT), "--backup-dir", str(directory)]
            preview = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn("Would delete 2 of 5 eligible backups", preview.stdout)
            self.assertEqual(len(list(directory.glob("*.db"))), 6 + (symlink is not None))
            applied = subprocess.run([*command, "--apply"], capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("Deleted 2 of 5 eligible backups", applied.stdout)
            self.assertTrue(manual.exists())
            self.assertTrue(sidecar.exists())
            if symlink is not None:
                self.assertTrue(symlink.is_symlink())

    def test_legacy_snapshots_are_only_eligible_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index in range(5):
                (directory / f"radar-manual-{index}.db").write_bytes(f"backup{index}".encode())
            command = [sys.executable, str(SCRIPT), "--backup-dir", str(directory)]
            automatic = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn("Would delete 0 of 0 eligible backups", automatic.stdout)
            legacy = subprocess.run([*command, "--include-legacy"], capture_output=True, text=True, check=True)
            self.assertIn("Would delete 2 of 5 eligible backups", legacy.stdout)
            protected = subprocess.run(
                [*command, "--include-legacy", "--protect", "radar-manual-0.db"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Would delete 1 of 5 eligible backups", protected.stdout)
            changed = subprocess.run(
                [*command, "--include-legacy", "--apply", "--expect-delete-count", "3"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(changed.returncode, 0)
            self.assertEqual(len(list(directory.glob("*.db"))), 5)

    def test_squeeze_route_retention_does_not_touch_radar_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for index in range(5):
                (directory / f"squeeze-route-before-abcdef123456-20260924T12000{index}Z.db").write_bytes(
                    f"route{index}".encode()
                )
                (directory / f"radar-before-abcdef123456-20260924T12000{index}Z.db").write_bytes(
                    f"radar{index}".encode()
                )
            command = [
                sys.executable, str(SCRIPT), "--backup-dir", str(directory),
                "--prefix", "squeeze-route", "--apply",
            ]
            applied = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn("Deleted 2 of 5 eligible backups", applied.stdout)
            self.assertEqual(len(list(directory.glob("squeeze-route-before-*.db"))), 3)
            self.assertEqual(len(list(directory.glob("radar-before-*.db"))), 5)


if __name__ == "__main__":
    unittest.main()
