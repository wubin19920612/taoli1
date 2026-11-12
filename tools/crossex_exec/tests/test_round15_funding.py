"""Funding witnesses are independent of the delayed account-book fixture."""
import copy
import unittest
from unittest.mock import Mock

from tools.fill_sim.core import dec
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position

EXT = 'BINANCE_FUTURE_ETH_USDT'
OWN = 'BINANCE_FUTURE_BTC_USDT'


class FundingTests(ExternalFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.closed = dict(crossex=[], bitget=[])
        self.catalog((EXT, OWN))
        self.change(symbol=EXT)
        self.rows = [dict(position(EXT), position_id='external', create_time=str(int(self.now*1000)-1000),
                          funding_fee='0', funding_time='0')]
        self.anchor()
        self.now += 1
        self.rows.append(dict(position(OWN), position_id='program', funding_fee='0', funding_time='0',
                              create_time=str(int(self.now*1000))))
        self.store.save_order(dict(intent('entry'), leg=0, created_at=self.now, fill_ms=self.now*1000,
            state='TERMINAL', filled='1', fee='0', vwap='100', accounting_pending=False))

    def activity(self, start_ms, end_ms):
        result = super().activity(start_ms, end_ms)
        result.update(crossex_position_history=copy.deepcopy(self.closed['crossex']),
                      bitget_position_history=copy.deepcopy(self.closed['bitget']))
        return result

    def prove(self, engine=None):
        engine = engine or self.engine
        self.now += 1
        snapshot = engine.read_account_snapshot()
        engine.account_cache.publish(snapshot, self.now)
        snapshot = engine.account_cache.read()
        return engine.external_accounting.evaluate(snapshot, engine.external.split(snapshot))

    def settle(self):
        self.now += 1
        for row, value in zip(self.rows, ('25', '-25')):
            row['funding_fee'] = value
            row['funding_time'] = str(int(self.now*1000))
        self.settled_book = [dict(id='fund-'+str(n), business_id=row['position_id']+'-'+row['funding_time'],
            symbol=row['symbol'], exchange_type='BINANCE', coin='USDT', statement_type='FUNDING_FEE',
            change=row['funding_fee'], create_time=row['funding_time']) for n,row in enumerate(self.rows)]

    def test_first_missing_offsetting_funding_is_not_zero_then_delayed_rows_recover(self):
        self.assertTrue(self.prove()['verified'])
        self.settle()
        with self.assertRaisesRegex(ValueError, 'external_income_unverifiable'):
            self.prove()
        self.assertFalse(self.engine.can_open())
        with self.assertRaises(ValueError):
            self.engine.check_order(intent(ex='bitget'))
        self.book = copy.deepcopy(self.settled_book)
        result = self.prove()
        self.assertEqual(dec(result['program_daily']), -25)
        self.assertEqual(dec(result['program_total']), -25)
        self.assertTrue(result['funding_verified'])

    def test_missing_or_invalid_cumulative_value_cannot_certify_even_unchanged_cash(self):
        for value in (None, 'NaN', 'Infinity', True):
            with self.subTest(value=value):
                self.rows[1]['funding_fee'] = value
                with self.assertRaises(ValueError): self.prove()

    def test_closed_position_needs_independent_final_cumulative_witness(self):
        self.settle(); self.book = copy.deepcopy(self.settled_book)
        self.assertEqual(dec(self.prove()['program_total']), -25)
        row = self.rows.pop()
        self.now += 1
        self.store.save_order(dict(intent('exit', reduce=True), leg=0, created_at=self.now,
            fill_ms=self.now*1000, state='TERMINAL', filled='1', fee='0', vwap='100', accounting_pending=False))
        with self.assertRaises(ValueError): self.prove()
        self.closed['crossex'] = [dict(row, closed_type='COMPLETE_CLOSED', update_time=str(int(self.now*1000)))]
        self.assertEqual(dec(self.prove()['program_total']), -25)

    def test_closed_roundtrip_between_snapshots_requires_position_history(self):
        self.rows.pop()
        self.store.save_order(dict(intent('exit', reduce=True), leg=0, created_at=self.now,
            fill_ms=self.now*1000, state='TERMINAL', filled='1', fee='0', vwap='100', accounting_pending=False))
        with self.assertRaises(ValueError): self.prove()

    def test_changed_lifetime_or_missing_independent_history_is_not_zero(self):
        self.assertTrue(self.prove()['verified'])
        # Day rollover freezes the current open lifetime into the next anchor.
        self.now = (int(self.now)//86400+1)*86400
        self.catalog((EXT, OWN))
        self.prove()
        self.rows[1]['position_id'] = 'replacement'
        with self.assertRaises(ValueError): self.prove()

    def test_restart_preserves_cumulative_funding_check(self):
        self.assertTrue(self.prove()['verified'])
        self.settle()
        with Store(self.path) as reopened:
            engine = Engine(reopened, self.market, self.accounts, clock=lambda:self.now)
            try:
                with self.assertRaises(ValueError): self.prove(engine)
                self.book = copy.deepcopy(self.settled_book)
                self.assertEqual(dec(self.prove(engine)['program_total']), -25)
            finally:
                engine.close()

    def test_two_directions_same_contract_keep_separate_lifetimes(self):
        self.rows.insert(1, dict(self.rows[0], position_id='external-short', position_side='SHORT', funding_fee='0'))
        # This new direction is known to have opened after the anchor.
        self.rows[1]['create_time'] = str(int(self.now*1000))
        self.trades = [dict(transaction_id='dual', order_id='dual', symbol=EXT, exchange_type='BINANCE',
            business_type='FUTURE', fee_coin='USDT', position_mode='DUAL', position_side='SHORT',
            side='SELL', qty='1', price='100', fee='0', rpnl='0', create_time=str(int(self.now*1000)))]
        self.assertTrue(self.prove()['verified'])
        self.rows[1]['funding_fee'] = '25'
        self.rows[2]['funding_fee'] = '-25'
        with self.assertRaises(ValueError): self.prove()


class HistoryAdapterTests(unittest.TestCase):
    def test_cross_closed_history_uses_fixed_window_and_duplicate_page_guard(self):
        http = Mock(); http.request.return_value = []
        self.assertEqual(CrossEx(http).position_history(100, 200), [])
        call = http.request.call_args
        self.assertEqual(call.args[:2], ('GET', '/api/v4/crossex/history_positions'))
        self.assertEqual(call.args[2], {'from':100, 'to':200, 'page':1, 'limit':1000})
        duplicate = dict(position_id='1', update_time='150', closed_type='COMPLETE_CLOSED')
        http.request.return_value = [duplicate, duplicate]
        with self.assertRaises(Exception): CrossEx(http).position_history(100, 200)

    def test_bitget_closed_history_is_uta_paginated_and_fail_closed(self):
        http = Mock(); http.request.return_value = dict(list=[], cursor='')
        self.assertEqual(Bitget(http).position_history(100, 200), [])
        self.assertEqual(http.request.call_args.args[:2], ('GET', '/api/v3/position/history-position'))
        self.assertEqual(http.request.call_args.args[2],
                         dict(category='USDT-FUTURES', startTime='100', endTime='200', limit=100))
        http.request.return_value = dict(list=[dict(positionId='1')], cursor='repeat')
        with self.assertRaises(Exception): Bitget(http).position_history(100, 200)


if __name__ == '__main__': unittest.main()
