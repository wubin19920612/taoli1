"""Round 23: sampling gaps must not dilute program funding losses."""
import tempfile
import unittest
from pathlib import Path

from tools.fill_sim.core import dec
from tools.crossex_exec.program_funding import OWNERSHIP_KEY, allocate_funding
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule


class FundingBoundaryTests(unittest.TestCase):
    def world(self, *, persistent=False):
        path = ':memory:'
        if persistent:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            path = Path(tmp.name)/'round23.db'
        w = ProgramWorld(path=path)
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        return w

    def assert_loss(self, w, expected):
        income = w.store.meta('external_income')
        self.assertTrue(income['verified'], income)
        self.assertEqual(dec(income['program_daily']), dec(expected))
        self.assertEqual(dec(income['program_funding_total']), dec(expected))
        self.assertEqual(w.engine.totals(), (dec(expected), dec(expected)))
        self.assertIn('daily_loss', w.store.meta('stop_locks'))
        self.assertFalse(w.engine.can_open())

    def test_withdrawal_gap_charges_full_loss_and_keeps_daily_stop(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world()
                venue = w.venues[leg]
                own = venue.quantity
                venue.quantity = own*9
                venue.settle('-9'); w.advance()
                venue.quantity = own
                w.now += .5
                venue.settle('-21'); w.advance(.5)
                for _ in range(3):
                    w.advance()
                self.assert_loss(w, '-22')

    def test_withdrawal_gap_allocates_income_and_expense_before_netting(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world()
                venue = w.venues[leg]
                own = venue.quantity
                venue.quantity = own*9
                venue.settle('-9'); w.advance()
                venue.quantity = own
                w.now += .5
                venue.settle('90'); venue.settle('-30')
                w.advance(.5)
                for _ in range(3):
                    w.advance()
                # The income can still belong to the mixed 1/9 position;
                # the expense can belong entirely to the program.
                self.assert_loss(w, '-21')

    def test_entry_gap_cannot_dilute_loss_paid_before_external_entry(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world()
                venue = w.venues[leg]
                own = venue.quantity
                w.now += .25
                venue.settle('-31')
                w.now += .25
                venue.quantity = own*9
                venue.settle('90'); w.advance(.5)
                self.assertFalse(w.engine.can_open())
                venue.quantity = own
                w.advance()
                for _ in range(3):
                    w.advance()
                self.assert_loss(w, '-21')

    def test_gap_evidence_survives_delayed_history_and_sqlite_restart(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(persistent=True)
                venue = w.venues[leg]
                own = venue.quantity
                venue.quantity = own*9
                venue.settle('-9'); w.advance()
                venue.quantity = own
                w.now += .5
                venue.settle('-21')
                w.hide_cash = True
                w.advance(.5)
                self.assertFalse(w.store.meta('external_income')['verified'])
                self.assertFalse(w.engine.can_open())
                w.restart()
                w.hide_cash = False
                for _ in range(3):
                    w.advance()
                self.assert_loss(w, '-22')

    def test_legacy_interval_without_first_mixed_sample_stays_stopped(self):
        w = self.world()
        venue = w.venues[0]
        own = venue.quantity
        w.now += .5
        venue.settle('-21')
        venue.quantity = own*9
        w.advance(.5)
        venue.quantity = own
        w.advance()
        state = w.store.meta(OWNERSHIP_KEY)
        state['version'] = 1
        for contract in state['contracts'].values():
            for interval in contract['intervals']:
                interval.pop('first_mixed_ms', None)
        w.store.set_meta(OWNERSHIP_KEY, state)
        for _ in range(3):
            w.advance()
        income = w.store.meta('external_income')
        self.assertFalse(income['verified'], income)
        self.assertEqual(income['income_failure']['condition'], 'program_funding_attribution_unverifiable')
        self.assertFalse(w.engine.can_open())

    def test_stable_mixed_interior_keeps_original_adverse_ratios(self):
        w = self.world()
        venue = w.venues[0]
        own = venue.quantity
        venue.quantity = own*9
        w.advance()
        w.now += .5
        venue.settle('-90'); venue.settle('45')
        w.advance(.5)
        venue.quantity = own
        w.advance()
        income = w.store.meta('external_income')
        self.assertTrue(income['verified'], income)
        self.assertEqual(w.engine.totals(), (dec(-5), dec(-5)))
        self.assertTrue(income['funding_attribution_estimated'])

    def test_unprovable_gap_ratio_cannot_certify_zero(self):
        state = dict(contracts={'route|raw': dict(intervals=[dict(
            from_ms=1000, first_mixed_ms=2000, observed_ms=3000, to_ms=4000,
            certain=False, lower=dict(owned='0', remote='9'),
            upper=dict(owned='0', remote='9'), prior_ids=[])])})
        for at in (1500, 3500):
            for amount in ('-21', '90'):
                with self.subTest(at=at, amount=amount):
                    with self.assertRaisesRegex(ValueError, 'program_funding_attribution_unverifiable'):
                        allocate_funding(state, 'route', dict(id='cash', symbol='raw'), at, dec(amount))


if __name__ == '__main__':
    unittest.main()
