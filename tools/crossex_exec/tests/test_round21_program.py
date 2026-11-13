"""Round 21: automatic ownership and journal-only risk, entirely offline."""
import copy
import tempfile
import unittest
from pathlib import Path

from tools.fill_sim.core import dec
from tools.crossex_exec.accounts import remote_positions
from tools.crossex_exec.storage import Store
from round15_simulator import ProgramWorld
from test_exec import intent, setUpModule, tearDownModule
from test_round20_positions import PositionFixture, unitree, HL, ASTER


class AutomaticOwnershipTests(PositionFixture, unittest.TestCase):
    def test_external_open_add_reduce_flip_isolated_needs_no_registration(self):
        self.engine.limits.initialize(self.accounts.snapshot())
        for quantity, side, mode in [('2','LONG','CROSS'), ('8','LONG','CROSS'),
                ('1','LONG','CROSS'), ('5','SHORT','CROSS'), ('2','LONG','ISOLATED')]:
            self.http.responses['/api/v4/crossex/positions'] = [
                unitree(position_qty=quantity, position_side=side, margin_mode=mode),
                unitree(ASTER, margin_mode='CROSS', position_side='SHORT', leverage='2')]
            self.now += 1
            self.engine.reconcile()
            with self.subTest(quantity=quantity, side=side, mode=mode):
                self.assertTrue(self.engine.can_open(), self.store.meta('external_income'))
                self.assertFalse(self.store.meta('position_mismatch'))
                self.assertEqual(self.engine.totals(), (dec(0),dec(0)))
                self.assertEqual(self.engine.limits.exposure(self.engine.snapshot), 0)
        self.assertEqual(self.store.all_orders(), [])

    def test_intent_survives_reopen_but_shadow_and_unsent_do_not_claim_contract(self):
        for state in ('SHADOW','UNSENT','INTENT','SENT','UNKNOWN','TERMINAL','REJECTED'):
            order = dict(intent(),cid='owner-'+state,state=state,filled='0')
            order['instrument']['symbol'] = HL if state in ('SHADOW','UNSENT') else ASTER
            if state not in ('SHADOW','UNSENT'):
                order['instrument']['market']['exchange'] = 'aster'
            self.store.save_order(order)
        with Store(self.path) as reopened:
            from tools.crossex_exec.ownership import ProgramOwnership
            registry = ProgramOwnership(reopened,lambda:self.now)
            snapshot = self.accounts.snapshot()
            self.assertEqual(registry.split(snapshot)['positions'], {})
            self.http.responses['/api/v4/crossex/positions'] = [unitree(ASTER,margin_mode='CROSS')]
            self.assertEqual(registry.split(self.accounts.snapshot())['positions'],
                             {'crossex|'+ASTER+'|long':dec(2)})

    def test_external_on_either_side_blocks_even_after_new_intent_is_persisted(self):
        self.engine.limits.initialize(self.accounts.snapshot())
        self.engine.reconcile()
        order = dict(intent(),state='INTENT',filled='0')
        order['instrument']['symbol'] = HL
        order['position_side'] = 'short'
        for persisted in (False,True):
            if persisted:
                self.store.save_order(order)
            with self.subTest(persisted=persisted), self.assertRaisesRegex(ValueError,'external_contract_forbidden'):
                self.engine.check_order(order)

    def test_unrelated_account_cash_and_missing_full_history_do_not_block_rebaseline(self):
        self.accounts.activity = lambda *_: (_ for _ in ()).throw(AssertionError('whole account history'))
        self.engine.limits.initialize(self.accounts.snapshot())
        self.engine.rebaseline(confirm=True,reason='program_epoch',source='cli')
        self.engine.reconcile()
        self.assertTrue(self.engine.can_open())
        self.assertIsNone(self.store.meta('external_equity_anchor'))
        self.assertEqual(self.engine.totals(),(dec(0),dec(0)))


class ProgramAccountingTests(unittest.TestCase):
    def world(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        world = ProgramWorld(path=Path(tmp.name)/'r21.db')
        self.addCleanup(world.close)
        return world

    def test_trading_cash_ledger_is_not_required_when_order_fills_are_complete(self):
        w = self.world(); w.advance(); w.open(); w.advance()
        w.hide_cash = True
        w.advance()
        self.assertTrue(w.store.meta('external_income')['verified'])
        w.venues[0].settle('-3'); w.advance()
        self.assertFalse(w.engine.can_open())
        self.assertFalse(w.store.meta('external_income')['verified'])
        w.hide_cash = False; w.advance()
        self.assertEqual(w.engine.totals(),(dec(-3),dec(-3)))

    def test_external_cash_equity_and_pnl_never_change_program_limits(self):
        w = self.world(); w.advance(); w.open(); w.advance()
        original = w.snapshot
        def volatile():
            snapshot = original()
            snapshot['equity_by_venue'] = dict(crossex='1',bitget='1')
            snapshot['equity'] = '2'
            row = unitree(ASTER,margin_mode='CROSS',upnl='-9000',position_qty='100')
            key = 'crossex|'+ASTER+'|long'
            snapshot['raw_positions']['crossex'].append(row)
            snapshot['positions'][key] = dec(100)
            snapshot['notionals'][key] = '10000'
            snapshot['upnl_by_position'][key] = '-9000'
            snapshot['upnl'] = str(dec(snapshot['upnl'])-9000)
            return snapshot
        w.accounts.snapshot = volatile
        w.advance()
        self.assertEqual(w.engine.totals(),(dec(0),dec(0)))
        self.assertNotEqual(w.store.meta('state'),'HALTING')
        self.assertLessEqual(w.engine.limits.exposure(w.engine.snapshot),600)
        w.venues[0].settle('-7'); w.advance()
        self.assertEqual(w.engine.totals(),(dec(-7),dec(-7)))

    def test_mixed_contract_reduction_is_capped_at_journal_quantity(self):
        w = self.world(); w.advance(); w.open(); w.advance()
        pair = w.store.pairs(active=True)[0]
        owned = dec(pair['remaining'][0])
        w.venues[0].quantity += dec(3)
        w.advance()
        self.assertFalse(w.engine.can_open())
        self.assertTrue(w.store.meta('position_mismatch'))
        self.assertTrue(w.store.meta('program_contract_conflicts'))
        pair = w.store.get_pair(pair['id'])
        self.assertEqual(dec(pair['remote_remaining'][0]),owned)
        w.engine.close_pair(pair,'mixed_contract_exit')
        self.assertEqual(w.venues[0].quantity,dec(3))
        self.assertEqual(w.venues[1].quantity,dec(0))
        for sent in w.sends:
            if sent['order']['reduce_only']:
                self.assertTrue(all(0<=a<=b for a,b in zip(sent['after'],sent['before'])))

    def test_physical_margin_breach_reduces_only_program_quantities(self):
        w = self.world(); w.advance(); w.open(); w.advance()
        original = w.accounts.snapshot
        def endangered():
            result = original()
            row = unitree(ASTER,margin_mode='CROSS',position_qty='100')
            key = 'crossex|'+ASTER+'|long'
            result['raw_positions']['crossex'].append(row)
            result['positions'][key] = dec(100)
            result['upnl_by_position'][key] = '0'
            result['notionals'][key] = '10000'
            result['margins'] = ['1.5','5']
            return result
        w.accounts.snapshot = endangered
        w.advance()
        self.assertEqual([v.quantity for v in w.venues],[dec(0),dec(0)])
        self.assertFalse(w.engine.can_open())
        self.assertTrue(all(s['order']['instrument']['symbol']!=ASTER for s in w.sends))


if __name__ == '__main__':
    unittest.main()
