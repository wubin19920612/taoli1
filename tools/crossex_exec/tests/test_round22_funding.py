"""Round 22: mixed funding attribution survives user withdrawal and restart."""
import tempfile
import unittest
from pathlib import Path

from tools.fill_sim.core import dec
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule


class MixedFundingTests(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        w = ProgramWorld(path=Path(tmp.name)/'round22.db')
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        return w

    def test_minus_90_mixed_funding_stays_minus_10_after_withdrawal_delayed_history_and_restart(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world()
                venue = w.venues[leg]
                own = venue.quantity
                venue.quantity = own*9
                venue.settle('-90')
                w.hide_cash = True
                w.advance()
                self.assertFalse(w.store.meta('external_income')['verified'])
                self.assertFalse(w.engine.can_open())
                venue.quantity = own
                w.restart()
                w.hide_cash = False; w.advance()
                income = w.store.meta('external_income')
                self.assertTrue(income['verified'], income)
                self.assertEqual(dec(income['program_funding_total']), dec(-10))
                self.assertEqual(w.engine.totals(), (dec(-10), dec(-10)))
                self.assertNotIn('daily_loss', w.store.meta('stop_locks', {}))
                self.assertTrue(w.store.meta('program_funding_ownership')['contracts'])
                venue.settle('-21'); w.advance()
                self.assertEqual(w.engine.totals(), (dec(-31), dec(-31)))
                self.assertIn('daily_loss', w.store.meta('stop_locks'))

    def test_uncertain_ratio_allocates_each_fee_adversely_before_netting(self):
        w = self.world()
        venue = w.venues[0]
        own = venue.quantity
        venue.quantity = own*3
        w.advance()
        venue.settle('90')
        venue.quantity = own*2
        w.advance()
        venue.settle('-90')
        w.advance()
        venue.quantity = own
        w.advance()
        income = w.store.meta('external_income')
        self.assertTrue(income['verified'], income)
        # Income takes the smaller observed 1/3 share; expense takes 1/2.
        self.assertEqual(dec(income['program_funding_total']), dec(-15))
        self.assertEqual(income['funding_attribution'], 'observed_quantity_ratio_estimate')
        self.assertTrue(income['funding_attribution_estimated'])

    def test_allocated_funding_still_requires_original_full_history_and_counters(self):
        w = self.world()
        venue = w.venues[0]
        own = venue.quantity
        venue.quantity = own*3
        venue.settle('-90'); w.advance()
        venue.quantity = own
        w.advance()
        self.assertEqual(w.engine.totals(), (dec(-30), dec(-30)))
        checkpoint = w.store.meta('program_funding_checkpoint')
        venue.book[-1]['change'] = '-3'
        w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        self.assertEqual(w.store.meta('program_funding_checkpoint'), checkpoint)
        self.assertFalse(w.engine.can_open())

    def test_new_charge_at_clean_boundary_cannot_be_diluted_by_old_mixed_ratio(self):
        w = self.world()
        venue = w.venues[0]
        own = venue.quantity
        venue.quantity = own*9
        venue.settle('-90'); w.advance()
        venue.quantity = own
        w.advance()
        self.assertEqual(w.engine.totals(), (dec(-10), dec(-10)))
        # Millisecond timestamps cannot order a fresh charge against the clean
        # observation. Refuse that ambiguity rather than dilute a -21 loss.
        venue.settle('-21'); w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        self.assertFalse(w.engine.can_open())

    def test_unknown_entry_during_mixed_interval_remains_unverifiable_after_query(self):
        w = self.world()
        venue = w.venues[0]
        entry = next(w.store.iter_orders(reduce_only=False))
        own = venue.quantity
        w.store.save_order(dict(entry, state='UNKNOWN', filled='0'))
        venue.quantity = own*3
        venue.settle('-90'); w.advance()
        w.store.save_order(entry)
        venue.quantity = own
        w.advance()
        income = w.store.meta('external_income')
        self.assertFalse(income['verified'])
        self.assertEqual(income['income_failure']['condition'], 'program_funding_attribution_unverifiable')
        self.assertFalse(w.engine.can_open())


if __name__ == '__main__':
    unittest.main()
