"""Round 23: sampling gaps must not dilute program funding losses."""
import unittest

from tools.fill_sim.core import dec
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule


class FundingBoundaryTests(unittest.TestCase):
    def world(self):
        w = ProgramWorld(path=':memory:')
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


if __name__ == '__main__':
    unittest.main()
