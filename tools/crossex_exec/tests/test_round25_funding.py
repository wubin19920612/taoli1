"""Round 25: sampled flat periods and contract-scoped mixed inventory."""
import tempfile
import unittest
from pathlib import Path

from tools.fill_sim.core import dec
from tools.crossex_exec.program_funding import OWNERSHIP_KEY, allocate_funding, observe_funding_ownership
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule


class FundingScopeTests(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        w = ProgramWorld(path=Path(tmp.name)/'round25.db')
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        return w

    def assert_income(self, w, amount, can_open=True):
        income = w.store.meta('external_income')
        self.assertTrue(income['verified'], income)
        self.assertEqual(w.engine.totals(), (dec(amount), dec(amount)))
        self.assertEqual(w.engine.can_open(), can_open, w.store.meta('stop_locks'))

    def user_open(self, w, leg):
        venue = w.venues[leg]
        venue.serial += 1
        venue.quantity = dec('.4')
        venue.live_position = dict(created=int(w.now*1000), id=str(venue.serial), funding='0')

    def user_close(self, w, leg):
        venue = w.venues[leg]
        venue.history.append(dict(venue.live_position, closed=int(w.now*1000)))
        venue.live_position = None
        venue.quantity = dec(0)

    def test_normal_flat_user_income_cannot_offset_reopened_program_loss(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                w.engine.close_pair(w.store.pairs(active=True)[0], 'ordinary_close')
                w.advance()
                self.assertTrue(all(v.quantity == 0 for v in w.venues))
                w.now += 1; self.user_open(w, leg); w.advance()
                w.now += 1; venue.settle('90'); w.advance()
                w.now += 1; self.user_close(w, leg); w.advance()
                w.restart(); w.advance(3601); w.open()
                self.assertTrue(all(v.quantity > 0 for v in w.venues))
                venue.settle('-21'); w.advance()
                self.assert_income(w, '-21', False)
                self.assertIn('daily_loss', w.store.meta('stop_locks'))

    def test_flat_interior_excludes_both_signs_with_delayed_cash_and_restart(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                w.engine.close_pair(w.store.pairs(active=True)[0], 'ordinary_close'); w.advance()
                w.now += 1; self.user_open(w, leg); w.advance()
                w.hide_cash = True
                w.now += 1; venue.settle('-90'); venue.settle('89'); w.advance()
                self.assertFalse(w.store.meta('external_income')['verified'])
                w.restart(); w.hide_cash = False; w.advance()
                self.assert_income(w, '0')

    def test_flat_sampling_boundaries_are_inclusive_and_adverse(self):
        w = self.world(); venue = w.venues[0]
        last_live = int(w.now*1000)
        w.engine.close_pair(w.store.pairs(active=True)[0], 'ordinary_close'); w.advance()
        first_flat = int(w.now*1000)
        w.advance(3602)
        last_flat = int(w.now*1000)
        w.open(); w.advance()
        self.assertTrue(all(v.quantity > 0 for v in w.venues))
        resumed = int(w.now*1000)
        state = w.store.meta(OWNERSHIP_KEY)
        row = dict(symbol=w.instruments[0].symbol)
        for at in (last_live, first_flat, last_flat, last_flat+1, resumed):
            with self.subTest(at=at):
                self.assertEqual(allocate_funding(state, 'crossex', row, at, dec(90)), (dec(0), True))
                self.assertEqual(allocate_funding(state, 'crossex', row, at, dec(-21)), (dec(-21), True))
        for value in (dec(-90), dec(90)):
            self.assertEqual(allocate_funding(state, 'crossex', row, first_flat+1, value), (dec(0), True))
        self.assertEqual(allocate_funding(state, 'crossex', row, resumed+1, dec(9)), (dec(9), False))

    def test_open_mixing_only_blocks_its_contract_and_keeps_full_expense(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                own = venue.quantity
                venue.quantity += dec('.4'); venue.settle('90'); venue.settle('-1'); w.advance()
                self.assert_income(w, '-1')
                self.assertFalse(w.store.meta('position_mismatch'))
                entry = next(r for r in w.store.iter_orders(reduce_only=False) if r['leg'] == leg)
                with self.assertRaisesRegex(ValueError, 'external_contract_forbidden'):
                    w.engine.external.check_order(entry)
                other = next(r for r in w.store.iter_orders(reduce_only=False) if r['leg'] != leg)
                w.engine.check_order(other)
                w.engine.close_pair(w.store.pairs(active=True)[0], 'only_own')
                self.assertEqual(venue.quantity, dec('.4'))
                self.assertGreater(own, 0)
                w.advance(); self.assert_income(w, '-1')

    def test_missing_program_leg_still_blocks_globally(self):
        w = self.world()
        own = w.venues[0].quantity
        w.venues[0].quantity -= dec('.1'); w.advance()
        self.assertTrue(w.store.meta('position_mismatch'))
        self.assertFalse(w.engine.can_open())
        self.assertFalse(w.store.meta('external_income')['verified'])
        w.venues[0].quantity = own
        for _ in range(3):
            w.advance()
        self.assert_income(w, '0')

    def test_roundtrip_between_flat_samples_keeps_every_program_expense(self):
        w = self.world(); venue = w.venues[0]
        w.engine.close_pair(w.store.pairs(active=True)[0], 'first_close'); w.advance(3601)
        w.open(); venue.settle('-1')
        w.engine.close_pair(w.store.pairs(active=True)[0], 'second_close'); w.advance()
        self.assert_income(w, '-1')
        w.restart(); w.advance()
        self.assert_income(w, '-1')

    def test_changed_journal_and_stale_samples_cannot_extend_flat_interior(self):
        w = self.world()
        w.engine.close_pair(w.store.pairs(active=True)[0], 'ordinary_close'); w.advance(3601)
        old_orders = list(w.store.iter_orders())
        flat_snapshot = w.accounts.snapshot(); flat_at = w.now
        w.open()
        observe_funding_ownership(w.store, flat_snapshot, w.now+1, old_orders)
        state = w.store.meta(OWNERSHIP_KEY)
        for record in state['contracts'].values():
            self.assertEqual(record['flat_periods'][-1]['last_flat_ms'], int(flat_at*1000))
        w.advance(2)
        state = w.store.meta(OWNERSHIP_KEY)
        observe_funding_ownership(w.store, flat_snapshot, flat_at, list(w.store.iter_orders()))
        self.assertEqual(w.store.meta(OWNERSHIP_KEY), state)


if __name__ == '__main__':
    unittest.main()
