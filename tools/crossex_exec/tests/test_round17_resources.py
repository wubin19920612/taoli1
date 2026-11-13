"""Cache work, not truth: complete reads must still detect disappearing cash."""
import copy
import unittest
from unittest.mock import patch

from tools.crossex_exec.external import digest
from tools.crossex_exec.security import Redactor
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position


class ResourceSafetyTests(ExternalFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.symbol = 'BINANCE_FUTURE_BTC_USDT'
        self.store.save_order(dict(intent('funding-owner'),created_at=self.now,state='TERMINAL',
            filled='0',fee='0',accounting_pending=False))
        self.rows = []
        self.anchor()
        self.now += 1
        self.book = [dict(id=str(n),business_id=str(n),symbol=self.symbol,exchange_type='BINANCE',
            coin='USDT',statement_type='FUNDING_FEE',change='0',create_time=str(int(self.now*1000)))
            for n in range(250)]

    def test_one_history_index_per_income_proof_and_unchanged_rows_reuse_hashes(self):
        from tools.crossex_exec import program_accounting as accounting
        original = accounting.history_index
        with patch.object(accounting,'history_index',wraps=original) as index:
            self.engine.reconcile()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertEqual(index.call_count, 1)
        with patch('tools.crossex_exec.external_accounting.digest',wraps=digest) as hashed:
            self.now += 1
            self.engine.reconcile()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertLessEqual(hashed.call_count, 1)

    def test_full_window_recheck_detects_missing_revised_and_late_cached_rows(self):
        self.engine.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])
        before = copy.deepcopy(self.store.meta('program_verified_history'))
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
            self.assertEqual(self.store.meta('program_verified_history'), before)
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

    def test_unchanged_verified_ledger_is_not_serialized_and_rewritten(self):
        self.engine.reconcile()
        before = self.store.meta('program_verified_history')
        with patch.object(self.store,'set_meta',wraps=self.store.set_meta) as writes:
            self.now += 1
            self.engine.reconcile()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertFalse(any(c.args[0]=='program_verified_history' for c in writes.call_args_list))
        self.book.append(dict(self.book[-1],id='new-row',create_time=str(int(self.now*1000))))
        self.now += 1
        with patch.object(self.store,'set_meta',wraps=self.store.set_meta) as writes:
            self.engine.reconcile()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertEqual(sum(c.args[0]=='program_verified_history' for c in writes.call_args_list),1)
        after = self.store.meta('program_verified_history')
        self.assertEqual({k:after['crossex_book'][k] for k in before['crossex_book']},before['crossex_book'])
        self.assertIn('new-row',after['crossex_book'])


class RedactionCostTests(unittest.TestCase):
    def test_long_untrusted_string_does_not_allocate_every_substring(self):
        import random
        import string
        import tracemalloc
        redactor = Redactor()
        for n in range(2048): redactor.signature(f'{n:064x}')
        text = ''.join(random.Random(17).choices(string.ascii_letters,k=100000))
        tracemalloc.start()
        try:
            self.assertEqual(redactor.clean(text), text)
            self.assertLess(tracemalloc.get_traced_memory()[1], 4*1024*1024)
        finally:
            tracemalloc.stop()

    def test_digest_streams_plain_json_without_building_a_second_object_tree(self):
        import hashlib
        import json
        from decimal import Decimal
        from tools.crossex_exec.external import plain
        value = dict(rows=[dict(v=Decimal('-0.12'), ids=('a','b'))])
        expected = hashlib.sha256(json.dumps(plain(value),sort_keys=True,
            separators=(',',':'),allow_nan=False).encode()).hexdigest()
        with patch('tools.crossex_exec.external.plain', side_effect=AssertionError('extra_tree')):
            self.assertEqual(digest(value), expected)

    def test_history_hash_cache_distinguishes_json_types_not_python_numeric_equality(self):
        from tools.crossex_exec.external_accounting import history_index
        cache = {}
        activity = dict(from_ms=0,to_ms=1,crossex_book=[dict(id='one',create_time='1',extra=1)],
                        crossex_trades=[],bitget_book=[])
        first = history_index(activity,cache)
        activity['crossex_book'][0]['extra'] = True
        second = history_index(activity,cache)
        self.assertNotEqual(first, second)

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
