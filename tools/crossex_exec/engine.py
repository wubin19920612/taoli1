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
from .accounts import local_positions, position_attributes
from .policy import Instrument, review, exit_reason, risk_decision, limit_price, send_review
from .execution import Executor, NONTERMINAL
from .limits import HardLimits, DEFAULT_VAR
from .background import SnapshotCache, PeriodicWorker, account_failure_details
from .stops import StopLocks
from .baseline import Baseline
from .admission import OpeningLimits, AdmissionError, BOOK_RETRIES, book_skew_ms, SKIP_REASONS
from .settings import Settings, risk_settings
from .sizing import size_plan
from .universe import Universe, check_mapping_ratio
from .external import ExternalRegistry, ExternalAdmin, ExternalUnverifiable, plain
from .external_accounting import ExternalAccounting
from .ownership import ProgramOwnership, bounded_pair_positions, retain_crash_claims, journal_positions
from .program_accounting import ProgramAccounting, read_program_snapshot
from .external_fx import NATIVE_USD, require_fresh_fx
from .position_values import key_currency, notional_values, value_snapshot, usd_value
from .program_risk import require_program_proof, program_notionals


ACCOUNT_RECOVERY_SNAPSHOTS = 3


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
        # Lock order: coordinator (when needed) -> Executor -> Store. All
        # account/alert/stop state uses the SAME Store RLock, never an AB/BA pair.
        self._alert_lock=self._lifecycle_lock=store.lock
        self._coordinator_lock=threading.RLock()
        self._account_fresh_streak=self._liability_clear_streak=0
        self._account_fresh_at=None
        self._equity_admin_active=False
        self._closed=False
        self.workers=[]
        self.bases=None
        self.baseline=Baseline(store,clock)
        self.opening_limits=OpeningLimits(config)
        self.settings=Settings(store,clock)
        self.universe=Universe(store,clock)
        self.external=ExternalRegistry(store,clock)
        self.external_accounting=ExternalAccounting(store,self.external,clock)
        self.requested_markets=set()
        self.next_catalog=self.next_fees=0
        self.fee_keys=set()
        self.next_halt=0
        self.halt_attempts=0
        self._repairing=set()
        self.account_cache=SnapshotCache(clock,store)
        self.stops=StopLocks(store)
        self.limits=HardLimits(store,self.account_cache.read,config,alert=self.alert,clock=clock)
        if self.limits.program_scope:
            self.external=ProgramOwnership(store,clock)
            self.external_accounting=ProgramAccounting(store,self.external,clock)
        self.executor=Executor(store,{'crossex':accounts.cross,'bitget':accounts.bitget},live=live,
                               can_open=self.can_open,check_order=self.check_order,check_pair=self.check_pair,
                               check_release=self.check_release,alert=self.alert,clock=clock)

    def alert(self,reason,*,details=None,event_kind=None):
        with self._alert_lock:
            values=details or {}
            if self.limits.program_scope or 'account_failure' in values:
                from .alerts import emit
                owner=values.get('pair_id','global') if reason in ('unhedged_position','repair_stopped') else 'global'
                incident=owner not in self.stops.reasons.get(reason,())
                self.stops.add(reason,owner)
                emit(self.store,self.clock,reason,values,event_kind=event_kind,
                     sink=self.alert_sink,new_incident=incident,
                     cause=values.get('account_failure',{}).get('reason'),
                     status={name:{k:v for k,v in values[name].items() if k in
                         ('stage','route','endpoint','page','condition','error_kind','http_status','api_code')}
                         for name in ('activity_failure','income_failure') if name in values} or None)
                return
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
        with self.store.lock:
            self.external.check_order(intent)
            if not intent['reduce_only'] and self.limits.program_scope:
                self.require_external_income()
                self.external.split(self.account_cache.read())
            self.limits.check_order(intent)

    def check_pair(self,intents):
        # The API writer shares this path-scoped lock. One version/decision set
        # is observed at the shared admission linearization point for BOTH legs.
        with self.store.lock:
            try:
                return self._check_pair(intents)
            except ValueError as error:
                reason=error.reason if isinstance(error,AdmissionError) else str(error)
                if reason in SKIP_REASONS:
                    pair=self.store.get_pair(intents[0]['pair_id']) or {}
                    self.store.skip(pair.get('trigger_id','pair:'+str(pair.get('id'))),reason,
                                    stage='send',pair_id=pair.get('id'))
                raise

    def _check_pair(self,intents):
        if len(intents)!=2 or {i['leg'] for i in intents}!={0,1} or len({i['pair_id'] for i in intents})!=1:
            raise ValueError('opening_pair_missing')
        pair=self.store.get_pair(intents[0]['pair_id'])
        if pair is None or pair['state'] in ('CLOSED','SHADOW','SKIPPED'):
            raise ValueError('opening_pair_missing')
        orders=sorted(intents,key=lambda o:o['leg'])
        instruments=[Instrument.load(i) for i in pair['instruments']]
        config=pair.get('config_at_open')
        limits=self.opening_limits.restricted(config['values']) if config else self.opening_limits
        self.check_send_config(pair,instruments)
        if config:
            self.check_volumes(instruments,config['values'])
        venues=tuple(i.market.exchange for i in instruments)
        for attempt in range(BOOK_RETRIES+1):
            try:
                state=self.baseline.require(pair['base'],*venues)
                limits.flat(state)
                limits.flat(dict(state,baseline_bps=pair['baseline_at_open']))
                fees=self.fees_for(instruments)
                skew=limits.books(pair['last_books'],self.clock(),send=True)
                limits.funding(pair['funding'])
                self.check_send_size(pair,orders,instruments)
                pricing=send_review(pair,orders,fees,self.clock())
                evidence=limits.outlier(venues,pair['last_books'],self.reference_snapshot(instruments),
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
                limits.books(pair['last_books'],self.clock(),send=True)
                if config and self.settings.read()!=config:
                    raise ValueError('config_version_changed')
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
        with self.store.lock:
            pair=self.store.get_pair(intents[0]['pair_id'])
            if pair is None or pair['state'] in ('CLOSED','SHADOW','SKIPPED'):
                raise ValueError('opening_pair_missing')
            try:
                instruments=[Instrument.load(i) for i in pair['instruments']]
                self.check_send_config(pair,instruments)
                self.check_send_size(pair,sorted(intents,key=lambda o:o['leg']),instruments)
                if not self.can_open(ignore_cids={i['cid'] for i in intents}):
                    raise ValueError('open_gate_changed')
                config=pair.get('config_at_open')
                if config:
                    self.check_cached_volumes(instruments,config['values'])
                for intent in intents:
                    self.check_order(intent)
                limits=self.opening_limits.restricted(config['values']) if config else self.opening_limits
                # Last check: all preceding reads/commits consume this budget.
                # Success performs no further I/O before releasing BOTH senders.
                limits.books(pair['last_books'],self.clock(),send=True)
            except ValueError as error:
                reason=error.reason if isinstance(error,AdmissionError) else str(error)
                if reason in SKIP_REASONS:
                    self.store.skip(pair.get('trigger_id','pair:'+pair['id']),reason,
                                    stage='release',pair_id=pair['id'])
                raise

    def check_send_config(self,pair,instruments):
        for i in instruments:
            self.external.check_order(dict(instrument=i.dump()))
        config=pair.get('config_at_open')
        if config and self.settings.read()!=config:
            raise ValueError('config_version_changed')
        if any(i.market.key not in self.market.mapping for i in instruments):
            raise ValueError('send_catalog_unavailable')
        if config:
            current=self.universe.resolve([self.market.mapping[i.market.key] for i in instruments])
            if [i.dump() for i in current]!=[i.dump() for i in instruments]:
                raise ValueError('send_catalog_changed')

    def check_send_size(self,pair,orders,instruments):
        config=pair.get('config_at_open')
        if not config:
            return
        common_books=[Book(**b) for b in pair['last_books']]
        # Journal books use common units; ratio validation expects native prices.
        check_mapping_ratio(instruments,[Book([[str(dec(b.bids[0][0])*i.multiplier),'1']],
            [[str(dec(b.asks[0][0])*i.multiplier),'1']],b.received_at,b.exchange_at) for i,b in zip(instruments,common_books)])
        own=sum(dec(o['quantity'])*dec(o['price']) for o in orders)
        available=max(dec(0),min(self.limits.total,dec(config['values']['max_total_open_usd']))-
                      self.limits.exposure(self.account_cache.read())+own)
        plan=size_plan([i.common() for i in instruments],common_books,pair['open_interests'],config['values'],available,self.clock())
        if any(dec(o['quantity'])*i.multiplier>dec(plan['quantity']) for o,i in zip(orders,instruments)):
            raise ValueError('send_size_changed')

    def fetch_opening_books(self,instruments):
        # MarketData submits the whole pair together through the existing
        # shared public queue/limiter. Never refresh only the failing leg.
        try:
            fetch=getattr(self.market,'opening_books',self.market.books)
            return [i.common_book(b) for i,b in zip(instruments,fetch(instruments))]
        except Exception:
            return [None,None]

    def opening_review(self,instruments,fees,funding,turnovers,baseline,config=None):
        config=config or self.settings.read()
        values=config['values']
        limits=self.opening_limits.restricted(values)
        interests=None
        common=[i.common() for i in instruments]
        for attempt in range(BOOK_RETRIES+1):
            books=self.fetch_opening_books(instruments)
            try:
                limits.books(books,self.clock())
                check_mapping_ratio(common,books)
                if interests is None:
                    interests=self.market.open_interests(instruments,books)
                # Public OI reads also consume the opening book age budget.
                limits.books(books,self.clock())
                available=max(dec(0),min(self.limits.total,dec(values['max_total_open_usd']))-
                              self.limits.exposure(self.account_cache.read()))
                sizing=size_plan(common,books,interests,values,available,self.clock())
                fill=review(common,books,fees,funding,turnovers,self.clock(),baseline_bps=baseline['baseline_bps'],
                            quantity=sizing['quantity'],min_turnover=values['min_quote_volume_usd'],min_excess_bps=values['min_excess_bps'])
                fill.update(sizing=sizing,open_interests=interests)
                evidence=limits.outlier(tuple(i.market.exchange for i in instruments),books,
                    self.reference_snapshot(instruments),fill['excess_bps'],self.clock())
                limits.books(books,self.clock())
                return books,fill,evidence
            except AdmissionError as error:
                if error.reason not in ('book_skew','book_stale') or attempt==BOOK_RETRIES:
                    raise

    def reference_snapshot(self,instruments):
        snapshot=self.baseline.latest(instruments[0].base)
        # Equity references must track the selected underlying too. An unselected
        # ADR/other exchange cannot be the third venue that admits an equity pair.
        if self.universe.eligibility(instruments[0])[0]=='listed_equity':
            allowed={i.market.exchange for i in self.universe.compatible_markets(instruments)}
            snapshot=dict(snapshot,mids={ex:mid for ex,mid in snapshot['mids'].items() if ex in allowed})
        return snapshot

    def refresh_accounts(self):
        started=self.clock()
        revision=self.store.meta('equity_baseline_revision',0)
        try:
            snapshot=self.read_account_snapshot()
            with self._lifecycle_lock:
                if not self._closed:
                    if self._equity_admin_active or revision!=self.store.meta('equity_baseline_revision',0):
                        self.store.event('ACCOUNT_REFRESH_SUPERSEDED',dict(started_at=started,revision=revision))
                        return
                    if self.limits.program_scope:
                        # A certificate belongs to one account observation. The
                        # coordinator must recompute PnL before this new cache
                        # can admit either leg, even within the 10-second TTL.
                        self.risk='STOP'
                        self.last_reconcile=0
                        self.store.set_meta('external_income',dict(verified=False,reason='external_refresh_pending',at=started))
                        self.store.set_meta('account_risk_snapshot',plain(dict(snapshot,captured_at=started)))
                    self.account_cache.publish(snapshot,started)
                    self.limits.check_equity(snapshot)
                    self.roll_daily_upnl(snapshot,started)
                    self.observe_account_snapshot(snapshot,started)
        except Exception as error:
            with self._lifecycle_lock:
                if not self._closed:
                    self._account_fresh_streak=self._liability_clear_streak=0
                    self.risk='STOP'
                    self.last_reconcile=0
                    self.account_cache.invalidate()
                    self.preserve_account_failure(error)
                    diagnostic=account_failure_details(error)
                    self.store.event('ACCOUNT_REFRESH_FAILED',diagnostic)
                    self.alert('account_snapshot_unavailable',details=dict(account_failure=diagnostic))

    def preserve_account_failure(self,error):
        evidence=getattr(error,'account_snapshot',None)
        if evidence is not None:
            self.store.set_meta('account_risk_snapshot',plain(evidence))
            if self.limits.program_scope:
                self.risk='STOP'
                self.store.set_meta('external_failed_snapshot',plain(evidence))
                self.store.set_meta('external_income',dict(verified=False,reason='external_identity_unverifiable',at=self.clock()))
                self.alert('external_identity_unverifiable')

    def observe_account_snapshot(self,snapshot,captured_at):
        """Only distinct, consecutive, fully fresh reads recover their own lock."""
        with self.store.lock:
            now=self.clock()
            age=now-captured_at
            fresh=0<=age<=10
            refresh=self.store.meta('account_refresh')
            if not refresh or captured_at>refresh['captured_at']:
                self.store.set_meta('account_refresh',dict(captured_at=captured_at,completed_at=now,
                                    age_seconds=age,fresh=fresh))
            if not fresh:
                self._account_fresh_streak=self._liability_clear_streak=0
                self.alert('account_snapshot_unavailable')
                return
            previous=self._account_fresh_at
            if previous is not None and captured_at<=previous:
                return
            if previous is not None and captured_at-previous>10:
                self._account_fresh_streak=self._liability_clear_streak=0
            self._account_fresh_at=captured_at
            self._account_fresh_streak+=1
            self._liability_clear_streak=0 if self.limits.liabilities(snapshot)['blocking'] else self._liability_clear_streak+1
            if self._account_fresh_streak>=ACCOUNT_RECOVERY_SNAPSHOTS:
                self.stops.recovered('account_snapshot_unavailable','ACCOUNT_SNAPSHOT_RECOVERED',
                    dict(consecutive=self._account_fresh_streak,captured_at=captured_at,age_seconds=age))
            if self._liability_clear_streak>=ACCOUNT_RECOVERY_SNAPSHOTS:
                self.stops.recovered('account_liability','ACCOUNT_LIABILITY_RECOVERED',
                    dict(consecutive=self._liability_clear_streak,captured_at=captured_at))

    def rebaseline(self,*,confirm,reason,source,release_halt=False):
        from .equity import EquityAdmin
        with self._coordinator_lock:
            if self.limits.program_scope:
                self.invalidate_external_income('external_admin_pending')
            return EquityAdmin(self.store,self.accounts,engine=self,clock=self.clock).apply(
                confirm=confirm,reason=reason,source=source,release_halt=release_halt)

    def external_admin(self,**kwargs):
        with self._coordinator_lock:
            with self.executor.lock,self.store.lock:
                self._equity_admin_active=True
            try:
                self.invalidate_external_income('external_admin_pending')
                result=ExternalAdmin(self.store,self.accounts,engine=self,clock=self.clock).apply(**kwargs)
                if self.limits.program_scope and not isinstance(self.external,ProgramOwnership):
                    self.external=ProgramOwnership(self.store,self.clock)
                    self.external_accounting=ProgramAccounting(self.store,self.external,self.clock)
                return result
            finally:
                with self.store.lock:
                    self._equity_admin_active=False

    def invalidate_external_income(self,reason):
        # A failed new administrative observation cannot leave a prior proof
        # authorizing sends. Reconciliation must certify a new observation.
        with self.store.lock:
            self.risk='STOP'
            self.last_reconcile=0
            self.store.set_meta('external_income',dict(verified=False,reason=reason,at=self.clock()))

    def read_account_snapshot(self):
        snapshot=(read_program_snapshot(self.accounts,self.store,self.clock) if self.limits.program_scope
                  else self.accounts.snapshot())
        currencies={r['identity']['quote'] for r in self.external.records().values()}-NATIVE_USD
        if self.limits.program_scope:
            from .external import route_symbol
            currencies|={key_currency('|'.join((*route_symbol(r['instrument']),r['position_side'])))
                for r in self.store.iter_orders(exclude_states=('SHADOW','UNSENT'))}-NATIVE_USD
        try:
            currencies|={key_currency(k) for k in snapshot['notionals']}-NATIVE_USD
        except (KeyError,ValueError,TypeError):
            pass  # value_snapshot and final risk checks will refuse unknown units.
        snapshot['external_fx']={}
        if currencies:
            try:
                snapshot['external_fx']=self.market.spot_fx(currencies)
            except Exception:
                # Missing public prices must not hide accounts or hard exits.
                snapshot['external_fx_error']='external_fx_unavailable'
        return value_snapshot(snapshot,self.clock())

    def require_external_income(self):
        with self.store.lock:
            return self._require_external_income()

    def _require_external_income(self):
        if self.limits.program_scope:
            try:
                snapshot=self.account_cache.read()
                require_program_proof(self.store,snapshot,self.clock())
                notional_values(program_notionals(self.store,snapshot,self.clock()),self.clock())
                return
            except (KeyError,TypeError,ValueError,ArithmeticError):
                raise ExternalUnverifiable('external_income_unverifiable') from None
        value=self.store.meta('external_income',{})
        snapshot=self.account_cache.read()
        if (value.get('verified') is not True or value.get('revision')!=self.store.meta('external_attribution_revision',0)
                or value.get('snapshot_version')!=snapshot['snapshot_version']
                or value.get('equity_revision')!=self.store.meta('equity_baseline_revision',0)
                or value.get('day')!=day(self.clock()) or not 0<=self.clock()-value.get('captured_at',0)<=10):
            raise ExternalUnverifiable('external_income_unverifiable')
        try:
            require_fresh_fx(value.get('external_fx',{}),self.clock())
            notional_values(snapshot,self.clock())
        except (ValueError,KeyError,TypeError,ArithmeticError):
            raise ExternalUnverifiable('external_income_unverifiable') from None

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
        instruments=tuple(i for i in self.market.mapping.values() if i.spec.base in (self.bases or ()) or i.market.key in self.requested_markets)
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
            mids,bases=self.market.sample_quotes(self.settings.read()['values']['sampling_min_quote_volume_usd'])
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

    def background_error(self,worker,kind,location):
        with self._lifecycle_lock:
            if not self._closed:
                self.store.event('BACKGROUND_FAILED',dict(worker=worker,error_type=kind,location=location))

    def close(self):
        with self._lifecycle_lock:
            self._closed=True
        for worker in self.workers:
            worker.close()

    def can_open(self,ignore_cids=()):
        try:
            if self.limits.program_scope:
                self.require_external_income()
            elif self.account_cache.value is not None:
                notional_values(self.account_cache.read(),self.clock())
        except (ValueError,TypeError,KeyError):
            return False
        exposed=any(p.get('remote_pending') or len(set(map(dec,p.get(field,['0','0']))))>1
                    for p in self.store.pairs(active=True) if p['state'] not in ('SHADOW','SKIPPED')
                    for field in ('remaining','remote_remaining'))
        recent=rejected=0
        for o in self.executor.iter_orders(sent_since=self.clock()*1000-3600000):
            if o['cid'] not in ignore_cids:
                recent+=1
                rejected+=o['state']=='REJECTED' or o.get('rejected',False)
        local_ok=(self.store.meta('single_failures',0)<3 and (not recent or dec(rejected)/recent<=dec('.3')))
        return (local_ok and not exposed and self.risk=='RUN' and not self._equity_admin_active and not self.stop_file.exists() and not self.stops
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
        if self.limits.program_scope:
            retain_crash_claims(self.store)
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
            self.start_background(bases)
            self.flatten_all('startup_halt_recovery')
            return
        self.account_cache.publish(self.snapshot,started)
        if any(o['clientOid'] not in known for o in self.snapshot.get('open_orders',[])):
            if self.store.meta('state')!='HALTING':
                raise ValueError('foreign_bitget_unfilled_orders')
            self.alert('foreign_bitget_unfilled_orders')
        self.limits.initialize(self.snapshot)
        # Rebuild from the immutable journal, including crash after terminal before pair save.
        for pair in self.store.pairs(active=True):
            self.refresh_pair(pair)
        if self.store.meta('state')=='HALTING':
            # Refreshers are independent of the coordinator's reduction retry.
            # Expired catalogs/accounts must recover even while openings stay locked.
            self.start_background(bases)
            # A known equity breach must not wait for catalog/fee bootstrap.
            self.flatten_all('startup_equity_drop')
            # Residual exposure stays in-process; tick owns bounded backoff retries.
            return
        self.reconcile()
        if self.store.meta('state')!='HALTED':
            self.start_background(bases)

    def totals(self,*,program=True):
        if program and self.limits.program_scope:
            value=require_program_proof(self.store,self.snapshot,self.clock())
            return dec(value['program_daily']),dec(value['program_total'])
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
        program_floating=floating
        external=bool(self.external.records()) and program
        if external:
            projection=self.external.split(self.snapshot)
            program_floating=dec(projection['upnl'])
            daily+=dec(self.store.meta('external_income',{})['program_funding'])
        offset=self.store.meta('risk_pnl_baseline',{})
        total-=dec(offset.get('total',0))
        if offset.get('day')==today:
            daily-=dec(offset.get('daily',0))
        baseline=self.roll_daily_upnl(self.snapshot or {},(self.snapshot or {}).get('captured_at',self.clock()))
        if external:
            baseline=self.roll_program_upnl(projection,self.snapshot['captured_at'])
        if not baseline or baseline['day']!=today:
            raise DailyBaselinePending('daily_baseline_pending')
        return daily+program_floating-dec(baseline['upnl']),total+floating

    def roll_program_upnl(self,projection,captured):
        today=day(self.clock())
        baseline=self.store.meta('program_daily_upnl')
        last=self.store.meta('program_last_upnl')
        observation=dict(upnl=projection['upnl'],positions=projection['upnl_by_position'],captured_at=captured)
        if not baseline or baseline['day']!=today:
            if not last:
                raise ExternalUnverifiable('external_baseline_unverifiable')
            baseline=dict(last,day=today)
            self.store.set_meta('program_daily_upnl',baseline)
        if not last or captured>=last['captured_at']:
            self.store.set_meta('program_last_upnl',observation)
        return baseline

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
        if self.limits.program_scope:
            self.save_program_tracking_upnl(pair,books,now)
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
                price=dec(entry.get('vwap') or entry.get('average_price') or entry['price'])/Instrument.load(entry['instrument']).multiplier
                mark=dec(walk(books[n].bids if n==0 else books[n].asks,quantity=q)['vwap'])
                positions[key]=str(q*(mark-price)*(1 if n==0 else -1))
        except (KeyError,ValueError,TypeError):
            return
        self.roll_daily_upnl(dict(upnl=str(sum(map(dec,positions.values()),dec(0))),upnl_by_position=positions),now)

    def save_program_tracking_upnl(self,pair,books,now):
        with self.store.lock:
            try:
                # Never seed a program observation from whole-account last_upnl.
                # Project exact contracts, then use only their confirmed fills.
                owned=self.external.split(dict(positions=journal_positions(self.store)),pnl=False)['positions']
                prior=self.store.meta('program_last_upnl') or {}
                positions={k:v for k,v in prior.get('positions',{}).items() if k in owned}
                quantities=journal_positions(self.store,pair_id=pair['id'])
                entries={o['leg']:o for o in self.executor.iter_orders(pair_id=pair['id'],reduce_only=False)}
                fx=(self.store.meta('account_snapshot') or {}).get('external_fx',{})
                for n in (0,1):
                    key=self.position_key(pair,n)
                    native=quantities.get(key,dec(0))
                    if not native:
                        positions.pop(key,None)
                        continue
                    entry=entries[n]
                    multiplier=Instrument.load(entry['instrument']).multiplier
                    price=dec(entry['vwap'])/multiplier
                    quantity=native*multiplier
                    mark=dec(walk(books[n].bids if n==0 else books[n].asks,quantity=quantity)['vwap'])
                    value=quantity*(mark-price)*(1 if n==0 else -1)
                    positions[key]=str(usd_value(value,key_currency(key),fx,now,upper=False))
                if set(positions)!=set(owned):
                    raise ValueError('program_position_mark_unknown')
                self.roll_program_upnl(dict(upnl=str(sum(map(dec,positions.values()),dec(0))),
                    upnl_by_position=positions),now)
            except (KeyError,ValueError,TypeError,ArithmeticError):
                # Missing program observations stop openings, never recovery.
                self.alert('program_income_unverifiable')

    def reconcile_positions(self,snapshot):
        if self.limits.program_scope or self.external.records():
            snapshot=self.external.split(snapshot)
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
        expected=local_positions(reconciled_pairs())
        same=snapshot['positions']==expected
        if self.limits.program_scope:
            conflicts={k:dict(expected=str(expected.get(k,0)),observed=str(q),
                reason='该合约远端仓位超出程序账本，禁止混仓；仅按程序数量减仓')
                for k,q in snapshot['positions'].items() if dec(q)>expected.get(k,dec(0))}
            if conflicts!=self.store.meta('program_contract_conflicts',{}):
                self.store.set_meta('program_contract_conflicts',conflicts)
                if conflicts:
                    from .alerts import emit
                    emit(self.store,self.clock,'program_contract_mixed',dict(contracts=conflicts),
                         event_kind='PROGRAM_CONTRACT_MIXED',sink=self.alert_sink,status=conflicts)
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

    def observe_margin_modes(self,snapshot):
        # Parsing is observational. Only verified registered contracts may be
        # isolated; program and unregistered exposure retain an opening lock.
        try:
            projection=self.external.split(snapshot,pnl=False)
        except ExternalUnverifiable:
            projection=snapshot
        isolated=any(v['margin_mode']=='ISOLATED' and k in projection['positions']
                     for k,v in position_attributes(snapshot).items())
        if isolated:
            self.alert('isolated_position')
        else:
            self.stops.recovered('isolated_position','POSITION_MARGIN_MODE_RECOVERED',
                                 dict(captured_at=snapshot['captured_at']))

    def reconcile(self):
        try:
            # On startup/tests a direct read seeds the cache. Once running, account
            # I/O belongs to its own worker; risk reads cannot block position tracking.
            if self.workers:
                self.snapshot=self.account_cache.read()
            else:
                started=self.clock()
                self.snapshot=self.read_account_snapshot()
                self.account_cache.publish(self.snapshot,started)
                self.snapshot=self.account_cache.read()
            if self.external.records():
                with self.store.lock:
                    if self.snapshot['snapshot_version']==self.store.meta('account_snapshot_version'):
                        self.store.set_meta('account_risk_snapshot',plain(self.snapshot))
            equity_halt=self.limits.check_equity(self.snapshot)
            self.roll_daily_upnl(self.snapshot,self.snapshot['captured_at'])
            self.observe_account_snapshot(self.snapshot,self.snapshot['captured_at'])
            self.observe_margin_modes(self.snapshot)
            if day(self.snapshot['captured_at'])!=day(self.clock()):
                self.risk='STOP'
                if equity_halt:
                    self.flatten_all('circuit_breaker')
                return
            same=self.reconcile_positions(self.snapshot)
            self.last_reconcile=self.clock()
            income=None
            if self.limits.program_scope:
                projection=self.external.split(self.snapshot)
                income=self.external_accounting.evaluate(self.snapshot,projection)
                equity_halt=self.limits.check_equity(self.snapshot)
                self.store.set_meta('program_projection',plain({k:projection[k] for k in
                    ('positions','upnl','upnl_by_position','external_positions','captured_at')}))
                self.stops.recovered('external_identity_unverifiable','EXTERNAL_IDENTITY_RECOVERED',
                    dict(captured_at=self.snapshot['captured_at']))
                self.stops.recovered('external_income_unverifiable','EXTERNAL_INCOME_RECOVERED',
                    dict(captured_at=self.snapshot['captured_at']))
                for reason in ('external_baseline_unverifiable','history_coverage','program_income_unverifiable'):
                    self.stops.recovered(reason,'PROGRAM_ACCOUNTING_RECOVERED',
                        dict(captured_at=self.snapshot['captured_at']))
            daily,total=self.totals()
            current_day=day(self.clock())
            if self.store.meta('daily_loss_day') not in (None,current_day):
                self.store.set_meta('daily_loss_day',None)
                self.stops.discard('daily_loss')
            # Legacy dedicated-account mode retains account equity deltas.
            # Program mode uses only the certified journal/funding/mark values.
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
                if not self.limits.program_scope:
                    total=min(total,dec(equity)-dec(baseline))
                    daily=min(daily,dec(equity)-dec(daily_base['equity']))
            recent=rejected=0
            for o in self.store.iter_orders(sent_since=self.clock()*1000-3600000):
                recent+=1
                rejected+=o['state']=='REJECTED' or o.get('rejected',False)
            settings=self.risk_settings()
            self.store.set_meta('account_loss_current',dict(daily=str(daily),total=str(total),at=self.clock(),
                scope='program' if self.limits.program_scope else 'dedicated_account',
                snapshot_version=self.snapshot['snapshot_version']))
            self.risk=risk_decision(daily=daily,total=total,margins=self.snapshot['margins'],
                single_failures=self.store.meta('single_failures',0),rejected=rejected,attempted=recent,reconciled=same,
                daily_limit=settings['daily_loss_stop_usd'],total_limit=settings['total_loss_stop_usd'])
            if daily<=-dec(settings['daily_loss_stop_usd']):
                self.store.set_meta('daily_loss_day',current_day)
                self.alert('daily_loss')
            if self.store.meta('daily_loss_day')==current_day and self.risk=='RUN':
                self.risk='STOP'
            self.store.event('RECONCILE',dict(matched=same,margins=self.snapshot['margins'],daily=str(daily),total=str(total),risk=self.risk))
            if (total<=-dec(settings['total_loss_stop_usd']) or equity_halt) and self.store.meta('state')!='HALTED':
                self.store.set_meta('state','HALTING')
                if not equity_halt:
                    self.store.set_meta('halt_reason','total_loss')
            if self.risk in ('HALTED','FLATTEN') or self.store.meta('state')=='HALTING':
                self.flatten_all('circuit_breaker')
        except DailyBaselinePending:
            self.risk='STOP'
        except ExternalUnverifiable as error:
            self.risk='STOP'
            reason=('program_income_unverifiable' if isinstance(self.external_accounting,ProgramAccounting)
                    else 'external_identity_unverifiable' if str(error) in ('external_identity_unverifiable','external_catalog_unavailable')
                    else 'external_income_unverifiable')
            details={}
            if self.snapshot.get('activity',{}).get('failure'):
                details['activity_failure']=self.snapshot['activity']['failure']
            if getattr(error,'diagnostic',None):
                details['income_failure']=error.diagnostic
            self.store.set_meta('external_income',dict(verified=False,reason=str(error),at=self.clock(),**details))
            self.store.set_meta('external_failed_snapshot',plain(self.snapshot))
            self.alert(reason,details=details)
            self.enforce_account_protection()
        except Exception as error:
            self.risk='STOP'
            self._account_fresh_streak=self._liability_clear_streak=0
            self.preserve_account_failure(error)
            self.alert('account_snapshot_unavailable')
            self.enforce_account_protection()

    def enforce_account_protection(self):
        """Attribution/read failure must never suppress full-account hard exits."""
        snapshot=self.snapshot or {}
        if not snapshot:
            return
        equity_halt=self.limits.check_equity(snapshot)
        if self.limits.program_scope:
            # A failed ledger may not turn outside PnL/capital into program loss.
            # Already latched program halts and whole-account margin exits survive.
            margin=any(m is not None and dec(m)<2 for m in snapshot.get('margins',()))
            if equity_halt or margin:
                self.flatten_all('circuit_breaker')
            return
        baseline=self.store.meta('equity_baseline')
        loss=dec(snapshot['equity'])-dec(baseline) if baseline is not None and snapshot.get('equity') is not None else dec(0)
        try:
            _,total=self.totals(program=False)
            loss=min(loss,total)
        except (ValueError,KeyError,TypeError):
            pass
        total_halt=loss<=-dec(self.risk_settings()['total_loss_stop_usd'])
        margin=any(m is not None and dec(m)<2 for m in snapshot.get('margins',()))
        if total_halt and self.store.meta('state') not in ('HALTING','HALTED'):
            self.store.set_meta('state','HALTING')
            self.store.set_meta('halt_reason','total_loss')
        if equity_halt or total_halt or margin:
            self.flatten_all('circuit_breaker')

    def risk_settings(self):
        return risk_settings(self.store)

    def check_new_risk(self,values):
        # A tighter NEW version can deny entry without liquidating older positions.
        snapshot=self.account_cache.read()
        if self.limits.check_equity(snapshot):
            self.flatten_all('account_equity_drop')
            raise ValueError('account_equity_halt')
        if self.limits.liabilities(snapshot)['blocking']:
            raise ValueError('account_liability')
        if self.limits.program_scope:
            loss=require_program_proof(self.store,snapshot,self.clock())
            if (dec(loss['program_daily'])<=-dec(values['daily_loss_stop_usd'])
                    or dec(loss['program_total'])<=-dec(values['total_loss_stop_usd'])):
                raise ValueError('runtime_loss_limit')
            if dec(loss['program_total'])<=-min(self.limits.drop,dec(values['max_equity_drop_usd'])):
                raise ValueError('runtime_equity_limit')
            return
        loss=self.store.meta('account_loss_current',{})
        if (dec(loss.get('daily',0))<=-dec(values['daily_loss_stop_usd'])
                or dec(loss.get('total',0))<=-dec(values['total_loss_stop_usd'])):
            raise ValueError('runtime_loss_limit')
        baseline=self.store.meta('account_equity_baseline')
        if any(dec(baseline[v])-dec(q)>=min(self.limits.drop,dec(values['max_equity_drop_usd']))
               for v,q in self.limits.equities(snapshot).items()):
            raise ValueError('runtime_equity_limit')

    def check_volumes(self,instruments,values):
        return self.validate_volumes(instruments,values,self.market.turnovers(instruments))

    def check_cached_volumes(self,instruments,values):
        return self.validate_volumes(instruments,values,self.market.cached_turnovers(instruments))

    def validate_volumes(self,instruments,values,turnovers):
        try:
            if len(turnovers)!=2 or any(dec(v)<0 for v in turnovers):
                raise ValueError('volume_unknown')
        except (ValueError,TypeError,ArithmeticError):
            raise ValueError('volume_unknown') from None
        if any(dec(v)<dec(values['min_quote_volume_usd']) for v in turnovers):
            raise ValueError('volume_too_low')
        if values['min_venues_with_volume']>2:
            if len(self.universe.volume_venues(instruments,values))<values['min_venues_with_volume']:
                raise ValueError('volume_too_low')
        return turnovers

    def ingest(self,payload):
        with self._coordinator_lock:
            return self._ingest(payload)

    def _ingest(self,payload):
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
            for i in instruments:
                self.external.check_order(dict(instrument=i.dump()))
            config=self.settings.read()
            values=config['values']
            opening_limits=self.opening_limits.restricted(values)
            self.check_new_risk(values)
            for m,i in zip(markets,instruments):
                i.spec.validate(m)
            instruments=self.universe.resolve(instruments)
            base=instruments[0].base
            if self.bases and base not in self.bases:
                raise ValueError('base_not_whitelisted')
            if payload['symbol']!=base:
                raise ValueError('trigger_base_mismatch')
            active=self.store.pairs(active=True)
            if len(active)>=min(3,values['max_open_pairs']) or any(p['base']==base for p in active):
                raise ValueError('position_limit')
            if now-self.store.meta('mapping')['at']>21600:
                raise ValueError('reference_data_stale')
            baseline=self.baseline.require(base,*(i.market.exchange for i in instruments))
            opening_limits.flat(baseline)
            self.requested_markets.update(i.market.key for i in instruments)
            try:
                fees=self.fees_for(instruments)
            except ValueError:
                self.accounts.fees(instruments)
                fees=self.fees_for(instruments)
            self.accounts.check_instruments(instruments)
            try:
                funding=self.market.funding(instruments)
            except Exception:
                raise AdmissionError('funding_diff') from None
            opening_limits.funding(funding)
            turnovers=self.check_volumes(instruments,values)
            books,fill,evidence=self.opening_review(instruments,fees,funding,turnovers,baseline,config)
            if self.clock()-now>120 or not self.can_open():
                raise ValueError('open_gate_changed')
        except Exception as error:
            reason=(error.reason if isinstance(error,AdmissionError) else str(error)
                    if isinstance(error,ValueError) and str(error) in SKIP_REASONS+(
                        'baseline_warming','structural_spread','pair_cooldown','mapping_unconfirmed','excluded',
                        'external_contract_forbidden') else 'review_failed_closed')
            self.store.skip(identity,reason,stage='review')
            return
        pair=dict(id=uuid.uuid4().hex,trigger_id=identity,base=base,state='OPENING',opened_at=self.clock(),
                  baseline_at_open=fill['baseline_bps'],excess_at_open=fill['excess_bps'],
                  open_book_skew_ms=float(book_skew_ms(books)),close_book_skew_ms=None,outlier_at_open=evidence,
                  instruments=[i.dump() for i in instruments],fill=fill,funding=funding,turnovers=turnovers,
                  config_at_open=config,sizing=fill['sizing'],open_interests=fill['open_interests'],
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
                    side=side,position_side='long' if leg==0 else 'short',quantity=str(dec(quantity)/i.multiplier),price=str(dec(price)*i.multiplier),
                    reduce_only=reduce,created_at=self.clock(),estimate=(str(dec(estimate)*i.multiplier) if estimate is not None else None)
                    if reduce else str(dec(pair['fill']['buy' if leg==0 else 'sell']['vwap'])*i.multiplier))

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
            remaining[o['leg']]+=q*Instrument.load(o['instrument']).multiplier*(-1 if o['reduce_only'] else 1)
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
        if self.limits.program_scope:
            snapshot=bounded_pair_positions(self.store,pair,snapshot)
        quantities=[str(dec(snapshot['positions'].get(self.position_key(pair,n),0))*Instrument.load(pair['instruments'][n]).multiplier) for n in (0,1)]
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
        native=Instrument.load(pair['instruments'][leg])
        i=native.common()
        step=dec(i.spec.qty_step)
        quantity=(min(dec(quantity),dec(pair['remote_remaining'][leg]),dec(i.spec.max_qty))/step).to_integral_value(rounding=ROUND_FLOOR)*step
        if quantity<=0 or quantity<dec(i.spec.min_qty):
            raise ValueError('reduction_below_min')
        if book is None:
            book=native.common_book(self.market.books([native])[0])
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
        step=dec(Instrument.load(pair['instruments'][leg]).common().spec.qty_step)
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
                books=[i.common_book(b) for i,b in zip(instruments,self.market.books(instruments))]
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
                after[intents[0]['leg']]-=dec(intents[0]['quantity'])*Instrument.load(intents[0]['instrument']).multiplier
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
        books=[i.common_book(b) for i,b in zip(instruments,self.market.books(instruments))]
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
            entry=[dec(o.get('vwap') or o.get('average_price') or o['price'])/Instrument.load(o['instrument']).multiplier for o in opens]
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
        status=dict(attempt_id=uuid.uuid4().hex,verified=False,program_flat=False,account_flat=False,
                    external_positions={},started_at=self.clock(),captured_at=None,reason=reason)
        self.store.set_meta('flatten_status',status)
        if halting and self.clock()<self.next_halt and reason!='shutdown':
            return status
        if halting:
            self.halt_attempts+=1
        try:
            self.executor.recover(startup=False)
            for p in self.store.pairs(active=True):
                try:
                    self.close_pair(p,reason)
                except Exception:
                    self.alert('flatten_retry_pending')
            if reason=='shutdown' or self.store.meta('state')=='HALTING' or self.external.records():
                # A process must not exit successfully with residual or unknown program exposure.
                try:
                    captured=self.clock()
                    raw=self.accounts.position_snapshot()
                    captured=raw.get('captured_at',captured)
                    if not 0<=self.clock()-captured<=10:
                        raise ValueError('flatten_snapshot_stale')
                    projection=self.external.split(raw,pnl=False)
                    remote=projection['positions']
                    program_flat=(not self.store.pairs(active=True) and not remote and not self.executor.pending_writes
                        and next(self.executor.iter_orders(states=(*NONTERMINAL,'INTENT')),None) is None
                        and next(self.executor.iter_orders(accounting_pending=True),None) is None)
                    status=dict(status,verified=True,program_flat=program_flat,
                        account_flat=program_flat and not bool(raw['positions']),
                        external_positions=plain(projection['external_positions']),captured_at=captured,
                        remaining_positions=plain(remote),local_active_pairs=len(self.store.pairs(active=True)))
                    self.store.set_meta('flatten_status',status)
                    if program_flat and projection['external_positions']:
                        self.store.event('PROGRAM_FLATTENED_EXTERNAL_REMAINS',status)
                except Exception:
                    status=dict(status,verified=False,program_flat=False,account_flat=False)
                    self.store.set_meta('flatten_status',status)
                    self.alert('flatten_snapshot_unavailable')
                    return status
                if program_flat and self.store.meta('state')=='HALTING':
                    self.store.set_meta('state','HALTED')
            return status
        finally:
            if halting:
                self.next_halt=self.clock()+min(60,2**min(self.halt_attempts,6))

    def tick(self):
        with self._coordinator_lock:
            return self._tick()

    def _tick(self):
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
