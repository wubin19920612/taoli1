"""R19 real cash domains, exact contract identity, and non-contract cash flows."""
import copy
import tempfile
import unittest
from pathlib import Path

from tools.fill_sim.core import dec
from tools.crossex_exec.external_accounting import program_activity
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import SYMBOL
from round19_fixtures import BINANCE, CONVERT, ObservedFixture, ObservedProgramWorld


class ObservedExternalTests(ObservedFixture, unittest.TestCase):
    def test_external_binance_and_dual_hl_cash_funding_and_trades_keep_native_units(self):
        self.settle(BINANCE, '3')
        self.settle(SYMBOL, '-2')
        self.trade(BINANCE)
        self.trade(SYMBOL, fee='.2')
        value = self.prove()
        self.assertTrue(value['verified'])
        self.assertTrue(value['funding_daily_verified'])
        self.assertEqual({k:dec(v) for k,v in value['external_cash_by_currency'].items()},
                         {'USDT':dec('2.9'), 'USDC':dec('-2.2')})
        self.assertEqual(dec(value['program_total']), 0)
        checkpoint = self.store.meta('funding_checkpoint')
        self.assertEqual(len(checkpoint['funding_positions']), 3)
        self.assertIn('crossex|CROSSEX|USDT', self.store.meta('external_equity_anchor')['cash_balances'])
        self.assertTrue(all('_FUTURE_' in p['contract'] for p in checkpoint['funding_positions'].values()))
        self.now += 1
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())

    def test_cash_identity_does_not_relax_position_or_trade_exchange_identity(self):
        self.trade(BINANCE)
        self.trades[0]['exchange_type'] = 'CROSSEX'
        with self.assertRaisesRegex(ValueError, 'external_income_unverifiable'):
            self.prove()
        self.trades[0]['exchange_type'] = 'BINANCE'
        self.rows[0]['exchange_type'] = 'CROSSEX'
        with self.assertRaisesRegex(ValueError, 'external_identity_unverifiable'):
            self.prove()

    def test_wrong_exchange_quote_and_other_dex_cash_remain_rejected(self):
        row = self.cash(BINANCE, 'TRANSACTION', '0', 'CROSSEX', 'USDT')
        original = copy.deepcopy(row)
        for changes in ({'exchange_type':'OKX'}, {'coin':'USDC'},
                        {'symbol':'OKX_FUTURE_OPENAI_USDT'},
                        {'symbol':'HYPERLIQUID_FUTURE_xyz:OAI_USDC', 'coin':'USDC'},
                        {'symbol':SYMBOL, 'coin':'USDC'},
                        {'business_type':'SPOT'}, {'quote':'USDC'}):
            with self.subTest(changes=changes):
                row.clear(); row.update(original); row.update(changes)
                with self.assertRaises(ValueError):
                    self.prove()
        row.clear(); row.update(original)
        self.assertTrue(self.prove()['verified'])

    def test_rebaseline_and_next_checkpoint_accept_observed_cash_domain(self):
        self.settle(BINANCE, '-1')
        self.assertTrue(self.prove()['verified'])
        self.anchor()
        self.now += 1
        self.settle(BINANCE, '-2')
        self.assertEqual(dec(self.prove()['external_cash']), -2)


class ProgramCashTests(unittest.TestCase):
    def test_real_program_first_fill_funding_close_and_file_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = ObservedProgramWorld(path=Path(tmp)/'program.db')
            try:
                self.assertTrue(world.engine.can_open())
                world.advance(); world.open(); world.advance()
                self.assertTrue(world.store.meta('external_income')['verified'])
                self.assertTrue(all(v.quantity > 0 for v in world.venues))
                world.venues[0].settle('-3'); world.venues[1].settle('-4')
                world.advance()
                self.assertEqual(dec(world.store.meta('external_income')['program_total']), -7)
                world.engine.close_pair(world.store.pairs(active=True)[0], 'r19_exit')
                world.advance(); world.restart(); world.advance()
                self.assertTrue(world.store.meta('external_income')['verified'])
                self.assertTrue(all(v.quantity == 0 for v in world.venues))
                world.engine.rebaseline(confirm=True, reason='r19_cash_domain', source='cli')
                world.advance()
                self.assertTrue(world.store.meta('external_income')['funding_daily_verified'])
            finally:
                world.close()

    def test_program_nonzero_fee_pnl_and_funding_for_cex_usdt(self):
        with tempfile.TemporaryDirectory() as tmp, Store(Path(tmp)/'journal.db') as store:
            for exchange in ('BINANCE', 'OKX', 'GATE', 'BYBIT'):
                with self.subTest(exchange=exchange):
                    order = dict(intent(exchange), leg=0, state='TERMINAL', filled='1', vwap='100',
                                 pair_id=exchange, fee='.1', fill_ms=1000, accounting_pending=False)
                    order['instrument']['market']['exchange'] = exchange.lower()
                    order['instrument']['symbol'] = exchange+'_FUTURE_BTC_USDT'
                    store.save_order(order)
                    store.save_order(dict(order, cid=exchange+'-close', reduce_only=True,
                                          vwap='102', fee='.2', fill_ms=1100))
            book = []
            for order in store.iter_orders():
                pnl = '2' if order['reduce_only'] else '0'
                for kind, amount in (('TRADING_FEE',str(-dec(order['fee']))), ('TRANSACTION',pnl)):
                    book.append(dict(id=order['cid']+kind, symbol=order['instrument']['symbol'],
                        exchange_type='CROSSEX', coin='USDT', statement_type=kind, change=amount,
                        create_time='1000'))
                if not order['reduce_only']:
                    book.append(dict(book[-1], id=order['cid']+'funding',
                                     statement_type='FUNDING_FEE', change='-.5'))
            activity = dict(complete=True, from_ms=0, to_ms=2000, crossex_book=book, bitget_book=[])
            self.assertEqual(program_activity(store, activity, 2), -2)
            # The raw symbol must still match the LOCAL exchange and quote.
            for section, field, value in (('market','exchange','okx'), ('spec','quote','USDC')):
                order = store.order('BINANCE')
                original = copy.deepcopy(order)
                order['instrument'][section][field] = value
                store.save_order(order)
                with self.assertRaises(ValueError):
                    program_activity(store, activity, 2)
                store.save_order(original)
            book[0]['change'] = '-.09'
            with self.assertRaisesRegex(ValueError, 'program_cash_journal_mismatch'):
                program_activity(store, activity, 2)


