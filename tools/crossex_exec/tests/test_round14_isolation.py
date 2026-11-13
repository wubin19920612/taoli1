"""Round 14: program risk is proved independently of user capital and positions."""
import copy
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec, day
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position


EXTERNAL = 'HYPERLIQUID_FUTURE_io:OAI_USDT'
KEY = 'crossex|' + EXTERNAL + '|long'


class IsolationFixture(ExternalFixture):
    def setUp(self):
        super().setUp()
        self.cash = dec(1000)
        self.catalog((EXTERNAL, 'BINANCE_FUTURE_BTC_USDT'))
        self.rows = [dict(position(EXTERNAL), initial_margin='100')]
        self.change(symbol=EXTERNAL)
        self.anchor()

    def snapshot(self):
        result = super().snapshot()
        if hasattr(self, 'cash'):
            result['raw_accounts']['crossex']['assets'] = [dict(
                exchange_type='HYPERLIQUID', coin='USDT', balance=str(self.cash),
                liability=str(sum((dec(r['liability']) for r in self.liabilities
                    if r.get('route') == 'crossex' and r.get('coin') == 'USDT'
                    and r.get('exchange_type') == 'HYPERLIQUID'), dec(0))),
                futures_initial_margin='100')]
        return result

    def reconcile(self):
        self.now += 1
        self.engine.reconcile()
        return self.store.meta('account_loss_current', {})

    def own_funding(self, amount, identity='funding'):
        if self.store.order('own') is None:
            self.store.save_order(dict(intent('own', ex='bitget'), created_at=self.now,state='TERMINAL',
                filled='0', fee='0', leg=1, accounting_pending=False))
        self.bg_book.append(dict(id=identity, category='USDT-FUTURES', symbol='BTCUSDT',
            coin='USDT', type='CONTRACT_MAIN_SETTLE_FEE_USER_OUT' if dec(amount)<0
            else 'CONTRACT_MAIN_SETTLE_FEE_USER_IN', amount=str(amount), fee='0',
            ts=str(int((self.now + .5)*1000))))
        self.bitget_equity = str(dec(self.bitget_equity) + dec(amount))

    def debt(self, amount='50', **changes):
        self.liabilities = [dict(route='crossex', exchange_type='HYPERLIQUID', coin='USDT',
            liability=amount, source_field='liability', **changes)]


