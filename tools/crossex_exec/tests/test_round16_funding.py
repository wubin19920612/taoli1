"""UTC attribution must be proved by independent position observations."""
import copy
import unittest

from tools.fill_sim.core import dec
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position

OWN = 'BINANCE_FUTURE_BTC_USDT'
EXT = 'BINANCE_FUTURE_ETH_USDT'
DAY_MS = 86400000


class DailyFundingTests(ExternalFixture, unittest.TestCase):
    def setUp(self):
        self.independent_funding = True
        super().setUp()
        self.closed = []
        self.catalog((OWN, EXT))
        self.change(symbol=EXT)
        self.rows = [dict(position(EXT), position_id='external', funding_fee='0',
                          funding_time='0', create_time=str(int(self.now*1000)-1000))]
        self.anchor()
        self.now += 1
        self.rows.append(dict(position(OWN), position_id='program', funding_fee='0',
                              funding_time='0', create_time=str(int(self.now*1000))))
        self.store.save_order(dict(intent('entry'), leg=0, created_at=self.now,
            fill_ms=self.now*1000, state='TERMINAL', filled='1', fee='0', vwap='100',
            accounting_pending=False))
        self.prove()

    def activity(self, start_ms, end_ms):
        result = super().activity(start_ms, end_ms)
        return dict(result, crossex_position_history=copy.deepcopy(self.closed), bitget_position_history=[])

    def prove(self, engine=None):
        engine = engine or self.engine
        snapshot = engine.read_account_snapshot()
        engine.account_cache.publish(snapshot, self.now)
        snapshot = engine.account_cache.read()
        return engine.external_accounting.evaluate(snapshot, engine.external.split(snapshot))

    def funding_row(self, at, amount, suffix):
        return dict(id='fund-'+suffix, business_id='program-'+str(at), symbol=OWN,
            exchange_type='BINANCE', coin='USDT', statement_type='FUNDING_FEE',
            change=str(amount), create_time=str(at))

    def opposite_days(self):
        boundary = (int(self.now)//86400+1)*DAY_MS
        # Two real settlements eight hours apart; neither has been returned.
        self.truth = [self.funding_row(boundary-4*3600000, 25, 'yesterday'),
                      self.funding_row(boundary+4*3600000, -25, 'today')]
        self.now = (boundary+4*3600000+1000)/1000
        self.rows[1].update(funding_fee='0', funding_time=str(boundary+4*3600000))
        self.catalog((OWN, EXT))

    def test_opposite_days_first_omission_blocks_without_rolling_any_checkpoint(self):
        anchor = copy.deepcopy(self.store.meta('external_equity_anchor'))
        checkpoint = copy.deepcopy(self.store.meta('funding_checkpoint'))
        self.opposite_days()
        with self.assertRaisesRegex(ValueError, 'external_income_unverifiable'):
            self.prove()
        self.assertEqual(self.store.meta('external_equity_anchor'), anchor)
        self.assertEqual(self.store.meta('funding_checkpoint'), checkpoint)
        self.assertFalse(self.engine.can_open())
        with self.assertRaises(ValueError):
            self.engine.check_order(intent(ex='bitget'))

    def test_restart_and_late_cash_rows_do_not_create_missing_boundary_evidence(self):
        self.opposite_days()
        anchor = copy.deepcopy(self.store.meta('external_equity_anchor'))
        with Store(self.path) as reopened:
            engine = Engine(reopened, self.market, self.accounts, clock=lambda:self.now)
            try:
                for rows in ([], self.truth[:1], self.truth):
                    self.book = copy.deepcopy(rows)
                    with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                        self.prove(engine)
                    self.assertEqual(reopened.meta('external_equity_anchor'), anchor)
                    self.assertFalse(engine.can_open())
            finally:
                engine.close()

    def test_observed_boundary_counter_then_late_today_loss_is_exact_after_restart(self):
        boundary = (int(self.now)//86400+1)*DAY_MS
        yesterday = self.funding_row(boundary-4*3600000, 25, 'yesterday')
        self.now = boundary/1000+1
        self.rows[1].update(funding_fee='25', funding_time=yesterday['create_time'])
        self.book = [yesterday]; self.equity = '1025'; self.catalog((OWN, EXT))
        first = self.prove()
        self.assertEqual(dec(first['program_daily']), 0)
        self.assertEqual(dec(first['program_total']), 25)
        self.assertTrue(first['funding_daily_verified'])
        checkpoint = self.store.meta('funding_checkpoint')
        self.assertEqual(checkpoint['captured_at'], self.now)
        today = self.funding_row(boundary+4*3600000, -25, 'today')
        self.now = boundary/1000+4*3600+1
        self.rows[1].update(funding_fee='0', funding_time=today['create_time'])
        self.equity = '1000'
        with Store(self.path) as reopened:
            engine = Engine(reopened, self.market, self.accounts, clock=lambda:self.now)
            try:
                with self.assertRaises(ValueError): self.prove(engine)
                self.assertEqual(reopened.meta('funding_checkpoint'), checkpoint)
                self.book.append(today)
                value = self.prove(engine)
                self.assertEqual(dec(value['program_daily']), -25)
                self.assertEqual(dec(value['program_total']), 0)
                engine.snapshot = engine.account_cache.read()
                engine.reconcile()
                self.assertIn('daily_loss', reopened.meta('stop_locks'))
                self.assertFalse(engine.can_open())
            finally:
                engine.close()

    def test_same_day_opposite_settlements_can_net_but_not_cross_days(self):
        at = int(self.now*1000)+1000
        self.now += 10
        self.rows[1].update(funding_fee='0', funding_time=str(at+1000))
        self.book = [self.funding_row(at, 25, 'a'), self.funding_row(at+1000, -25, 'b')]
        value = self.prove()
        self.assertEqual(dec(value['program_daily']), 0)
        self.assertTrue(value['funding_daily_verified'])

    def test_missing_or_false_settlement_time_cannot_prove_cross_day_zero(self):
        self.opposite_days()
        for value in (None, True, '-1', 'NaN', str(int(self.now*1000)+1000)):
            with self.subTest(value=value):
                self.rows[1]['funding_time'] = value
                with self.assertRaises(ValueError): self.prove()

    def test_closed_cross_day_lifetime_without_collection_time_is_not_zero(self):
        self.opposite_days()
        row = self.rows.pop()
        row.pop('funding_time')
        self.closed = [dict(row, closed_type='COMPLETE_CLOSED', update_time=str(int(self.now*1000)))]
        self.store.save_order(dict(intent('exit', reduce=True), leg=0, created_at=self.now,
            fill_ms=self.now*1000, state='TERMINAL', filled='1', fee='0', vwap='100', accounting_pending=False))
        with self.assertRaises(ValueError): self.prove()


if __name__ == '__main__': unittest.main()
