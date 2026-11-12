"""Versioned opening settings. Changes and their audit commit together."""
import copy
import json
import math
import time


def rule(default, minimum, maximum=None, integer=False):
    return dict(default=default, min=minimum, max=maximum, integer=integer)


SCHEMA = {
    'min_quote_volume_usd': rule(1000000, 100000),
    'min_venues_with_volume': rule(2, 2, integer=True),
    'sampling_min_quote_volume_usd': rule(200000, 0),
    'order_usd_max': rule(50, 5, 150),
    'depth_fraction': rule(.20, .01, .50),
    'oi_fraction': rule(.0005, .00001, .005),
    'max_total_open_usd': rule(600, 0, 600),
    'max_open_pairs': rule(3, 0, 3, integer=True),
    'max_equity_drop_usd': rule(150, 10, 150),
    'daily_loss_stop_usd': rule(20, 1, 20),
    'total_loss_stop_usd': rule(100, 1, 100),
    'min_excess_bps': rule(15, 15),
    'max_book_skew_ms': rule(300, 0, 300),
    'max_book_age_seconds': rule(1, 0, 1),
    'max_funding_diff_bps_h': rule(.5, 0, .5),
    'max_funding_rate_8h': rule(.0005, 0, .0005),
    'max_baseline_bps': rule(10, 0, 10),
    'max_baseline_mad_bps': rule(10, 0, 10),
    'max_normal_deviation_bps': rule(10, 0, 10),
    'max_reference_age_seconds': rule(90, 0, 90),
    'min_outlier_share': rule(.7, .7, 1),
    'min_venues': rule(3, 3, 6, integer=True),
}

RISK_NAMES = ('max_equity_drop_usd', 'daily_loss_stop_usd', 'total_loss_stop_usd')


def risk_settings(store):
    """Global account stops use the strictest OPEN position's frozen version.

    Old positions without a snapshot retain the original defaults. With no open
    positions, current settings apply. Changing a setting cannot rewrite an
    existing position's stop policy; immutable code/env ceilings still apply.
    """
    positions = store.pairs(active=True)
    versions = [p.get('config_at_open', {}).get('values', {}) for p in positions]
    if not versions:
        versions = [Settings(store).read()['values']]
    return {k:min(v.get(k,SCHEMA[k]['default']) for v in versions) for k in RISK_NAMES}


def validate(changes):
    if not isinstance(changes, dict) or not changes or set(changes)-set(SCHEMA):
        raise ValueError('invalid_config')
    for name, value in changes.items():
        bounds = SCHEMA[name]
        try:
            finite=type(value) in (int,float) and math.isfinite(value)
        except OverflowError:
            finite=False
        if (not finite
                or (bounds['integer'] and type(value) is not int)
                or (bounds['min'] is not None and value < bounds['min'])
                or (bounds['max'] is not None and value > bounds['max'])):
            raise ValueError('invalid_config')
    return dict(changes)


class Settings:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def read(self):
        defaults = {k: r['default'] for k, r in SCHEMA.items()}
        try:
            record = self.store.meta('runtime_config')
            if record is None:
                return dict(version=0, values=defaults, fallback=False)
            if (type(record['version']) is not int or record['version'] < 1
                    or set(record['values'])-set(SCHEMA)):
                raise ValueError('invalid_config_record')
            values = defaults | validate(record['values'])
            return dict(version=record['version'], values=values, fallback=False)
        except Exception:
            # This is an operational alert, not a removable trading stop lock.
            try:
                self.store.event('CONFIG_DEFAULTS_ALERT', dict(reason='config_unreadable', at=self.clock()))
            except Exception:
                pass
            return dict(version=0, values=defaults, fallback=True)

    def describe(self):
        return dict(self.read(), schema=copy.deepcopy(SCHEMA))

    def update(self, changes, *, source):
        changes = validate(changes)
        if source != 'admin_api':
            raise ValueError('invalid_config_source')
        with self.store.transaction():
            old = self.read()
            if old['fallback']:
                raise ValueError('config_unreadable')
            new = dict(version=old['version']+1, values=old['values'] | changes, fallback=False)
            self.store.set_meta('runtime_config', new)
            self.store.event('CONFIG_CHANGED', dict(version=new['version'], at=self.clock(), source=source,
                            old={k: old['values'][k] for k in changes}, new=changes))
        return new

    def audit(self):
        return [json.loads(e['value']) for e in self.store.events() if e['kind'] in ('CONFIG_CHANGED', 'CONFIG_CONFLICT')]
