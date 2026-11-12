"""Non-overridable ceilings and a single admission check for every opening send."""
from itertools import chain
import time
from tools.fill_sim.core import dec
from .stops import StopLocks
from .settings import risk_settings
from .position_values import notional_values, key_currency, usd_value
from .program_risk import require_program_proof, program_notionals, debt_attribution


MAX_LEG_USD = dec('150')
MAX_TOTAL_USD = dec('600')
MAX_EQUITY_DROP_USD = dec('150')
DEFAULT_VAR = '/home/ubuntu/wubin/crossex-exec/var'
DEFAULT_ENV = '/home/ubuntu/wubin/crossex-exec/crossex-exec.env'


class HardLimits:
    def __init__(self,store,snapshot,config=None,alert=None,clock=time.time):
        self.store,self.snapshot=store,snapshot
        self.clock=clock
        self.alert=alert
        self.stops=None if alert else StopLocks(store)
        self.lock=store.lock
        self._last_equity_halt=None
        config=config or {}
        self.scope=config.get('CROSSEX_RISK_SCOPE','program')
        if self.scope not in ('program','dedicated_account'):
            raise ValueError('risk_scope_invalid')
        def lowered(name,ceiling):
            value=dec(config.get(name,ceiling))
            if not 0<value<=ceiling:
                raise ValueError('hard_limit_config_invalid')
            return value
        self.leg=lowered('CROSSEX_MAX_LEG_USD',MAX_LEG_USD)
        self.total=lowered('CROSSEX_MAX_TOTAL_USD',MAX_TOTAL_USD)
        self.drop=lowered('CROSSEX_MAX_EQUITY_DROP_USD',MAX_EQUITY_DROP_USD)

    @property
    def program_scope(self):
        # Explicit legacy dedicated-account mode is stricter and only applicable
        # without an external registry. It cannot disable shared-account proof.
        return self.scope=='program' or bool(self.store.meta('external_contracts',{}))

    def liabilities(self,snapshot):
        return (debt_attribution(self.store,snapshot,self.clock()) if self.program_scope
                else dict(external=[],blocking=snapshot['liabilities']))

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
        debts=self.liabilities(snapshot)
        self.store.set_meta('liability_attribution',dict(debts,snapshot_version=snapshot.get('snapshot_version'),at=self.clock()))
        if debts['external']:
            from .alerts import emit
            status=sorted((r['route'],r['exchange_type'],r['coin']) for r in debts['external'])
            previous=self.store.meta('external_liability_active')
            emit(self.store,self.clock,'external_liability',{'assets':debts['external']},
                 event_kind='EXTERNAL_LIABILITY',status=status,new_incident=not previous,
                 active_key='external_liability_active',
                 sink=getattr(getattr(self.alert,'__self__',None),'alert_sink',None))
        elif self.store.meta('external_liability_active'):
            with self.store.transaction():
                self.store.set_meta('external_liability_active',False)
                self.store.event('EXTERNAL_LIABILITY_CLEARED')
        if debts['blocking']:
            if self.alert:
                self.alert('account_liability',details={'assets':debts['blocking']},event_kind='ACCOUNT_LIABILITY')
            else:
                if 'account_liability' not in self.stops.reasons:
                    self.store.event('ACCOUNT_LIABILITY',{'assets':snapshot['liabilities']})
                self.stops.add('account_liability')
        threshold=min(self.drop,MAX_EQUITY_DROP_USD,dec(risk_settings(self.store)['max_equity_drop_usd']))
        loss=None
        if self.program_scope:
            try:
                loss=require_program_proof(self.store,snapshot,self.clock())
            except (KeyError,TypeError,ValueError,ArithmeticError):
                # Unknown is not zero. Opening checks require the proof; this
                # observation may still enforce liability and margin protection.
                return False
            breached=['program'] if dec(loss['program_total'])<=-threshold else []
        else:
            breached=[v for v in equities if dec(baseline[v])-dec(equities[v])>=threshold]
        if breached:
            # The coordinator flattens AFTER concurrent IOC workers join. Never
            # close an in-flight pair recursively from inside a send callback.
            if self.store.meta('state')!='HALTED':
                self.store.set_meta('state','HALTING')
                self.store.set_meta('halt_reason','account_equity_drop')
            values=dict(venues=breached,current=equities,baseline=baseline)
            if loss:
                values.update(scope='program',program_total=loss['program_total'],snapshot_version=loss['snapshot_version'])
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
        now=self.clock()
        def converted(key,amount):
            return usd_value(amount,key_currency(key),snapshot.get('external_fx',{}),now,upper=True)
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
                    pending+=converted(key,max(dec(0),dec(o['quantity'])-filled)*dec(o['price']))
            positions[key]=(qty,price)
        view=program_notionals(self.store,snapshot,now) if self.program_scope else snapshot
        notionals={k:dec(v) for k,v in notional_values(view,now).items()}
        if any(v<0 for v in notionals.values()):
            raise ValueError('negative_notional')
        for key,(qty,price) in positions.items():
            notionals[key]=max(notionals.get(key,dec(0)),converted(key,max(qty,dec(0))*price))
        return sum(notionals.values(),dec(0))+pending

    def check_order(self,intent):
        # Closing intents do not require risk-read availability or notional room.
        if intent['reduce_only']:
            return
        with self.lock:
            snapshot=self.snapshot()  # Engine supplies a <=10s cache; never private I/O here.
            breached=self.check_equity(snapshot)
            venue='bitget' if intent['instrument']['market']['exchange']=='bitget' else 'crossex'
            key='|'.join((venue,intent['instrument']['symbol'],intent['position_side']))
            notional=usd_value(dec(intent['quantity'])*dec(intent['price']),key_currency(key),
                               snapshot.get('external_fx',{}),self.clock(),upper=True)
            total=self.exposure(snapshot,intent)
            pair=self.store.get_pair(intent['pair_id']) or {}
            values=pair.get('config_at_open',{}).get('values',{})
            if breached or self.store.meta('state') in ('HALTING','HALTED'):
                raise ValueError('account_equity_halt')
            if self.liabilities(snapshot)['blocking']:
                raise ValueError('account_liability')
            if notional>min(self.leg,MAX_LEG_USD,dec(values.get('order_usd_max',MAX_LEG_USD))):
                raise ValueError('leg_notional_limit')
            if total>min(self.total,MAX_TOTAL_USD,dec(values.get('max_total_open_usd',MAX_TOTAL_USD))):
                raise ValueError('total_notional_limit')
            if any(m is not None and dec(m)<2 for m in snapshot['margins']):
                self.store.set_meta('flatten_requested',True)
            if len(snapshot['margins'])!=2 or any(m is None or dec(m)<3 for m in snapshot['margins']):
                raise ValueError('margin_open_limit')
            if self.program_scope:
                proof=require_program_proof(self.store,snapshot,self.clock())
                settings=risk_settings(self.store)
                if (dec(proof['program_daily'])<=-dec(settings['daily_loss_stop_usd'])
                        or dec(proof['program_total'])<=-dec(settings['total_loss_stop_usd'])):
                    raise ValueError('runtime_loss_limit')
