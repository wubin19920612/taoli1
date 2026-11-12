"""Non-overridable ceilings and a single admission check for every opening send."""
from itertools import chain
from tools.fill_sim.core import dec
from .stops import StopLocks
from .settings import risk_settings


MAX_LEG_USD = dec('150')
MAX_TOTAL_USD = dec('600')
MAX_EQUITY_DROP_USD = dec('150')
DEFAULT_VAR = '/home/ubuntu/wubin/crossex-exec/var'
DEFAULT_ENV = '/home/ubuntu/wubin/crossex-exec/crossex-exec.env'


class HardLimits:
    def __init__(self,store,snapshot,config=None,alert=None):
        self.store,self.snapshot=store,snapshot
        self.alert=alert
        self.stops=None if alert else StopLocks(store)
        self.lock=store.lock
        self._last_equity_halt=None
        config=config or {}
        def lowered(name,ceiling):
            value=dec(config.get(name,ceiling))
            if not 0<value<=ceiling:
                raise ValueError('hard_limit_config_invalid')
            return value
        self.leg=lowered('CROSSEX_MAX_LEG_USD',MAX_LEG_USD)
        self.total=lowered('CROSSEX_MAX_TOTAL_USD',MAX_TOTAL_USD)
        self.drop=lowered('CROSSEX_MAX_EQUITY_DROP_USD',MAX_EQUITY_DROP_USD)

    def equities(self,snapshot):
        return {v:str(dec(snapshot['equity_by_venue'][v])) for v in ('crossex','bitget')}

    def initialize(self,snapshot):
        equities=self.equities(snapshot)
        if self.store.meta('account_equity_baseline') is None:
            if next(self.store.iter_orders(exclude_states=('SHADOW','UNSENT','INTENT')),None) is not None:
                raise ValueError('account_baseline_missing_with_history')
            self.store.set_meta('account_equity_baseline',equities)
        self.store.event('STARTUP_EQUITY',dict(current=equities,baseline=self.store.meta('account_equity_baseline')))
        self.check_equity(snapshot)

    def check_equity(self,snapshot):
        with self.lock:
            return self._check_equity(snapshot)

    def _check_equity(self,snapshot):
        equities=self.equities(snapshot)
        baseline=self.store.meta('account_equity_baseline')
        if baseline is None:
            raise ValueError('account_baseline_missing')
        self.store.set_meta('account_equity_current',equities)
        self.store.set_meta('liabilities',snapshot['liabilities'])
        if snapshot['liabilities']:
            if self.alert:
                self.alert('account_liability',details={'assets':snapshot['liabilities']},event_kind='ACCOUNT_LIABILITY')
            else:
                if 'account_liability' not in self.stops.reasons:
                    self.store.event('ACCOUNT_LIABILITY',{'assets':snapshot['liabilities']})
                self.stops.add('account_liability')
        threshold=min(self.drop,MAX_EQUITY_DROP_USD,dec(risk_settings(self.store)['max_equity_drop_usd']))
        breached=[v for v in equities if dec(baseline[v])-dec(equities[v])>=threshold]
        if breached:
            # The coordinator flattens AFTER concurrent IOC workers join. Never
            # close an in-flight pair recursively from inside a send callback.
            if self.store.meta('state')!='HALTED':
                self.store.set_meta('state','HALTING')
                self.store.set_meta('halt_reason','account_equity_drop')
            values=dict(venues=breached,current=equities,baseline=baseline)
            if self.alert:
                self.alert('account_equity_drop',details=values,event_kind='EQUITY_HALT')
            else:
                if values!=self._last_equity_halt:
                    self.store.event('EQUITY_HALT',values)
                    self._last_equity_halt=values
                self.stops.add('account_equity_drop')
        return bool(breached)

    def exposure(self,snapshot,intent=None):
        orders=self.store.iter_orders(exclude_states=('SHADOW','UNSENT'))
        if intent is not None and self.store.order(intent['cid']) is None:
            orders=chain(orders,[dict(intent,state='INTENT',filled='0')])
        positions,pending={},dec(0)
        for o in orders:
            if o['state'] in ('SHADOW','UNSENT'):
                continue
            venue='bitget' if o['instrument']['market']['exchange']=='bitget' else 'crossex'
            key='|'.join((venue,o['instrument']['symbol'],o['position_side']))
            filled=dec(o.get('filled','0'))
            qty,price=positions.get(key,(dec(0),dec(0)))
            qty+=filled*(-1 if o['reduce_only'] else 1)
            if not o['reduce_only']:
                price=max(price,dec(o['price']),dec(o.get('vwap') or 0))
                if o['state'] in ('INTENT','SENT','ACK','UNKNOWN'):
                    pending+=max(dec(0),dec(o['quantity'])-filled)*dec(o['price'])
            positions[key]=(qty,price)
        notionals={k:dec(v) for k,v in snapshot['notionals'].items()}
        if any(v<0 for v in notionals.values()):
            raise ValueError('negative_notional')
        for key,(qty,price) in positions.items():
            notionals[key]=max(notionals.get(key,dec(0)),max(qty,dec(0))*price)
        return sum(notionals.values(),dec(0))+pending

    def check_order(self,intent):
        # Closing intents do not require risk-read availability or notional room.
        if intent['reduce_only']:
            return
        with self.lock:
            snapshot=self.snapshot()  # Engine supplies a <=10s cache; never private I/O here.
            breached=self.check_equity(snapshot)
            notional=dec(intent['quantity'])*dec(intent['price'])
            total=self.exposure(snapshot,intent)
            pair=self.store.get_pair(intent['pair_id']) or {}
            values=pair.get('config_at_open',{}).get('values',{})
            if breached or self.store.meta('state') in ('HALTING','HALTED'):
                raise ValueError('account_equity_halt')
            if snapshot['liabilities']:
                raise ValueError('account_liability')
            if notional>min(self.leg,MAX_LEG_USD,dec(values.get('order_usd_max',MAX_LEG_USD))):
                raise ValueError('leg_notional_limit')
            if total>min(self.total,MAX_TOTAL_USD,dec(values.get('max_total_open_usd',MAX_TOTAL_USD))):
                raise ValueError('total_notional_limit')
            if any(m is not None and dec(m)<2 for m in snapshot['margins']):
                self.store.set_meta('flatten_requested',True)
            if len(snapshot['margins'])!=2 or any(m is None or dec(m)<3 for m in snapshot['margins']):
                raise ValueError('margin_open_limit')
