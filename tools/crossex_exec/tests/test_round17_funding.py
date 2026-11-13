"""Restore an actually r15-rolled database; net evidence is not daily evidence."""
import copy
from contextlib import closing
import sqlite3
import subprocess
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from tools.fill_sim.core import dec
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position
import test_round16_funding as r16


class FundingOriginTests(ExternalFixture, unittest.TestCase):
    def setUp(self):
        self.independent_funding = True
        super().setUp()
        self.closed = []
        self.catalog((r16.OWN, r16.EXT))
        self.change(symbol=r16.EXT)
        self.rows = [dict(position(r16.EXT), position_id='external', funding_fee='0',
                          funding_time='0', create_time=str(int(self.now*1000)-1000))]
        self.anchor()
        self.now += 1
        self.rows.append(dict(position(r16.OWN), position_id='program', funding_fee='0',
                              funding_time='0', create_time=str(int(self.now*1000))))
        self.store.save_order(dict(intent('entry'), leg=0, created_at=self.now,
            fill_ms=self.now*1000, state='TERMINAL', filled='1', fee='0', vwap='100',
            accounting_pending=False))
        self.prove()

    prove = r16.DailyFundingTests.prove
    funding_row = r16.DailyFundingTests.funding_row
    opposite_days = r16.DailyFundingTests.opposite_days

    def activity(self, start_ms, end_ms):
        return dict(super().activity(start_ms, end_ms),
                    crossex_position_history=copy.deepcopy(self.closed), bitget_position_history=[])

    def legacy_roll(self):
        # Execute the reviewed local r15 implementation, not a mock of its result.
        root = Path(__file__).resolve().parents[3]
        source = subprocess.check_output(['git', 'show',
            'b61662d:tools/crossex_exec/external_accounting.py'], cwd=root).decode('utf-8')
        module = types.ModuleType('tools.crossex_exec.r15_accounting_fixture')
        module.__package__ = 'tools.crossex_exec'
        exec(compile(source, '<committed-r15-accounting>', 'exec'), module.__dict__)
        counters = types.ModuleType('tools.crossex_exec.r15_funding_fixture')
        counters.__package__ = 'tools.crossex_exec'
        code = subprocess.check_output(['git', 'show',
            'b61662d:tools/crossex_exec/funding.py'], cwd=root).decode('utf-8')
        exec(compile(code, '<committed-r15-funding>', 'exec'), counters.__dict__)
        module.funding_positions = counters.funding_positions
        module.funding_continuity = counters.funding_continuity
        from legacy_cash_diagnostics import anchor, snapshot as diagnostic_snapshot
        from tools.crossex_exec.external import ExternalRegistry
        anchor(self)
        old = self.store.meta('external_equity_anchor')
        old.pop('funding_daily_proof', None)
        for row in old['funding_positions'].values(): row.pop('settled_ms', None)
        self.store.set_meta('external_equity_anchor', old)
        self.store.set_meta('funding_checkpoint', None)
        self.opposite_days()
        snapshot = diagnostic_snapshot(self)
        self.engine.account_cache.publish(snapshot, self.now)
        snapshot = self.engine.account_cache.read()
        value = module.ExternalAccounting(self.store, ExternalRegistry(self.store,lambda:self.now),
                                          lambda:self.now).evaluate(snapshot, ExternalRegistry(self.store,lambda:self.now).split(snapshot))
        self.assertTrue(value['verified'])
        self.assertEqual(dec(value['program_daily']), 0)
        rolled = self.store.meta('external_equity_anchor')
        self.assertEqual(rolled['captured_at'], self.now)
        self.assertIsNone(self.store.meta('funding_checkpoint'))
        self.assertNotIn('funding_daily_proof', rolled)
        return rolled

    def assert_upgrade_stopped(self, store, rolled):
        engine = Engine(store, self.market, self.accounts, clock=lambda:self.now)
        self.accounts.validate_startup = self.snapshot
        try:
            with patch.object(engine, 'start_background'):
                engine.startup()
            for cash in ([], self.truth[:1], self.truth):
                self.book = copy.deepcopy(cash)
                engine.reconcile()
                self.assertFalse(store.meta('external_income')['verified'])
                self.assertEqual(store.meta('external_equity_anchor'), rolled)
                self.assertIsNone(store.meta('funding_checkpoint'))
                self.assertFalse(engine.can_open())
                with self.assertRaises(ValueError):
                    engine.check_order(intent(ex='bitget'))
        finally:
            engine.close()

    def test_r15_wrong_roll_upgrade_never_certifies_missing_days(self):
        rolled = self.legacy_roll()
        with Store(self.path) as reopened:
            self.assert_upgrade_stopped(reopened, rolled)

    def test_r15_sqlite_backup_restore_never_certifies_missing_days(self):
        rolled = self.legacy_roll()
        backup = Path(self.tmp.name)/'restored.db'
        with closing(sqlite3.connect(backup)) as target:
            self.store.db.backup(target)
        with Store(backup) as restored:
            self.assert_upgrade_stopped(restored, rolled)

    def test_certified_anchor_can_replace_missing_checkpoint_and_records_source(self):
        anchor = self.store.meta('program_funding_anchor')
        proof = anchor.get('funding_daily_proof', {})
        self.assertEqual(proof.get('version'), 1)
        self.assertEqual(proof.get('source'), 'audited_baseline')
        self.store.set_meta('program_funding_checkpoint', None)
        self.now += 1
        value = self.prove()
        self.assertTrue(value['funding_daily_verified'])
        self.assertEqual(self.store.meta('program_funding_checkpoint')['funding_daily_proof']['source'],
                         'daily_continuity')

    def test_legacy_or_tampered_anchor_cannot_replace_missing_checkpoint(self):
        original = self.store.meta('program_funding_anchor')
        for mode in ('missing', 'version', 'source', 'payload'):
            anchor = copy.deepcopy(original)
            proof = anchor.setdefault('funding_daily_proof', {})
            if mode == 'missing': anchor.pop('funding_daily_proof')
            if mode == 'version': proof['version'] = 0
            if mode == 'source': proof['source'] = 'legacy_net'
            if mode == 'payload': anchor['program_funding_carry'] = '999'
            with self.subTest(mode=mode):
                self.store.set_meta('program_funding_anchor', anchor)
                self.store.set_meta('program_funding_checkpoint', None)
                with self.assertRaises(ValueError): self.prove()
                self.assertEqual(self.store.meta('program_funding_anchor'), anchor)
                self.assertIsNone(self.store.meta('program_funding_checkpoint'))

    def test_daily_roll_has_bound_proof_and_failed_write_cannot_leave_certificate(self):
        boundary = (int(self.now)//86400+1)*86400
        self.now = boundary+1
        self.catalog((r16.OWN, r16.EXT))
        before = self.store.meta('program_funding_anchor')
        original = self.store.set_meta
        def fail(key, value):
            if key == 'program_funding_checkpoint': raise ValueError('write_failed')
            return original(key, value)
        with patch.object(self.store, 'set_meta', side_effect=fail), self.assertRaises(ValueError):
            self.prove()
        self.assertEqual(self.store.meta('program_funding_anchor'), before)
        self.prove()
        rolled = self.store.meta('program_funding_anchor')
        self.assertEqual(rolled,before)
        self.assertEqual(self.store.meta('program_funding_checkpoint')['funding_daily_proof']['source'], 'daily_continuity')
        self.store.set_meta('program_funding_checkpoint', None)
        self.now += 1
        self.assertTrue(self.prove()['funding_daily_verified'])


if __name__ == '__main__': unittest.main()
