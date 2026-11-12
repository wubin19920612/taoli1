"""Allowlisted projections of execution facts; no engine, adapters or writer Store."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time

from tools.fill_sim.core import Market, walk
from .limits import DEFAULT_VAR, MAX_LEG_USD, MAX_TOTAL_USD, MAX_EQUITY_DROP_USD
from .admission import SKIP_REASONS
from .public_store import public_read
from .settings import SCHEMA, RISK_NAMES


TIERS = ('0.5–1%', '1–2%', '2–5%', '>5%')
EXCHANGES = ('gate', 'binance', 'bybit', 'okx', 'aster', 'bitget')
EXIT_REASONS = {
    'converged': 'converged', 'loss_limit': 'stop_loss', 'adverse_spread': 'stop_loss',
    'max_age': 'max_hold', 'funding_exit': 'pre_funding', 'funding_unknown': 'pre_funding',
    'stale_exit': 'stale', 'one_leg': 'one_leg', 'unhedged_position': 'one_leg',
    'repair_failed': 'one_leg', 'unknown_order': 'one_leg',
    'circuit_breaker': 'halt', 'account_equity_drop': 'halt', 'startup_equity_drop': 'halt',
    'margin_ratio': 'halt', 'shutdown': 'halt',
    **{v: v for v in ('stop_loss', 'max_hold', 'pre_funding', 'stale', 'halt')},
}
REASONS = frozenset((
    'actual_fees_unavailable', 'accounting_pending', 'entry_accounting_unknown',
    'position_mismatch', 'account_snapshot_unavailable', 'account_equity_drop',
    'unknown_order', 'negative_local_position', 'funding_settlement_accounting_required',
    'unhedged_position', 'unknown_close', 'close_incomplete', 'funding_refresh_failed',
    'exit_pricing_unavailable', 'fills_pending', 'cycle_failed_closed',
))


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def utc(value):
    value = number(value)
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z') if value is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def elapsed(start, end):
    start, end = number(start), number(end)
    return max(0, end-start) if start is not None and end is not None else None


def text_field(value):
    # Only short scalar identifiers may be projected, never arbitrary nested data.
    return value if isinstance(value, str) and len(value) <= 128 else None


def reason(value):
    return value if value in REASONS else ('execution_alert' if value else None)


def complete_sum(values):
    values = list(values)
    return sum(values) if all(v is not None for v in values) else None


class Budget:
    """Fail with 503 instead of unbounded history scans or response allocation."""
    def __init__(self):
        self.deadline = time.monotonic()+2
        self.bytes = 0

    def check(self):
        if time.monotonic() > self.deadline:
            raise TimeoutError('api_read_budget')

    def decode(self, raw):
        self.check()
        self.bytes += len(raw.encode('utf-8'))
        if self.bytes > 8*1024*1024:
            raise ValueError('api_read_budget')
        return json.loads(raw)


@contextmanager
def readonly(path, budget=None):
    """mode=ro never creates a DB; authorizer prevents disabling query_only/ATTACH."""
    budget = budget or Budget()
    conn = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True,
                           timeout=0.05, isolation_level=None)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('PRAGMA cache_size=-512')
        conn.execute('PRAGMA temp_store=MEMORY')
        allowed = (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION)
        functions = {'json_extract', 'json_valid', 'coalesce', 'count', 'min', 'max', 'abs'}
        def authorize(action, arg1, arg2, database, source):
            if action in allowed or (action == sqlite3.SQLITE_FUNCTION and arg2 in functions):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        conn.set_authorizer(authorize)
        conn.set_progress_handler(lambda: int(time.monotonic() > budget.deadline), 1000)
        yield conn
    finally:
        conn.close()


def query_options(endpoint, query):
    allowed = {'trades': {'limit', 'before'}, 'stats': {'days'}}.get(endpoint, set())
    if set(query)-allowed:
        raise ValueError('invalid_query')
    result = {}
    for key, default in (('limit', 200), ('days', 7)):
        if key not in allowed:
            continue
        raw = str(query.get(key, default))
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 9 or int(raw) < 1:
            raise ValueError('invalid_query')
        value = int(raw)
        if key == 'days' and value > 365:
            raise ValueError('invalid_query')
        result[key] = min(value, 500) if key == 'limit' else value
    if 'before' in query:
        value = datetime.fromisoformat(query['before'].replace('Z', '+00:00'))
        if value.tzinfo is None:
            raise ValueError('invalid_query')
        result['before'] = value.timestamp()
    return result


def _entries(orders, leg, reduce=False):
    return [o for o in orders if o.get('leg') == leg and o.get('reduce_only') is reduce]


def _vwap(orders):
    if not orders:
        return None, None
    quantity = complete_sum(number(o.get('filled')) for o in orders)
    if any(o.get('accounting_pending') is not False for o in orders):
        return quantity, None
    costs = complete_sum((number(o.get('filled')) * number(o.get('vwap')))
                        if number(o.get('filled')) and number(o.get('vwap')) is not None
                        else (0 if number(o.get('filled')) == 0 else None) for o in orders)
    return quantity, costs/quantity if quantity and costs is not None else None


def _gap(orders, field):
    entry = [_entries(orders, leg) for leg in (0, 1)]
    if any(len(rows) != 1 for rows in entry):
        return None
    a, b = (number(rows[0].get(field)) for rows in entry)
    return abs(a-b) if a is not None and b is not None else None


def _instruments(pair):
    rows = pair.get('instruments', [])
    return [rows[n] if n < len(rows) and isinstance(rows[n], dict) else {} for n in (0, 1)]


def _exchange(instrument):
    value = instrument.get('market', {}).get('exchange')
    return value if value in EXCHANGES else None


def _symbol(pair):
    instruments = _instruments(pair)
    if (all(i.get('mapping_id') and i.get('canonical_base') for i in instruments)
            and instruments[0]['mapping_id']==instruments[1]['mapping_id']
            and instruments[0]['canonical_base']==instruments[1]['canonical_base']):
        return text_field(instruments[0]['canonical_base']+'USDT')
    specs = [i.get('spec', {}) for i in _instruments(pair)]
    if all(s.get('base') and s.get('quote') == 'USDT' for s in specs) and specs[0]['base'] == specs[1]['base']:
        return text_field(specs[0]['base']+'USDT')
    return None


def _spread(a, b):
    return (b/a-1)*10000 if a and b is not None else None


def observations(pair):
    return dict(baseline_bps=number(pair.get('baseline_at_open')),
                excess_bps=number(pair.get('excess_at_open')),
                funding_cost_bps=number(pair.get('fill', {}).get('funding_cost_bps')),
                open_book_skew_ms=number(pair.get('open_book_skew_ms')),
                close_book_skew_ms=number(pair.get('close_book_skew_ms')), **opening_details(pair))


def opening_details(pair):
    config=pair.get('config_at_open') or {}
    version=config.get('version')
    size=pair.get('sizing') or {}
    def pair_numbers(value):
        return [number(v) for v in value] if isinstance(value,list) and len(value)==2 else [None,None]
    sizing=None
    if size:
        sizing=dict(quantity=number(size.get('quantity')),depth_usd=pair_numbers(size.get('depth_usd')),
            oi_usd=pair_numbers(size.get('oi_usd')),remaining_total_usd=number(size.get('remaining_total_usd')),
            budget_usd=number(size.get('budget_usd')),
            constraints={k:number(size.get('constraints',{}).get(k)) for k in ('order_usd_max','depth','oi','remaining_total')},
            limiting=size.get('limiting') if size.get('limiting') in ('order_usd_max','depth','oi','remaining_total') else None)
    return dict(config_version=version if type(version) is int else None,
                config_at_open={k:number(v) for k,v in config.get('values',{}).items() if k in SCHEMA} or None,
                sizing=sizing,turnovers_usd=pair_numbers(pair.get('turnovers')))


def denomination(instrument):
    value=number(instrument.get('quantity_multiplier','1'))
    return value if value is not None and value>0 else None


def native_fields(instrument, qty, price):
    m=denomination(instrument)
    market=instrument.get('market',{})
    return dict(raw_symbol=text_field(market.get('raw_symbol')),native_base=text_field(instrument.get('spec',{}).get('base')),
                quantity_multiplier=m,mapping_id=text_field(instrument.get('mapping_id')),
                native_qty=qty/m if qty is not None and m else None,
                native_entry_price=price*m if price is not None and m else None)


def position(pair, orders, now):
    legs, prices, quantities = [], [], pair.get('remaining', [None, None])
    for n, instrument in enumerate(_instruments(pair)):
        _, price = _vwap(_entries(orders, n))
        multiplier=denomination(instrument)
        price=price/multiplier if price is not None and multiplier else None
        qty = number(quantities[n]) if len(quantities) > n else None
        exchange = _exchange(instrument)
        legs.append(dict(exchange=exchange, route=('bitget' if exchange == 'bitget' else 'crossex') if exchange else None,
                         side='long' if n == 0 else 'short', qty=qty, entry_price=price,
                         notional_usd=qty*price if qty is not None and price is not None else None,
                         mark_price=None, **native_fields(instrument,qty,price)))  # Journal books are common units.
        prices.append(price)
    entry, spread, pnl, pnl_bps = _spread(*prices), None, None, None
    try:
        books = pair['last_books']
        if len(books) == 2 and all(0 <= now-number(b['received_at']) < 60 for b in books):
            exits = [float(walk(books[n]['bids' if n == 0 else 'asks'], quantity=legs[n]['qty'])['vwap']) for n in (0, 1)]
            spread = _spread(*exits)
            if all(p is not None for p in prices):
                pnl = sum(legs[n]['qty']*(exits[n]-prices[n])*(1 if n == 0 else -1) for n in (0, 1))
                notional = legs[0]['notional_usd']
                pnl_bps = pnl/notional*10000 if notional else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        pass
    state = pair.get('state')
    status = {'OPENING': 'opening', 'OPEN': 'open', 'CLOSING': 'closing', 'UNKNOWN': 'one_leg'}.get(state)
    if state == 'OPEN' and legs[0]['qty'] != legs[1]['qty']:
        status = 'one_leg'
    funding = [number(f.get('next')) for f in pair.get('funding', [])]
    next_funding = min(funding) if len(funding) == 2 and all(v is not None for v in funding) else None
    opened = number(pair.get('opened_at'))
    return dict(id=text_field(pair.get('id')), symbol=_symbol(pair), opened_at=utc(opened),
                held_seconds=elapsed(opened, now), legs=legs, entry_spread_bps=entry,
                current_spread_bps=spread, unrealized_usd=pnl, unrealized_bps=pnl_bps,
                next_funding_at=utc(next_funding),
                stop_distance_bps=entry+50-spread if entry is not None and spread is not None else None,
                max_hold_at=utc(opened+7200) if opened is not None else None, status=status, **observations(pair))


def trade(pair, orders, simulation=None):
    legs = []
    for n, instrument in enumerate(_instruments(pair)):
        qty, entry = _vwap(_entries(orders, n))
        _, exit_price = _vwap(_entries(orders, n, True))
        multiplier=denomination(instrument)
        qty=qty*multiplier if qty is not None and multiplier else None
        entry=entry/multiplier if entry is not None and multiplier else None
        exit_price=exit_price/multiplier if exit_price is not None and multiplier else None
        legs.append(dict(exchange=_exchange(instrument), side='long' if n == 0 else 'short',
                         entry_vwap=entry, exit_vwap=exit_price, qty=qty,
                         native_exit_vwap=exit_price*multiplier if exit_price is not None and multiplier else None,
                         **native_fields(instrument,qty,entry)))
    costs = []
    for order in orders:
        qty = number(order.get('filled'))
        costs.append(number(order.get('fee')) if order.get('accounting_pending') is False
                     and qty is not None and qty > 0 else (0 if qty == 0 and order.get('accounting_pending') is False else None))
    fees = complete_sum(costs) if orders else None
    funding = 0 if pair.get('funding_known') is True else None
    net = number(pair.get('net_usd')) if pair.get('accounting_pending') is False and funding is not None and fees is not None else None
    notional = legs[0]['qty']*legs[0]['entry_vwap'] if legs[0]['qty'] and legs[0]['entry_vwap'] else None
    bps = net/notional*10000 if net is not None and notional else None
    exchanges = [l['exchange'] for l in legs]
    sim = simulation['result'] if simulation else None
    return dict(id=text_field(pair.get('id')), symbol=_symbol(pair),
                pair='|'.join(sorted(exchanges)) if all(exchanges) else None,
                opened_at=utc(pair.get('opened_at')), closed_at=utc(pair.get('closed_at')),
                held_seconds=elapsed(pair.get('opened_at'), pair.get('closed_at')), legs=legs,
                entry_spread_bps=_spread(legs[0]['entry_vwap'], legs[1]['entry_vwap']),
                fees_usd=fees, funding_usd=funding, net_usd=net, net_bps=bps,
                exit_reason=EXIT_REASONS.get(pair.get('exit_reason')),
                sim=sim, slippage_bps=bps-sim['net_bps'] if bps is not None and sim and sim['net_bps'] is not None else None,
                leg_send_gap_ms=_gap(orders, 'sent_ms'), leg_fill_gap_ms=_gap(orders, 'fill_ms'),
                one_leg_incident=pair.get('single_failure') if isinstance(pair.get('single_failure'), bool) else None,
                **observations(pair))


def _sim_projection(row, pair):
    if (row.get('event_id') != pair.get('trigger_id') or row.get('variant') != 'measured'
            or row.get('amount') != 100 or number(row.get('closed_at')) is None):
        return None
    try:
        if [Market.from_payload(m) for m in row['markets']] != [Market.from_payload(i['market']) for i in _instruments(pair)]:
            return None
        entry = row['fill']
        a, b, c, d = (number(v) for v in (entry['buy']['notional'], entry['sell']['notional'],
                                         row.get('close_buy', {}).get('notional'), row.get('close_sell', {}).get('notional')))
        gross = (c-a+b-d)/a*10000 if a and all(v is not None for v in (b, c, d)) else None
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    return dict(result=dict(net_bps=number(row.get('net_bps')) if row.get('funding_known') is True else None,
                            gross_bps=gross),
                tier=row.get('spread_tier') if row.get('spread_tier') in TIERS else None)


def simulations(path, pairs, budget):
    if not path or not pairs:
        return {}
    # fill-sim rotates whole event families into adjacent daily archives.
    path = Path(path)
    wanted = {p['trigger_id']+':measured:100': p for p in pairs if isinstance(p.get('trigger_id'), str)}
    result, seen = {}, set()
    try:
        paths = [path] + sorted(path.parent.glob(path.stem+'-????-??-??.db'))
        for source in paths:
            budget.check()
            try:
                with readonly(source, budget) as conn:
                    ids = list(wanted)
                    for start in range(0, len(ids), 500):
                        chunk = ids[start:start+500]
                        sql = 'SELECT id,event_id,data FROM positions WHERE closed_at IS NOT NULL AND id IN ('+','.join('?' for _ in chunk)+')'
                        for identity, event_id, raw in conn.execute(sql, chunk):
                            pair = wanted[identity]
                            if event_id != pair['trigger_id']:
                                continue
                            projection = _sim_projection(budget.decode(raw), pair)
                            key = pair['id']
                            if key in seen:
                                result[key] = None  # Ambiguous duplicates never select a convenient result.
                            else:
                                seen.add(key)
                                result[key] = projection
            except (OSError, sqlite3.Error, ValueError, TypeError):
                continue
    except (OSError, TimeoutError):
        return {}  # Optional data cannot make the execution endpoints unavailable.
    return result


def _totals(rows):
    return dict(net_usd=complete_sum(r['net_usd'] for r in rows),
                fees_usd=complete_sum(r['fees_usd'] for r in rows),
                funding_usd=complete_sum(r['funding_usd'] for r in rows), trades=len(rows),
                wins=sum(r['net_usd'] > 0 for r in rows) if all(r['net_usd'] is not None for r in rows) else None)


def percentile(values, fraction):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    index = (len(values)-1)*fraction
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo]+(values[hi]-values[lo])*(index-lo)


def statistics(rows, tiers, days):
    histogram = [dict(lo=lo, hi=lo+25, count=0) for lo in range(-200, 200, 25)]
    for row in rows:
        if row['net_bps'] is not None:
            histogram[max(0, min(15, math.floor((row['net_bps']+200)/25)))]['count'] += 1
    def groups(field):
        grouped = defaultdict(list)
        for row in rows:
            grouped[tiers.get(row['id']) if field == 'tier' else row[field]].append(row)
        result = []
        for key, items in sorted(grouped.items(), key=lambda item: item[0] or ''):
            known = [r['net_bps'] for r in items if r['net_bps'] is not None]
            result.append(dict(key=key, n=len(items), net_usd=complete_sum(r['net_usd'] for r in items),
                               mean_bps=sum(known)/len(known) if len(known) == len(items) else None,
                               win_rate=sum(v > 0 for v in known)/len(known) if len(known) == len(items) else None))
        return result
    slips = [r['slippage_bps'] for r in rows]
    gaps = [r['leg_fill_gap_ms'] for r in rows]
    incidents = [r['one_leg_incident'] for r in rows]
    return dict(days=days, n=len(rows), histogram=histogram,
                by_symbol=groups('symbol'), by_pair=groups('pair'), by_exit_reason=groups('exit_reason'),
                by_spread_tier=groups('tier'), vs_sim=dict(
                    n=sum(v is not None for v in slips), slippage_bps_p50=percentile(slips, .5),
                    # Signed live-minus-sim: the adverse 90% tail is the lower decile.
                    slippage_bps_p90=percentile(slips, .1), leg_fill_gap_ms_p50=percentile(gaps, .5),
                    leg_fill_gap_ms_p90=percentile(gaps, .9),
                    one_leg_rate=sum(incidents)/len(incidents) if incidents and all(v is not None for v in incidents) else None))


class JournalView:
    def __init__(self, path, config, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.sim_path = config.get('FILL_SIM_DB')
        self.stop_file = Path(config.get('CROSSEX_STOP_FILE', DEFAULT_VAR+'/STOP'))
        self.mode = 'live' if config.get('CROSSEX_LIVE_ENABLED', '0') == '1' else 'shadow'
        self.caps = {}
        for name, env, ceiling in (('leg_max_usd', 'CROSSEX_MAX_LEG_USD', MAX_LEG_USD),
                                  ('total_max_usd', 'CROSSEX_MAX_TOTAL_USD', MAX_TOTAL_USD),
                                  ('equity_drop_max_usd', 'CROSSEX_MAX_EQUITY_DROP_USD', MAX_EQUITY_DROP_USD)):
            value = number(config.get(env, ceiling))
            if value is None or not 0 < value <= float(ceiling):
                raise ValueError('invalid_api_caps')
            self.caps[name] = value

    def get(self, endpoint, query):
        options, now, budget = query_options(endpoint, query), self.clock(), Budget()
        where, params, suffix = '1', [], ''
        if endpoint == 'summary':
            where = "coalesce(json_extract(value,'$.state'),'') NOT IN ('SHADOW','SKIPPED')"
        elif endpoint == 'positions':
            where = "json_extract(value,'$.state') NOT IN ('CLOSED','SHADOW','SKIPPED')"
        elif endpoint in ('trades', 'stats'):
            where = "json_extract(value,'$.state')='CLOSED'"
            if endpoint == 'trades':
                if 'before' in options:
                    where += " AND json_extract(value,'$.closed_at') < ?"
                    params.append(options['before'])
                suffix = " ORDER BY json_extract(value,'$.closed_at') DESC,id DESC LIMIT ?"
                params.append(options['limit'])
            else:
                where += " AND json_extract(value,'$.closed_at') >= ? AND json_extract(value,'$.closed_at') <= ?"
                params.extend([now-options['days']*86400, now])
        rows, active, closed, sim_pairs = [], [], [], []
        with readonly(self.path, budget) as conn:
            conn.execute('BEGIN')
            # Stream one selected pair and its orders at a time. Keep only the
            # public projection, never a second full historical journal in RAM.
            for (raw,) in conn.execute('SELECT value FROM pairs WHERE '+where+suffix, params):
                pair = budget.decode(raw)
                orders = [budget.decode(value) for (value,) in conn.execute(
                    "SELECT value FROM orders WHERE json_extract(value,'$.pair_id')=? ORDER BY rowid", (pair['id'],))]
                if endpoint == 'summary':
                    if pair.get('state') == 'CLOSED':
                        row = trade(pair, orders)
                        closed.append({k: row[k] for k in ('closed_at','net_usd','fees_usd','funding_usd')})
                    else:
                        active.append(position(pair, orders, now))
                elif endpoint == 'positions':
                    rows.append(position(pair, orders, now))
                else:
                    rows.append(trade(pair, orders))
                    sim_pairs.append({k: pair.get(k) for k in ('id','trigger_id','instruments')})
            if endpoint == 'summary':
                meta = {key: budget.decode(raw) for key, raw in conn.execute(
                    "SELECT key,value FROM meta WHERE key IN ('state','stop_reason','live_mode','account_equity_baseline',"
                    "'account_equity_current','actual_fees','flatten_requested','baseline_status','pair_cooldowns','opening_skip_counts','runtime_config')")}
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='baseline_state'").fetchone():
                    meta['baseline_status']=public_read(conn,'baseline_status',budget=budget)
                events = {}
                for kind in ('RECONCILE', 'ALERT'):
                    row = conn.execute('SELECT at,value FROM events WHERE kind=? ORDER BY id DESC LIMIT 1', (kind,)).fetchone()
                    events[kind] = (row[0], budget.decode(row[1])) if row else (None, {})
        # Release the execution DB snapshot before optional simulation reads / JSON work.
        if endpoint == 'positions':
            return rows
        if endpoint == 'summary':
            return self.summary(active, closed, meta, events, now)
        sim = simulations(self.sim_path, sim_pairs, budget)
        for row in rows:
            result = sim.get(row['id'])
            row['sim'] = result['result'] if result else None
            if row['net_bps'] is not None and row['sim'] and row['sim']['net_bps'] is not None:
                row['slippage_bps'] = row['net_bps']-row['sim']['net_bps']
        if endpoint == 'trades':
            return rows
        if endpoint == 'stats':
            return statistics(rows, {key: value['tier'] for key, value in sim.items() if value}, options['days'])
        raise ValueError('unknown_endpoint')

    def summary(self, active, closed, meta, events, now):
        today = [r for r in closed if r['closed_at'] and r['closed_at'][:10] == utc(now)[:10]]
        at, reconcile = events['RECONCILE']
        fresh = at is not None and 0 <= now-at < 60
        stopped = reason(meta.get('stop_reason'))
        accounts = {}
        for n, venue in enumerate(('crossex', 'bitget')):
            current = number((meta.get('account_equity_current') or {}).get(venue))
            baseline = number((meta.get('account_equity_baseline') or {}).get(venue))
            margins = reconcile.get('margins', [])
            margin = number(margins[n]) if len(margins) > n else None
            ok = fresh and current is not None and margin is not None and stopped != 'account_snapshot_unavailable'
            accounts[venue] = dict(equity_usd=current, baseline_usd=baseline,
                drop_usd=max(0, baseline-current) if current is not None and baseline is not None else None,
                margin_multiple=margin if fresh and margin is not None and margin < 1e30 else None,
                ok=ok if at is not None or current is not None else None,
                error=None if ok else ('account_snapshot_stale' if at is not None and not fresh else 'account_snapshot_unavailable'))
        rates = meta.get('actual_fees') or {}
        fees = dict(crossex_taker=None, bitget_taker=None)
        fee_at = number(rates.get('at'))
        if fee_at is not None and 0 <= now-fee_at <= 3600:
            for venue in ('crossex', 'bitget'):
                values = [number(v) for k, v in rates.get('rates', {}).items()
                          if k.split('|')[0] in (('bitget',) if venue == 'bitget' else EXCHANGES[:-1])]
                if values and None not in values and len(set(values)) == 1 and 0 <= values[0] < .02:
                    fees[venue+'_taker'] = values[0]
        state, stop = meta.get('state'), self.stop_file.exists()
        halted = state in ('HALTING', 'HALTED')
        halt_reason = (stopped or state.lower()) if halted else None
        paused = halt_reason or ('STOP' if stop else stopped)
        if not paused and (not fresh or reconcile.get('risk') != 'RUN' or meta.get('flatten_requested')):
            paused = 'account_snapshot_stale' if not fresh else 'risk_stopped'
        baselines = list((meta.get('baseline_status') or {}).values())
        ready = sum(s.get('ready') is True and number(s.get('latest_at')) is not None
                    and 0 <= now-number(s['latest_at']) <= 90 and 0 <= now-s['at'] <= 90 for s in baselines)
        blacklisted = sum(s.get('blocked') is True for s in baselines)
        cooling = sum(number(until) is not None and number(until) > now
                      for until in (meta.get('pair_cooldowns') or {}).values())
        current={k:r['default'] for k,r in SCHEMA.items()} | (meta.get('runtime_config') or {}).get('values',{})
        frozen=[p.get('config_at_open') or {} for p in active]
        risk={k:min(v.get(k,SCHEMA[k]['default']) for v in (frozen or [current])) for k in RISK_NAMES}
        return dict(generated_at=utc(now), mode=('live' if meta['live_mode'] else 'shadow')
                    if isinstance(meta.get('live_mode'), bool) else self.mode,
                    halted=halted, halt_reason=halt_reason, stop_file=stop, opening_paused_reason=paused,
                    caps=dict(self.caps, max_pairs=3, open_pairs=len(active),
                              total_open_usd=complete_sum(l['notional_usd'] for p in active for l in p['legs'])),
                    config_version=(meta.get('runtime_config') or {}).get('version',0),
                    effective_risk_limits=risk,
                    accounts=accounts, today=dict(_totals(today), daily_loss_limit_usd=risk['daily_loss_stop_usd']),
                    total=dict(_totals(closed), total_loss_limit_usd=risk['total_loss_stop_usd']), fees=fees,
                    baseline_ready_pairs=ready,structural_spread_pairs=blacklisted,cooldown_pairs=cooling,
                    opening_skip_counts={r:meta.get('opening_skip_counts',{}).get(r,0) for r in SKIP_REASONS},
                    last_error=reason(events['ALERT'][1].get('reason')))
