"""Single coordinator owns pair state; parallel workers only own individual orders."""
import time
import threading
import uuid
from functools import lru_cache
from itertools import chain
from decimal import ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
from tools.fill_sim.core import Book, Market, dec, walk, closing, track_interval, day
from tools.fill_sim.engine import trigger_reason
from .accounts import local_positions
from .policy import Instrument, review, exit_reason, risk_decision, limit_price, send_review
from .execution import Executor, NONTERMINAL
from .limits import HardLimits, DEFAULT_VAR
from .background import SnapshotCache, PeriodicWorker
from .stops import StopLocks
from .baseline import Baseline
from .admission import OpeningLimits, AdmissionError, BOOK_RETRIES, book_skew_ms


class DailyBaselinePending(ValueError):
    """The UTC date changed while processing the previous day's snapshot."""


class Engine:
    def __init__(self,store,market,accounts,*,live=False,stop_file=DEFAULT_VAR+'/STOP',clock=time.time,alert_sink=None,config=None):
        self.store,self.market,self.accounts,self.live,self.clock=store,market,accounts,live,clock
        self.stop_file=Path(stop_file)
        self.risk='STOP'
        self.last_reconcile=0
        self.snapshot=None
        self.last_accounting_retry=0
        self.alert_sink=alert_sink
        self._last_alert_reason=None
        self._last_alert_values=None
        self._halt_alerts={}
        self._alert_lock=threading.RLock()
        self._lifecycle_lock=threading.RLock()
        self._closed=False
        self.workers=[]
        self.bases=None
        self.baseline=Baseline(store,clock)
        self.opening_limits=OpeningLimits(config)
        self.next_catalog=self.next_fees=0
        self.fee_keys=set()
        self.next_halt=0
        self.halt_attempts=0
        self._repairing=set()
        self.account_cache=SnapshotCache(clock)
        self.stops=StopLocks(store)
        self.limits=HardLimits(store,self.account_cache.read,config,alert=self.alert)
        self.executor=Executor(store,{'crossex':accounts.cross,'bitget':accounts.bitget},live=live,
                               can_open=self.can_open,check_order=self.check_order,check_pair=self.check_pair,
                               check_release=self.check_release,alert=self.alert,clock=clock)

    def alert(self,reason,*,details=None,event_kind=None):
        with self._alert_lock:
            values=details or {}
            if self.store.meta('state')=='HALTING':
                changed=reason not in self._halt_alerts or self._halt_alerts[reason]!=values
                self._halt_alerts[reason]=values
            else:
                self._halt_alerts.clear()
                changed=(self._last_alert_reason!=reason or self._last_alert_values!=values
                         or self.store.meta('stop_reason')!=reason)
            owner=values.get('pair_id','global') if reason in ('unhedged_position','repair_stopped') else 'global'
            self.stops.add(reason,owner)
            if changed:
                if event_kind:
                    self.store.event(event_kind,values)
                self.store.event('ALERT',dict(reason=reason,**values))
                self._last_alert_reason=reason
                self._last_alert_values=values
                if self.alert_sink:
                    try:
                        self.alert_sink(reason)
                    except Exception:
                        self.store.event('ALERT_SINK_FAILED',{'reason':reason})

    def fees_for(self,instruments,now=None):
        snapshots=self.accounts.fee_snapshots
        now=self.clock() if now is None else now
        rates=[]
        for i in instruments:
            version=snapshots.get(i.market.key)
            if version is None or not 0<=now-version.captured_at<=3600:
                raise ValueError('actual_fee_missing_or_stale')
            rates.append(version.rate)
        return rates

    def check_order(self,intent):
        self.limits.check_order(intent)

    def check_pair(self,intents):
        if len(intents)!=2 or {i['leg'] for i in intents}!={0,1} or len({i['pair_id'] for i in intents})!=1:
            raise ValueError('opening_pair_missing')
        pair=self.store.get_pair(intents[0]['pair_id'])
        if pair is None or pair['state'] in ('CLOSED','SHADOW','SKIPPED'):
            raise ValueError('opening_pair_missing')
        orders=sorted(intents,key=lambda o:o['leg'])
        instruments=[Instrument.load(i) for i in pair['instruments']]
        if any(i.market.key not in self.market.mapping for i in instruments):
            raise ValueError('send_catalog_unavailable')
        venues=tuple(i.market.exchange for i in instruments)
        for attempt in range(BOOK_RETRIES+1):
            try:
                state=self.baseline.require(pair['base'],*venues)
                self.opening_limits.flat(state)
                self.opening_limits.flat(dict(state,baseline_bps=pair['baseline_at_open']))
                fees=self.fees_for(instruments)
                skew=self.opening_limits.books(pair['last_books'],self.clock(),send=True)
                self.opening_limits.funding(pair['funding'])
                pricing=send_review(pair,orders,fees,self.clock())
                evidence=self.opening_limits.outlier(venues,pair['last_books'],self.baseline.latest(pair['base']),
                                                      pricing['excess_bps'],self.clock())
                pair.update(open_book_skew_ms=float(skew),outlier_at_open=evidence,
                            excess_at_open=pricing['excess_bps'])
                pair['fill'].update(pricing)
                pair['last_good']=[b['received_at'] for b in pair['last_books']]
                self.store.pair(pair)
                # A retry may spend time waiting on the public limiter. Recheck
                # original hard limits and final book age before releasing BOTH senders.
                for order in orders:
                    self.check_order(order)
                self.opening_limits.books(pair['last_books'],self.clock(),send=True)
                return
            except AdmissionError as error:
                if error.reason in ('book_skew','book_stale') and attempt<BOOK_RETRIES:
                    pair['last_books']=[b.to_dict() if b else None for b in self.fetch_opening_books(instruments)]
                    continue
                self.store.skip(pair.get('trigger_id','pair:'+pair['id']),error.reason,
                                stage='send',pair_id=pair['id'])
                raise

    def check_release(self,intents):
        """Final shared decision AFTER durable SENT; never fetch/refresh here."""
        if not self.can_open(ignore_cids={i['cid'] for i in intents}):
            raise ValueError('open_gate_changed')
        pair=self.store.get_pair(intents[0]['pair_id'])
        if pair is None or pair['state'] in ('CLOSED','SHADOW','SKIPPED'):
            raise ValueError('opening_pair_missing')
        try:
            # This must be last: preceding risk reads/writes count towards age.
            self.opening_limits.books(pair['last_books'],self.clock(),send=True)
        except AdmissionError as error:
            self.store.skip(pair.get('trigger_id','pair:'+pair['id']),error.reason,
                            stage='release',pair_id=pair['id'])
            raise

    def fetch_opening_books(self,instruments):
        # MarketData submits the whole pair together through the existing
        # shared public queue/limiter. Never refresh only the failing leg.
        try:
            fetch=getattr(self.market,'opening_books',self.market.books)
            return fetch(instruments)
        except Exception:
            return [None,None]

    def opening_review(self,instruments,fees,funding,turnovers,baseline):
        for attempt in range(BOOK_RETRIES+1):
            books=self.fetch_opening_books(instruments)
            try:
                self.opening_limits.books(books,self.clock())
                fill=review(instruments,books,fees,funding,turnovers,self.clock(),baseline_bps=baseline['baseline_bps'])
                evidence=self.opening_limits.outlier(tuple(i.market.exchange for i in instruments),books,
                    self.baseline.latest(instruments[0].spec.base),fill['excess_bps'],self.clock())
                self.opening_limits.books(books,self.clock())
                return books,fill,evidence
            except AdmissionError as error:
                if error.reason not in ('book_skew','book_stale') or attempt==BOOK_RETRIES:
                    raise

    def refresh_accounts(self):
        started=self.clock()
        try:
            snapshot=self.accounts.snapshot()
            with self._lifecycle_lock:
                if not self._closed:
                    self.account_cache.publish(snapshot,started)
                    self.limits.check_equity(snapshot)
                    self.roll_daily_upnl(snapshot,started)
        except Exception:
            with self._lifecycle_lock:
                if not self._closed:
                    self.store.event('ACCOUNT_REFRESH_FAILED')

    def refresh_reference(self):
        now=self.clock()
        if now>=self.next_catalog:
            try:
                self.market.bootstrap(self.bases)
                failed=getattr(self.market,'catalog_errors',{})
                self.next_catalog=now+(30 if failed else 21600)
            except Exception:
                self.next_catalog=now+30
                with self._lifecycle_lock:
                    if not self._closed:
                        self.store.event('CATALOG_REFRESH_FAILED')
        # Empty whitelist means no opening reference reads, never every contract.
        instruments=tuple(i for i in self.market.mapping.values() if i.spec.base in (self.bases or ()))
        keys={i.market.key for i in instruments}
        if now>=self.next_fees or keys!=self.fee_keys:
            try:
                rates=self.accounts.fees(instruments) if instruments else {}
                if not instruments:
                    self.accounts.fee_snapshots={}
            except Exception:
                rates={}
                self.accounts.fee_snapshots={}
            with self._lifecycle_lock:
                if not self._closed:
                    versions=self.accounts.fee_snapshots
                    self.store.set_meta('actual_fees',dict(at=now,rates={k:v.rate for k,v in versions.items()},
                        times={k:v.captured_at for k,v in versions.items()}))
                    if keys-set(rates):
                        self.store.event('FEES_UNAVAILABLE',{'markets':sorted(keys-set(rates))})
            self.next_fees=now+(30 if keys-set(rates) else 3600)
            self.fee_keys=keys

    def refresh_baselines(self):
        bases=self.bases or ()
        started=self.clock()
        try:
            mids=self.market.baseline_quotes(bases)
        except Exception:
            # Even a whole-round failure invalidates the old reference set.
            with self._lifecycle_lock:
                if not self._closed:
                    self.baseline.record({},bases,at=started)
            raise
        with self._lifecycle_lock:
            if not self._closed:
                # Never combine a delayed round with newly captured quotes.
                if not 0<=self.clock()-started<=20:
                    mids={}
                self.baseline.record(mids,bases,at=started)

    def start_background(self,bases):
        self.bases=tuple(bases)
        if not self.workers:
            self.workers=[PeriodicWorker(self.refresh_accounts,5,'exec-accounts',self.background_error),
                          PeriodicWorker(self.refresh_reference,5,'exec-reference',self.background_error),
                          PeriodicWorker(self.refresh_baselines,30,'exec-baseline',self.background_error)]

    def background_error(self,worker,kind):
        with self._lifecycle_lock:
            if not self._closed:
                self.store.event('BACKGROUND_FAILED',dict(worker=worker,error_type=kind))

    def close(self):
        with self._lifecycle_lock:
            self._closed=True
        for worker in self.workers:
            worker.close()

    def can_open(self,ignore_cids=()):
        exposed=any(p.get('remote_pending') or len(set(map(dec,p.get(field,['0','0']))))>1
                    for p in self.store.pairs(active=True) if p['state'] not in ('SHADOW','SKIPPED')
                    for field in ('remaining','remote_remaining'))
        recent=rejected=0
        for o in self.executor.iter_orders(sent_since=self.clock()*1000-3600000):
            if o['cid'] not in ignore_cids:
                recent+=1
                rejected+=o['state']=='REJECTED' or o.get('rejected',False)
        local_ok=(self.store.meta('single_failures',0)<3 and (not recent or dec(rejected)/recent<=dec('.3')))
        return (local_ok and not exposed and self.risk=='RUN' and not self.stop_file.exists() and not self.stops
                and not self.executor.pending_writes and not any(o['cid'] not in ignore_cids
                    for o in self.executor.iter_orders(states=NONTERMINAL))
                and not self.store.meta('position_mismatch')
                and self.store.meta('daily_loss_day')!=day(self.clock())
                and not self.store.meta('flatten_requested')
                and self.store.meta('state') not in ('HALTED','HALTING') and 0<=self.clock()-self.last_reconcile<60)

    def startup(self,bases=()):
        self.store.assert_startable()
        previous=self.store.meta('live_mode')
        if previous is not None and previous!=self.live and self.store.pairs(active=True):
            raise ValueError('mode_change_with_live_positions')
        self.store.set_meta('live_mode',self.live)
        known={o['cid'] for o in self.executor.iter_orders(states=NONTERMINAL)
               if o['instrument']['market']['exchange']=='bitget'}
        # Even a failed account/mode check cannot strand a durable INTENT or SENT.
        self.executor.recover()
        for pair in self.store.pairs(active=True):
            self.refresh_pair(pair)
            if self.needs_recovery(pair):
                self.recover_pair(pair)
        started=self.clock()
        try:
            self.snapshot=self.accounts.validate_startup()
        except Exception:
            if self.store.meta('state')!='HALTING':
                raise
            self.alert('halting_startup_account_unavailable')
            self.flatten_all('startup_halt_recovery')
            return
        self.account_cache.publish(self.snapshot,started)
        if any(o['clientOid'] not in known for o in self.snapshot.get('open_orders',[])):
            raise ValueError('foreign_bitget_unfilled_orders')
        self.limits.initialize(self.snapshot)
        # Rebuild from the immutable journal, including crash after terminal before pair save.
        for pair in self.store.pairs(active=True):
            self.refresh_pair(pair)
        if self.store.meta('state')=='HALTING':
            # A known equity breach must not wait for catalog/fee bootstrap.
            self.flatten_all('startup_equity_drop')
            # Residual exposure stays in-process; tick owns bounded backoff retries.
            return
        self.reconcile()
        if self.store.meta('state') not in ('HALTING','HALTED'):
            self.start_background(bases)

    def totals(self):
        total=daily=dec(0)
        today=day(self.clock())
        @lru_cache(maxsize=64)
        def entries(pair_id):
            return {o['leg']:o for o in self.executor.iter_orders(pair_id=pair_id,reduce_only=False) if o.get('vwap')}
        for o in self.executor.iter_orders(exclude_states=('SHADOW',)):
            if o['state']=='SHADOW':
                continue
            if o.get('accounting_pending'):
                self.alert('accounting_pending')
                continue
            realized=-dec(o.get('fee') or 0)
            if o['reduce_only'] and dec(o['filled']):
                entry=entries(o['pair_id']).get(o['leg'])
                if not entry or not o.get('vwap'):
                    self.alert('entry_accounting_unknown')
                    continue
                realized+=dec(o['filled'])*(dec(o['vwap'])-dec(entry['vwap']))*(1 if o['leg']==0 else -1)
            total+=realized
            if day((o.get('fill_ms') or o['created_at']*1000)/1000)==today:
                daily+=realized
        floating=dec(self.snapshot['upnl']) if self.snapshot else dec(0)
        baseline=self.roll_daily_upnl(self.snapshot or {},(self.snapshot or {}).get('captured_at',self.clock()))
        if not baseline or baseline['day']!=today:
            raise DailyBaselinePending('daily_baseline_pending')
        return daily+floating-dec(baseline['upnl']),total+floating

    def roll_daily_upnl(self,snapshot,captured_at):
        with self._lifecycle_lock:
            today=day(self.clock())
            baseline=self.store.meta('daily_upnl')
            last=self.store.meta('last_upnl')
            observation=None
            if 'upnl' in snapshot:
                observation=dict(upnl=str(snapshot['upnl']),positions=snapshot.get('upnl_by_position',{}),
                                 captured_at=captured_at)
            if not baseline or baseline['day']!=today:
                source=last if last and day(last['captured_at'])<today else observation
                if source and (source is last or day(captured_at)==today):
                    # Freeze the last PRE-midnight observation before recording any
                    # new-day observation (the position may already be closed).
                    baseline=dict(source,day=today)
                    self.store.set_meta('daily_upnl',baseline)
            if observation and (not last or captured_at>=last['captured_at']):
                self.store.set_meta('last_upnl',observation)
            return baseline

    def save_tracking_upnl(self,pair,books,now):
        """Persist a per-position exit-price estimate before any tracking close."""
        if not all(books):
            return
        positions=dict((self.store.meta('last_upnl') or {}).get('positions',{}))
        entries={o['leg']:o for o in self.executor.iter_orders(pair_id=pair['id'],reduce_only=False)}
        try:
            for n,q in enumerate(self.quantities(pair)):
                key=self.position_key(pair,n)
                if not q:
                    positions.pop(key,None)
                    continue
                entry=entries[n]
                price=dec(entry.get('vwap') or entry.get('average_price') or entry['price'])
                mark=dec(walk(books[n].bids if n==0 else books[n].asks,quantity=q)['vwap'])
                positions[key]=str(q*(mark-price)*(1 if n==0 else -1))
        except (KeyError,ValueError,TypeError):
            return
        self.roll_daily_upnl(dict(upnl=str(sum(map(dec,positions.values()),dec(0))),upnl_by_position=positions),now)

    def reconcile_positions(self,snapshot):
        captured=snapshot['captured_at']
        if (self.executor.pending_writes or next(self.executor.iter_orders(states=NONTERMINAL),None) is not None
                or next(self.executor.iter_orders(accounting_pending=True),None) is not None):
            self.store.set_meta('position_match_streak',0)
            return None
        latest=max((max(float(o.get('fill_observed_at',0)),float(o.get('fill_ms') or 0)/1000,
                        float(o.get('terminal_ms') or 0)/1000)
                    for o in self.executor.iter_orders() if dec(o.get('filled',0))>0),default=0)
        if captured<=latest:
            # The snapshot may have started before an IOC completed. It says
            # nothing about the new position; wait for a later background read.
            return None
        def reconciled_pairs():
            for pair in self.store.iter_pairs(exclude_states=('SHADOW','SKIPPED')):
                if pair['state']!='CLOSED':
                    self.refresh_pair(pair)
                    self.save_remote(pair,snapshot)
                    self.clear_repair_locks(pair)
                yield pair
        same=snapshot['positions']==local_positions(reconciled_pairs())
        previous=self.store.meta('position_snapshot_at',0)
        if captured>previous:
            self.store.set_meta('position_snapshot_at',captured)
            if not same:
                self.store.set_meta('position_mismatch',True)
                self.store.set_meta('position_match_streak',0)
                self.alert('position_mismatch')
            elif self.store.meta('position_mismatch') or self.store.meta('stop_reason')=='position_mismatch':
                streak=self.store.meta('position_match_streak',0)+1
                self.store.set_meta('position_match_streak',streak)
                if streak>=3:
                    self.store.set_meta('position_mismatch',False)
                    self.stops.discard('position_mismatch')
                    self.store.event('POSITION_MATCH_RECOVERED',dict(consecutive=streak,captured_at=captured))
        if same:
            for reason in ('unknown_order','unknown_close','accounting_pending','fills_pending','entry_accounting_unknown'):
                self.stops.discard(reason)
        return same

    def reconcile(self):
        try:
            # On startup/tests a direct read seeds the cache. Once running, account
            # I/O belongs to its own worker; risk reads cannot block position tracking.
            if self.workers:
                self.snapshot=self.account_cache.read()
            else:
                started=self.clock()
                self.snapshot=self.accounts.snapshot()
                self.account_cache.publish(self.snapshot,started)
                self.snapshot=self.account_cache.read()
            equity_halt=self.limits.check_equity(self.snapshot)
            self.roll_daily_upnl(self.snapshot,self.snapshot['captured_at'])
            if day(self.snapshot['captured_at'])!=day(self.clock()):
                self.risk='STOP'
                if equity_halt:
                    self.flatten_all('circuit_breaker')
                return
            same=self.reconcile_positions(self.snapshot)
            self.last_reconcile=self.clock()
            daily,total=self.totals()
            current_day=day(self.clock())
            if self.store.meta('daily_loss_day') not in (None,current_day):
                self.store.set_meta('daily_loss_day',None)
                self.stops.discard('daily_loss')
            # Equity delta also catches funding/fees delayed beyond the trade journal.
            # Transfers are deliberately not assumed to be profit: use dedicated accounts.
            equity=self.snapshot.get('equity')
            if equity is not None:
                baseline=self.store.meta('equity_baseline')
                if baseline is None:
                    if next(self.store.iter_orders(),None) is not None:
                        raise ValueError('equity_baseline_missing_with_history')
                    baseline=str(equity)
                    self.store.set_meta('equity_baseline',baseline)
                current_day=day(self.clock())
                daily_base=self.store.meta('daily_equity')
                if not daily_base or daily_base['day']!=current_day:
                    daily_base=dict(day=current_day,equity=str(equity))
                    self.store.set_meta('daily_equity',daily_base)
                total=min(total,dec(equity)-dec(baseline))
                daily=min(daily,dec(equity)-dec(daily_base['equity']))
            recent=rejected=0
            for o in self.store.iter_orders(sent_since=self.clock()*1000-3600000):
                recent+=1
                rejected+=o['state']=='REJECTED' or o.get('rejected',False)
            self.risk=risk_decision(daily=daily,total=total,margins=self.snapshot['margins'],
                single_failures=self.store.meta('single_failures',0),rejected=rejected,attempted=recent,reconciled=same)
            if daily<=-20:
                self.store.set_meta('daily_loss_day',current_day)
                self.alert('daily_loss')
            if self.store.meta('daily_loss_day')==current_day and self.risk=='RUN':
                self.risk='STOP'
            self.store.event('RECONCILE',dict(matched=same,margins=self.snapshot['margins'],daily=str(daily),total=str(total),risk=self.risk))
            self.stops.discard('account_snapshot_unavailable')
            if (total<=-100 or equity_halt) and self.store.meta('state')!='HALTED':
                self.store.set_meta('state','HALTING')
            if self.risk in ('HALTED','FLATTEN') or self.store.meta('state')=='HALTING':
                self.flatten_all('circuit_breaker')
        except DailyBaselinePending:
            self.risk='STOP'
        except Exception:
            self.risk='STOP'
            self.alert('account_snapshot_unavailable')

    def ingest(self,payload):
        now=self.clock()
        identity=payload.get('id')
        if not isinstance(identity,str) or not identity or len(identity)>128:
            return
        if not self.store.claim(identity,{'received_at':now}):
            return
        self.store.event('TRIGGER',{'id':identity})
        if trigger_reason(payload,now) or not self.can_open():
            self.store.skip(identity,'filter_or_open_gate')
            return
        try:
            markets=[Market.from_payload(payload[k]) for k in ('buy_market_key','sell_market_key')]
            instruments=[self.market.mapping[m.key] for m in markets]
            for m,i in zip(markets,instruments):
                i.spec.validate(m)
            base=instruments[0].spec.base
            if self.bases is not None and base not in self.bases:
                raise ValueError('base_not_whitelisted')
            if payload['symbol']!=base:
                raise ValueError('trigger_base_mismatch')
            active=self.store.pairs(active=True)
            if len(active)>=3 or any(p['base']==base for p in active):
                raise ValueError('position_limit')
            if now-self.store.meta('mapping')['at']>21600:
                raise ValueError('reference_data_stale')
            baseline=self.baseline.require(base,*(i.market.exchange for i in instruments))
            self.opening_limits.flat(baseline)
            self.accounts.check_instruments(instruments)
            fees=self.fees_for(instruments)
            try:
                funding=self.market.funding(instruments)
            except Exception:
                raise AdmissionError('funding_diff') from None
            self.opening_limits.funding(funding)
            turnovers=self.market.turnovers(instruments)
            books,fill,evidence=self.opening_review(instruments,fees,funding,turnovers,baseline)
            if self.clock()-now>120 or not self.can_open():
                raise ValueError('open_gate_changed')
        except Exception as error:
            reason=(error.reason if isinstance(error,AdmissionError) else str(error)
                    if isinstance(error,ValueError) and str(error) in (
                        'baseline_warming','structural_spread','pair_cooldown') else 'review_failed_closed')
            self.store.skip(identity,reason,stage='review')
            return
        pair=dict(id=uuid.uuid4().hex,trigger_id=identity,base=base,state='OPENING',opened_at=self.clock(),
                  baseline_at_open=fill['baseline_bps'],excess_at_open=fill['excess_bps'],
                  open_book_skew_ms=float(book_skew_ms(books)),close_book_skew_ms=None,outlier_at_open=evidence,
                  instruments=[i.dump() for i in instruments],fill=fill,funding=funding,turnovers=turnovers,
                  remaining=['0','0'],last_books=[b.to_dict() for b in books],last_good=[b.received_at for b in books],
                  next_track=self.clock()+3,single_failure=False,repair_attempts=0,round=0)
        self.store.pair(pair)
        self.store.event('REVIEW',{'pair_id':pair['id'],'fill_sim_estimate':fill,'funding':funding,'turnovers':turnovers})
        intents=[self.new_intent(pair,n,fill['quantity'],fill['limits'][n],False) for n in (0,1)]
        results=self.executor.execute_pair(intents)
        # The shared gate may have refreshed and committed the opening snapshot.
        pair=self.store.get_pair(pair['id'])
        if not self.live:
            pair['state']='SHADOW'
            self.store.pair(pair)
            return pair
        self.refresh_pair(pair)
        if self.store.meta('state')=='HALTING':
            self.flatten_all('account_equity_drop')
            return pair
        if self.store.meta('flatten_requested'):
            self.flatten_all('margin_ratio')
            return pair
        if any(o['state']=='UNKNOWN' for o in results):
            self.alert('unknown_order')
        else:
            self.balance(pair)
        return pair

    def new_intent(self,pair,leg,quantity,price,reduce,estimate=None):
        i=Instrument.load(pair['instruments'][leg])
        side=('sell' if leg==0 else 'buy') if reduce else ('buy' if leg==0 else 'sell')
        return dict(cid='ce-'+uuid.uuid4().hex[:29],pair_id=pair['id'],leg=leg,instrument=i.dump(),
                    side=side,position_side='long' if leg==0 else 'short',quantity=str(quantity),price=str(price),
                    reduce_only=reduce,created_at=self.clock(),estimate=estimate if reduce else pair['fill']['buy' if leg==0 else 'sell']['vwap'])

    def refresh_pair(self,pair):
        orders=self.executor.orders(pair_id=pair['id'])
        entries=sorted([o for o in orders if not o['reduce_only']],key=lambda o:o['leg'])
        for field,name in (('sent_ms','send_delta_ms'),('fill_ms','fill_delta_ms')):
            values=[o.get(field) for o in entries]
            delta=abs(values[1]-values[0]) if len(values)==2 and all(v is not None for v in values) else None
            if pair.get(name)!=delta:
                self.store.event('PAIR_TIMING',{'pair_id':pair['id'],name:delta})
            pair[name]=delta
        remaining=[dec(0),dec(0)]
        cash=fees=dec(0)
        unknown=pending=False
        for o in orders:
            if o['state'] in ('SENT','ACK','UNKNOWN','INTENT'):
                unknown=True
            q=dec(o.get('filled',0))
            remaining[o['leg']]+=q*(-1 if o['reduce_only'] else 1)
            if o.get('accounting_pending'):
                pending=True
            if q and o.get('vwap') is not None:
                cash+=q*dec(o['vwap'])*(1 if o['side']=='sell' else -1)
                fees+=dec(o['fee'])
        anchor=pair.pop('position_anchor',None)
        if anchor:
            pair.update(remote_remaining=anchor['remaining'],remote_captured_at=anchor['at'])
        if min(remaining)<0:
            pending=True  # Delayed entry accounting can trail a confirmed close.
            if not unknown:
                self.alert('negative_local_position')
        pair['remaining']=[str(q) for q in remaining]
        pair['accounting_pending']=pending
        pair['net_usd']=str(cash-fees) if not pending and not any(remaining) and not unknown else None
        if unknown:
            pair['state']='UNKNOWN'
        elif not any(remaining) and not any(map(dec,pair.get('remote_remaining',['0','0']))) and not pair.get('remote_pending'):
            pair.update(state='CLOSED',closed_at=pair.get('closed_at',self.clock()))
            pair['funding_known']=all(pair['closed_at']<s['next'] for s in pair['funding'])
            if not pair['funding_known']:
                # This service intentionally avoids funding settlement. An overdue
                # position needs account-bill reconciliation, never invented PnL.
                pair['trading_net_usd']=pair['net_usd']
                pair['net_usd']=None
                self.alert('funding_settlement_accounting_required')
        elif pair.get('exit_reason'):
            pair['state']='CLOSING'
        elif pair.get('state')!='CLOSING':
            pair['state']='OPEN'
        self.store.pair(pair)

    @staticmethod
    def position_key(pair,leg):
        i=Instrument.load(pair['instruments'][leg])
        return '|'.join(('bitget' if i.market.exchange=='bitget' else 'crossex',i.symbol,'long' if leg==0 else 'short'))

    @staticmethod
    def quantities(pair):
        return list(map(dec,pair.get('remote_remaining',pair['remaining'])))

    def save_remote(self,pair,snapshot):
        quantities=[str(dec(snapshot['positions'].get(self.position_key(pair,n),0))) for n in (0,1)]
        if any(dec(q)<0 for q in quantities):
            raise ValueError('negative_remote_position')
        pair.update(remote_remaining=quantities,remote_captured_at=snapshot.get('captured_at',self.clock()),remote_pending=False)
        self.store.pair(pair)

    def clear_repair_locks(self,pair):
        orders=self.executor.orders(pair_id=pair['id'])
        complete=not self.executor.pending_writes and not any(o['state'] in NONTERMINAL or o.get('accounting_pending') for o in orders)
        if ('remote_remaining' in pair and not pair.get('remote_pending') and complete and pair['id'] not in self._repairing
                and dec(pair['remote_remaining'][0])==dec(pair['remote_remaining'][1])
                and list(map(dec,pair['remaining']))==self.quantities(pair)):
            pair['repair_stopped']=False
            self.store.pair(pair)
            for reason in ('unhedged_position','repair_stopped'):
                self.stops.discard(reason,pair['id'])

    def needs_recovery(self,pair):
        return (next(self.executor.iter_orders(pair_id=pair['id'],states=NONTERMINAL),None) is not None
                or pair['state']=='UNKNOWN' or len(set(self.quantities(pair)))>1
                or pair.get('remote_pending') or (pair.get('exit_reason') and pair['state']!='CLOSED')
                or pair.get('repair_stopped',False))

    def recover_pair(self,pair):
        self.store.event('RECOVERY_ATTEMPT',dict(pair_id=pair['id']))
        if (pair.get('exit_reason') or self.store.meta('state')=='HALTING'
                or pair.get('repair_stopped')
                or next(self.executor.iter_orders(pair_id=pair['id'],states=NONTERMINAL),None) is not None):
            self.close_pair(pair,pair.get('exit_reason','repair_failed' if pair.get('repair_stopped') else 'unknown_order'))
        else:
            self.balance(pair)

    def resolve_exposure(self,pair):
        """Query, cancel if unresolved, then read BOTH positions; never rewrite fills."""
        if not self.live:
            return False
        self.executor.recover(startup=False,pair_id=pair['id'])
        try:
            snapshot=self.accounts.position_snapshot()
            self.save_remote(pair,snapshot)
            self.roll_daily_upnl(snapshot,snapshot.get('captured_at',self.clock()))
        except Exception:
            self.alert('unknown_exposure_unavailable')
            return False
        self.store.event('POSITION_RECOVERY',{'pair_id':pair['id'],'remaining':pair['remote_remaining'],
            'unresolved':[o['cid'] for o in self.executor.iter_orders(pair_id=pair['id'],states=NONTERMINAL)]})
        self.refresh_pair(pair)
        self.clear_repair_locks(pair)
        self.stops.discard('unknown_exposure_unavailable')
        return True

    def reduction_intent(self,pair,leg,quantity,bps,book=None):
        i=Instrument.load(pair['instruments'][leg])
        step=dec(i.spec.qty_step)
        quantity=(min(dec(quantity),dec(pair['remote_remaining'][leg]),dec(i.spec.max_qty))/step).to_integral_value(rounding=ROUND_FLOOR)*step
        if quantity<=0 or quantity<dec(i.spec.min_qty):
            raise ValueError('reduction_below_min')
        if book is None:
            book=self.market.books([i])[0]
        if book:
            pair['last_books'][leg]=book.to_dict()
            pair['last_good'][leg]=book.received_at
        else:
            book=Book(**pair['last_books'][leg])
        side='sell' if leg==0 else 'buy'
        levels=book.bids if leg==0 else book.asks
        estimate=None
        try:
            priced=walk(levels,quantity=quantity)
            deepest,estimate=priced['deepest'],priced['vwap']
        except ValueError:
            deepest=levels[-1][0]
        price=limit_price(deepest,side,i.tick,bps)
        if quantity*dec(price)<dec(i.spec.min_notional):
            raise ValueError('reduction_below_min_notional')
        skew=book_skew_ms(pair['last_books'])
        pair['close_book_skew_ms']=float(skew) if skew is not None else None
        return self.new_intent(pair,leg,quantity,price,True,estimate)

    def reduce(self,pair,leg,quantity,bps):
        if not self.live:
            raise ValueError('shadow_position_cannot_reduce')
        intent=self.reduction_intent(pair,leg,quantity,bps)
        self.store.pair(pair)
        return self.executor.execute(intent)

    def unhedged(self,pair):
        a,b=self.quantities(pair)
        uncertain=pair.get('remote_pending',False)
        if pair.get('repair_stopped'):
            self.stops.add('repair_stopped',pair['id'])
        self.alert('unhedged_position',event_kind='UNHEDGED',details={'pair_id':pair['id'],
            'quantity':None if uncertain else str(abs(a-b)),
            'remaining':None if uncertain else [str(a),str(b)],
            'excess_leg':None if uncertain else 0 if a>b else 1})

    def repair_intents(self,pair,bps):
        quantities=self.quantities(pair)
        leg=0 if quantities[0]>quantities[1] else 1
        diff=abs(quantities[0]-quantities[1])
        try:
            return [self.reduction_intent(pair,leg,diff,bps)]
        except ValueError:
            pass
        # A coarse step may cross zero only if THIS SINGLE order reduces the
        # absolute exposure. Never rely on a future fill on the opposite venue.
        step=dec(pair['instruments'][leg]['spec']['qty_step'])
        cut=(diff/step).to_integral_value(rounding=ROUND_CEILING)*step
        if cut<=diff or cut>quantities[leg] or abs(diff-cut)>=diff:
            return []
        try:
            return [self.reduction_intent(pair,leg,cut,bps)]
        except ValueError:
            return []

    def balance(self,pair):
        self._repairing.add(pair['id'])
        try:
            self._balance(pair)
        finally:
            self._repairing.discard(pair['id'])
            self.clear_repair_locks(pair)
            if pair.get('repair_stopped') and not pair.get('next_close'):
                # Stop this repair round, not recovery forever. The coordinator
                # will retire the remaining positions after the persisted delay.
                pair['next_close']=self.clock()+2
                self.store.pair(pair)

    def _balance(self,pair):
        self.refresh_pair(pair)
        if pair['state']=='UNKNOWN':
            self.close_pair(pair,'unknown_order')
            return
        if not self.resolve_exposure(pair):
            pair['repair_stopped']=True
            self.store.pair(pair)
            self.unhedged(pair)
            return
        if len(set(self.quantities(pair)))==1:
            self.store.set_meta('single_failures',0)
            return
        if pair.get('repair_stopped'):
            self.unhedged(pair)
            return
        self.unhedged(pair)
        if not pair['single_failure']:
            pair['single_failure']=True
            self.store.set_meta('single_failures',self.store.meta('single_failures',0)+1)
        self.store.pair(pair)
        for attempt in range(pair.get('repair_attempts',0)+1,4):
            a,b=self.quantities(pair)
            if a==b:
                return
            intents=self.repair_intents(pair,3+10*attempt)
            if not intents:
                break
            pair['repair_attempts']=attempt
            self.store.pair(pair)
            result=self.executor.execute(intents[0])
            self.refresh_pair(pair)
            # Confirm/query the one IOC, then reread BOTH venue positions before
            # choosing a new quantity. A zero fill or increased exposure ends repair.
            if not self.resolve_exposure(pair):
                pair['repair_stopped']=True
                self.store.pair(pair)
                self.unhedged(pair)
                return
            exposure=abs(self.quantities(pair)[0]-self.quantities(pair)[1])
            if result['state']=='UNKNOWN' or exposure>=abs(a-b):
                break
        if len(set(self.quantities(pair)))>1:
            pair['repair_stopped']=True
            self.store.pair(pair)
            self.unhedged(pair)

    def close_pair(self,pair,reason):
        self.roll_daily_upnl({},self.clock())
        self.refresh_pair(pair)
        if pair['state']=='CLOSED':
            return
        if self.clock()<pair.get('next_close',0):
            # Backoff governs new reductions, never delayed fill confirmation.
            self.executor.recover(startup=False,pair_id=pair['id'])
            self.refresh_pair(pair)
            return
        pair.update(exit_reason=pair.get('exit_reason',reason),close_attempts=pair.get('close_attempts',0)+1)
        self.store.pair(pair)
        try:
            self._close_pair(pair,reason)
        finally:
            # Even unavailable snapshots, journal failures and UNKNOWN results
            # retain a durable exit obligation. Delay starts AFTER this round.
            if pair['state']!='CLOSED':
                pair['next_close']=self.clock()+min(60,2**min(pair['close_attempts'],6))
                self.store.pair(pair)
                if pair.get('remote_pending') or len(set(self.quantities(pair)))>1:
                    self.unhedged(pair)
                self.store.event('CLOSE_RETRY',dict(pair_id=pair['id'],attempt=pair['close_attempts'],
                                                   next_close=pair['next_close']))
            else:
                pair['next_close']=0
                self.store.pair(pair)

    def _close_pair(self,pair,reason):
        if not self.resolve_exposure(pair):
            return
        if pair['state']=='CLOSED':
            return
        pair.update(state='CLOSING',exit_reason=reason)
        self.store.pair(pair)
        # The first two-leg close may create transient exposure. Subsequent
        # reductions repair that imbalance before another two-leg dispatch.
        for attempt in range(3):
            quantities=self.quantities(pair)
            indices=[n for n in (0,1) if quantities[n]>0]
            if not indices:
                return
            bps=3+10*attempt+(10 if reason=='stale_exit' else 0)
            if pair.get('close_repair') and quantities[0]!=quantities[1]:
                intents=self.repair_intents(pair,bps)
            else:
                instruments=[Instrument.load(pair['instruments'][n]) for n in indices]
                books=self.market.books(instruments)
                intents=[]
                for n,i,b in zip(indices,instruments,books):
                    try:
                        intents.append(self.reduction_intent(pair,n,quantities[n],bps,b))
                    except ValueError:
                        self.unhedged(pair)
            if not intents:
                return
            if len(intents)==1:
                after=list(quantities)
                after[intents[0]['leg']]-=dec(intents[0]['quantity'])
                if abs(after[0]-after[1])>abs(quantities[0]-quantities[1]):
                    self.unhedged(pair)
                    return
            # A crash or unreadable post-send snapshot must not leave the old
            # balanced snapshot looking like proof that this close is hedged.
            pair.update(remote_pending=True,close_repair=True)
            self.store.pair(pair)
            result=self.executor.execute_pair(intents)
            self.refresh_pair(pair)
            if not self.resolve_exposure(pair):
                return
            remaining=self.quantities(pair)
            pair['close_repair']=remaining[0]!=remaining[1]
            self.store.pair(pair)
            if pair['close_repair']:
                self.unhedged(pair)
            if any(o['state']=='UNKNOWN' for o in result):
                self.alert('unknown_close')
                return
            if len(intents)==1 and abs(remaining[0]-remaining[1])>=abs(quantities[0]-quantities[1]):
                return
        if any(self.quantities(pair)):
            self.alert('close_incomplete')

    def track(self,pair):
        self.roll_daily_upnl({},self.clock())
        self.refresh_pair(pair)
        if pair['state']=='UNKNOWN':
            self.close_pair(pair,pair.get('exit_reason','unknown_order'))
            return
        now=self.clock()
        instruments=[Instrument.load(i) for i in pair['instruments']]
        books=self.market.books(instruments)
        self.save_tracking_upnl(pair,books,now)
        for n,b in enumerate(books):
            if b:
                pair['last_books'][n]=b.to_dict()
                pair['last_good'][n]=b.received_at
        stale=max(now-t for t in pair['last_good'])
        pair['next_track']=now+track_interval(now-pair['opened_at'])
        self.store.pair(pair)
        if pair['state']=='CLOSING' or self.store.meta('state')=='HALTING':
            self.close_pair(pair,pair.get('exit_reason','circuit_breaker'))
            return
        if stale>=60:
            self.close_pair(pair,'stale_exit')
            return
        age=now-pair['opened_at']
        next_funding=min(s['next']-now for s in pair['funding'])
        if age>=7200 or next_funding<300:
            self.close_pair(pair,'max_age' if age>=7200 else 'funding_exit')
            return
        if now-pair.get('funding_checked_at',pair['opened_at'])>=300:
            try:
                fresh=self.market.current_funding(instruments)
                # Never roll an already-due settlement forward to hide an overrun.
                for old,new in zip(pair['funding'],fresh):
                    old['next']=min(old['next'],new['next'])
                pair['funding_checked_at']=now
                self.store.pair(pair)
                if min(s['next']-self.clock() for s in pair['funding'])<300:
                    self.close_pair(pair,'funding_exit')
                    return
            except Exception:
                self.alert('funding_refresh_failed')
                self.close_pair(pair,'funding_unknown')
                return
        if not all(books):
            return
        a,b=self.quantities(pair)
        if a!=b:
            self.balance(pair)
            return
        if not a:
            return
        try:
            sell=walk(books[0].bids,quantity=a)
            buy=walk(books[1].asks,quantity=b)
            spread=(dec(buy['vwap'])/dec(sell['vwap'])-1)*10000
            opens=list(self.store.iter_orders(pair_id=pair['id'],reduce_only=False))
            if len(opens)!=2:
                self.alert('entry_accounting_unknown')
                return
            opens.sort(key=lambda o:o['leg'])
            entry=[dec(o.get('vwap') or o.get('average_price') or o['price']) for o in opens]
            entry_spread=(entry[1]/entry[0]-1)*10000
            notional=a*entry[0]
            pnl=a*(dec(sell['vwap'])-entry[0]+entry[1]-dec(buy['vwap']))
            reason=exit_reason(age=age,spread=spread,entry_spread=entry_spread,pnl=pnl,
                               notional=notional,next_funding=next_funding,stale_age=stale,
                               baseline_at_open=pair.get('baseline_at_open'),excess_at_open=pair.get('excess_at_open'))
            if reason:
                # Retain fill-sim's estimate, never mislabel it realized accounting.
                try:
                    pair['fill_sim_close']=closing(pair,*books,now,stale=reason=='stale_exit')
                except Exception:
                    pair['fill_sim_close']=None
                    self.store.event('SIMULATION_CLOSE_UNAVAILABLE',{'pair_id':pair['id']})
                self.store.pair(pair)
                self.close_pair(pair,reason)
        except (ValueError,KeyError,TypeError):
            self.alert('exit_pricing_unavailable')

    def flatten_all(self,reason):
        halting=self.store.meta('state')=='HALTING'
        if halting and self.clock()<self.next_halt:
            return
        if halting:
            self.halt_attempts+=1
        try:
            self.executor.recover(startup=False)
            for p in self.store.pairs(active=True):
                try:
                    self.close_pair(p,reason)
                except Exception:
                    self.alert('flatten_retry_pending')
            if self.store.meta('state')=='HALTING':
                # A process must not exit successfully with residual or unknown exposure.
                try:
                    remote=self.accounts.position_snapshot()['positions']
                except Exception:
                    self.alert('flatten_snapshot_unavailable')
                    return
                if (not self.store.pairs(active=True) and not remote and not self.executor.pending_writes
                        and next(self.executor.iter_orders(states=(*NONTERMINAL,'INTENT')),None) is None):
                    self.store.set_meta('state','HALTED')
        finally:
            if halting:
                self.next_halt=self.clock()+min(60,2**min(self.halt_attempts,6))

    def tick(self):
        if self.store.meta('state')=='HALTED':
            return
        self.stops.flush()
        self.roll_daily_upnl({},self.clock())
        pending={o['pair_id'] for o in self.executor.iter_orders(states=NONTERMINAL)}
        self.executor.recover(startup=False)
        active=self.store.pairs(active=True)
        active_ids={p['id'] for p in active}
        for p in chain(active,(self.store.get_pair(pid) for pid in pending-active_ids)):
            if p is None:
                continue
            self.refresh_pair(p)
            if p['id'] in pending or self.needs_recovery(p):
                if self.store.meta('state')=='HALTING':
                    self.store.event('RECOVERY_ATTEMPT',dict(pair_id=p['id']))
                else:
                    self.recover_pair(p)
        if self.store.meta('state')=='HALTING':
            self.flatten_all('circuit_breaker')
            return
        if self.clock()-self.last_accounting_retry>=60:
            pending_pairs=set()
            for o in self.store.iter_orders(states=('TERMINAL',),accounting_pending=True):
                self.executor.account(o)
                pending_pairs.add(o['pair_id'])
            for pid in pending_pairs:
                p=self.store.get_pair(pid)
                if p is not None:
                    self.refresh_pair(p)
            self.last_accounting_retry=self.clock()
        for p in self.store.pairs(active=True):
            if self.clock()-self.last_reconcile>=60:
                self.reconcile()
                if self.store.meta('state') in ('HALTING','HALTED'):
                    return
            if self.clock()>=p.get('next_track',0):
                self.track(p)
        if self.clock()-self.last_reconcile>=60:
            self.reconcile()
        if self.store.meta('state')=='HALTING':
            self.flatten_all('circuit_breaker')
        elif self.store.meta('flatten_requested'):
            self.flatten_all('margin_ratio')
            if not self.store.pairs(active=True):
                self.store.set_meta('flatten_requested',False)
