"""Cache work, not truth: complete reads must still detect disappearing cash."""
import copy
import unittest
from unittest.mock import patch

from tools.crossex_exec.external import digest
from tools.crossex_exec.security import Redactor
from test_exec import setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position


class ResourceSafetyTests(ExternalFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.symbol = 'BINANCE_FUTURE_BTC_USDT'
        self.change(symbol=self.symbol)
        self.rows = [position(self.symbol)]
        self.anchor()
        self.now += 1
        self.book = [dict(id=str(n),business_id=str(n),symbol=self.symbol,exchange_type='BINANCE',
            coin='USDT',statement_type='TRANSACTION',change='0',create_time=str(int(self.now*1000)))
            for n in range(250)]

    def test_one_history_index_per_income_proof_and_unchanged_rows_reuse_hashes(self):
        from tools.crossex_exec import external_accounting as accounting
        original = accounting.history_index
        with patch.object(accounting,'history_index',wraps=original) as index:
            self.engine.reconcile()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertEqual(index.call_count, 1)
        with patch.object(accounting,'digest',wraps=digest) as hashed:
            self.now += 1
            self.engine.reconcile()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertLessEqual(hashed.call_count, 1)

    def test_full_window_recheck_detects_missing_revised_and_late_cached_rows(self):
        self.engine.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])
        before = copy.deepcopy(self.store.meta('external_verified_history'))
        truth = copy.deepcopy(self.book)
        for mode in ('missing','changed','partial'):
            self.book = copy.deepcopy(truth)
            self.activity_complete = True
            if mode == 'missing': self.book.pop(0)
            if mode == 'changed': self.book[0]['business_id'] = 'revised'
            if mode == 'partial': self.activity_complete = False
            self.now += 1
            self.engine.reconcile()
            self.assertFalse(self.store.meta('external_income')['verified'])
            self.assertFalse(self.engine.can_open())
            self.assertEqual(self.store.meta('external_verified_history'), before)
        self.book = truth
        self.activity_complete = True
        self.now += 1
        self.engine.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])

    def test_hash_cache_mutation_failure_and_rebaseline_do_not_supply_old_evidence(self):
        self.engine.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])
        self.book[0]['change'] = '-1'
        self.now += 1
        self.engine.reconcile()
        self.assertFalse(self.store.meta('external_income')['verified'])
        self.assertFalse(self.engine.can_open())
        with self.assertRaises(ValueError): self.anchor()


class RedactionCostTests(unittest.TestCase):
    def test_signature_inventory_is_not_sorted_for_each_scalar(self):
        redactor = Redactor()
        redactor.register('secret-key')
        for n in range(2048): redactor.signature(f'{n:064x}')
        rows = [dict(symbol='BTCUSDT',amount=str(n),id=str(n)) for n in range(1000)]
        with patch('builtins.sorted', wraps=sorted) as sorting:
            self.assertEqual(redactor.clean(rows), rows)
            self.assertLessEqual(sorting.call_count, 2)

    def test_cached_inventory_still_masks_new_secrets_signatures_keys_and_substrings(self):
        redactor = Redactor()
        redactor.register('short', 'long-short-secret')
        first = dict(public='prefix long-short-secret / short suffix', api_key='remove',
                     nested=[dict(headers='remove',ok='safe')])
        self.assertEqual(redactor.clean(first),
            dict(public='prefix [REDACTED] / [REDACTED] suffix',nested=[dict(ok='safe')]))
        signature = 'f'*64
        redactor.signature(signature)
        redactor.register('safe')
        value = redactor.clean(dict(v='x'+signature+'x',nested=['safe']))
        self.assertEqual(value, dict(v='x[REDACTED]x',nested=['[REDACTED]']))


if __name__ == '__main__': unittest.main()