class ProgramIsolationTests(IsolationFixture, unittest.TestCase):
    def test_external_extreme_profit_loss_and_verified_deposits_withdrawals_are_not_program_loss(self):
        for n, (pnl, transfer) in enumerate((('-900', '-500'), ('9000', '800'), ('-600', '-200'))):
            with self.subTest(pnl=pnl, transfer=transfer):
                self.rows[0]['upnl'] = pnl
                self.cash += dec(transfer)
                self.equity = str(self.cash + dec(pnl))
                self.book.append(dict(id='transfer-'+str(n), business_id='capital-'+str(n),
                    symbol='', exchange_type='HYPERLIQUID', coin='USDT', change=transfer,
                    statement_type='TRANSFER_IN' if dec(transfer)>0 else 'TRANSFER_OUT',
                    create_time=str(int((self.now+.5)*1000))))
                loss = self.reconcile()
                self.assertEqual((dec(loss['daily']), dec(loss['total'])), (dec(0), dec(0)))
                self.assertTrue(self.engine.can_open())
                self.assertNotIn(self.store.meta('state'), ('HALTING', 'HALTED'))
                self.assertEqual(self.store.meta('account_equity_current')['crossex'], self.equity)

    def test_own_funding_daily_and_cumulative_survive_external_profit_midnight_and_restart(self):
        self.own_funding('-60')
        self.rows[0]['upnl'] = '9000'; self.equity = '10000'
        loss = self.reconcile()
        self.assertEqual(dec(loss['daily']), -60)
        self.assertEqual(dec(loss['total']), -60)
        self.assertIn('daily_loss', self.store.meta('stop_locks'))
        self.now = (int(self.now)//86400+1)*86400
        self.catalog((EXTERNAL, 'BINANCE_FUTURE_BTC_USDT'))
        self.assertEqual(dec(self.reconcile()['total']), -60)
        with Store(self.path) as reopened:
            engine = Engine(reopened, self.market, self.accounts, clock=lambda:self.now,
                            stop_file=self.engine.stop_file)
            try:
                self.own_funding('-41', 'next-day')
                self.now += 1; engine.reconcile()
                self.assertEqual(dec(reopened.meta('account_loss_current')['total']), -101)
                self.assertEqual(dec(reopened.meta('account_loss_current')['daily']), -41)
                self.assertEqual(reopened.meta('state'), 'HALTED')
                self.assertFalse(reopened.meta('flatten_status')['account_flat'])
            finally:
                engine.close()

    def test_own_150_loss_halts_without_touching_external(self):
        self.own_funding('-150')
        self.rows[0]['upnl'] = '1000'; self.equity = '2000'
        self.reconcile()
        self.assertEqual(self.store.meta('state'), 'HALTED')
        self.assertEqual(self.store.meta('halt_reason'), 'account_equity_drop')
        self.assertTrue(self.store.meta('flatten_status')['program_flat'])
        self.assertFalse(self.store.meta('flatten_status')['account_flat'])
        self.assertEqual(len(self.store.all_orders()), 1)

    def test_external_notional_does_not_consume_600_but_pending_program_legs_do(self):
        self.reconcile()
        snapshot = self.engine.account_cache.read()
        snapshot['notionals'][KEY] = '60000'
        self.assertEqual(self.engine.limits.exposure(snapshot), 0)
        for n in range(5):
            self.store.save_order(dict(intent(str(n), ex='bitget', quantity='1.2'),
                state='UNKNOWN', filled='0'))
        self.assertEqual(self.engine.limits.exposure(snapshot), 600)
        self.assertGreater(self.engine.limits.exposure(snapshot, intent('extra', ex='bitget')), 600)
        with self.assertRaisesRegex(ValueError,'total_notional_limit'):
            self.engine.limits.check_order(intent('extra',ex='bitget'))
        with self.assertRaisesRegex(ValueError,'leg_notional_limit'):
            self.engine.limits.check_order(intent('large',ex='bitget',quantity='1.51'))

    def test_realized_fees_funding_and_floating_are_all_program_owned(self):
        entry=dict(intent('entry',ex='bitget'),state='TERMINAL',filled='1',fee='1',vwap='100',
            leg=0,accounting_pending=False,fill_ms=int((self.now+.5)*1000))
        close=dict(intent('close',ex='bitget',reduce=True),state='TERMINAL',filled='1',fee='2',
            vwap='90',leg=0,accounting_pending=False,fill_ms=int((self.now+.5)*1000))
        self.store.save_order(entry); self.store.save_order(close)
        at=str(int((self.now+.5)*1000))
        self.bg_book=[dict(id='trade',category='USDT-FUTURES',symbol='BTCUSDT',coin='USDT',
                          type='CLOSE_LONG',amount='-10',fee='-3',ts=at)]
        self.bitget_equity='29987'
        self.own_funding('-7')
        self.rows[0]['upnl']='10000'; self.equity='11000'
        loss=self.reconcile()
        self.assertEqual(dec(loss['daily']),-20)
        self.assertEqual(dec(loss['total']),-20)
        self.assertIn('daily_loss',self.store.meta('stop_locks'))
        self.assertFalse(self.engine.can_open())

    def test_unproved_legacy_funding_epoch_cannot_silently_restart_at_zero(self):
        self.reconcile()
        anchor=self.store.meta('program_funding_anchor')
        anchor.pop('funding_daily_proof')
        self.store.set_meta('program_funding_anchor',anchor)
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())

    def test_missing_income_never_becomes_zero_and_external_equity_cannot_force_halt(self):
        self.own_funding('-1')
        self.reconcile()
        old = self.store.meta('account_loss_current')
        self.activity_complete = False
        self.rows[0]['upnl'] = '-900'; self.equity = '100'
        self.reconcile()
        self.assertFalse(self.engine.can_open())
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertNotIn(self.store.meta('state'), ('HALTING', 'HALTED'))
        self.assertEqual(self.store.meta('account_loss_current'), old)
        self.margin = '1.5'
        with patch.object(self.engine, 'flatten_all') as flatten:
            self.reconcile()
        flatten.assert_called_once()

    def test_unknown_or_incomplete_transfer_remains_unverifiable(self):
        for kind in ('OTHER', 'AUTO_REPAY', 'INTEREST_ISOLATED', 'TRANSFER_IN'):
            with self.subTest(kind=kind):
                self.book = [dict(id='x', symbol='', exchange_type='HYPERLIQUID', coin='USDT',
                    statement_type=kind, change='-1', create_time=str(int((self.now+.5)*1000)))]
                self.reconcile()
                self.assertTrue(self.engine.can_open())
                self.assertTrue(self.store.meta('external_income')['verified'])

    def test_missing_program_fee_is_not_treated_as_zero(self):
        self.store.save_order(dict(intent('own', ex='bitget'), state='TERMINAL',
            filled='1', vwap='100', fee=None, fill_ms=int(self.now*1000), accounting_pending=False))
        self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())

    def test_new_snapshot_invalidates_proof_and_blocks_final_gate(self):
        self.reconcile()
        self.assertTrue(self.engine.can_open())
        old = self.store.meta('external_income')
        self.engine.account_cache.publish(self.snapshot(), self.now)
        self.store.set_meta('external_income', old)
        with self.assertRaises(ValueError):
            self.engine.check_order(intent(ex='bitget'))
        self.assertFalse(self.engine.can_open())

    def test_order_change_during_pnl_calculation_cannot_certify_old_result(self):
        from tools.crossex_exec import program_accounting as external_accounting
        original=external_accounting.journal_pnl
        def changed(store,now,**kwargs):
            value=original(store,now,**kwargs)
            store.save_order(dict(intent('late',ex='bitget'),state='TERMINAL',
                filled='0',fee='-999',leg=1,accounting_pending=False,fill_ms=int(now*1000)))
            return value
        with patch.object(external_accounting,'journal_pnl',side_effect=changed):
            self.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())

    def test_unknown_remote_position_still_mismatches_and_alert_does_not_repeat(self):
        self.rows.append(position('BINANCE_FUTURE_BTC_USDT'))
        for _ in range(4):
            self.reconcile()
        self.assertFalse(self.store.meta('position_mismatch'))
        self.assertTrue(self.engine.can_open())
        alerts = [e for e in self.store.events() if e['kind']=='ALERT'
                  and 'position_mismatch' in e['value']]
        self.assertEqual(len(alerts), 0)

    def test_margin_stop_and_flatten_leave_external_registered_position_intact(self):
        for margin, calls in (('3', 0), ('2.9', 0), ('2', 0), ('1.99', 1)):
            with self.subTest(margin=margin), patch.object(self.engine, 'flatten_all') as flatten:
                self.margin = margin
                self.reconcile()
                self.assertEqual(self.engine.can_open(), margin=='3')
                self.assertEqual(flatten.call_count, calls)
                self.assertEqual(self.rows[0]['position_qty'], '1')

    def test_bitget_maintenance_margin_uses_whole_account_and_only_reduces_program(self):
        original=self.accounts.snapshot
        for margin in ('2.99','1.99'):
            def snapshot():
                value=original(); value['margins'][1]=margin; return value
            self.accounts.snapshot=snapshot
            with self.subTest(margin=margin),patch.object(self.engine,'flatten_all') as flatten:
                self.reconcile()
                self.assertFalse(self.engine.can_open())
                self.assertEqual(flatten.call_count,1 if margin=='1.99' else 0)
                self.assertEqual(self.rows[0]['position_qty'],'1')


