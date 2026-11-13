"""Disposable, byte-budgeted exact windows. SQLite remains the source of truth."""
from collections import OrderedDict
from array import array
from dataclasses import dataclass
from decimal import getcontext
import threading


CACHE_BYTES = 256*1024*1024
CACHE_ENTRIES = 8192
MAX_WINDOW_BYTES = 128*1024


def arithmetic_context():
    context = getcontext()
    return (context.prec, context.rounding, context.Emin, context.Emax, context.capitals,
            context.clamp, tuple(k for k, v in context.traps.items() if v))


@dataclass(frozen=True)
class Window:
    since: float
    until: float
    revision: tuple
    payload: bytes
    stats: dict
    context: tuple
    charge: int

    def rows(self):
        count = int.from_bytes(self.payload[:2], 'little')
        stamps = array('d')
        stamps.frombytes(self.payload[2:2+count*8])
        values = self.payload[2+count*8:].decode('ascii').split('\n') if count else ()
        return list(zip(stamps, values))


class WindowCache:
    def __init__(self, budget=CACHE_BYTES):
        self.budget = budget
        self.bytes = 0
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                self.entries.move_to_end(key)
            return value

    def put(self, key, since, until, revision, rows, stats):
        # SQLite REAL timestamps are already doubles. Bps retain their exact
        # Decimal text/scale; only one window is ever decoded into Python rows.
        if not rows:
            # Catalog combinations without observations must not evict useful
            # windows; their DB read and empty statistics are already cheap.
            with self.lock:
                old = self.entries.pop(key, None)
                if old is not None:
                    self.bytes -= old.charge
            return
        if 2+9*len(rows)+sum(len(r[1]) for r in rows) > MAX_WINDOW_BYTES:
            return
        if (any(not r[1].isascii() or '\n' in r[1] for r in rows)
                or any(a[0] >= b[0] for a, b in zip(rows, rows[1:]))):
            return  # Noncanonical imported data still uses the exact DB path.
        stamps = array('d', (r[0] for r in rows)).tobytes()
        values = '\n'.join(r[1] for r in rows).encode('ascii')
        payload = len(rows).to_bytes(2, 'little')+stamps+values
        # Conservatively charge key, entry, OrderedDict node, statistics, and
        # allocator overhead as well as the payload. The entry limit is separate.
        charge = len(payload)+4096+4*len(key)
        if charge > self.budget:
            return
        entry = Window(since, until, revision, payload, dict(stats), arithmetic_context(), charge)
        with self.lock:
            old = self.entries.pop(key, None)
            if old is not None:
                self.bytes -= old.charge
            while self.entries and (self.bytes+charge > self.budget or len(self.entries) >= CACHE_ENTRIES):
                _, old = self.entries.popitem(last=False)
                self.bytes -= old.charge
            self.entries[key] = entry
            self.bytes += charge