class NonContractCashTests(ObservedFixture, unittest.TestCase):
    def test_conversion_does_not_require_futures_pnl_or_fill_fields(self):
        self.convert()
        # The task specifies conversion identity, not futures fee/PnL or size
        # fields. No position or contract-income proof may depend on them.
        for field in ('fee', 'rpnl', 'qty', 'price', 'side'):
            self.trades[0].pop(field)
        value = self.prove()
        self.assertTrue(value['verified'])
        self.assertEqual(dec(value['program_total']), 0)
        self.assertEqual(dec(value['external_cash']), 0)

    def test_conversion_pair_trade_and_lighter_interest_are_not_program_income(self):
        self.convert()
        self.cash('', 'INTEREST_ISOLATED', '-.25', 'LIGHTER', 'USDC')
        self.cash('', 'TRANSFER_IN', '5', 'CROSSEX', 'USDT')
        self.cash('', 'TRANSFER_OUT', '-1', 'CROSSEX', 'USDT')
        self.cash('', 'TRANSFER_IN', '2', 'LIGHTER', 'USDC')
        value = self.prove()
        self.assertTrue(value['verified'])
        self.assertEqual(dec(value['program_total']), 0)
        self.assertEqual(dec(value['program_daily']), 0)
        self.assertEqual(dec(value['external_cash']), 0)
        self.assertEqual(dec(value['program_funding']), 0)
        self.assertEqual(len(self.store.meta('funding_checkpoint')['funding_positions']), 3)
        self.now += 1
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.anchor()
        self.assertTrue(self.prove()['verified'])

    def test_missing_conversion_leg_or_interest_still_fails_balance_continuity(self):
        self.convert()
        self.cash('', 'INTEREST_ISOLATED', '-.25', 'LIGHTER', 'USDC')
        original = list(self.book)
        history = self.store.meta('external_verified_history')
        for missing in range(3):
            with self.subTest(missing=missing):
                self.book = [r for n,r in enumerate(original) if n != missing]
                with self.assertRaisesRegex(ValueError, 'external_income_unverifiable'):
                    self.prove()
                self.assertEqual(self.store.meta('external_verified_history'), history)
        self.book = original
        self.assertTrue(self.prove()['verified'])

    def test_unknown_types_and_interest_outside_exact_domain_remain_closed(self):
        row = self.cash('', 'INTEREST_ISOLATED', '-.25', 'LIGHTER', 'USDC')
        original = dict(row)
        for changes in ({'statement_type':'UNKNOWN'}, {'statement_type':'AUTO_REPAY'},
                        {'symbol':SYMBOL}, {'exchange_type':'HYPERLIQUID'},
                        {'coin':'USDT'}, {'change':'.25'}):
            with self.subTest(changes=changes):
                row.clear(); row.update(original); row.update(changes)
                with self.assertRaises(ValueError): self.prove()

    def test_conversion_does_not_bypass_unknown_cash_types_or_contract_trades(self):
        self.convert()
        for kind in ('OTHER', 'FUNDING_FEE', 'TRADING_FEE'):
            with self.subTest(kind=kind):
                self.book[0]['statement_type'] = kind
                with self.assertRaises(ValueError): self.prove()
        self.book[0]['statement_type'] = 'TRANSACTION'
        original = dict(self.trades[0])
        for changes in ({'symbol':BINANCE}, {'symbol':'LIGHTER_CONVERT_USDC_USDT'},
                        {'business_type':'FUTURE'}, {'position_side':'LONG'}, {'fee_coin':'USDC'}):
            with self.subTest(changes=changes):
                self.trades[0] = dict(original, **changes)
                with self.assertRaises(ValueError): self.prove()
        self.trades[0] = original
        self.assertTrue(self.prove()['verified'])


if __name__ == '__main__':
    unittest.main()