class DebtAndAlertTests(IsolationFixture, unittest.TestCase):
    def test_warning_state_and_notification_audit_commit_atomically(self):
        self.debt()
        save=self.store.set_meta
        def failed(key,value):
            if key=='external_liability_active':
                raise OSError('injected alert state failure')
            return save(key,value)
        with patch.object(self.store,'set_meta',side_effect=failed),self.assertRaises(OSError):
            self.engine.limits.check_equity(self.snapshot())
        self.assertFalse(any(e['kind']=='EXTERNAL_LIABILITY' for e in self.store.events()))
        self.assertIsNone(self.store.meta('alert_states'))

    def test_fully_attributed_native_debt_warns_without_stopping_or_blocking_send(self):
        self.debt()
        self.reconcile()
        self.assertTrue(self.engine.can_open())
        self.assertNotIn('account_liability', self.store.meta('stop_locks'))
        self.engine.check_order(intent(ex='bitget'))
        self.assertTrue(any(e['kind']=='EXTERNAL_LIABILITY' for e in self.store.events()))

    def test_unattributed_excess_wrong_currency_or_unknown_margin_still_blocks(self):
        for case in ('excess', 'wrong_coin', 'wrong_venue', 'margin_missing', 'program_group'):
            with self.subTest(case=case):
                self.debt('100.01' if case=='excess' else '50')
                if case=='wrong_coin': self.liabilities[0]['coin']='USDC'
                if case=='wrong_venue': self.liabilities[0]['exchange_type']='BINANCE'
                original = copy.deepcopy(self.rows)
                if case=='margin_missing': self.rows[0].pop('initial_margin', None)
                if case=='program_group': self.rows.append(position('HYPERLIQUID_FUTURE_BTC_USDT'))
                self.reconcile()
                self.assertIn('account_liability', self.store.meta('stop_locks'))
                self.assertFalse(self.engine.can_open())
                self.rows = original

    def test_debt_warning_deduplicates_across_reasons_restart_and_hour_then_state_change(self):
        self.debt()
        for _ in range(3):
            self.reconcile()
            self.engine.alert('other_reason')
        count = lambda:sum(e['kind']=='EXTERNAL_LIABILITY' for e in self.store.events())
        self.assertEqual(count(), 1)
        self.engine.close()
        self.engine = Engine(self.store,self.market,self.accounts,clock=lambda:self.now,
                             stop_file=self.engine.stop_file)
        self.addCleanup(self.engine.close)
        self.reconcile()
        self.assertEqual(count(), 1)
        self.now += 3600
        self.reconcile()
        self.assertEqual(count(), 2)
        self.liabilities = []
        self.reconcile()
        self.debt(); self.reconcile()
        self.assertEqual(count(), 3)

    def test_blocking_debt_recovers_only_after_three_distinct_attributable_snapshots(self):
        self.debt('101'); self.reconcile()
        self.debt('50')
        for n in range(3):
            self.reconcile()
            self.assertEqual('account_liability' in self.store.meta('stop_locks'), n<2)
            self.engine.observe_account_snapshot(self.engine.account_cache.read(), self.now)
            self.assertEqual('account_liability' in self.store.meta('stop_locks'), n<2)
        self.assertTrue(self.engine.can_open())

    def test_rebaseline_can_anchor_attributed_debt_but_cannot_clear_old_daily_lock(self):
        self.debt()
        self.store.set_meta('daily_loss_day',day(self.now)); self.engine.stops.add('daily_loss')
        self.anchor()
        self.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])
        self.assertIn('daily_loss',self.store.meta('stop_locks'))
        self.assertFalse(self.engine.can_open())

    def test_simultaneous_reason_alerts_and_changing_values_do_not_spam(self):
        for n in range(5):
            self.engine.alert('position_mismatch', details={'observed':n})
            self.engine.alert('external_income_unverifiable')
        self.assertEqual(sum(e['kind']=='ALERT' for e in self.store.events()), 2)


