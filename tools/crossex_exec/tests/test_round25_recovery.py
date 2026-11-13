"""Round 25: successful evidence releases only the locks it disproves."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.crossex_exec.transport import APIError
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule


class RecoveryTests(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        w = ProgramWorld(path=Path(tmp.name)/'locks.db'); self.addCleanup(w.close)
        w.market.current_funding = w.funding
        w.advance(); w.open(); w.advance()
        return w

    def events(self, w, kind):
        return [e for e in w.store.events() if e['kind'] == kind]

    def test_overdue_close_unlocks_only_after_actual_funding_certificate(self):
        w = self.world()
        pair = w.store.pairs(active=True)[0]
        for funding in pair['funding']:
            funding['next'] = w.now-1
        w.store.pair(pair); w.venues[0].settle('-1')
        w.engine.close_pair(pair, 'overdue_close')
        reason = 'funding_settlement_accounting_required'
        self.assertIn(reason, w.store.meta('stop_locks'))
        w.hide_cash = True; w.advance()
        self.assertIn(reason, w.store.meta('stop_locks'))
        w.hide_cash = False; w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        self.assertNotIn(reason, w.store.meta('stop_locks'))
        self.assertTrue(w.engine.can_open())
        self.assertEqual(len(self.events(w, 'FUNDING_SETTLEMENT_RECOVERED')), 1)
        w.restart(); w.advance()
        self.assertTrue(w.engine.can_open())
        self.assertEqual(len(self.events(w, 'FUNDING_SETTLEMENT_RECOVERED')), 1)

    def test_refresh_timeout_closed_pair_retries_after_restart_and_unlocks(self):
        w = self.world(); w.advance(301)
        with patch.object(w.market, 'current_funding', side_effect=APIError('timeout')):
            w.engine.track(w.store.pairs(active=True)[0])
            self.assertFalse(w.store.pairs(active=True))
            self.assertIn('funding_refresh_failed', w.store.meta('stop_locks'))
            w.advance(); w.restart(); w.advance()
            self.assertTrue(w.store.meta('external_income')['verified'])
            self.assertIn('funding_refresh_failed', w.store.meta('stop_locks'))
            self.assertFalse(w.engine.can_open())
        w.advance()
        self.assertNotIn('funding_refresh_failed', w.store.meta('stop_locks'))
        self.assertTrue(w.engine.can_open())
        self.assertEqual(len(self.events(w, 'FUNDING_REFRESH_RECOVERED')), 1)
        w.advance()
        self.assertEqual(len(self.events(w, 'FUNDING_REFRESH_RECOVERED')), 1)

    def test_recovery_audit_failure_cannot_clear_the_lock(self):
        w = self.world()
        w.engine.alert('funding_refresh_failed')
        original = w.store.event
        def event(kind, *args, **kwargs):
            if kind == 'FUNDING_REFRESH_RECOVERED':
                raise OSError('audit write failed')
            return original(kind, *args, **kwargs)
        with patch.object(w.store, 'event', side_effect=event):
            w.advance()
        self.assertIn('funding_refresh_failed', w.store.meta('stop_locks'))
        self.assertIn('funding_refresh_failed', w.engine.stops.reasons)
        self.assertFalse(self.events(w, 'FUNDING_REFRESH_RECOVERED'))
        for _ in range(3):
            w.advance()
        self.assertNotIn('funding_refresh_failed', w.store.meta('stop_locks'))
        self.assertTrue(w.engine.can_open())

    def test_other_observational_locks_recover_without_dropping_close_obligations(self):
        w = self.world()
        close_reasons = ('close_incomplete', 'flatten_retry_pending', 'flatten_snapshot_unavailable')
        read_reasons = ('negative_local_position', 'unknown_exposure_unavailable',
                        'exit_pricing_unavailable', 'cycle_failed_closed')
        for reason in close_reasons+read_reasons:
            w.engine.alert(reason)
        w.advance()
        for reason in close_reasons:
            self.assertIn(reason, w.store.meta('stop_locks'))
        for reason in read_reasons:
            self.assertNotIn(reason, w.store.meta('stop_locks'))
        w.engine.close_pair(w.store.pairs(active=True)[0], 'finish_obligation'); w.advance()
        for reason in close_reasons:
            self.assertNotIn(reason, w.store.meta('stop_locks'))
        self.assertTrue(w.engine.can_open())

    def test_foreign_orders_require_successful_empty_read(self):
        w = self.world()
        w.engine.alert('foreign_bitget_unfilled_orders')
        w.engine.alert('halting_startup_account_unavailable')
        with patch.object(w.accounts, 'validate_startup', side_effect=APIError('timeout')):
            with patch.object(w.accounts.bitget, 'open_orders', return_value=[dict(clientOid='foreign')]):
                w.advance()
        self.assertIn('foreign_bitget_unfilled_orders', w.store.meta('stop_locks'))
        self.assertIn('halting_startup_account_unavailable', w.store.meta('stop_locks'))
        w.advance()
        self.assertNotIn('foreign_bitget_unfilled_orders', w.store.meta('stop_locks'))
        self.assertNotIn('halting_startup_account_unavailable', w.store.meta('stop_locks'))
        self.assertTrue(w.engine.can_open())


if __name__ == '__main__':
    unittest.main()
