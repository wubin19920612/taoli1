"""Late history failures must not retain a mixture of old and new caches."""
import copy
import unittest

from tools.crossex_exec.external_accounting import history_index
from test_exec import setUpModule, tearDownModule


def activity(**rows):
    return dict(dict(from_ms=0, to_ms=1, crossex_book=[], crossex_trades=[], bitget_book=[]), **rows)


class HistoryCacheTests(unittest.TestCase):
    def test_second_and_third_history_failure_leave_bounded_prior_cache_intact(self):
        for count, padding in ((25001, ''), (9000, 'x'*1024)):
            old_rows = [dict(id=str(n), ts='1', padding=padding) for n in range(count)]
            new_rows = [dict(id=str(n), create_time='1', padding=padding) for n in range(count)]
            for stage in ('crossex_trades', 'bitget_book'):
                with self.subTest(count=count, stage=stage):
                    cache = {}
                    history_index(activity(bitget_book=old_rows), cache)
                    before = copy.deepcopy(cache)
                    bad = dict(transaction_id='bad', id='bad', create_time='2')
                    for _ in range(2):
                        with self.assertRaisesRegex(ValueError, 'history_window'):
                            history_index(activity(crossex_book=new_rows, **{stage:[bad]}), cache)
                        self.assertLessEqual(sum(len(rows) for rows in cache.values()), 25000)
                        self.assertLessEqual(sum(len(value[0]) for rows in cache.values()
                                                 for value in rows.values()), 8*1024*1024)
                        self.assertTrue(cache == before, 'failed validation changed the prior cache')

    def test_late_duplicate_failure_does_not_publish_new_first_partition(self):
        cache = {}
        history_index(activity(bitget_book=[dict(id='old', ts='1')]), cache)
        before = copy.deepcopy(cache)
        duplicate = dict(transaction_id='duplicate', create_time='1')
        with self.assertRaisesRegex(ValueError, 'history_duplicate'):
            history_index(activity(crossex_book=[dict(id='new', create_time='1')],
                                   crossex_trades=[duplicate, duplicate]), cache)
        self.assertEqual(cache, before)

    def test_success_replaces_all_partitions_and_eviction_keeps_complete_index(self):
        cache = {}
        history_index(activity(bitget_book=[dict(id='old', ts='1')]), cache)
        rows = [dict(id=str(n), create_time='1') for n in range(25001)]
        result = history_index(activity(crossex_book=rows), cache)
        self.assertEqual(len(result['crossex_book']), 25001)
        self.assertEqual(len(cache['crossex_book']), 25000)
        self.assertEqual(cache['bitget_book'], {})
        self.assertEqual(cache['crossex_trades'], {})


if __name__ == '__main__':
    unittest.main()
