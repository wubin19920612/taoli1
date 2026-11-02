from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from backup_tools import KEY_TABLES, check_directory, compare_counts, restore_drill


DEPLOY = Path(__file__).resolve().parent
SCRIPT = DEPLOY / "backup_tools.py"
UPDATE = DEPLOY / "linux-update.sh"
SHELL = shutil.which("bash")


def make_database(path: Path, tables: tuple[str, ...], rows: int = 2) -> None:
    with closing(sqlite3.connect(path)) as connection:
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            connection.execute(f"CREATE TABLE {quoted} (id INTEGER)")
            connection.executemany(f"INSERT INTO {quoted} VALUES (?)", [(i,) for i in range(rows)])
        connection.commit()


class CapacityTests(unittest.TestCase):
    def test_counts_both_prefixes_and_manual_nested_files_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "radar-before-abcdef123456-20261102T000000Z.db").write_bytes(b"a" * 5)
            (root / "squeeze-route-before-abcdef123456-20261102T000000Z.db").write_bytes(b"b" * 7)
            (root / "env-before-test").write_bytes(b"secret")
            (root / "nested").mkdir()
            (root / "nested" / "radar-before-abcdef123456-20261102T000000Z.db").write_bytes(b"c")
            (root / "failed.db.partial").write_bytes(b"d")
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            output = StringIO()
            with redirect_stdout(output):
                check_directory(root, "15")
            self.assertIn("total_bytes=20", output.getvalue())
            self.assertIn('name="env-before-test"', output.getvalue())
            self.assertIn("UNMANAGED files=3 bytes=8", output.getvalue())
            self.assertNotIn("secret", output.getvalue())
            self.assertEqual(before, {p.relative_to(root): p.read_bytes()
                                      for p in root.rglob("*") if p.is_file()})

    def test_limit_allows_equality_refuses_overflow_and_lists_unmanaged_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            item = root / "env-before-test"
            with item.open("wb") as stream:
                stream.truncate(1024)
            with redirect_stdout(StringIO()):
                check_directory(root, "0.00000095367431640625")
            with item.open("ab") as stream:
                stream.write(b"x")
            output = StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(ValueError, "refusing deployment"):
                check_directory(root, "0.00000095367431640625")
            self.assertIn("env-before-test", output.getvalue())
            self.assertEqual(item.stat().st_size, 1025)

    def test_rejects_bad_limits_and_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for limit in ("0", "-1", "NaN", "Infinity", "bad", "0.000000000001"):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    check_directory(root, limit)
            with self.assertRaises(ValueError):
                check_directory(root / "missing", "15")

    def test_does_not_follow_symlinks_or_treat_them_as_managed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backups = root / "backups"
            backups.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "large").write_bytes(b"x" * 8192)
            link = backups / "radar-before-abcdef123456-20261102T000000Z.db"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            output = StringIO()
            with redirect_stdout(output):
                check_directory(backups, "15")
            self.assertIn("UNMANAGED files=1", output.getvalue())
            self.assertNotIn("large", output.getvalue())
            with self.assertRaises(ValueError):
                check_directory(link, "15")


