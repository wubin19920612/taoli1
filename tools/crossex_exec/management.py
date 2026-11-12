"""Bounded read projections and separately authenticated local administration."""
from pathlib import Path
import re
import time

from tools.fill_sim.core import dec
from .api_data import readonly, Budget, number, utc
from .settings import Settings, validate
from .storage import Store
from .universe import Universe
from .public_store import PUBLIC_KEYS, public_read


READS = {'config', 'config/audit', 'universe', 'mappings', 'external-positions'}


class WriteConflict(ValueError):
    """Fixed public response, with a separate bounded audit of the precondition."""
    def __init__(self, kind, response, expected, **identity):
        super().__init__(response['error'])
        self.kind, self.response = kind, response
        self.audit = dict(at=time.time(), source='admin_api', **identity, **expected, **response)
        if kind == 'CONFIG_CONFLICT':
            self.audit.update(version=response['current_version'], old={}, new={})


def mapping_multiplier(row):
    legs = sorted(row['legs'], key=lambda leg: leg['market_key'])
    if len(legs) != 2 or any(dec(leg['quantity_multiplier']) <= 0 for leg in legs):
        raise ValueError('mapping_unavailable')
    return format((dec(legs[0]['quantity_multiplier'])/dec(legs[1]['quantity_multiplier'])).normalize(), 'f')


def mapping_view(row):
    return dict(id=row['id'],base=row['base'],status=row['status'],multiplier=mapping_multiplier(row),at=utc(row['at']),ratio=number(row['ratio']),
                legs=[dict(market_key=leg['market_key'],raw_base=leg['raw_base'],
                           quantity_multiplier=number(leg['quantity_multiplier'])) for leg in row['legs']])


def equity_view(row):
    """Project an audit without changing exact Decimal strings in the journal."""
    result=dict(row)
    for key in ('at','captured_at'):
        if key in result:
            result[key]=utc(result[key])
    for name in ('old','new'):
        if name not in result:
            continue
        values=dict(result[name])
        equities=values.get('account_equity_baseline')
        if equities is not None:
            values['account_equity_baseline']={v:number(q) for v,q in equities.items()}
        values['equity_baseline']=number(values.get('equity_baseline'))
        for field,amounts in (('daily_equity',('equity',)),('daily_upnl',('upnl',)),
                             ('risk_pnl_baseline',('total','daily'))):
            raw=values.get(field)
            if raw is None:
                continue
            item=dict(raw)
            for amount in amounts:
                item[amount]=number(item.get(amount))
            if field=='daily_upnl':
                item['captured_at']=utc(item.get('captured_at'))
                item['positions']={k:number(v) for k,v in item.get('positions',{}).items()}
            values[field]=item
        result[name]=values
    return result


def write_route(method, path):
    if method == 'PUT' and path == '/api/v1/config':
        return ('config',)
    if method == 'POST':
        if path in ('/api/v1/external-positions/register','/api/v1/external-positions/unregister'):
            return ('external-positions',path.rsplit('/',1)[1])
        if path == '/api/v1/equity/rebaseline':
            return ('equity','rebaseline')
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
        return [dict(kind=kind, value=raw) for kind, raw in reversed(self.conn.execute(
            "SELECT kind,value FROM events WHERE kind IN ('CONFIG_CHANGED','CONFIG_CONFLICT') ORDER BY id DESC LIMIT 500").fetchall())]


