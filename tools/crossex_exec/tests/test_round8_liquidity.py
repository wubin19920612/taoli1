"""Independent sizing boundaries and public OI units/identity/freshness."""
import unittest
from dataclasses import replace

from tools.fill_sim.core import Book, Market, dec
from test_exec import instrument, setUpModule, tearDownModule


class LiquidityTests(unittest.TestCase):
    def inputs(self):
        from tools.crossex_exec.settings import SCHEMA
        instruments = [instrument(), instrument('bitget')]
        books = [Book([['100', '10']], [['100', '10']], 1000),
                 Book([['102', '10']], [['102', '10']], 1000)]
        config = {k: r['default'] for k, r in SCHEMA.items()}
        oi = [dict(market_key=i.market.key, usd='10000000', at=1000, unit='base',
                   unit_verified=True, source='fixture') for i in instruments]
        return instruments, books, oi, config

    def test_each_constraint_binds_and_both_leg_limits_stay_inside_budget(self):
        from tools.crossex_exec.sizing import size_plan
        for name in ('order_usd_max', 'depth', 'oi', 'remaining_total'):
            i, books, oi, config = self.inputs()
            remaining = 600
            if name == 'depth':
                books[0].asks = [['100', '1']]
            elif name == 'oi':
                oi[1]['usd'] = '40000'
            elif name == 'remaining_total':
                remaining = 40
            plan = size_plan(i, books, oi, config, remaining, 1000)
            self.assertEqual(plan['limiting'], name)
            quantity = dec(plan['quantity'])
            self.assertGreater(quantity, 0)
            self.assertEqual(quantity % dec('.001'), 0)
            self.assertLessEqual(sum(quantity*dec(p) for p in plan['limits']), remaining)
            for p in plan['limits']:
                self.assertLessEqual(quantity*dec(p), dec(plan['budget_usd']))

    def test_only_verified_fresh_oi_legs_constrain_and_no_oi_rejects(self):
        from tools.crossex_exec.sizing import size_plan
        i, books, oi, config = self.inputs()
        for missing in (None, dict(oi[0], unit_verified=False), dict(oi[0], at=699),
                        dict(oi[0], market_key='foreign'), dict(oi[0], at=1001)):
            for leg in (0, 1):
                rows = list(oi)
                rows[leg] = missing
                plan = size_plan(i, books, rows, config, 600, 1000)
                self.assertEqual(plan['oi_legs_used'], [1-leg])
        with self.assertRaisesRegex(ValueError, '^oi_missing$'):
            size_plan(i, books, [None, None], config, 600, 1000)
        self.assertEqual(size_plan(i, books, [dict(oi[0], at=700), None], config, 600, 1000)['oi_legs_used'], [0])

    def test_band_depth_cannot_include_far_levels_or_bump_the_minimum(self):
        from tools.crossex_exec.sizing import size_plan
        i, books, oi, config = self.inputs()
        books[0].asks = [['100', '.01'], ['101', '10000']]
        with self.assertRaisesRegex(ValueError, '^size_too_small$'):
            size_plan(i, books, oi, config, 600, 1000)
        i, books, oi, config = self.inputs()
        i[0].spec = replace(i[0].spec, min_qty='.491')
        with self.assertRaisesRegex(ValueError, '^size_too_small$'):
            size_plan(i, books, oi, config, 600, 1000)


class OpenInterestTests(unittest.TestCase):
    def test_bitget_v2_base_units_and_exact_symbol_only(self):
        from tools.crossex_exec.open_interest import parse_oi, oi_urls
        m = Market('bitget', 'future', 'BTCUSDT')
        data = {'code': '00000', 'data': {'openInterestList': [{'symbol': 'BTCUSDT', 'size': '25'}], 'ts': '1000000'}}
        row = parse_oi(m, [data], 100, 1000)
        self.assertEqual(dec(row['usd']), 2500)
        self.assertEqual(row['unit'], 'base')
        self.assertIn('/api/v2/mix/market/open-interest', oi_urls(m)[0])
        data['data']['openInterestList'][0]['symbol'] = 'ETHUSDT'
        with self.assertRaises(ValueError):
            parse_oi(m, [data], 100, 1000)
        with self.assertRaises((ValueError, KeyError)):
            parse_oi(m, [{'code': '00000', 'data': [{'symbol': 'BTCUSDT', 'openInterest': '25'}]}], 100, 1000)

    def test_binance_ratio_verification_keeps_native_1000_symbol_units(self):
        from tools.crossex_exec.open_interest import parse_oi
        m = Market('binance', 'future', '1000SHIBUSDT')
        history = [dict(symbol=m.raw_symbol, sumOpenInterest='100000', sumOpenInterestValue='400', timestamp=1000000)]
        mark = dict(symbol=m.raw_symbol, markPrice='.004', time=1000000)
        row = parse_oi(m, [history, mark], '.004', 1000)
        self.assertEqual(dec(row['usd']), 400)
        self.assertEqual(row['unit'], 'native_base')
        for value in ('.000004', '.005', '0', 'NaN'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_oi(m, [history, dict(mark, markPrice=value)], '.004', 1000)

    def test_other_documented_units_and_unsupported_aster(self):
        from tools.crossex_exec.open_interest import parse_oi, oi_urls
        fixtures = [
            ('bybit', 'BTCUSDT', {'retCode': 0, 'result': {'category': 'linear', 'symbol': 'BTCUSDT',
              'list': [{'openInterest': '20', 'singleOpenInterest': '10', 'timestamp': '1000000'}]}}, '2000', 'base_both_sides'),
            ('okx', 'BTC-USDT-SWAP', {'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP', 'oi': '20',
              'oiCcy': '2', 'oiUsd': '200', 'ts': '1000000'}]}, '200', 'USD'),
            ('gate', 'BTC_USDT', [{'open_interest': '2000', 'open_interest_usd': '200', 'time': 1000}], '200', 'USDT'),
        ]
        for ex, raw, data, expected, unit in fixtures:
            row = parse_oi(Market(ex, 'future', raw), [data], 100, 1000)
            self.assertEqual((row['usd'], row['unit']), (expected, unit))
            with self.assertRaises(ValueError):
                parse_oi(Market(ex, 'future', raw), [data], 100, 1301)
        self.assertEqual(oi_urls(Market('aster', 'future', 'BTCUSDT')), [])
