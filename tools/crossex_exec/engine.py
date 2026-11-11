"""Single coordinator owns pair state; parallel workers only own individual orders."""
import time
import threading
import uuid
from decimal import ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
from tools.fill_sim.core import Book, Market, dec, walk, closing, track_interval, day
from tools.fill_sim.engine import trigger_reason
from .accounts import local_positions
from .policy import Instrument, review, exit_reason, risk_decision, limit_price, send_review
from .execution import Executor
from .limits import HardLimits, DEFAULT_VAR
from .background import SnapshotCache, PeriodicWorker


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
        self._alert_lock=threading.RLock()
        self._lifecycle_lock=threading.RLock()
        self._closed=False
        self.workers=[]
        self.bases=None
        self.next_catalog=self.next_fees=0
        self.fee_keys=set()
        self.next_halt=0
        self.halt_attempts=0
        self.account_cache=SnapshotCache(clock)
        self.limits=HardLimits(store,self.account_cache.read,config,alert=self.alert)
        self.executor=Executor(store,{'crossex':accounts.cross,'bitget':accounts.bitget},live=live,
                               can_open=self.can_open,check_order=self.check_order,alert=self.alert)

    def alert(self,reason):
        with self._alert_lock:
            changed=self._last_alert_reason!=reason or self.store.meta('stop_reason')!=reason
            self.store.set_meta('stop_reason',reason)
            if changed:
                self.store.event('ALERT',{'reason':reason})
                self._last_alert_reason=reason
                if self.alert_sink:
                    try:
                        self.alert_sink(reason)
                    except Exception:
                        self.store.event('ALERT_SINK_FAILED',{'reason':reason})

    def fees_for(self,instruments):
        rates=self.accounts.fees_by_market
        times=getattr(self.accounts,'fee_times_by_market',None)
        meta=self.store.meta('actual_fees',{})
        for i in instruments:
            at=times.get(i.market.key,0) if isinstance(times,dict) else meta.get('at',0)
            if not 0<=self.clock()-at<=3600 or i.market.key not in rates:
                raise ValueError('actual_fee_missing_or_stale')
        return [rates[i.market.key] for i in instruments]

    def check_order(self,intent):
        self.limits.check_order(intent)
        if intent['reduce_only']:
            return
        pair=next(p for p in self.store.pairs(active=True) if p['id']==intent['pair_id'])
        orders=sorted([o for o in self.store.all_orders() if o['pair_id']==pair['id'] and not o['reduce_only']
                       and o['state']!='UNSENT'],key=lambda o:o['leg'])
        instruments=[Instrument.load(i) for i in pair['instruments']]
        if any(i.market.key not in self.market.mapping for i in instruments):
            raise ValueError('send_catalog_unavailable')
        fees=self.fees_for(instruments)
        send_review(pair,orders,fees,self.clock())

    def refresh_accounts(self):
        started=self.clock()
        try:
            snapshot=self.accounts.snapshot()
            with self._lifecycle_lock:
                if not self._closed:
                    self.account_cache.publish(snapshot,started)
                    self.limits.check_equity(snapshot)
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
                    self.accounts.fees_by_market={}
            except Exception:
                rates={}
                self.accounts.fees_by_market={}
            with self._lifecycle_lock:
                if not self._closed:
                    times=getattr(self.accounts,'fee_times_by_market',{})
                    self.store.set_meta('actual_fees',dict(at=now,rates=rates,times=times if isinstance(times,dict) else {}))
                    if keys-set(rates):
                        self.store.event('FEES_UNAVAILABLE',{'markets':sorted(keys-set(rates))})
            self.next_fees=now+(30 if keys-set(rates) else 3600)
            self.fee_keys=keys

    def start_background(self,bases):
        self.bases=tuple(bases)
        if not self.workers:
            self.workers=[PeriodicWorker(self.refresh_accounts,5,'exec-accounts'),
                          PeriodicWorker(self.refresh_reference,5,'exec-reference')]

    def close(self):
        with self._lifecycle_lock:
            self._closed=True
        for worker in self.workers:
            worker.close()

    def can_open(self):
        recent=[o for o in self.store.all_orders() if o.get('sent_ms',0)>=self.clock()*1000-3600000]
        rejected=sum(o['state']=='REJECTED' or o.get('rejected',False) for o in recent)
        local_ok=(self.store.meta('single_failures',0)<3 and (not recent or dec(rejected)/len(recent)<=dec('.3')))
        return (local_ok and self.risk=='RUN' and not self.stop_file.exists() and not self.store.meta('stop_reason')
                and self.store.meta('daily_loss_day')!=day(self.clock())
                and not self.store.meta('flatten_requested')
                and self.store.meta('state') not in ('HALTED','HALTING') and 0<=self.clock()-self.last_reconcile<60)

    def startup(self,bases=()):
        self.store.assert_startable()
        previous=self.store.meta('live_mode')
        if previous is not None and previous!=self.live and self.store.pairs(active=True):
            raise ValueError('mode_change_with_live_positions')
        self.store.set_meta('live_mode',self.live)
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
        known={o['cid'] for o in self.store.all_orders() if o['state'] in ('SENT','ACK','UNKNOWN')
               and o['instrument']['market']['exchange']=='bitget'}
        if any(o['clientOid'] not in known for o in self.snapshot.get('open_orders',[])):
            raise ValueError('foreign_bitget_unfilled_orders')
        self.limits.initialize(self.snapshot)
        self.executor.recover()
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
        orders=self.store.all_orders()
        entries={(o['pair_id'],o['leg']):o for o in orders if not o['reduce_only'] and o.get('vwap')}
        for o in orders:
            if o['state']=='SHADOW':
                continue
            if o.get('accounting_pending'):
                self.alert('accounting_pending')
                continue
            realized=-dec(o.get('fee') or 0)
            if o['reduce_only'] and dec(o['filled']):
                entry=entries.get((o['pair_id'],o['leg']))
                if not entry or not o.get('vwap'):
                    self.alert('entry_accounting_unknown')
                    continue
                realized+=dec(o['filled'])*(dec(o['vwap'])-dec(entry['vwap']))*(1 if o['leg']==0 else -1)
            total+=realized
            if day((o.get('fill_ms') or o['created_at']*1000)/1000)==today:
                daily+=realized
        floating=dec(self.snapshot['upnl']) if self.snapshot else dec(0)
        return daily+floating,total+floating

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
            equity_halt=self.limits.check_equity(self.snapshot)
            same=self.snapshot['positions']==local_positions(self.store.pairs(active=True))
            if not same:
                self.alert('position_mismatch')
            self.last_reconcile=self.clock()
            daily,total=self.totals()
            current_day=day(self.clock())
            if self.store.meta('daily_loss_day') not in (None,current_day):
                self.store.set_meta('daily_loss_day',None)
                if self.store.meta('stop_reason')=='daily_loss':
                    self.store.set_meta('stop_reason',None)
            # Equity delta also catches funding/fees delayed beyond the trade journal.
            # Transfers are deliberately not assumed to be profit: use dedicated accounts.
            equity=self.snapshot.get('equity')
            if equity is not None:
                baseline=self.store.meta('equity_baseline')
                if baseline is None:
                    if self.store.all_orders():
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
            recent=[o for o in self.store.all_orders() if o.get('sent_ms',0)>=self.clock()*1000-3600000]
            rejected=sum(o['state']=='REJECTED' or o.get('rejected',False) for o in recent)
            self.risk=risk_decision(daily=daily,total=total,margins=self.snapshot['margins'],
                single_failures=self.store.meta('single_failures',0),rejected=rejected,attempted=len(recent),reconciled=same)
            if daily<=-20:
                self.store.set_meta('daily_loss_day',current_day)
                self.alert('daily_loss')
            if self.store.meta('daily_loss_day')==current_day and self.risk=='RUN':
                self.risk='STOP'
            self.store.event('RECONCILE',dict(matched=same,margins=self.snapshot['margins'],daily=str(daily),total=str(total),risk=self.risk))
            if total<=-100 or equity_halt:
                self.store.set_meta('state','HALTING')
            if self.risk in ('HALTED','FLATTEN') or self.store.meta('state')=='HALTING':
                self.flatten_all('circuit_breaker')
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
            self.store.event('SKIP',{'id':identity,'reason':'filter_or_open_gate'})
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
            self.accounts.check_instruments(instruments)
            fees=self.fees_for(instruments)
            funding=self.market.funding(instruments)
            turnovers=self.market.turnovers(instruments)
            books=self.market.books(instruments)
            fill=review(instruments,books,fees,funding,turnovers,self.clock())
            if self.clock()-now>120 or not self.can_open():
                raise ValueError('open_gate_changed')
        except Exception:
            self.store.event('SKIP',{'id':identity,'reason':'review_failed_closed'})
            return
        pair=dict(id=uuid.uuid4().hex,trigger_id=identity,base=base,state='OPENING',opened_at=self.clock(),
                  instruments=[i.dump() for i in instruments],fill=fill,funding=funding,turnovers=turnovers,
                  remaining=['0','0'],last_books=[b.to_dict() for b in books],last_good=[b.received_at for b in books],
                  next_track=self.clock()+3,single_failure=False,repair_attempts=0,round=0)
        self.store.pair(pair)
        self.store.event('REVIEW',{'pair_id':pair['id'],'fill_sim_estimate':fill,'funding':funding,'turnovers':turnovers})
        intents=[self.new_intent(pair,n,fill['quantity'],fill['limits'][n],False) for n in (0,1)]
        results=self.executor.execute_pair(intents)
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
        orders=[o for o in self.store.all_orders() if o['pair_id']==pair['id']]
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
        journal_remaining=list(remaining)
        anchor=pair.get('position_anchor')
        if anchor:
            remaining=list(map(dec,anchor['remaining']))
            for o in orders:
                # A delayed report for an order present in the remote snapshot
                # must not count its fills twice. Re-read positions before acting.
                if o['cid'] in anchor['unresolved']:
                    unknown=True
                    continue
                delta=dec(o.get('filled',0))-dec(anchor['filled'].get(o['cid'],0))
                remaining[o['leg']]+=delta*(-1 if o['reduce_only'] else 1)
            if remaining!=journal_remaining:
                pending=True
        if min(remaining)<0:
            self.alert('negative_local_position')
            pair['state']='UNKNOWN'
            self.store.pair(pair)
            return
        pair['remaining']=[str(q) for q in remaining]
        pair['accounting_pending']=pending
        pair['net_usd']=str(cash-fees) if not pending and not any(remaining) and not unknown else None
        if unknown:
            pair['state']='UNKNOWN'
        elif not any(remaining):
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

    def resolve_exposure(self,pair):
        """Cancel/query original IDs, then anchor BOTH legs to venue positions.

        The anchor never fabricates fills, fees or a terminal order. It survives
        restart and isolates subsequent reduce fills from delayed entry reports.
        """
        if not self.live:
            return False
        orders=[o for o in self.store.all_orders() if o['pair_id']==pair['id']]
        for o in orders:
            if o['state'] in ('SENT','ACK','UNKNOWN'):
                self.executor.cancel(o)
        try:
            snapshot=self.accounts.snapshot()
            quantities=[]
            for n,raw in enumerate(pair['instruments']):
                i=Instrument.load(raw)
                venue='bitget' if i.market.exchange=='bitget' else 'crossex'
                key='|'.join((venue,i.symbol,'long' if n==0 else 'short'))
                quantities.append(str(dec(snapshot['positions'].get(key,0))))
            if any(dec(q)<0 for q in quantities):
                raise ValueError('negative_remote_position')
        except Exception:
            self.alert('unknown_exposure_unavailable')
            return False
        orders=[o for o in self.store.all_orders() if o['pair_id']==pair['id']]
        pair['position_anchor']=dict(at=self.clock(),remaining=quantities,
            filled={o['cid']:o.get('filled','0') for o in orders},
            unresolved=[o['cid'] for o in orders if o['state'] in ('SENT','ACK','UNKNOWN','INTENT')])
        self.store.event('POSITION_RECOVERY',{'pair_id':pair['id'],'remaining':quantities,
                                             'unresolved':pair['position_anchor']['unresolved']})
        self.refresh_pair(pair)
        return True

    def reduction_intent(self,pair,leg,quantity,bps,book=None):
        i=Instrument.load(pair['instruments'][leg])
        step=dec(i.spec.qty_step)
        quantity=(min(dec(quantity),dec(pair['remaining'][leg]),dec(i.spec.max_qty))/step).to_integral_value(rounding=ROUND_FLOOR)*step
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
        return self.new_intent(pair,leg,quantity,price,True,estimate)

    def reduce(self,pair,leg,quantity,bps):
        if not self.live:
            raise ValueError('shadow_position_cannot_reduce')
        return self.executor.execute(self.reduction_intent(pair,leg,quantity,bps))

    def unhedged(self,pair):
        a,b=map(dec,pair['remaining'])
        self.store.event('UNHEDGED',{'pair_id':pair['id'],'quantity':str(abs(a-b)),
                                    'remaining':pair['remaining'],'excess_leg':0 if a>b else 1})
        self.alert('unhedged_position')

    def repair_intents(self,pair,bps):
        quantities=list(map(dec,pair['remaining']))
        leg=0 if quantities[0]>quantities[1] else 1
        other=1-leg
        diff=abs(quantities[0]-quantities[1])
        try:
            return [self.reduction_intent(pair,leg,diff,bps)]
        except ValueError:
            pass
        # A sub-step excess can require a step on the coarse leg followed by
        # reducing the now-excess fine leg. Both orders only CLOSE; choose this
        # plan only if its final absolute exposure is strictly smaller.
        step=dec(pair['instruments'][leg]['spec']['qty_step'])
        cut=(diff/step).to_integral_value(rounding=ROUND_CEILING)*step
        if cut<=diff or cut>quantities[leg]:
            return []
        other_step=dec(pair['instruments'][other]['spec']['qty_step'])
        remainder=cut-diff
        options=[]
        for rounding in (ROUND_FLOOR,ROUND_CEILING):
            other_cut=(remainder/other_step).to_integral_value(rounding=rounding)*other_step
            if abs(remainder-other_cut)>=diff or other_cut>quantities[other]:
                continue
            try:
                plan=[self.reduction_intent(pair,leg,cut,bps),self.reduction_intent(pair,other,other_cut,bps)]
                options.append((abs(remainder-other_cut),other_cut,plan))
            except ValueError:
                continue
        return min(options,key=lambda option:option[:2])[2] if options else []

    def balance(self,pair):
        self.refresh_pair(pair)
        if pair['state']=='UNKNOWN':
            self.close_pair(pair,'unknown_order')
            return
        if dec(pair['remaining'][0])==dec(pair['remaining'][1]):
            self.store.set_meta('single_failures',0)
            return
        if not pair['single_failure']:
            pair['single_failure']=True
            self.store.set_meta('single_failures',self.store.meta('single_failures',0)+1)
        self.store.pair(pair)
        for attempt in range(pair.get('repair_attempts',0)+1,4):
            a,b=map(dec,pair['remaining'])
            if a==b:
                return
            intents=self.repair_intents(pair,3+10*attempt)
            if not intents:
                break
            pair['repair_attempts']=attempt
            self.store.pair(pair)
            results=self.executor.execute_pair(intents)
            self.refresh_pair(pair)
            if any(result['state']=='UNKNOWN' for result in results):
                break
        if dec(pair['remaining'][0])!=dec(pair['remaining'][1]):
            self.unhedged(pair)

    def close_pair(self,pair,reason):
        self.refresh_pair(pair)
        if pair['state']=='UNKNOWN' and not self.resolve_exposure(pair):
            return
        if pair['state']=='CLOSED':
            return
        pair.update(state='CLOSING',exit_reason=reason)
        self.store.pair(pair)
        # Concurrent reduce-only closes. Never reopen a leg to repair a close.
        for attempt in range(3):
            quantities=list(map(dec,pair['remaining']))
            indices=[n for n in (0,1) if quantities[n]>0]
            if not indices:
                return
            instruments=[Instrument.load(pair['instruments'][n]) for n in indices]
            books=self.market.books(instruments)
            intents=[]
            for n,i,b in zip(indices,instruments,books):
                try:
                    bps=3+10*attempt+(10 if reason=='stale_exit' else 0)
                    intents.append(self.reduction_intent(pair,n,quantities[n],bps,b))
                except ValueError:
                    self.unhedged(pair)
            if not intents:
                return
            result=self.executor.execute_pair(intents)
            self.refresh_pair(pair)
            if any(o['state']=='UNKNOWN' for o in result):
                self.alert('unknown_close')
                return
        if any(dec(q) for q in pair['remaining']):
            self.alert('close_incomplete')

    def track(self,pair):
        if pair['state']=='UNKNOWN':
            self.close_pair(pair,pair.get('exit_reason','unknown_order'))
            return
        now=self.clock()
        instruments=[Instrument.load(i) for i in pair['instruments']]
        books=self.market.books(instruments)
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
        a,b=map(dec,pair['remaining'])
        if a!=b:
            self.balance(pair)
            return
        if not a:
            return
        try:
            sell=walk(books[0].bids,quantity=a)
            buy=walk(books[1].asks,quantity=b)
            spread=(dec(buy['vwap'])/dec(sell['vwap'])-1)*10000
            opens=[o for o in self.store.all_orders() if o['pair_id']==pair['id'] and not o['reduce_only']]
            if len(opens)!=2:
                self.alert('entry_accounting_unknown')
                return
            opens.sort(key=lambda o:o['leg'])
            entry=[dec(o.get('vwap') or o.get('average_price') or o['price']) for o in opens]
            entry_spread=(entry[1]/entry[0]-1)*10000
            notional=a*entry[0]
            pnl=a*(dec(sell['vwap'])-entry[0]+entry[1]-dec(buy['vwap']))
            reason=exit_reason(age=age,spread=spread,entry_spread=entry_spread,pnl=pnl,
                               notional=notional,next_funding=next_funding,stale_age=stale)
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
            self.next_halt=self.clock()+min(60,2**min(self.halt_attempts,6))
        for p in self.store.pairs(active=True):
            try:
                self.close_pair(p,reason)
            except Exception:
                self.alert('flatten_retry_pending')
        if self.store.meta('state')=='HALTING':
            # A process must not exit successfully with residual or unknown exposure.
            try:
                remote=self.accounts.snapshot()['positions']
            except Exception:
                self.alert('flatten_snapshot_unavailable')
                return
            if not self.store.pairs(active=True) and not remote:
                self.store.set_meta('state','HALTED')

    def tick(self):
        if self.store.meta('state')=='HALTED':
            return
        if self.store.meta('state')=='HALTING':
            self.flatten_all('circuit_breaker')
            return
        if self.clock()-self.last_accounting_retry>=60:
            pending_pairs=set()
            for o in self.store.all_orders():
                if o['state']=='TERMINAL' and o.get('accounting_pending'):
                    self.executor.account(o)
                    pending_pairs.add(o['pair_id'])
            for p in self.store.pairs():
                if p['id'] in pending_pairs:
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
