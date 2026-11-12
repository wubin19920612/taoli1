"""Typed public observations: no credential-bearing payloads, JSON blobs or redactor.

The three old meta keys remain compatibility projections. Production sampling
updates individual state rows; private journal/event writes still use encode().
"""
import json
from tools.fill_sim.core import Market, dec
from .baseline import BATCH_URLS, pair_key

PUBLIC_KEYS = frozenset(('baseline_status', 'baseline_quotes', 'market_tickers'))
STATE_COLUMNS = ('key', 'baseline_bps', 'mad_bps', 'count', 'first_at', 'latest_at',
                 'ready', 'blocked', 'checked_at', 'at')


def state_row(row):
    result = dict(zip(STATE_COLUMNS, row))
    result.update(ready=bool(result['ready']), blocked=bool(result['blocked']))
    return result


def public_read(conn, key, default=None, budget=None):
    def rows(sql):
        for row in conn.execute(sql):
            if budget is not None:
                budget.check()
                budget.bytes += sum(len(str(v).encode('utf-8')) for v in row)
                if budget.bytes > 8*1024*1024:
                    raise ValueError('api_read_budget')
            yield row
    if key == 'baseline_status':
        return {r[0]:state_row(r) for r in rows('SELECT * FROM baseline_state')}
    if key == 'baseline_quotes':
        stamp = conn.execute('SELECT at FROM public_round WHERE name=?', (key,)).fetchone()
        if stamp is None:
            return default
        mids = {}
        for ex,base,mid in rows('SELECT exchange,base,mid FROM baseline_quotes'):
            mids.setdefault(ex,{})[base] = mid
        return dict(at=stamp[0],mids=mids)
    if key == 'market_tickers':
        return {r[0]:dict(market_key=r[0],mid=r[1],volume=r[2],at=r[3],received_at=r[4],
                         volume_basis=r[5],quote='USDT') for r in rows('SELECT * FROM market_tickers')}
    raise ValueError('unknown_public_table')


class PublicStore:
    def initialize_public(self):
        self.db.executescript('''
            PRAGMA cache_size=-4096;
            CREATE TABLE IF NOT EXISTS public_round (name TEXT PRIMARY KEY, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS baseline_quotes (
                exchange TEXT NOT NULL, base TEXT NOT NULL, mid TEXT NOT NULL, PRIMARY KEY(exchange,base));
            CREATE INDEX IF NOT EXISTS baseline_quotes_base ON baseline_quotes(base);
            CREATE TABLE IF NOT EXISTS baseline_state (
                key TEXT PRIMARY KEY, baseline_bps TEXT, mad_bps TEXT, count INTEGER NOT NULL,
                first_at REAL, latest_at REAL, ready INTEGER NOT NULL, blocked INTEGER NOT NULL,
                checked_at REAL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS market_tickers (
                market_key TEXT PRIMARY KEY, mid TEXT NOT NULL, volume TEXT NOT NULL,
                at REAL NOT NULL, received_at REAL NOT NULL, volume_basis TEXT NOT NULL);
        ''')
        # Migrate only existing legacy rows, atomically. Never erase structural
        # latches on restart and never re-import an old blob over newer rows.
        with self.transaction():
            for key in PUBLIC_KEYS:
                row = self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                if row:
                    self.write_public(key,json.loads(row[0]))
                    self.db.execute('DELETE FROM meta WHERE key=?',(key,))

    def read_public(self, key, default=None):
        with self.lock:
            return public_read(self.db,key,default)

    def write_public(self, key, value):
        # Caller holds transaction/lock. All persisted fields are allowlisted and
        # numerically normalized; arbitrary fields and remote text are discarded.
        if key == 'baseline_status':
            self.db.execute('DELETE FROM baseline_state')
            for identity,state in value.items():
                self.save_baseline_state(dict(state,key=identity))
        elif key == 'baseline_quotes':
            rows = []
            for ex,values in value['mids'].items():
                if ex not in BATCH_URLS:
                    raise ValueError('invalid_public_exchange')
                for base,mid in values.items():
                    pair_key(base,'binance','bitget')
                    mid = dec(mid)
                    if mid <= 0:
                        raise ValueError('invalid_public_mid')
                    rows.append((ex,base,str(mid)))
            stamp = float(dec(value['at']))
            self.db.execute('DELETE FROM baseline_quotes')
            self.db.executemany('INSERT INTO baseline_quotes VALUES (?,?,?)', rows)
            self.db.execute('INSERT OR REPLACE INTO public_round VALUES (?,?)',(key,stamp))
        elif key == 'market_tickers':
            rows = []
            for identity,q in value.items():
                ex,kind,raw = identity.split('|')
                Market.from_payload(dict(exchange=ex,market_type=kind,raw_symbol=raw))
                mid,volume = dec(q['mid']),dec(q['volume'])
                if kind != 'future' or mid <= 0 or volume < 0:
                    raise ValueError('invalid_public_ticker')
                basis = q.get('volume_basis','quote_volume')
                if basis not in ('quote_volume','base_times_low24h_lower_bound'):
                    raise ValueError('invalid_volume_basis')
                rows.append((identity,str(mid),str(volume),float(dec(q['at'])),
                             float(dec(q.get('received_at',q['at']))),basis))
            self.db.execute('DELETE FROM market_tickers')
            self.db.executemany('INSERT INTO market_tickers VALUES (?,?,?,?,?,?)',rows)
        else:
            raise ValueError('unknown_public_table')

    def baseline_state(self, key):
        with self.lock:
            row = self.db.execute('SELECT * FROM baseline_state WHERE key=?',(key,)).fetchone()
            return state_row(row) if row else {}

    def save_baseline_state(self, state):
        base,a,b = state['key'].split('|')
        if pair_key(base,a,b) != state['key']:
            raise ValueError('baseline_identity_invalid')
        row = [state['key']]
        for name in STATE_COLUMNS[1:]:
            value = state[name]
            if name in ('ready','blocked'):
                if type(value) is not bool:
                    raise ValueError('invalid_public_state')
                value = int(value)
            elif value is not None:
                number = dec(value)
                value = str(number) if name in ('baseline_bps','mad_bps') else float(number)
            row.append(value)
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO baseline_state VALUES (?,?,?,?,?,?,?,?,?,?)',row)

    def prune_baseline_states(self, bases):
        bases = set(bases)
        with self.lock:
            removed = [(r[0],) for r in self.db.execute('SELECT key FROM baseline_state') if r[0].split('|')[0] not in bases]
            self.db.executemany('DELETE FROM baseline_state WHERE key=?',removed)

    def latest_baseline(self, base):
        with self.lock:
            stamp = self.db.execute("SELECT at FROM public_round WHERE name='baseline_quotes'").fetchone()
            return dict(at=stamp[0] if stamp else None, mids=dict(self.db.execute(
                'SELECT exchange,mid FROM baseline_quotes WHERE base=?',(base,))))
