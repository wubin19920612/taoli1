"""Every automatic lock gets a failed observation followed by real recovery."""
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.transport import APIError
from round26_fixtures import world, halt, release
from test_exec import setUpModule, tearDownModule


class LockRecoveryTests(unittest.TestCase):
    def locked(self, w, reason):
        self.assertIn(reason, w.engine.stops.reasons)
        self.assertIn(reason, w.store.meta('stop_locks'))
        self.assertFalse(w.engine.can_open())

    def recovered(self, w, reason):
        self.assertNotIn(reason, w.engine.stops.reasons)
        self.assertNotIn(reason, w.store.meta('stop_locks'))
        self.assertTrue(w.engine.can_open(), w.store.meta('stop_locks'))

    def test_funding_settlement_accounting_required(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        for row in pair['funding']:
            row['next'] = w.now-1
        w.store.pair(pair)
        w.venues[0].settle('-1')
        w.engine.close_pair(pair, 'settlement_late')
        self.locked(w, 'funding_settlement_accounting_required')
        w.advance()
        self.recovered(w, 'funding_settlement_accounting_required')
        self.assertEqual(w.engine.totals(), (dec(-1), dec(-1)))

    def test_funding_refresh_failed(self):
        w = world(self)
        w.advance(301)
        with patch.object(w.market, 'current_funding', side_effect=APIError('timeout')):
            w.engine.track(w.store.pairs(active=True)[0])
        self.locked(w, 'funding_refresh_failed')
        w.advance()
        self.recovered(w, 'funding_refresh_failed')

    def test_negative_local_position(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        w.engine.close_pair(pair, 'delayed_entry_evidence')
        entry = next(w.store.iter_orders(reduce_only=False))
        w.store.save_order(dict(entry, filled='0'))
        w.engine.refresh_pair(pair)
        self.locked(w, 'negative_local_position')
        # The venue's original confirmed fill arrives; no new trade is invented.
        w.store.save_order(entry)
        w.engine.refresh_pair(pair)
        w.advance()
        self.recovered(w, 'negative_local_position')

    def test_unknown_exposure_unavailable(self):
        w = world(self)
        with patch.object(w.accounts, 'position_snapshot', side_effect=APIError('timeout')):
            self.assertFalse(w.engine.resolve_exposure(w.store.pairs(active=True)[0]))
        self.locked(w, 'unknown_exposure_unavailable')
        w.advance()
        self.recovered(w, 'unknown_exposure_unavailable')

    def test_close_incomplete(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        w.fractions = [dec(0), dec(0)]
        w.engine.close_pair(pair, 'once_no_fill')
        self.locked(w, 'close_incomplete')
        w.fractions = [dec(1), dec(1)]
        w.advance(3)
        self.locked(w, 'close_incomplete')  # matching open inventory is not an exit
        w.engine.close_pair(w.store.get_pair(pair['id']), 'once_no_fill')
        w.advance()
        self.recovered(w, 'close_incomplete')

    def test_flatten_retry_pending(self):
        w = world(self)
        with patch.object(w.engine, 'close_pair', side_effect=APIError('temporary_failure')):
            halt(w)
        self.locked(w, 'flatten_retry_pending')
        self.assertEqual(w.store.meta('state'), 'HALTING')
        w.now += 3
        w.engine.tick()
        self.assertEqual(w.store.meta('state'), 'HALTED')
        self.assertNotIn('flatten_retry_pending', w.engine.stops.reasons)
        release(w)
        w.advance()
        self.recovered(w, 'flatten_retry_pending')

    def test_exit_pricing_unavailable(self):
        w = world(self)
        # Pricing failure is recorded by track; reproduce its failed evidence.
        w.engine.alert('exit_pricing_unavailable')
        with patch.object(w.market, 'books', return_value=[None, None]):
            w.advance()
        self.locked(w, 'exit_pricing_unavailable')
        w.advance()
        self.recovered(w, 'exit_pricing_unavailable')

    def test_halting_startup_account_unavailable(self):
        w = world(self, opened=False)
        w.store.set_meta('state', 'HALTING')
        w.store.set_meta('halt_reason', 'total_loss')
        with patch.object(w.accounts, 'validate_startup', side_effect=APIError('timeout')):
            w.engine.startup()
        self.locked(w, 'halting_startup_account_unavailable')
        w.advance()
        self.assertNotIn('halting_startup_account_unavailable', w.engine.stops.reasons)
        self.assertEqual(w.store.meta('state'), 'HALTED')
        release(w)
        w.advance()
        self.recovered(w, 'halting_startup_account_unavailable')

    def test_foreign_bitget_unfilled_orders(self):
        w = world(self, opened=False)
        w.store.set_meta('state', 'HALTING')
        w.store.set_meta('halt_reason', 'total_loss')
        snapshot = dict(w.snapshot(), open_orders=[dict(clientOid='user_order')])
        with patch.object(w.accounts, 'validate_startup', return_value=snapshot):
            w.engine.startup()
        self.locked(w, 'foreign_bitget_unfilled_orders')
        w.advance()  # the owner has removed their order; open_orders() proves empty
        self.assertNotIn('foreign_bitget_unfilled_orders', w.engine.stops.reasons)
        release(w)
        w.advance()
        self.recovered(w, 'foreign_bitget_unfilled_orders')
        self.assertEqual(w.cancels, 0)

    def test_account_snapshot_unavailable(self):
        w = world(self)
        with patch.object(w.accounts, 'snapshot', side_effect=APIError('timeout')):
            w.engine.refresh_accounts()
        self.locked(w, 'account_snapshot_unavailable')
        for _ in range(2):
            w.advance()
            self.locked(w, 'account_snapshot_unavailable')
        w.advance()
        self.recovered(w, 'account_snapshot_unavailable')

    def test_account_liability(self):
        w = world(self)
        snapshot = w.snapshot()
        snapshot['liabilities'] = [dict(route='bitget', coin='USDT', liability='1')]
        with patch.object(w.accounts, 'snapshot', return_value=snapshot):
            w.advance()
        self.locked(w, 'account_liability')
        for _ in range(2):
            w.advance()
            self.locked(w, 'account_liability')
        w.advance()
        self.recovered(w, 'account_liability')

    def test_isolated_position(self):
        w = world(self)
        snapshot = w.snapshot()
        snapshot['raw_positions']['crossex'][0].update(margin_mode='ISOLATED', isolated_margin='1')
        snapshot['raw_accounts']['crossex'].update(position_mode='HEDGE', account_mode='CROSS_EXCHANGE',
            margin_balance='400', maintenance_margin='100', maintenance_margin_rate='4')
        with patch.object(w.accounts, 'snapshot', return_value=snapshot):
            w.advance()
        self.locked(w, 'isolated_position')
        w.advance()
        self.recovered(w, 'isolated_position')

    def test_external_identity_unverifiable(self):
        w = world(self)
        error = ValueError('external_identity_unverifiable')
        error.account_snapshot = w.snapshot()
        with patch.object(w.accounts, 'snapshot', side_effect=error):
            w.engine.refresh_accounts()
        self.locked(w, 'external_identity_unverifiable')
        for _ in range(3):
            w.advance()
        self.recovered(w, 'external_identity_unverifiable')

    def test_program_income_unverifiable(self):
        w = world(self)
        w.venues[0].settle('-1')
        w.hide_cash = True
        w.advance()
        self.locked(w, 'program_income_unverifiable')
        w.hide_cash = False
        w.advance()
        self.recovered(w, 'program_income_unverifiable')
        self.assertEqual(w.engine.totals(), (dec(-1), dec(-1)))

    def test_position_mismatch(self):
        w = world(self)
        quantity = w.venues[0].quantity
        w.venues[0].quantity -= dec('.001')
        w.advance()
        self.locked(w, 'position_mismatch')
        w.venues[0].quantity = quantity
        for _ in range(2):
            w.advance()
            self.locked(w, 'position_mismatch')
        w.advance()
        self.recovered(w, 'position_mismatch')

    def test_daily_loss(self):
        w = world(self)
        w.venues[0].settle('-21')
        w.advance()
        self.locked(w, 'daily_loss')
        w.venues[0].settle('2')
        w.advance()
        self.locked(w, 'daily_loss')  # same-day profit cannot erase the latch
        # Both venues supply finalized lifecycle counters before crossing UTC.
        w.engine.close_pair(w.store.pairs(active=True)[0], 'before_utc_rollover')
        w.advance()
        w.advance(86400)
        self.recovered(w, 'daily_loss')
        self.assertEqual(w.engine.totals(), (dec(0), dec(-19)))

    def test_unknown_global_and_equity_halts_do_not_auto_release(self):
        for reason in ('manual_review', 'unhedged_position', 'repair_stopped', 'account_equity_drop', 'total_loss'):
            with self.subTest(reason=reason):
                w = world(self, opened=False)
                w.engine.alert(reason)
                if reason in ('account_equity_drop', 'total_loss'):
                    w.store.set_meta('state', 'HALTED')
                for _ in range(3):
                    w.advance()
                self.locked(w, reason)

    def test_real_missing_leg_does_not_recover_from_extra_other_leg(self):
        w = world(self)
        w.venues[0].quantity -= dec('.001')
        w.venues[1].quantity += dec('.4')
        for _ in range(4):
            w.advance()
        self.locked(w, 'position_mismatch')
        self.assertIn('program_income_unverifiable', w.engine.stops.reasons)

    def test_pair_owner_recovery_does_not_clear_another_owner(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        pair['repair_stopped'] = True
        w.store.pair(pair)
        w.engine.unhedged(pair)
        for reason in ('unhedged_position', 'repair_stopped'):
            w.engine.stops.add(reason, 'unknown_other_pair')
        w.advance()
        for reason in ('unhedged_position', 'repair_stopped'):
            self.assertEqual(w.engine.stops.reasons[reason], {'unknown_other_pair'})
        self.assertFalse(w.engine.can_open())


def pending_confirmation_case(reason):
    def test(self):
        w = world(self)
        entry = next(w.store.iter_orders(reduce_only=False))
        # Persist the failed observation from this lock's originating path.
        w.store.save_order(dict(entry, accounting_pending=True))
        w.engine.alert(reason)
        w.advance()
        self.locked(w, reason)
        w.store.save_order(entry)  # original venue fill/accounting becomes available
        w.advance()
        self.recovered(w, reason)
    return test


for _reason in ('unknown_order', 'unknown_close', 'accounting_pending', 'fills_pending', 'entry_accounting_unknown'):
    setattr(LockRecoveryTests, 'test_'+_reason, pending_confirmation_case(_reason))


def historical_observation_case(reason):
    def test(self):
        w = world(self)
        w.engine.alert(reason)  # replay the durable lock from CLI/older versions
        with patch.object(w.accounts, 'program_activity', side_effect=APIError('history_timeout')):
            w.advance()
        self.locked(w, reason)
        w.advance()
        self.recovered(w, reason)
    return test


for _reason in ('cycle_failed_closed', 'external_income_unverifiable', 'external_baseline_unverifiable', 'history_coverage'):
    setattr(LockRecoveryTests, 'test_'+_reason, historical_observation_case(_reason))


def owner_recovery_case(reason):
    def test(self):
        w = world(self)
        pair = w.store.pairs(active=True)[0]
        pair.update(repair_stopped=True, remote_pending=True)
        w.store.pair(pair)
        w.engine.unhedged(pair)
        self.locked(w, reason)
        w.advance()
        self.recovered(w, reason)
        self.assertFalse(w.store.get_pair(pair['id'])['repair_stopped'])
    return test


for _reason in ('unhedged_position', 'repair_stopped'):
    setattr(LockRecoveryTests, 'test_'+_reason, owner_recovery_case(_reason))


if __name__ == '__main__':
    unittest.main()