class RestoreTests(unittest.TestCase):
    def test_cli_restores_both_profiles_and_preserves_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for kind, tables in KEY_TABLES.items():
                with self.subTest(kind=kind):
                    backup = root / f"{kind}-before-abcdef123456-20261102T000000Z.db"
                    make_database(backup, tables)
                    before = backup.read_bytes()
                    result = subprocess.run([sys.executable, str(SCRIPT), "restore-drill", str(backup)],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("PASS integrity_check=ok tables=3", result.stdout)
                    self.assertEqual(result.stdout.count("backup=2 restored=2"), 3)
                    self.assertEqual(backup.read_bytes(), before)
                    self.assertFalse(Path(str(backup) + "-wal").exists())
                    self.assertFalse(Path(str(backup) + "-shm").exists())

    def test_corrupt_snapshot_and_missing_tables_fail_and_temp_files_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "snapshot.db"
            backup.write_bytes(b"not sqlite")
            result = subprocess.run([sys.executable, str(SCRIPT), "restore-drill", str(backup),
                                     "--kind", "radar"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAIL", result.stderr)
            backup.unlink()
            make_database(backup, ("unrelated",))
            with self.assertRaisesRegex(ValueError, "missing key table"):
                restore_drill(backup, "radar", None)

    def test_row_mismatch_is_a_failure_and_sql_identifiers_are_quoted(self) -> None:
        with closing(sqlite3.connect(":memory:")) as source, \
                closing(sqlite3.connect(":memory:")) as target:
            table = 'odd"; DROP TABLE important;--'
            quoted = '"' + table.replace('"', '""') + '"'
            for connection in (source, target):
                connection.execute(f"CREATE TABLE {quoted} (id)")
            source.execute(f"INSERT INTO {quoted} VALUES (1)")
            with redirect_stdout(StringIO()), self.assertRaisesRegex(ValueError, "row counts differ"):
                compare_counts(source, target, [table])

    def test_zero_row_tables_pass_and_temporary_files_are_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "empty.db"
            make_database(backup, ("key_table",), rows=0)
            original_tempdir = tempfile.TemporaryDirectory
            created = []

            def tracked_tempdir(**kwargs):
                handle = original_tempdir(**kwargs)
                created.append(Path(handle.name))
                return handle

            with patch("backup_tools.tempfile.TemporaryDirectory", side_effect=tracked_tempdir), \
                    redirect_stdout(StringIO()):
                restore_drill(backup, None, ["key_table"])
                with self.assertRaises(ValueError):
                    restore_drill(backup, None, ["missing"])
            self.assertEqual(len(created), 2)
            self.assertTrue(all(not path.exists() for path in created))

    def test_live_names_paths_sidecars_and_unknown_profile_are_refused(self) -> None:
        for path in (Path("/data/snapshot.db"), Path("/var/lib/docker/volumes/test/snapshot.db")):
            with self.assertRaisesRegex(ValueError, "forbidden"):
                restore_drill(path, "radar", None)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("radar.db", "radar-squeeze-route.db"):
                with self.assertRaisesRegex(ValueError, "basenames"):
                    restore_drill(root / name, "radar", None)
            snapshot = root / "snapshot.db"
            make_database(snapshot, ("test",))
            with self.assertRaisesRegex(ValueError, "set --kind"):
                restore_drill(snapshot, None, None)
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(str(snapshot) + suffix)
                sidecar.touch()
                with self.assertRaisesRegex(ValueError, "sidecars"):
                    restore_drill(snapshot, "radar", None)
                sidecar.unlink()


class IsolatedBackupTests(unittest.TestCase):
    def test_embedded_backup_handles_live_and_closed_wal_and_refuses_overwrite(self) -> None:
        # Execute the actual embedded SQLite code; Docker/privileged ownership
        # operations are outside this local test's acceptance boundary.
        script = UPDATE.read_text(encoding="utf-8").split("<<'ISOLATED_PY'\n", 1)[1].split("\nISOLATED_PY", 1)[0]
        if os.name == "nt":
            script = "import os\nos.chown = lambda *args: None\n" + script
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            with closing(sqlite3.connect(source)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE sample (id)")
                writer.execute("INSERT INTO sample VALUES (42)")
                writer.commit()
                self.assertTrue(Path(str(source) + "-wal").exists())
                for state in ("live", "closed"):
                    if state == "closed":
                        writer.close()
                    destination = root / f"{state}.db"
                    result = subprocess.run([sys.executable, "-", str(source), str(destination)],
                                            input=script, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), hashlib.sha256(destination.read_bytes()).hexdigest())
                    with closing(sqlite3.connect(destination)) as restored:
                        self.assertEqual(restored.execute("SELECT id FROM sample").fetchall(), [(42,)])
                    before = destination.read_bytes()
                    retry = subprocess.run([sys.executable, "-", str(source), str(destination)],
                                           input=script, capture_output=True, text=True)
                    self.assertNotEqual(retry.returncode, 0)
                    self.assertEqual(destination.read_bytes(), before)
                    self.assertFalse(Path(str(destination) + ".partial").exists())

    def test_failed_backup_is_not_published_and_partial_is_not_overwritten(self) -> None:
        script = UPDATE.read_text(encoding="utf-8").split("<<'ISOLATED_PY'\n", 1)[1].split("\nISOLATED_PY", 1)[0]
        if os.name == "nt":
            script = "import os\nos.chown = lambda *args: None\n" + script
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "invalid.db"
            source.write_bytes(b"not sqlite")
            destination = root / "snapshot.db"
            partial = Path(str(destination) + ".partial")
            result = subprocess.run([sys.executable, "-", str(source), str(destination)],
                                    input=script, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())
            self.assertTrue(partial.exists())
            before = partial.read_bytes()
            retry = subprocess.run([sys.executable, "-", str(source), str(destination)],
                                   input=script, capture_output=True, text=True)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(partial.read_bytes(), before)


@unittest.skipUnless(SHELL, "bash unavailable")
class ShellTests(unittest.TestCase):
    def run_shell(self, command: str, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([SHELL, "-c", command, "test", *args], capture_output=True, text=True,
                              env={**os.environ, "PYTHON": sys.executable, **env})

    def test_check_only_entry_never_calls_git_docker_or_reads_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_shell('git() { exit 97; }; docker() { exit 98; }; '
                                    'sudo() { exit 99; }; export -f git docker sudo; bash "$1" --check-backups "$2"',
                                    UPDATE.as_posix(), Path(temporary).as_posix())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("BACKUP_DIR_CHECK PASS", result.stdout)
            (Path(temporary) / "env-before-test").write_bytes(b"x" * 2048)
            result = self.run_shell('bash "$1" --check-backups "$2"', UPDATE.as_posix(),
                                    Path(temporary).as_posix(), BACKUP_DIR_MAX_GB="0.000001")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("refusing deployment", result.stderr)

    def test_restore_wrapper_reports_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            backup = Path(temporary) / "test.db"
            make_database(backup, ("key_table",))
            for table, code in (("key_table", 0), ("missing", 1)):
                result = self.run_shell('bash "$1" "$2" --table "$3"',
                                        (DEPLOY / "restore_drill.sh").as_posix(), backup.as_posix(), table)
                self.assertEqual(result.returncode, code, result.stderr)

    def test_isolated_function_enforces_resource_flags_and_host_hash(self) -> None:
        function = UPDATE.read_text(encoding="utf-8").split("backup_sqlite_isolated() {", 1)[1]
        function = "backup_sqlite_isolated() {" + function.split("\nbackup_function=", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root / "calls"
            wrapper = '''set -Eeuo pipefail
sudo() { printf '%s\\n' "$*" >> "$MOCK_LOG"; cat >/dev/null; printf '%s\\n' "$MOCK_SHA"; }
sha256sum() { printf '%s  file\\n' "$HOST_SHA"; }
ROOT_DIR="$1"
backend_container=abc123abc123
backup_image=sha256:mock-image
'''
            for host_sha, expected in (("a" * 64, 0), ("b" * 64, 1)):
                result = self.run_shell(wrapper + function + '\nbackup_sqlite_isolated /data/radar.db unused "$1/out.db"',
                                        root.as_posix(), MOCK_LOG=log.as_posix(),
                                        MOCK_SHA="a" * 64, HOST_SHA=host_sha)
                self.assertEqual(result.returncode, expected, result.stderr)
            calls = log.read_text()
            for flag in ("--memory=128m", "--memory-swap=128m", "--network none", "--pull=never",
                         "--read-only", "--volumes-from abc123abc123:rw", "--entrypoint python"):
                self.assertIn(flag, calls)

    def test_legacy_selection_is_explicit_and_invalid_mode_stops_early(self) -> None:
        result = self.run_shell('BACKUP_MODE=invalid bash "$1"', UPDATE.as_posix())
        self.assertEqual(result.returncode, 2)
        self.assertIn("BACKUP_MODE must", result.stderr)
        selection = UPDATE.read_text(encoding="utf-8").split("backup_function=backup_sqlite_isolated\n", 1)[1]
        selection = "backup_function=backup_sqlite_isolated\n" + selection.split("\nbackup_timestamp=", 1)[0]
        result = self.run_shell('set -Eeuo pipefail; backup_mode=legacy; sudo() { exit 99; }; '
                                + selection + '\nprintf "%s\\n" "$backup_function"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "backup_sqlite")

    def test_default_selection_pins_compose_backend_image_and_rejects_multiple_containers(self) -> None:
        selection = UPDATE.read_text(encoding="utf-8").split("backup_function=backup_sqlite_isolated\n", 1)[1]
        selection = "backup_function=backup_sqlite_isolated\n" + selection.split("\nbackup_timestamp=", 1)[0]
        mock = '''set -Eeuo pipefail
backup_mode=isolated
sudo() {
  if [[ "$*" == *"compose ps -q backend" ]]; then printf '%s\\n' "$MOCK_CONTAINER";
  elif [[ "$*" == *"inspect abc123abc123 --format {{.Image}}" ]]; then printf '%s\\n' "$MOCK_IMAGE";
  else return 99; fi
}
'''
        for container, expected in (("abc123abc123", 0), ("abc123abc123\ndef456def456", 1), ("", 1)):
            result = self.run_shell(mock + selection + '\nprintf "%s %s\\n" "$backup_function" "$backup_image"',
                                    MOCK_CONTAINER=container, MOCK_IMAGE="sha256:" + "a" * 64)
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected == 0:
                self.assertIn("backup_sqlite_isolated sha256:", result.stdout)

    def test_both_databases_are_backed_up_and_route_probe_errors_fail_closed(self) -> None:
        flow = UPDATE.read_text(encoding="utf-8").split("backup_timestamp=", 1)[1]
        flow = "backup_timestamp=" + flow.split("\n# Refuse the update", 1)[0]
        mock = '''set -Eeuo pipefail
commit=abcdef123456abcdef123456abcdef123456abcd
backup_function=mock_backup
mock_backup() { printf 'MOCK_BACKUP %s\\n' "$*"; }
sudo() { if [[ "$MOCK_ROUTE" == error ]]; then return 99; fi; printf '%s\\n' "$MOCK_ROUTE"; }
'''
        for route, expected, count in (("present", 0, 2), ("absent", 0, 1), ("error", 99, 1), ("unknown", 1, 1)):
            with self.subTest(route=route):
                result = self.run_shell(mock + flow, MOCK_ROUTE=route)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(result.stdout.count("MOCK_BACKUP"), count)
                if route == "present":
                    self.assertIn("/data/radar-squeeze-route.db", result.stdout)


if __name__ == "__main__":
    unittest.main()
