"""Public observations are incrementally persisted without losing invalidations."""
import unittest
from tools.crossex_exec.storage import Store
from tools.crossex_exec.baseline import Baseline
from test_exec import setUpModule, tearDownModule


class IncrementalTests(unittest.TestCase):
    def test_crash_before_wire_observation_keeps_recent_risk_window(self):
        from test_exec import intent
        from tools.crossex_exec.storage import matches_order
        with Store(':memory:') as store:
            row=dict(intent(),state='REJECTED',sent_persisted_ms=1000,filled='0',accounting_pending=False)
            store.save_order(row)
            self.assertEqual([o['cid'] for o in store.iter_orders(sent_since=1000)],[row['cid']])
            self.assertTrue(matches_order(row,sent_since=1000))
            self.assertFalse(matches_order(row,sent_since=1001))
            self.assertNotIn('sent_ms',store.order(row['cid']))
            row['sent_ms']=1100
            store.save_order(row)
            self.assertEqual(len(list(store.iter_orders(sent_since=1100))),1)
            self.assertTrue(matches_order(row,sent_since=1100))
            self.assertEqual(list(store.iter_orders(sent_since=1101)),[])

    def test_sample_window_uses_bucket_range_without_changing_exact_time_edges(self):
        with Store(':memory:') as store:
            key='BTC|binance|bitget'
            store.add_baseline_samples([(key,int(t//30),t,str(t)) for t in (29,30,60,91)],100)
            self.assertEqual(store.baseline_samples(key,30,90),[(30,'30'),(60,'60')])
            queries=[]
            store.db.set_trace_callback(queries.append)
            self.assertEqual(store.baseline_samples(key,31,91),[(60,'60'),(91,'91')])
            query=next(q for q in queries if 'SELECT at,bps' in q)
            plan=' '.join(str(tuple(r)) for r in store.db.execute('EXPLAIN QUERY PLAN '+query))
            self.assertIn('bucket>?',plan)
            self.assertIn('bucket<?',plan)

    def test_identical_public_round_is_noop_and_removed_quotes_are_revoked(self):
        with Store(':memory:') as store:
            for key,value,smaller in (
                ('baseline_quotes',dict(at=100,mids={'binance':{'BTC':'100'},'bitget':{'BTC':'101'}}),
                 dict(at=101,mids={'binance':{'BTC':'100'}})),
                ('market_tickers',{'binance|future|BTCUSDT':dict(at=100,mid='100',volume='1000000'),
                                   'bitget|future|BTCUSDT':dict(at=100,mid='101',volume='1000000')},
                 {'binance|future|BTCUSDT':dict(at=101,mid='100',volume='1000000')})):
                with self.subTest(key=key):
                    store.set_meta(key,value)
                    changes=store.db.total_changes
                    store.set_meta(key,value)
                    self.assertEqual(store.db.total_changes,changes)
                    store.set_meta(key,smaller)
                    current=store.meta(key)
                    self.assertNotIn('bitget',current.get('mids',{}))
                    self.assertNotIn('bitget|future|BTCUSDT',current)
            self.assertEqual(store.db.execute("SELECT count(*) FROM meta WHERE key IN ('market_tickers','baseline_quotes')").fetchone()[0],0)

    def test_identical_baseline_state_writes_nothing_but_changed_age_revokes_ready(self):
        with Store(':memory:') as store:
            state=dict(key='BTC|binance|bitget',baseline_bps='0',mad_bps='0',count=400,
                       first_at=1,latest_at=12000,ready=True,blocked=False,checked_at=12000,at=12000)
            store.save_baseline_state(state)
            changes=store.db.total_changes
            store.save_baseline_state(state)
            self.assertEqual(store.db.total_changes,changes)
            store.save_baseline_state(dict(state,ready=False,at=12091))
            self.assertFalse(store.baseline_state(state['key'])['ready'])
