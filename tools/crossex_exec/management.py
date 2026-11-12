"""Bounded read projections and separately authenticated local administration."""
from pathlib import Path
import re

from .api_data import readonly, Budget, number, utc
from .settings import Settings
from .storage import Store
from .universe import Universe
from .public_store import PUBLIC_KEYS, public_read


READS = {'config', 'config/audit', 'universe', 'mappings'}


def mapping_view(row):
    return dict(id=row['id'],base=row['base'],status=row['status'],at=utc(row['at']),ratio=number(row['ratio']),
                legs=[dict(market_key=leg['market_key'],raw_base=leg['raw_base'],
                           quantity_multiplier=number(leg['quantity_multiplier'])) for leg in row['legs']])


def write_route(method, path):
    if method == 'PUT' and path == '/api/v1/config':
        return ('config',)
    if method == 'POST':
        match = re.fullmatch('/api/v1/universe/([A-Z0-9]{1,40})/(exclude|unexclude|classify)', path)
        if match:
            return ('universe', *match.groups())
        match = re.fullmatch('/api/v1/mappings/([0-9a-f]{24})/(confirm|reject)', path)
        if match:
            return ('mappings', *match.groups())
    return None


class ReadStore:
    def __init__(self, conn, budget):
        self.conn, self.budget, self.cache = conn, budget, {}

    def meta(self, key, default=None):
        if key not in self.cache:
            if key in PUBLIC_KEYS:
                self.cache[key] = public_read(self.conn,key,default,budget=self.budget)
                return self.cache[key]
            row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            self.cache[key] = self.budget.decode(row[0]) if row else default
        return self.cache[key]

    def events(self):
        return [dict(kind='CONFIG_CHANGED', value=raw) for (raw,) in reversed(self.conn.execute(
            "SELECT value FROM events WHERE kind='CONFIG_CHANGED' ORDER BY id DESC LIMIT 500").fetchall())]


class Management:
    def __init__(self, path):
        self.path = Path(path)

    def get(self, endpoint):
        budget = Budget()
        with readonly(self.path, budget) as conn:
            conn.execute('BEGIN')
            store = ReadStore(conn, budget)
            if endpoint == 'config':
                return Settings(store).describe()
            if endpoint == 'config/audit':
                return [dict(row,at=utc(row['at'])) for row in Settings(store).audit()]
            if endpoint == 'universe':
                rows=Universe(store).describe()
                for row in rows:
                    row['markets']=[dict(m,at=utc(m['at']),volume_usd=number(m['volume_usd'])) for m in row['markets']]
                return rows
            if endpoint == 'mappings':
                return [mapping_view(row) for row in Universe(store).mappings()]
        raise ValueError('unknown_endpoint')

    def write(self, route, body):
        if not self.path.is_file():
            raise OSError('database_unavailable')
        if not isinstance(body, dict):
            raise ValueError('invalid_body')
        with Store(self.path) as store:
            if route == ('config',):
                return Settings(store).update(body, source='admin_api')
            universe = Universe(store)
            if route[0] == 'universe':
                _, base, action = route
                if action == 'classify':
                    return universe.classify(base, body)
                if body:
                    raise ValueError('invalid_body')
                return universe.exclude(base, action == 'exclude')
            if route[0] == 'mappings':
                if body:
                    raise ValueError('invalid_body')
                return mapping_view(universe.decide_mapping(route[1], 'confirmed' if route[2] == 'confirm' else 'rejected'))
        raise ValueError('unknown_route')
