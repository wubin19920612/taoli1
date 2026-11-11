"""PM's round-five exposure contract, including persistent retry obligations."""
import unittest
from collections import Counter
from unittest.mock import patch

from tools.fill_sim.core import dec
from test_exec import setUpModule, tearDownModule
import test_invariants as simulation
import test_round5 as round5


class PMInvariantTests(unittest.TestCase):
    snapshot = round5.Round5Tests.snapshot
    pair = round5.Round5Tests.pair
    publish = round5.Round5Tests.publish
    setUp = round5.Round5Tests.setUp

    def world(self):
        world = simulation.World(True, Counter())
        self.addCleanup(lambda: world.engine.close())
        world.open()
        self.assertEqual(len(world.store.pairs(active=True)), 1)
        self.assertEqual(world.venues[0].quantity, world.venues[1].quantity)
        return world

    def stranded_close(self, world):
        world.fractions = [dec(0), dec(1)]
        pair = world.store.pairs(active=True)[0]
        world.engine.close_pair(pair, 'test_partial_exit')
        self.assertGreater(world.venues[0].quantity, 0)
        self.assertEqual(world.venues[1].quantity, 0)
        return world.store.pairs(active=True)[0]

    def test_i1a_single_close_cannot_hide_behind_exit_reason(self):
        sent = dict(order=dict(quantity='.5', leg=1), before=[dec(1), dec('.8')],
                    after=[dec(1), dec('.3')], batch=dict(paired_close=False), closing=True)
        with self.assertRaisesRegex(AssertionError, 'I1a'):
            simulation.InvariantSimulationTests().assert_reduction(sent, Counter())

    def test_i1b_checks_intermediate_fill_and_original_batch_bound(self):
        sent = dict(order=dict(quantity='1', leg=0), before=[dec(1), dec(1)],
                    after=[dec(0), dec(1)], batch=dict(paired_close=True, before=[dec(1), dec(1)]))
        simulation.InvariantSimulationTests().assert_reduction(sent, Counter())
        sent['after'] = [dec(0), dec('1.1')]
        with self.assertRaisesRegex(AssertionError, 'I1b'):
            simulation.InvariantSimulationTests().assert_reduction(sent, Counter())

    def test_i1c_incomplete_close_locks_alerts_and_retries_to_flat(self):
        world = self.world()
        pair = self.stranded_close(world)
        self.assertIn(pair['id'], world.store.meta('stop_locks', {}).get('unhedged_position', []))
        self.assertTrue(any(e['kind'] == 'UNHEDGED' for e in world.store.events()))
        self.assertGreater(pair.get('next_close', 0), world.now)
        self.assertFalse(world.engine.can_open())
        sent = len(world.sends)
        world.engine.tick()
        self.assertEqual(len(world.sends), sent, 'backoff must prevent immediate resubmission')
        world.fractions = [dec(1), dec(1)]
        world.now = pair['next_close']
        world.references()
        world.engine.tick()
        self.assertTrue(all(v.quantity == 0 for v in world.venues), 'due retry must flatten the remaining leg')

    def test_i1c_close_retry_survives_restart_and_backs_off_from_completion(self):
        world = self.world()
        pair = self.stranded_close(world)
        self.assertEqual(pair.get('next_close'), world.now+2)
        sent = len(world.sends)
        world.restart()
        self.assertEqual(len(world.sends), sent, 'restart must preserve the retry deadline')
        world.now = pair['next_close']
        world.engine.tick()
        newer = world.store.pairs(active=True)[0]
        self.assertGreater(len(world.sends), sent)
        self.assertEqual(newer.get('next_close'), world.now+4)

    def test_i1c_stopped_entry_repair_eventually_retries_remaining_positions(self):
        pair = self.pair(('.107', '.1'), steps=('.01', '.001'))
        self.venues[0].reject = True
        self.engine.balance(pair)
        self.assertTrue(pair.get('repair_stopped'))
        self.venues[0].reject = False
        self.now[0] += 61
        self.engine.tick()
        self.assertLess(self.venues[0].quantity, dec('.107'), 'repair_stopped cannot permanently strand exposure')

    def test_i1d_remote_exposure_blocks_admission_without_a_stop_lock(self):
        pair = self.pair(('.7', '.4'))
        pair['remote_remaining'] = ['.7', '.4']
        self.store.pair(pair)
        self.engine.risk = 'RUN'
        self.engine.last_reconcile = self.now[0]
        self.assertFalse(self.engine.stops)
        self.assertFalse(self.engine.can_open())

    def test_i1d_local_exposure_blocks_admission_with_old_balanced_remote(self):
        pair = self.pair(('.7', '.4'))
        pair['remote_remaining'] = ['.7', '.7']
        self.store.pair(pair)
        self.engine.risk = 'RUN'
        self.engine.last_reconcile = self.now[0]
        self.assertFalse(self.engine.stops)
        self.assertFalse(self.engine.can_open())

    def test_i1c_post_close_snapshot_failure_latches_uncertain_exposure(self):
        world = self.world()
        pair = world.store.pairs(active=True)[0]
        world.fractions = [dec('.5'), dec(1)]
        sent = len(world.sends)
        positions = world.positions
        def snapshot():
            if len(world.sends) > sent:
                raise ValueError('post-send position outage')
            return positions()
        with patch.object(world.accounts, 'position_snapshot', side_effect=snapshot):
            world.engine.close_pair(pair, 'test_partial_exit')
        self.assertNotEqual(world.venues[0].quantity, world.venues[1].quantity)
        self.assertIn(pair['id'], world.store.meta('stop_locks', {}).get('unhedged_position', []))
        self.assertGreater(pair.get('next_close', 0), world.now)
        self.assertFalse(world.engine.can_open())
        world.now = pair['next_close']
        world.fractions = [dec(1), dec(1)]
        world.engine.tick()
        self.assertTrue(all(v.quantity == 0 for v in world.venues))

    def test_i1c_retry_deadline_starts_after_slow_snapshot_failure(self):
        world = self.world()
        pair = world.store.pairs(active=True)[0]
        def unavailable():
            world.now += 17
            raise ValueError('slow position outage')
        with patch.object(world.accounts, 'position_snapshot', side_effect=unavailable):
            world.engine.close_pair(pair, 'test_outage')
        self.assertEqual(pair.get('next_close'), world.now+2)
        self.assertEqual(pair.get('exit_reason'), 'test_outage')

    def test_i1a_one_untradable_close_leg_cannot_turn_pair_into_unsafe_single(self):
        world = self.world()
        pair = world.store.pairs(active=True)[0]
        pair['instruments'][1]['spec']['min_notional'] = '1000'
        world.store.pair(pair)
        sent = len(world.sends)
        world.engine.close_pair(pair, 'test_minimum')
        self.assertEqual(len(world.sends), sent)
        self.assertEqual(world.venues[0].quantity, world.venues[1].quantity)
        self.assertGreater(pair.get('next_close', 0), world.now)
