"""Crash-durable SQLite journal; a lock serializes commits from both IOC workers."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from .security import REDACTOR
from .baseline import cooldown_for, RETENTION_SECONDS


def encode(value):
    return json.dumps(REDACTOR.clean(value), separators=(',',':'), allow_nan=False)


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (cid TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pairs (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS triggers (id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL NOT NULL, cid TEXT, kind TEXT, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS baseline_samples (
                pair_key TEXT NOT NULL, bucket INTEGER NOT NULL, at REAL NOT NULL, bps TEXT NOT NULL,
                PRIMARY KEY(pair_key,bucket));
            CREATE INDEX IF NOT EXISTS baseline_samples_at ON baseline_samples(at);
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def meta(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.lock:
            self.db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,encode(value)))

    def assert_startable(self):
        if self.meta('state') == 'HALTED':
            raise ValueError('HALTED_manual_clear_required')

    def event(self, kind, value=None, cid=None):
        with self.lock:
            self.db.execute('INSERT INTO events(at,cid,kind,value) VALUES (?,?,?,?)',(time.time(),cid,kind,encode(value or {})))

    def events(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY id')]

    def order(self, cid):
        with self.lock:
            row = self.db.execute('SELECT value FROM orders WHERE cid=?',(cid,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_order(self, order, kind=None):
        with self.transaction():
            self.db.execute('INSERT INTO orders VALUES (?,?) ON CONFLICT(cid) DO UPDATE SET value=excluded.value',(order['cid'],encode(order)))
            if kind:
                self.event(kind,order,order['cid'])

    def all_orders(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT value FROM orders')]

    def pair(self, value):
        with self.transaction():
            self.db.execute('INSERT INTO pairs VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(value['id'],encode(value)))
            cooldown = cooldown_for(value)
            if cooldown:
                key, until = cooldown
                rows = self.meta('pair_cooldowns', {})
                if until > rows.get(key, 0):
                    rows[key] = until
                    self.set_meta('pair_cooldowns', rows)

    def add_baseline_samples(self, rows, now):
        with self.transaction():
            self.db.executemany('INSERT OR IGNORE INTO baseline_samples VALUES (?,?,?,?)', rows)
            self.db.execute('DELETE FROM baseline_samples WHERE at < ?', (now-RETENTION_SECONDS,))

    def baseline_samples(self, key, since, until):
        with self.lock:
            return [(r[0],r[1]) for r in self.db.execute(
                'SELECT at,bps FROM baseline_samples WHERE pair_key=? AND at>=? AND at<=? ORDER BY bucket',
                (key,since,until))]

    def pairs(self, active=False):
        with self.lock:
            values = [json.loads(r[0]) for r in self.db.execute('SELECT value FROM pairs')]
        return [p for p in values if p['state'] not in ('CLOSED','SHADOW','SKIPPED')] if active else values

    def claim(self, identity, value):
        with self.lock:
            cur = self.db.execute('INSERT OR IGNORE INTO triggers VALUES (?,?)',(identity,encode(value)))
            return cur.rowcount == 1