class NoExternalEvidenceTests(ExternalFixture, unittest.TestCase):
    def test_missing_position_pnl_cannot_be_certified_without_external_registry(self):
        self.engine.reconcile()
        own=dict(intent('own'),state='TERMINAL',filled='1',fee='0',vwap='100',leg=0,
                 accounting_pending=False,fill_ms=int(self.now*1000))
        self.store.save_order(own)
        self.rows=[position('BINANCE_FUTURE_BTC_USDT',upnl='-200')]
        self.equity='800'
        original=self.accounts.snapshot
        def missing():
            value=original(); value['upnl_by_position']={}; return value
        self.accounts.snapshot=missing
        self.now+=1; self.engine.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])

    def test_default_program_scope_also_requires_complete_evidence_without_external(self):
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.activity_complete = False; self.now += 1
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.assertTrue(self.store.meta('external_income')['verified'])

    def test_verified_transfer_with_no_external_is_observation_only(self):
        self.engine.reconcile()
        self.now += 1
        self.equity = '500'
        self.book = [dict(id='withdrawal', business_id='transfer', symbol='',
            exchange_type='BINANCE', coin='USDT', statement_type='TRANSFER_OUT',
            change='-500', create_time=str(int(self.now*1000)))]
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.assertEqual(dec(self.store.meta('account_loss_current')['total']), 0)

    def test_bitget_transfer_is_excluded_but_wrong_sign_or_fee_is_not(self):
        self.engine.reconcile()
        self.now+=1; self.bitget_equity='29000'
        self.bg_book=[dict(id='out',category='USDT-FUTURES',symbol='',coin='USDT',
            type='TRANSFER_OUT',amount='-1000',fee='0',ts=str(int(self.now*1000)))]
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.assertEqual(dec(self.store.meta('account_loss_current')['total']),0)
        self.bg_book[0]['fee']='-1'
        self.now+=1; self.engine.reconcile()
        self.assertTrue(self.engine.can_open())


if __name__ == '__main__':
    unittest.main()
