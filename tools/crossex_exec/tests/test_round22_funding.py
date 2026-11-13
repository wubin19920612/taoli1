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

    def test_minus_90_mixed_funding_stays_full_after_withdrawal_delayed_history_and_restart(self):
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
                self.assertEqual(dec(income['program_funding_total']), dec(-90))
                self.assertEqual(w.engine.totals(), (dec(-90), dec(-90)))
                self.assertIn('daily_loss', w.store.meta('stop_locks', {}))
                self.assertTrue(w.store.meta('program_funding_ownership')['contracts'])
                venue.settle('-21'); w.advance()
                # Full expenses now breach the unchanged cumulative loss limit;
                # refresh the proof after its automatic closing fills.
                w.advance()
                self.assertTrue(all(v.quantity == 0 for v in w.venues))
                self.assertEqual(w.engine.totals(), (dec(-111), dec(-111)))
                self.assertIn('daily_loss', w.store.meta('stop_locks'))

    def test_mixed_interval_charges_expenses_and_discards_income_before_netting(self):
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
        # Round 24 replaces all sampled ratios: +90 counts zero, -90 in full.
        self.assertEqual(dec(income['program_funding_total']), dec(-90))
        self.assertEqual(income['funding_attribution'], 'adverse_only_mixed_interval')
        self.assertTrue(income['funding_attribution_estimated'])

    def test_allocated_funding_still_requires_original_full_history_and_counters(self):
        w = self.world()
        venue = w.venues[0]
        own = venue.quantity
        venue.quantity = own*3
        venue.settle('-90'); w.advance()
        venue.quantity = own
        w.advance()
        self.assertEqual(w.engine.totals(), (dec(-90), dec(-90)))
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
        self.assertEqual(w.engine.totals(), (dec(-90), dec(-90)))
        # The inclusive clean endpoint now finalizes the whole new expense.
        venue.settle('-21'); w.advance()
        w.advance()  # certify the new fills from the unchanged total-loss exit
        self.assertTrue(all(v.quantity == 0 for v in w.venues))
        self.assertTrue(w.store.meta('external_income')['verified'])
        self.assertEqual(w.engine.totals(), (dec(-111), dec(-111)))
        self.assertIn('daily_loss', w.store.meta('stop_locks'))
        self.assertFalse(w.engine.can_open())

    def test_unknown_entry_during_real_mixing_finalizes_after_query_and_withdrawal(self):
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
        self.assertTrue(income['verified'], income)
        self.assertEqual(w.engine.totals(), (dec(-90), dec(-90)))
        self.assertIn('daily_loss', w.store.meta('stop_locks'))
        self.assertFalse(w.engine.can_open())


if __name__ == '__main__':
    unittest.main()