class Management:
    def __init__(self, path, equity_admin=None, external_admin=None):
        self.path = Path(path)
        self.equity_admin=equity_admin
        self.external_admin=external_admin

    def get(self, endpoint):
        budget = Budget()
        with readonly(self.path, budget) as conn:
            conn.execute('BEGIN')
            store = ReadStore(conn, budget)
            if endpoint == 'external-positions':
                from .external import ExternalRegistry
                return dict(ExternalRegistry(store).describe(),income=store.meta('external_income'),
                    flatten_status=store.meta('flatten_status'),projection=store.meta('program_projection'))
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
        if route[0]=='external-positions':
            from .external import checked_identity
            if set(body)!={'identity','expected_status','reason'} or route[1] not in ('register','unregister'):
                raise ValueError('invalid_body')
            checked_identity(body['identity'])
            if self.external_admin is None:
                raise OSError('running_executor_required')
            return self.external_admin(**body,registered=route[1]=='register',source='admin_api')
        if route==('equity','rebaseline'):
            reason=body.get('reason')
            if (set(body)!={'confirm','reason'} or body['confirm'] is not True
                    or not isinstance(reason,str) or not reason.strip() or len(reason)>200
                    or any(ord(c)<32 for c in reason)):
                raise ValueError('invalid_body')
            if self.equity_admin is None:
                raise OSError('running_executor_required')
            return equity_view(self.equity_admin(confirm=True,reason=reason,source='admin_api'))
        with Store(self.path) as store:
            conflict = None
            with store.transaction():
                try:
                    return self._update(store, route, body)
                except WriteConflict as error:
                    # Commit the conflict event, but no mutation. Raising inside
                    # this transaction would incorrectly roll the audit back.
                    store.event(error.kind, error.audit)
                    conflict = error
            if conflict is not None:
                raise conflict
        raise ValueError('unknown_route')

    @staticmethod
    def _update(store, route, body):
        if route == ('config',):
            expected = body.get('expected_version')
            if type(expected) is not int or expected < 0:
                raise ValueError('invalid_expected_version')
            changes = validate({k: v for k, v in body.items() if k != 'expected_version'})
            settings = Settings(store)
            current = settings.read()
            if current['fallback']:
                raise ValueError('config_unreadable')
            if expected != current['version']:
                raise WriteConflict('CONFIG_CONFLICT', dict(error='version_conflict', current_version=current['version']),
                                    dict(expected_version=expected))
            return settings.update(changes, source='admin_api')
        universe = Universe(store)
        expected = body.get('expected_status')
        if route[0] == 'universe':
            _, base, action = route
            if not isinstance(expected, str) or not re.fullmatch(r'u1:[0-9a-f]{64}', expected):
                raise ValueError('invalid_expected_status')
            fields = {k: v for k, v in body.items() if k != 'expected_status'}
            if action not in ('classify', 'exclude', 'unexclude') or (action != 'classify' and fields):
                raise ValueError('invalid_body')
            current = universe.administrative_status(base)
            if expected != current:
                raise WriteConflict('UNIVERSE_CONFLICT', dict(error='status_conflict', current_status=current),
                                    dict(expected_status=expected), base=base, action=action)
            value = universe.classify(base, fields) if action == 'classify' else universe.exclude(base, action == 'exclude')
            return dict(value, status=universe.administrative_status(base))
        if route[0] == 'mappings':
            multiplier = body.get('expected_multiplier')
            if (set(body) != {'expected_status', 'expected_multiplier'}
                    or not isinstance(expected, str) or expected not in ('pending', 'confirmed', 'rejected')
                    or not isinstance(multiplier, str) or not re.fullmatch(r'[0-9.eE+\-]{1,80}', multiplier)):
                raise ValueError('invalid_body')
            row = store.meta('symbol_mappings', {}).get(route[1])
            if not row or not row.get('available', True) or not 0 <= universe.clock()-row['at'] <= 300:
                raise ValueError('mapping_unavailable')
            current = mapping_multiplier(row)
            if expected != row['status'] or multiplier != current:
                raise WriteConflict('MAPPING_CONFLICT', dict(error='mapping_conflict', current_status=row['status'], current_multiplier=current),
                                    dict(expected_status=expected, expected_multiplier=multiplier), id=route[1], action=route[2])
            return mapping_view(universe.decide_mapping(route[1], 'confirmed' if route[2] == 'confirm' else 'rejected'))
        raise ValueError('unknown_route')
