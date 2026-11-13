"""Round 22: tracking observations must stay inside program ownership."""
import unittest

from tools.fill_sim.core import day, dec
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule
from test_round20_positions import unitree, ASTER


class TrackingPnlTests(unittest.TestCase):
    def test_external_minus_9000_never_becomes_next_day_program_profit(self):
        w = ProgramWorld()
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        pair = w.store.pairs(active=True)[0]
        w.user_rows = [unitree(ASTER, margin_mode='CROSS', position_qty='100', upnl='-9000', mark_price='100')]
        w.advance()
        self.assertEqual(w.engine.totals(), (dec(0), dec(0)))
        w.engine.save_tracking_upnl(pair, w.books(w.instruments), w.now)
        w.engine.close_pair(pair, 'tracking_exit')
        # Cross the UTC boundary before the next account observation, exactly as
        # tracking and recovery can do in production.
        w.advance(86400)
        self.assertEqual(w.engine.totals(), (dec(0), dec(0)))
        self.assertEqual(dec(w.store.meta('program_daily_upnl')['upnl']), 0)
        self.assertNotIn('crossex|'+ASTER+'|long', w.store.meta('program_last_upnl')['positions'])
        w.open(); w.advance()
        w.venues[0].settle('-21'); w.advance()
        self.assertEqual(w.engine.totals(), (dec(-21), dec(-21)))
        self.assertEqual(w.store.meta('daily_loss_day'), day(w.now))
        self.assertFalse(w.engine.can_open())

    def test_tracking_mixed_contract_uses_confirmed_quantity_and_program_entry(self):
        w = ProgramWorld()
        self.addCleanup(w.close)
        w.advance(); w.open(); w.advance()
        pair = w.store.pairs(active=True)[0]
        owned = dec(pair['remaining'][0])
        w.venues[0].quantity += dec(3)
        w.prices[0] = dec(90)
        w.advance()
        # Simulate stale/legacy merged tracking quantities. They must not be
        # another authority for computing the program's marked inventory.
        pair['remote_remaining'][0] = str(owned+3)
        w.engine.save_tracking_upnl(pair, w.books(w.instruments), w.now)
        self.assertEqual(dec(w.store.meta('program_last_upnl')['upnl']), owned*dec(-10))


if __name__ == '__main__':
    unittest.main()
