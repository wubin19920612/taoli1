"""Single writer, WAL, transactional events/fills/positions and UTC archives."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
from .core import day


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def run_lock(path):
    """A second local service must not double the shared-IP request budget."""
    path = Path(str(path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, t0 REAL, t1 REAL NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, variant TEXT NOT NULL, amount INTEGER NOT NULL,
 status TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(event_id,variant,amount));
CREATE TABLE IF NOT EXISTS positions (
 id TEXT PRIMARY KEY, event_id TEXT NOT NULL, opened_at REAL NOT NULL, closed_at REAL,
 data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS positions_open ON positions(closed_at);
CREATE INDEX IF NOT EXISTS fills_event ON fills(event_id);
CREATE INDEX IF NOT EXISTS events_time ON events(t1);
CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, t1 REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime (
 ts REAL PRIMARY KEY, observed REAL NOT NULL, rss REAL, stalls_ok INTEGER, limits_ok INTEGER);
CREATE TABLE IF NOT EXISTS rate_limit_samples (ts REAL PRIMARY KEY, delta INTEGER NOT NULL);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=3000")
        self.db.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        with self.db:
            yield

    def meta(self, key, default=None):
        row = self.db.execute("SELECT data FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, data):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(data, allow_nan=False)))

    def runtime(self, now, observed, rss, metrics, own_rate_limits):
        stalls = metrics.get("taoli_stalls_not_worse")
        limits = metrics.get("no_429_increase")
        # Persist the sampling baseline in the same transaction as evidence.
        # Older databases can seed it from their persisted limiter snapshot.
        old = self.meta("runtime_rate_limits")
        if old is None:
            old = sum(s.get("rate_limits", 0) for s in self.meta("limiter", {}).values())
        delta = own_rate_limits-old if own_rate_limits >= old else own_rate_limits
        if delta:
            limits = False
        self.db.execute("INSERT OR REPLACE INTO runtime VALUES (?,?,?,?,?)", (
            now, max(0, observed), rss,
            int(stalls) if isinstance(stalls, bool) else None,
            int(limits) if isinstance(limits, bool) else None))
        self.set_meta("runtime_rate_limits", own_rate_limits)
        self.db.execute("INSERT OR REPLACE INTO rate_limit_samples VALUES (?,?)", (now, delta))

    def runtime_window(self, since, until):
        n, observed, rss_n, rss, stalls_n, stalls, limits_n, limits = self.db.execute(
            "SELECT count(*),coalesce(sum(observed),0),count(rss),max(rss),"
            "count(stalls_ok),min(stalls_ok),count(limits_ok),min(limits_ok) "
            "FROM runtime WHERE ts>=? AND ts<?", (since, until)).fetchone()
        return observed, dict(max_rss_mb=rss if n and rss_n == n else None,
                              taoli_stalls_not_worse=bool(stalls) if n and stalls_n == n else None,
                              no_429_increase=bool(limits) if n and limits_n == n else None)

    def status_counts(self, since, until):
        """Scalar SQL only: status never constructs a trading report/window."""
        events = opened = observations = unknown = 0
        for connection in self.sources():
            events += connection.execute("SELECT count(*) FROM events WHERE t1>=? AND t1<?", (since, until)).fetchone()[0]
            opened += connection.execute("SELECT count(*) FROM fills f JOIN events e ON e.id=f.event_id "
                                         "WHERE e.t1>=? AND e.t1<? AND f.variant='measured' AND f.amount=300 AND f.status='opened'",
                                         (since, until)).fetchone()[0]
            n, u = connection.execute("SELECT count(*),coalesce(sum(coalesce(json_extract(data,'$.funding_known'),0)=0),0) "
                                      "FROM positions WHERE coalesce(closed_at,opened_at)>=? AND coalesce(closed_at,opened_at)<?",
                                      (since, until)).fetchone()
            observations += n
            unknown += u
        return dict(events=events, measured_300_open_events=opened, funding_observations=observations,
                    funding_unknown=unknown, funding_unknown_ratio=unknown/observations if observations else None)

    def seen(self, event_id):
        return self.db.execute("SELECT 1 FROM seen WHERE id=?", (event_id,)).fetchone() is not None

    def event(self, event):
        self.db.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (event["id"], event["t1"]))
        self.db.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?)", (
            event["id"], event.get("t0"), event["t1"], event["status"], json.dumps(event, allow_nan=False)))

    def fill(self, fill):
        self.db.execute("INSERT OR REPLACE INTO fills VALUES (?,?,?,?,?,?)", (
            fill["id"], fill["event_id"], fill["variant"], fill["amount"], fill["status"], json.dumps(fill, allow_nan=False)))

    def position(self, position):
        self.db.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?)", (
            position["id"], position["event_id"], position["opened_at"], position.get("closed_at"), json.dumps(position, allow_nan=False)))

    def open_positions(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM positions WHERE closed_at IS NULL")]

    def recover(self):
        # Never replay partial opening sequences using new books after restart.
        with self.transaction():
            for (raw,) in self.db.execute("SELECT data FROM events WHERE status IN ('pending','measured','awaiting_specs','awaiting_funding')").fetchall():
                e = json.loads(raw)
                e["status"] = "interrupted_restart"
                self.event(e)
                for variant in ("measured", "plus_1s", "leg_delay"):
                    for amount in (100, 300, 1000, 3000):
                        fid = "%s:%s:%s" % (e["id"], variant, amount)
                        if not self.db.execute("SELECT 1 FROM fills WHERE id=?", (fid,)).fetchone():
                            self.fill(dict(id=fid, event_id=e["id"], variant=variant, amount=amount, status="interrupted_restart"))

    def sources(self):
        paths = sorted(self.path.parent.glob(self.path.stem + "-????-??-??.db")) + [self.path]
        for path in paths:
            connection = self.db if path == self.path else sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
            try:
                yield connection
            finally:
                if connection is not self.db:
                    connection.close()

    def rows(self, table, since=None, until=None, by_close=False):
        if table not in ("events", "fills", "positions"):
            raise ValueError("invalid_table")
        source, field = table + " t", "t.t1"
        if table == "fills":
            source = "fills t JOIN events e ON e.id=t.event_id"
            field = "e.t1"
        elif table == "positions":
            field = "coalesce(t.closed_at,t.opened_at)" if by_close else "t.opened_at"
        clauses, params = [], []
        for bound, operator in ((since, ">="), (until, "<")):
            if bound is not None:
                clauses.append(field + operator + "?")
                params.append(bound)
        query = "SELECT t.data FROM " + source + (" WHERE " + " AND ".join(clauses) if clauses else "")
        for connection in self.sources():
            for (raw,) in connection.execute(query, params):
                yield json.loads(raw)

    def rotate(self, now):
        """Move completed event families to their discovery-date archive.

        Attach+copy+delete in DELETE journal mode is SQLite's atomic multi-file
        transaction; switch back to WAL afterwards. Active families never move.
        Seen tombstones remain for 30 days, including across restarts/rotation.
        """
        today = day(now)
        if self.meta("rotation_day") == today:
            return
        self.db.commit()
        dates = [r[0] for r in self.db.execute("""SELECT DISTINCT date(t1,'unixepoch') FROM events
            WHERE date(t1,'unixepoch') < ? AND status NOT IN ('pending','measured','awaiting_specs','awaiting_funding')
            AND id NOT IN (SELECT event_id FROM positions WHERE closed_at IS NULL)""", (today,))]
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.execute("PRAGMA journal_mode=DELETE")
        try:
            for date in dates:
                archive = self.path.with_name(self.path.stem + "-" + date + ".db")
                conn = sqlite3.connect(str(archive))
                conn.executescript(SCHEMA)
                conn.close()
                self.db.execute("ATTACH DATABASE ? AS archive", (str(archive),))
                try:
                    with self.transaction():
                        self.db.execute("CREATE TEMP TABLE moving AS SELECT id FROM events WHERE date(t1,'unixepoch')=? AND status NOT IN ('pending','measured','awaiting_specs','awaiting_funding') AND id NOT IN (SELECT event_id FROM positions WHERE closed_at IS NULL)", (date,))
                        for table, key in (("events", "id"), ("fills", "event_id"), ("positions", "event_id")):
                            self.db.execute("INSERT OR REPLACE INTO archive.%s SELECT * FROM main.%s WHERE %s IN (SELECT id FROM moving)" % (table, table, key))
                            self.db.execute("DELETE FROM main.%s WHERE %s IN (SELECT id FROM moving)" % (table, key))
                        self.db.execute("DROP TABLE moving")
                finally:
                    self.db.execute("DETACH DATABASE archive")
            with self.transaction():
                self.db.execute("DELETE FROM seen WHERE t1 < ? AND id NOT IN (SELECT id FROM events)", (now - 30 * 86400,))
                self.db.execute("DELETE FROM runtime WHERE ts < ?", (now-30*86400,))
                self.db.execute("DELETE FROM rate_limit_samples WHERE ts < ?", (now-30*86400,))
                self.db.execute("DELETE FROM meta WHERE key LIKE 'shadow:%' AND substr(key,8) NOT IN (SELECT id FROM seen)")
                self.set_meta("rotation_day", today)
        finally:
            self.db.execute("PRAGMA journal_mode=WAL")
        cutoff = day(now - 30 * 86400)
        for path in self.path.parent.glob(self.path.stem + "-????-??-??.db"):
            date = path.stem[-10:]
            if date < cutoff:
                path.unlink()

    def close(self):
        self.db.close()
