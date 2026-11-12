"""Crash-durable SQLite journal; a lock serializes commits from both IOC workers."""
import json
import sqlite3
import threading
import time
from pathlib import Path
from weakref import WeakValueDictionary
from contextlib import contextmanager
from .security import REDACTOR
from .baseline import cooldown_for, RETENTION_SECONDS
from .public_store import PublicStore, PUBLIC_KEYS


_PATH_LOCKS = WeakValueDictionary()
_PATH_LOCK_GUARD = threading.Lock()


def path_lock(path):
    with _PATH_LOCK_GUARD:
        key = str(Path(path).resolve()).casefold() if str(path) != ':memory:' else object()
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def encode(value):
    return json.dumps(REDACTOR.clean(value), separators=(',',':'), allow_nan=False)


def matches_order(order, *, pair_id=None, states=None, exclude_states=(), sent_since=None,
                  accounting_pending=None, reduce_only=None):
    """Same predicates as SQL, also applied to not-yet-durable observations."""
    return ((pair_id is None or order.get('pair_id') == pair_id)
            and (states is None or order.get('state') in states)
            and order.get('state') not in exclude_states
            and (sent_since is None or order.get('sent_ms', 0) >= sent_since)
            and (accounting_pending is None or bool(order.get('accounting_pending')) == accounting_pending)
            and (reduce_only is None or bool(order.get('reduce_only')) == reduce_only))


class Store(PublicStore):
    def __init__(self, path):
        self.lock = path_lock(path)
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
            CREATE INDEX IF NOT EXISTS orders_pair ON orders(json_extract(value,'$.pair_id'));
            CREATE INDEX IF NOT EXISTS orders_state ON orders(json_extract(value,'$.state'));
            CREATE INDEX IF NOT EXISTS orders_sent ON orders(coalesce(json_extract(value,'$.sent_ms'),0));
            CREATE INDEX IF NOT EXISTS pairs_state ON pairs(json_extract(value,'$.state'));
        ''')
        with self.lock:
            self.initialize_public()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            # Nested journal writes participate in the outer atomic batch. The
            # RLock prevents another thread from borrowing this transaction.
            if self.db.in_transaction:
                yield
                return
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def meta(self, key, default=None):
        if key in PUBLIC_KEYS:
            return self.read_public(key,default)
        with self.lock:
            row = self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        if key in PUBLIC_KEYS:
            with self.lock:
                if self.db.in_transaction:
                    return self.write_public(key,value)
                with self.transaction():
                    return self.write_public(key,value)
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

    def save_orders(self, orders, kind=None):
        """Both records and their audit events commit or roll back together."""
        with self.transaction():
            for order in orders:
                self.save_order(order, kind)

    def _pages(self, table, where, params, page_size):
        if table not in ('orders', 'pairs') or not 1 <= page_size <= 512:
            raise ValueError('invalid_journal_page')
        after = 0
        with self.lock:
            ceiling = self.db.execute('SELECT coalesce(max(rowid),0) FROM '+table).fetchone()[0]
        while after < ceiling:
            with self.lock:
                rows = self.db.execute('SELECT rowid,value FROM '+table+
                    ' WHERE rowid > ? AND rowid <= ? AND '+where+' ORDER BY rowid LIMIT ?',
                    [after, ceiling, *params, page_size]).fetchall()
            if not rows:
                return
            after = rows[-1][0]
            # Decode one record at a time, outside the writer lock. A changing
            # filter cannot shift an OFFSET and silently skip recovery records.
            for row in rows:
                yield json.loads(row[1])

    def iter_orders(self, *, pair_id=None, states=None, exclude_states=(), sent_since=None,
                    accounting_pending=None, reduce_only=None, page_size=128):
        terms, params = [], []
        if pair_id is not None:
            terms.append("json_extract(value,'$.pair_id')=?")
            params.append(pair_id)
        for values, negative in ((states, False), (exclude_states, True)):
            if values is not None and (values or not negative):
                field = "coalesce(json_extract(value,'$.state'),'')" if negative else "json_extract(value,'$.state')"
                terms.append(field+' '+('NOT IN' if negative else 'IN')+
                             '('+','.join('?' for _ in values)+')')
                params.extend(values)
        if sent_since is not None:
            terms.append("coalesce(json_extract(value,'$.sent_ms'),0)>=?")
            params.append(sent_since)
        for field, value in (('accounting_pending', accounting_pending), ('reduce_only', reduce_only)):
            if value is not None:
                terms.append("coalesce(json_extract(value,'$."+field+"'),0)=?")
                params.append(int(value))
        yield from self._pages('orders', ' AND '.join(terms) or '1', params, page_size)

    def all_orders(self, **filters):
        """Explicit materialized export; runtime consumers use iter_orders."""
        return list(self.iter_orders(**filters))

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

    def get_pair(self, pair_id):
        with self.lock:
            row = self.db.execute('SELECT value FROM pairs WHERE id=?', (pair_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def iter_pairs(self, active=False, *, exclude_states=(), page_size=64):
        excluded = tuple(set(exclude_states) | ({'CLOSED','SHADOW','SKIPPED'} if active else set()))
        where = "coalesce(json_extract(value,'$.state'),'') NOT IN ("+','.join('?' for _ in excluded)+')' if excluded else '1'
        yield from self._pages('pairs', where, excluded, page_size)

    def pairs(self, active=False):
        return list(self.iter_pairs(active))

    def claim(self, identity, value):
        with self.lock:
            cur = self.db.execute('INSERT OR IGNORE INTO triggers VALUES (?,?)',(identity,encode(value)))
            return cur.rowcount == 1

    def skip(self, identity, reason, **details):
        """One durable abandonment per trigger, even with two waiting senders."""
        from .admission import SKIP_REASONS
        with self.transaction():
            row = self.db.execute('SELECT value FROM triggers WHERE id=?', (identity,)).fetchone()
            trigger = json.loads(row[0]) if row else {}
            if trigger.get('skip_reason'):
                return
            trigger['skip_reason'] = reason
            self.db.execute('INSERT INTO triggers VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',
                            (identity, encode(trigger)))
            if reason in SKIP_REASONS:
                counts = self.meta('opening_skip_counts', {})
                counts[reason] = counts.get(reason, 0)+1
                self.set_meta('opening_skip_counts', counts)
            self.event('SKIP', dict(id=identity, reason=reason, **details))
