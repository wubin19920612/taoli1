"""Round 26: independently timed books survive unrelated account refreshes."""
import unittest
from unittest.mock import patch

from tools.fill_sim.core import Book
from round26_fixtures import world
from test_exec import setUpModule, tearDownModule


class PricingRecoveryTests(unittest.TestCase):
    def test_slow_books_unlock_despite_account_version_change_every_retry(self):
        w = world(self)
        w.engine.alert('exit_pricing_unavailable')
        versions = []

        def books(instruments):
            old = w.store.meta('account_snapshot_version')
            w.now += 6
            w.engine.refresh_accounts()
            versions.append((old, w.store.meta('account_snapshot_version')))
            return w.books(instruments)

        with patch.object(w.market, 'books', side_effect=books):
            w.advance()
        self.assertTrue(versions)
        self.assertTrue(all(a != b for a, b in versions))
        self.assertNotIn('exit_pricing_unavailable', w.store.meta('stop_locks'))
        self.assertFalse(w.engine.can_open())  # account refresh still needs its own PnL proof
        w.advance()
        self.assertTrue(w.engine.can_open(), w.store.meta('stop_locks'))

    def test_stale_or_shallow_books_keep_lock_then_fresh_depth_recovers(self):
        for fault in ('stale', 'shallow'):
            with self.subTest(fault=fault):
                w = world(self)
                w.engine.alert('exit_pricing_unavailable')
                bad = [Book([['100','0.000001']], [['102','0.000001']], w.now)
                       for _ in range(2)] if fault == 'shallow' else w.books(w.instruments)
                if fault == 'stale':
                    for book in bad:
                        book.received_at -= 2
                with patch.object(w.market, 'books', return_value=bad):
                    w.advance()
                self.assertIn('exit_pricing_unavailable', w.engine.stops.reasons)
                self.assertFalse(w.engine.can_open())
                w.advance()
                self.assertNotIn('exit_pricing_unavailable', w.engine.stops.reasons)
                self.assertTrue(w.engine.can_open())

    def test_journal_change_during_book_read_invalidates_quantity_evidence(self):
        w = world(self)
        w.engine.alert('exit_pricing_unavailable')
        order = next(w.store.iter_orders(reduce_only=False))

        def books(instruments):
            w.store.save_order(dict(order, fee='1'))
            return w.books(instruments)

        with patch.object(w.market, 'books', side_effect=books):
            w.advance()
        self.assertIn('exit_pricing_unavailable', w.engine.stops.reasons)
        self.assertFalse(w.engine.can_open())
        w.advance()
        self.assertNotIn('exit_pricing_unavailable', w.engine.stops.reasons)
        self.assertTrue(w.engine.can_open())


if __name__ == '__main__':
    unittest.main()
