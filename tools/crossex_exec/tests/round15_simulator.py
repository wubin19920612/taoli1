"""Default-program venue simulator with independently maintained funding state."""
import copy
from collections import Counter

from tools.fill_sim.core import dec
from tools.crossex_exec.storage import Store
from tools.crossex_exec.transport import APIError
from baseline_fixtures import seed_baseline
from test_invariants import World, SimVenue, FakeDatabase


class ProgramVenue(SimVenue):
    def __init__(self, world, leg):
        super().__init__(world, leg)
        self.live_position = None
        self.history = []
        self.book = []
        self.funding_cash = dec(0)
        self.processed = set()
        self.serial = 0

    def open_orders(self):
        return []

    def place(self, order, before_send):
        w = self.world
        if w.explicit_reject and self.leg == 0 and not order['reduce_only']:
            before_send(); before_send.on_send()
            w.coverage['program_explicit_rejection'] += 1
            raise APIError('rejected')
        old_realized = self.realized
        try:
            return super().place(order, before_send)
        finally:
            with w.lock:
                row = self.orders.get(order['cid'])
                if row is not None and order['cid'] not in self.processed:
                    self.processed.add(order['cid'])
                    if dec(row['filled']):
                        if self.live_position is None:
                            self.serial += 1
                            self.live_position = dict(created=int(w.now*1000), id=str(self.serial), funding='0')
                        if self.quantity == 0:
                            self.history.append(dict(self.live_position, closed=int(w.now*1000)))
                            self.live_position = None
                    value = str(self.realized-old_realized)
                    self.book.append(self.cash_row(order['cid'], value, funding=False))

    def cash_row(self, cid, value, *, funding):
        symbol = self.world.instruments[self.leg].symbol
        at = str(int(self.world.now*1000))
        if self.leg == 0:
            return dict(id=cid, business_id=cid, symbol=symbol, coin='USDT', exchange_type='BINANCE',
                statement_type='FUNDING_FEE' if funding else 'TRANSACTION', change=value, create_time=at)
        return dict(id=cid, symbol=symbol, coin='USDT', category='USDT-FUTURES',
            type=('CONTRACT_MAIN_SETTLE_FEE_USER_OUT' if dec(value)<0 else 'CONTRACT_MAIN_SETTLE_FEE_USER_IN')
                  if funding else 'CLOSE_SHORT', amount=value, fee='0', ts=at)

    def settle(self, value):
        assert self.live_position is not None
        self.live_position['funding'] = str(dec(self.live_position['funding'])+dec(value))
        self.live_position['settled_at'] = int(self.world.now*1000)
        self.funding_cash += dec(value)
        self.book.append(self.cash_row('funding-'+str(len(self.book)), str(value), funding=True))

    def evidence(self, value, *, closed=False):
        symbol = self.world.instruments[self.leg].symbol
        if self.leg == 0:
            row = dict(symbol=symbol, position_side='LONG', position_id=value['id'],
                       create_time=str(value['created']), funding_fee=value['funding'],
                       funding_time=str(value.get('settled_at',0)))
            if closed:
                row.update(closed_type='COMPLETE_CLOSED', update_time=str(value['closed']))
            else:
                row.update(position_qty=str(self.quantity), margin_mode='CROSS')
        else:
            row = dict(symbol=symbol, posSide='short', category='USDT-FUTURES', marginCoin='USDT',
                       createdTime=str(value['created']), totalFunding=value['funding'])
            if closed:
                row.update(positionId=value['id'], updatedTime=str(value['closed']))
            else:
                row.update(total=str(self.quantity), marginMode='crossed')
        return row


class ProgramWorld(World):
    def __init__(self, *, path=None, coverage=None):
        super().__init__(True, Counter() if coverage is None else coverage)
        self.engine.close()
        self.explicit_reject = False
        self.hide_cash = False
        self.hide_history = False
        self.venues = [ProgramVenue(self,n) for n in (0,1)]
        self.accounts.cross, self.accounts.bitget = self.venues
        self.accounts.activity = self.activity
        self.accounts.program_activity = lambda contracts,start,end:self.accounts.activity(start,end)
        self.engine_config = {}  # actual default; no dedicated_account override
        self.path = path
        if path is not None:
            self.store = Store(path)
            seed_baseline(self.store,self.now,'5')
        self.engine = self.make_engine()
        self.engine.limits.initialize(self.snapshot())
        self.references()
        self.engine.reconcile()

    def snapshot(self):
        result = super().snapshot()
        if not hasattr(self,'explicit_reject'):
            return result  # the unchanged legacy World's bootstrap
        raw = dict(crossex=[],bitget=[])
        for n,route in enumerate(('crossex','bitget')):
            venue = self.venues[n]
            result['equity_by_venue'][route] = str(dec(result['equity_by_venue'][route])+venue.funding_cash)
            if venue.live_position is not None:
                row = venue.evidence(venue.live_position)
                key = '|'.join((route,self.instruments[n].symbol,'long' if n==0 else 'short'))
                row['upnl' if n==0 else 'unrealisedPnl'] = result['upnl_by_position'][key]
                raw[route].append(row)
        result.update(raw_positions=raw, equity=str(sum(map(dec,result['equity_by_venue'].values()),dec(0))),
            raw_accounts=dict(crossex=dict(assets=[dict(exchange_type='BINANCE', coin='USDT', liability='0',
                balance=str(1000+self.venues[0].realized+self.venues[0].funding_cash))]),
                bitget=dict(assets=[dict(coin='USDT', debt='0',
                balance=str(30000+self.venues[1].realized+self.venues[1].funding_cash))])))
        result['notionals']={k:str(q*self.prices[0 if k.startswith('crossex|') else 1]) for k,q in result['positions'].items()}
        return result

    def activity(self, start, end):
        def cash(venue):
            return [] if self.hide_cash else [copy.deepcopy(r) for r in venue.book
                if start<=int(r.get('ts',r.get('create_time')))<=end]
        def history(venue):
            return [] if self.hide_history else [venue.evidence(r,closed=True) for r in venue.history
                if start<=r['closed']<=end]
        return dict(from_ms=start,to_ms=end,complete=True,crossex_book=cash(self.venues[0]),
            bitget_book=cash(self.venues[1]),crossex_trades=[],
            crossex_position_history=history(self.venues[0]),bitget_position_history=history(self.venues[1]))

    def advance(self, seconds=1):
        self.now += seconds
        self.step += 1
        self.references()
        self.engine.reconcile()

    def restart(self):
        if self.path is None:
            super().restart()
        else:
            self.engine.close(); self.store.close()
            self.store = Store(self.path)
            self.engine = self.make_engine()
            self.engine.startup()
            self.coverage['restarts'] += 1

    def close(self):
        self.engine.close()
        if self.path is not None:
            self.store.close()
