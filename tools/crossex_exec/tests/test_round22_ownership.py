"""Round 22: confirmed ownership is the only authority to reduce inventory."""
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec.ownership import bounded_pair_positions
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


if __name__ == '__main__':
    unittest.main()
