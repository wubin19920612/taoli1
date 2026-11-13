"""Round 22: confirmed ownership is the only authority to reduce inventory."""
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.ownership import bounded_pair_positions, journal_positions
from tools.crossex_exec.transport import APIError
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule


class OwnershipTests(unittest.TestCase):
    def world(self):
        w = ProgramWorld()
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        return w

    def test_unknown_open_with_user_point_four_never_reduces_more_than_confirmed_point_one(self):
        w = self.world()
        pair = w.store.pairs(active=True)[0]
        entry = next(w.store.iter_orders(pair_id=pair['id'], reduce_only=False))
        self.assertEqual(entry['leg'], 0)
        entry.update(state='UNKNOWN', filled='0.100')
        w.store.save_order(entry)
        w.venues[0].orders[entry['cid']]['filled'] = '0.100'
        w.venues[0].quantity = dec('0.500')
        detail = w.venues[0].detail

        def delayed(order, **kwargs):
            if order['cid'] == entry['cid']:
                raise APIError('unknown')
            return detail(order, **kwargs)

        with patch.object(w.venues[0], 'detail', side_effect=delayed):
            w.engine.reconcile()
            self.assertFalse(w.engine.can_open())
            w.engine.recover_pair(pair)
            reduced = sum((s['filled'] for s in w.sends
                           if s['order']['reduce_only'] and s['order']['leg'] == 0), dec(0))
            self.assertEqual(reduced, dec('0.100'))
            self.assertEqual(w.venues[0].quantity, dec('0.400'))
            self.assertNotEqual(w.store.get_pair(pair['id'])['state'], 'CLOSED')
            self.assertTrue(w.store.meta('stop_locks'))

    def test_unconfirmed_opens_never_expand_and_unconfirmed_closes_reserve_the_bound(self):
        w = self.world()
        pair = w.store.pairs(active=True)[0]
        entry = next(w.store.iter_orders(pair_id=pair['id'], reduce_only=False))
        key = w.engine.position_key(pair, 0)
        for state in ('INTENT', 'SENT', 'ACK', 'UNKNOWN'):
            with self.subTest(state=state):
                entry.update(state=state, filled='0.100')
                w.store.save_order(entry)
                snapshot = dict(positions={key:dec('0.500')})
                self.assertEqual(bounded_pair_positions(w.store, pair, snapshot)['positions'][key], dec('0.100'))
                close = dict(entry, cid='unconfirmed-close', reduce_only=True, quantity='0.080', filled='0.020')
                w.store.save_order(close)
                self.assertEqual(bounded_pair_positions(w.store, pair, snapshot)['positions'][key], dec('0.020'))
                close.update(state='TERMINAL', filled='0')
                w.store.save_order(close)

    def test_delayed_entry_after_confirmed_partial_close_recovers_point_245_without_user_position(self):
        w = self.world()
        pair = w.store.pairs(active=True)[0]
        entry = next(w.store.iter_orders(pair_id=pair['id'], reduce_only=False))
        self.assertEqual(dec(entry['filled']), dec('0.490'))
        w.engine.reduce(pair, 0, dec('0.245'), 3)
        self.assertEqual(w.venues[0].quantity, dec('0.245'))
        entry.update(state='UNKNOWN', filled='0')
        w.store.save_order(entry)
        w.venues[0].quantity += dec('0.400')
        detail = w.venues[0].detail

        def delayed(order, **kwargs):
            if order['cid'] == entry['cid']:
                raise APIError('unknown')
            return detail(order, **kwargs)

        with patch.object(w.venues[0], 'detail', side_effect=delayed):
            self.assertTrue(w.engine.resolve_exposure(pair))
            self.assertEqual(w.engine.quantities(pair)[0], 0)
            self.assertEqual(w.venues[0].quantity, dec('0.645'))
            self.assertNotIn('unknown_exposure_unavailable', w.store.meta('stop_locks', {}))
            self.assertFalse(w.engine.can_open())
        # The next authoritative order query supplies the missing 0.490 entry.
        # Only its 0.245 remainder is now available for recovery.
        w.engine.recover_pair(pair)
        self.assertEqual(w.venues[0].quantity, dec('0.400'))
        self.assertEqual(w.venues[1].quantity, 0)
        self.assertEqual(w.store.get_pair(pair['id'])['state'], 'CLOSED')
        self.assertEqual(journal_positions(w.store), {})

    def test_negative_terminal_or_other_lifecycle_inventory_is_still_invalid(self):
        w = self.world()
        pair = w.store.pairs(active=True)[0]
        entry = next(w.store.iter_orders(pair_id=pair['id'], reduce_only=False))
        # A different lifecycle on the same contract cannot absorb this deficit.
        close = dict(entry, cid='invalid-close', pair_id='other', reduce_only=True, filled='0.245')
        w.store.save_order(close)
        with self.assertRaisesRegex(ValueError, 'negative_local_position'):
            journal_positions(w.store)


if __name__ == '__main__':
    unittest.main()
