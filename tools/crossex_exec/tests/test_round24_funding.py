"""Round 24: adverse-only funding, inclusive boundaries and automatic closure."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.external import route_symbol
from tools.crossex_exec.program_funding import OWNERSHIP_KEY, allocate_funding, observe_funding_ownership
from tools.crossex_exec.transport import APIError
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule
from test_round20_positions import ASTER, unitree


class AdverseFundingTests(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        w = ProgramWorld(path=Path(tmp.name)/'round24.db')
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        return w

    def intervals(self, w, leg=0):
        route = 'crossex' if leg == 0 else 'bitget'
        return w.store.meta(OWNERSHIP_KEY)['contracts'][route+'|'+w.instruments[leg].symbol]['intervals']

    def recover_samples(self, w):
        for _ in range(3):
            w.advance()

    def assert_income(self, w, amount, *, can_open):
        income = w.store.meta('external_income')
        self.assertTrue(income['verified'], income)
        self.assertEqual(w.engine.totals(), (dec(amount), dec(amount)))
        self.assertEqual(w.engine.can_open(), can_open, w.store.meta('stop_locks'))

    def test_last_clean_millisecond_loss_is_full_even_if_already_verified(self):
        for leg in (0, 1):
            for verified_first in (False, True):
                with self.subTest(leg=leg, verified_first=verified_first):
                    w = self.world(); venue = w.venues[leg]
                    own = venue.quantity
                    venue.settle('-21')
                    if verified_first:
                        w.advance(0)
                    venue.quantity = own*9
                    w.advance()
                    venue.quantity = own
                    self.recover_samples(w)
                    self.assert_income(w, '-21', can_open=False)
                    self.assertIn('daily_loss', w.store.meta('stop_locks'))

    def test_last_clean_millisecond_verified_income_has_no_prior_id_exemption(self):
        w = self.world(); venue = w.venues[0]
        own = venue.quantity
        venue.settle('9'); w.advance(0)
        self.assert_income(w, '9', can_open=True)
        venue.quantity = own*9; w.advance()
        venue.quantity = own
        self.recover_samples(w)
        self.assert_income(w, '0', can_open=True)

    def test_own_confirmation_timeout_then_query_and_flat_never_leaves_funding_stop(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                pair = w.store.pairs(active=True)[0]
                entry = next(r for r in w.store.iter_orders(reduce_only=False) if r['leg'] == leg)
                w.store.save_order(dict(entry, state='UNKNOWN', filled='0'))
                venue.settle('-1')
                with patch.object(venue, 'detail', side_effect=APIError('unknown')):
                    w.advance()
                    self.assertFalse(w.engine.can_open())  # unresolved order still blocks
                # Use the real query/recovery path, not a manual lock reset.
                w.engine.recover_pair(pair)
                self.recover_samples(w)
                self.assertTrue(all(v.quantity == 0 for v in w.venues))
                self.assert_income(w, '-1', can_open=True)
                self.assertEqual(self.intervals(w, leg), [])
                w.restart(); w.advance()
                self.assert_income(w, '-1', can_open=True)
                w.engine.rebaseline(confirm=True, reason='resolved_timeout', source='cli')

    def test_pending_open_remainder_explains_only_its_own_side_and_maximum(self):
        for state in ('SENT', 'ACK', 'UNKNOWN'):
            for leg in (0, 1):
                with self.subTest(state=state, leg=leg):
                    w = self.world(); venue = w.venues[leg]
                    entry = next(r for r in w.store.iter_orders(reduce_only=False) if r['leg'] == leg)
                    own = venue.quantity
                    w.store.save_order(dict(entry, state=state, filled=str(own/2)))
                    w.advance()
                    self.assertEqual(self.intervals(w, leg), [])
                    venue.quantity += dec('.001'); w.advance()
                    self.assertIsNone(self.intervals(w, leg)[-1]['to_ms'])

    def test_intent_and_pending_reduction_do_not_explain_external_entry(self):
        for kind in ('intent', 'reduce', 'opposite_side'):
            with self.subTest(kind=kind):
                w = self.world(); venue = w.venues[0]
                entry = next(w.store.iter_orders(reduce_only=False))
                row = dict(entry, cid='extra', state='UNKNOWN', filled='0')
                if kind == 'intent':
                    row['state'] = 'INTENT'
                elif kind == 'reduce':
                    row['reduce_only'] = True
                else:
                    row['position_side'] = 'short'
                w.store.save_order(row)
                venue.quantity *= 2
                w.advance()
                self.assertIsNone(self.intervals(w)[-1]['to_ms'])

    def test_legacy_missing_boundary_migrates_from_first_order_and_recovers_after_restart(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                own = venue.quantity
                venue.settle('9'); w.advance()
                venue.quantity = own*9; w.advance()
                venue.settle('-1'); w.advance()
                venue.quantity = own
                self.recover_samples(w)
                state = w.store.meta(OWNERSHIP_KEY)
                state['version'] = 1
                name = ('crossex' if leg == 0 else 'bitget')+'|'+w.instruments[leg].symbol
                state['contracts'][name]['intervals'][0].pop('first_mixed_ms', None)
                w.store.set_meta(OWNERSHIP_KEY, state)
                w.restart(); self.recover_samples(w)
                self.assert_income(w, '-1', can_open=True)
                origin = min(int(r['created_at']*1000) for r in w.store.iter_orders()
                             if '|'.join(route_symbol(r['instrument'])) == name)
                self.assertEqual(self.intervals(w, leg)[0]['from_ms'], origin)
                self.assertEqual(w.store.meta(OWNERSHIP_KEY)['version'], 3)

    def test_closed_uncertain_interval_and_both_endpoints_are_final_adverse_values(self):
        state = dict(contracts={'route|raw': dict(intervals=[dict(
            from_ms=1000, first_mixed_ms=2000, observed_ms=3000, to_ms=4000,
            certain=False, lower=dict(owned='0', remote='9'),
            upper=dict(owned='0', remote='9'), prior_ids=['cash'])])})
        for at in (1000, 2000, 2500, 3000, 4000):
            for amount, expected in (('-21', '-21'), ('90', '0'), ('0', '0')):
                with self.subTest(at=at, amount=amount):
                    self.assertEqual(allocate_funding(state, 'route', dict(id='cash', symbol='raw'),
                                                     at, dec(amount)), (dec(expected), True))
        for at in (999, 4001):
            self.assertEqual(allocate_funding(state, 'route', dict(id='cash', symbol='raw'),
                                             at, dec(9)), (dec(9), False))

    def test_program_flat_closes_interval_even_while_external_quantity_remains(self):
        w = self.world(); venue = w.venues[0]
        own = venue.quantity
        venue.quantity = own*9; venue.settle('-1'); w.advance()
        self.assertIsNone(self.intervals(w)[-1]['to_ms'])
        w.engine.close_pair(w.store.pairs(active=True)[0], 'program_flat')
        w.advance()
        self.assertEqual(venue.quantity, own*8)
        end = self.intervals(w)[-1]['to_ms']
        self.assertEqual(end, int(w.now*1000))
        self.assertEqual(allocate_funding(w.store.meta(OWNERSHIP_KEY), 'crossex', venue.book[-2],
                                         int(venue.book[-2]['create_time']), dec(-1)), (dec(-1), True))
        self.recover_samples(w)
        self.assertEqual(len(self.intervals(w)), 1)
        self.assertEqual(self.intervals(w)[-1]['to_ms'], end)
        self.assert_income(w, '-1', can_open=True)
        # A flat program can work elsewhere; the same raw contract still has
        # outside inventory, so its existing admission guard must reject entry.
        entry = next(w.store.iter_orders(reduce_only=False))
        with self.assertRaisesRegex(ValueError, 'external_contract_forbidden'):
            w.engine.external.check_order(dict(entry, cid='forbidden-reopen', state='INTENT', filled='0'))
        venue.settle('90'); venue.settle('-90'); w.advance()
        self.assert_income(w, '-1', can_open=True)
        w.restart(); w.advance()
        self.assert_income(w, '-1', can_open=True)
        # The external lifecycle ends. A later program lifecycle must regain
        # full funding attribution, including its opening millisecond.
        venue.history.append(dict(venue.live_position, closed=int(w.now*1000)))
        venue.live_position = None; venue.quantity = dec(0)
        w.advance(3601); w.open()
        self.assertTrue(all(v.quantity > 0 for v in w.venues))
        venue.settle('-2'); w.advance()
        self.assert_income(w, '-3', can_open=True)

    def test_round22_23_withdrawal_repro_keeps_daily_loss_under_more_conservative_rule(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                own = venue.quantity
                venue.quantity = own*9; venue.settle('-9'); w.advance()
                venue.quantity = own
                w.now += .5; venue.settle('-21'); w.advance(.5)
                self.recover_samples(w)
                self.assert_income(w, '-30', can_open=False)
                self.assertIn('daily_loss', w.store.meta('stop_locks'))

    def test_fifty_unrelated_external_changes_never_stop_program(self):
        w = self.world()
        for n in range(50):
            w.user_rows = [] if n%5 == 0 else [unitree(ASTER, margin_mode='CROSS',
                position_side='LONG' if n%2 else 'SHORT', position_qty=str(n+1),
                upnl=str((-1 if n%2 else 1)*n*100), mark_price='100')]
            w.user_cash = dec(n*13)
            w.advance()
            self.assert_income(w, '0', can_open=True)
            self.assertTrue(all(not c['intervals'] for c in w.store.meta(OWNERSHIP_KEY)['contracts'].values()))

    def test_stale_observation_cannot_close_a_newer_mixed_interval(self):
        w = self.world(); venue = w.venues[0]
        clean = w.accounts.snapshot(); clean_at = w.now
        venue.quantity *= 9; w.advance()
        before = w.store.meta(OWNERSHIP_KEY)
        observe_funding_ownership(w.store, clean, clean_at, list(w.store.iter_orders()))
        self.assertEqual(w.store.meta(OWNERSHIP_KEY), before)

    def test_confirmed_reduction_consumes_unknown_open_capacity_in_same_lifecycle(self):
        w = self.world(); venue = w.venues[0]
        pair = w.store.pairs(active=True)[0]
        entry = next(w.store.iter_orders(reduce_only=False))
        w.engine.reduce(pair, 0, dec('.245'), 3)
        w.store.save_order(dict(entry, state='UNKNOWN', filled='0'))
        w.advance()
        self.assertEqual(venue.quantity, dec('.245'))
        self.assertEqual(self.intervals(w), [])
        venue.quantity += dec('.100')
        w.advance()
        self.assertEqual(len(self.intervals(w)), 1)
        self.assertIsNone(self.intervals(w)[-1]['to_ms'])

    def test_legacy_missing_end_in_earlier_interval_also_closes_on_clean_sample(self):
        w = self.world(); venue = w.venues[0]
        own = venue.quantity
        for _ in range(2):
            venue.quantity = own*3; w.advance()
            venue.settle('-1'); w.advance()
            venue.quantity = own; self.recover_samples(w)
        state = w.store.meta(OWNERSHIP_KEY)
        name = 'crossex|'+w.instruments[0].symbol
        state['version'] = 1
        state['contracts'][name]['intervals'][0].pop('to_ms')
        state['contracts'][name]['intervals'][0].pop('first_mixed_ms')
        w.store.set_meta(OWNERSHIP_KEY, state)
        w.restart(); self.recover_samples(w)
        self.assert_income(w, '-2', can_open=True)
        self.assertTrue(all(v['to_ms'] is not None for v in self.intervals(w)))

    def test_read_with_changed_order_journal_cannot_close_interval(self):
        w = self.world(); venue = w.venues[0]
        own = venue.quantity
        venue.quantity *= 9; w.advance()
        before = list(w.store.iter_orders())
        w.engine.reduce(w.store.pairs(active=True)[0], 0, own, 3)
        w.now += 1
        observe_funding_ownership(w.store, w.accounts.snapshot(), w.now, before)
        self.assertIsNone(self.intervals(w)[-1]['to_ms'])
        observe_funding_ownership(w.store, w.accounts.snapshot(), w.now, list(w.store.iter_orders()))
        self.assertEqual(self.intervals(w)[-1]['to_ms'], int(w.now*1000))

    def test_closed_result_and_delayed_endpoint_cash_survive_restart_without_stop(self):
        for leg in (0, 1):
            with self.subTest(leg=leg):
                w = self.world(); venue = w.venues[leg]
                own = venue.quantity
                venue.quantity *= 9; w.advance()
                venue.settle('-1'); venue.settle('20'); w.advance()
                venue.quantity = own; w.advance()
                end = int(w.now*1000)
                venue.settle('-1'); venue.settle('30')  # late at clean endpoint
                self.recover_samples(w)
                self.assert_income(w, '-2', can_open=True)
                self.assertEqual(self.intervals(w, leg)[-1]['to_ms'], end)
                saved = w.store.meta('external_income')
                self.assertEqual(saved['funding_attribution'], 'adverse_only_mixed_interval')
                self.assertEqual(dec(saved['program_funding_total']), -2)
                intervals = self.intervals(w, leg)
                w.restart(); w.advance()
                self.assert_income(w, '-2', can_open=True)
                self.assertEqual(self.intervals(w, leg), intervals)
                # Once outside the closed interval, ordinary program income counts.
                venue.settle('3'); w.advance()
                self.assert_income(w, '1', can_open=True)

    def test_overlapping_intervals_count_one_expense_and_active_interval_still_blocks(self):
        interval = dict(from_ms=1000, to_ms=2000)
        state = dict(contracts={'route|raw': dict(intervals=[interval, dict(interval)])})
        row = dict(id='cash', symbol='raw')
        self.assertEqual(allocate_funding(state, 'route', row, 1500, dec(-1)), (dec(-1), True))
        state['contracts']['route|raw']['intervals'][1]['to_ms'] = None
        with self.assertRaisesRegex(ValueError, 'program_funding_attribution_unverifiable'):
            allocate_funding(state, 'route', row, 1500, dec(-1))

    def test_clean_program_retains_both_income_and_expense(self):
        w = self.world()
        w.venues[0].settle('9'); w.venues[1].settle('-1'); w.advance()
        self.assert_income(w, '8', can_open=True)
        self.assertEqual(w.store.meta('external_income')['funding_attribution'], 'exclusive_program')


if __name__ == '__main__':
    unittest.main()
