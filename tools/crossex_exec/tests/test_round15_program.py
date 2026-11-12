"""Real Engine/Executor lifecycle in the default risk scope, offline venues."""
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tools.fill_sim.core import dec
from round15_simulator import ProgramWorld
from test_exec import setUpModule, tearDownModule
from test_invariants import SEEDS


class ProgramLifecycleTests(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        world = ProgramWorld(path=Path(tmp.name)/'program.db')
        self.addCleanup(world.close)
        self.assertTrue(world.engine.limits.program_scope)
        self.assertTrue(world.engine.can_open())
        return world

    def test_real_default_program_open_close_cash_delay_and_file_restart(self):
        w = self.world()
        w.advance(); w.open()
        self.assertEqual(w.coverage['open'], 2)
        self.assertTrue(all(v.quantity>0 for v in w.venues))
        w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        w.venues[0].settle('-3'); w.venues[1].settle('-4')
        w.hide_cash = True; w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        self.assertFalse(w.engine.can_open())
        w.restart()
        self.assertFalse(w.engine.can_open())
        w.hide_cash = False; w.advance()
        self.assertEqual(dec(w.store.meta('external_income')['program_total']), -7)
        w.engine.close_pair(w.store.pairs(active=True)[0], 'test_exit')
        self.assertEqual(w.coverage['reduce'], 2)
        self.assertTrue(all(v.quantity==0 for v in w.venues))
        w.hide_history = True; w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        w.hide_history = False; w.advance(); w.restart(); w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        self.assertEqual(dec(w.store.meta('external_income')['program_total']), -7)

    def test_explicit_rejection_and_peer_fill_recover_without_permanent_income_failure(self):
        w = self.world()
        w.explicit_reject = True; w.advance(); w.open()
        self.assertEqual(w.coverage['program_explicit_rejection'], 1)
        self.assertGreater(w.coverage['reduce'], 0)
        self.assertTrue(all(v.quantity==0 for v in w.venues))
        w.explicit_reject = False; w.advance(); w.restart(); w.advance()
        rows = w.store.all_orders()
        rejected = [r for r in rows if r['state']=='REJECTED']
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]['fee'], '0')
        self.assertTrue(w.store.meta('external_income')['verified'])
        # Rejection-rate and cooldown policies still apply independently.
        w.advance(3601)
        self.assertTrue(w.engine.can_open())
        before = w.coverage['open']; w.open()
        self.assertEqual(w.coverage['open'], before+2)

    def test_uta_missing_counter_or_wrong_history_identity_never_becomes_zero(self):
        w=self.world()
        w.advance(); w.open(); w.advance()
        w.venues[1].live_position['funding']=None
        w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        self.assertFalse(w.engine.can_open())
        w.venues[1].live_position['funding']='0'
        w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        w.engine.close_pair(w.store.pairs(active=True)[0],'test_exit')
        original=w.accounts.activity
        def conflict(start,end):
            result=original(start,end)
            result['bitget_position_history'][0]['marginCoin']='USDC'
            return result
        w.accounts.activity=conflict
        w.advance()
        self.assertFalse(w.store.meta('external_income')['verified'])
        self.assertFalse(w.engine.can_open())

    def test_default_flat_account_requires_both_independent_history_responses(self):
        w=self.world()
        original=w.accounts.activity
        for missing in ('crossex_position_history','bitget_position_history'):
            def omitted(start,end):
                result=original(start,end); result.pop(missing); return result
            w.accounts.activity=omitted
            w.advance()
            self.assertFalse(w.store.meta('external_income')['verified'])
            self.assertFalse(w.engine.can_open())
        w.accounts.activity=original
        w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])

    def test_fractional_wall_clock_epoch_uses_the_ledger_millisecond_boundary(self):
        w=self.world()
        w.now += .123456
        w.engine.rebaseline(confirm=True,reason='fractional_clock',source='cli')
        w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        self.assertTrue(w.engine.can_open())


class ProgramSimulationTests(unittest.TestCase):
    def test_default_program_three_seeds(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            coverage = Counter()
            for episode in range(12):
                w = ProgramWorld(coverage=coverage)
                try:
                    w.explicit_reject = episode%3==0
                    w.advance(); w.open()
                    self.assertTrue(w.engine.limits.program_scope)
                    if w.explicit_reject:
                        self.assertTrue(all(v.quantity==0 for v in w.venues))
                        w.explicit_reject = False
                    else:
                        self.assertTrue(all(v.quantity>0 for v in w.venues))
                        w.now += 1
                        for venue in w.venues:
                            venue.settle(str(-rng.randint(1,5)))
                        w.hide_cash = True; w.advance()
                        self.assertFalse(w.engine.can_open())
                        self.assertFalse(w.store.meta('external_income')['verified'])
                        coverage['program_delayed_funding'] += 1
                        w.restart()
                        self.assertFalse(w.engine.can_open())
                        w.hide_cash = False; w.advance()
                        w.engine.close_pair(w.store.pairs(active=True)[0], 'sim_program_exit')
                        self.assertTrue(all(v.quantity==0 for v in w.venues))
                        coverage['program_roundtrip'] += 1
                    w.advance(); w.restart(); w.advance()
                    self.assertTrue(w.store.meta('external_income')['verified'])
                    self.assertEqual(dec(w.store.meta('external_income')['program_total']),
                                     sum((v.funding_cash+v.realized for v in w.venues),dec(0)))
                    for sent in w.sends:
                        if sent['order']['reduce_only']:
                            self.assertTrue(all(0<=a<=b for a,b in zip(sent['after'],sent['before'])))
                        else:
                            self.assertFalse(sent['locked'])
                            self.assertIsNotNone(sent['admission'])
                            self.assertLessEqual(sent['total'],600)
                            self.assertLessEqual(dec(sent['order']['quantity'])*dec(sent['order']['price']),150)
                        coverage['program_send_checks'] += 1
                    coverage['program_episodes'] += 1
                finally:
                    w.close()
            for key in ('open','reduce','program_explicit_rejection','program_delayed_funding',
                        'program_roundtrip','restarts','program_send_checks'):
                self.assertGreater(coverage[key],0)
            self.assertEqual(coverage['program_episodes'],12)
            print('PROGRAM_INVARIANTS '+json.dumps(dict(seed=seed,coverage=dict(coverage)),sort_keys=True),flush=True)


if __name__ == '__main__': unittest.main()
