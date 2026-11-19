"""Independent sizing boundaries and public OI units/identity/freshness."""
import unittest
from dataclasses import replace

from tools.crossex_exec.market import native_instrument, value_open_interests
from tools.crossex_exec.open_interest import oi_urls, parse_oi
from tools.fill_sim.core import Book, Market, dec
from tools.fill_sim.exchanges import parse_book
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
        fixtures = [
            ('bybit', 'BTCUSDT', {'retCode': 0, 'result': {'category': 'linear', 'symbol': 'BTCUSDT',
              'list': [{'openInterest': '20', 'singleOpenInterest': '10', 'timestamp': '1000000'}]}}, '2000', 'base_both_sides'),
            ('okx', 'BTC-USDT-SWAP', {'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP', 'oi': '20',
              'oiCcy': '2', 'oiUsd': '200', 'ts': '1000000'}]}, '200', 'USD'),
            ('gate', 'BTC_USDT', [dict(contract='BTC_USDT', total_size='2', quanto_multiplier='1', mark_price='100')], '200', 'USDT'),
        ]
        for ex, raw, data, expected, unit in fixtures:
            options = dict(contract_size='1', received_at=1000) if ex == 'gate' else {}
            row = parse_oi(Market(ex, 'future', raw), [data], 100, 1000, **options)
            self.assertEqual((row['usd'], row['unit']), (expected, unit))
            with self.assertRaises(ValueError):
                parse_oi(Market(ex, 'future', raw), [data], 100, 1301, **options)
        self.assertEqual(oi_urls(Market('aster', 'future', 'BTCUSDT')), [])


class GateTickerOITests(unittest.TestCase):
    def setUp(self):
        self.market = Market('gate', 'future', 'PIEVERSE_USDT')
        self.ticker = dict(contract='PIEVERSE_USDT', last='3.3457', mark_price='3.3433',
                           index_price='3.342637', total_size='804216', quanto_multiplier='10')

    def parse(self, data, *, price='3.3457', contract_size='10'):
        return parse_oi(self.market, [data], price, 1000, contract_size=contract_size, received_at=999.5)

    def test_pieverse_ticker_and_native_book_produce_exact_double_sided_oi(self):
        catalog = dict(name='PIEVERSE_USDT', quanto_multiplier='10', order_size_min='1', order_size_max='1000000',
                       order_price_round='.0001')
        rule = dict(symbol='GATE_FUTURE_PIEVERSE_USDT', state='live', business_type='FUTURE', exchange_type='GATE',
                    delist_time='0', support_cross='true', lot_size='10', min_size='10', max_limit_size='1000000',
                    tick_size='.0001', min_notional='0')
        gate = native_instrument('gate', catalog, [rule])
        native = parse_book(gate.market, gate.spec, dict(bids=[['3.3456', '2']], asks=[['3.3458', '1']]), 1000)
        url = 'https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=PIEVERSE_USDT'
        self.assertEqual(oi_urls(self.market), [url])
        self.assertEqual(native.bids[0], ('3.3456', '20'))
        # The same native quote must survive conversion to a mapped common book and back.
        for multiplier in ('1', '1000'):
            with self.subTest(quantity_multiplier=multiplier):
                mapped = replace(gate, quantity_multiplier=multiplier)
                payloads = [[([self.ticker], 999.5)]]
                row, = value_open_interests([mapped], [mapped.common_book(native)], payloads, 1000)
                self.assertEqual(row, dict(market_key=self.market.key, usd='26887353.5280', at=999.5, unit='USDT',
                                           unit_verified=True, source=url, evidence=dict(
                                               total_size='804216', quanto_multiplier='10', mark_price='3.3433')))

    def test_single_ticker_and_exact_contract_are_required(self):
        for data in ([], [self.ticker, self.ticker], self.ticker, None):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, '^oi_ambiguous$'):
                self.parse(data)
        for row in ({}, None, dict(self.ticker, contract='PIEVERSEUSDT'), dict(self.ticker, contract='OTHER_USDT')):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, '^oi_identity_mismatch$'):
                self.parse([row])

    def test_catalog_multiplier_must_be_available_and_equal(self):
        for size in (None, '1', '100', 'NaN'):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, '^oi_unit_unverified$'):
                self.parse([self.ticker], contract_size=size)
        self.assertEqual(self.parse([self.ticker], contract_size='10.0')['unit'], 'USDT')

    def test_quantities_and_mark_fail_closed_with_unit_reason(self):
        for field in ('total_size', 'quanto_multiplier', 'mark_price'):
            invalid_values = [None, '-1', 'Infinity', 'bad']
            if field != 'total_size':
                invalid_values.append('0')
            for invalid in invalid_values:
                ticker = dict(self.ticker)
                if invalid is None:
                    del ticker[field]
                else:
                    ticker[field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaisesRegex(ValueError, '^oi_unit_unverified$'):
                    self.parse([ticker])
        self.assertEqual(dec(self.parse([dict(self.ticker, total_size='0')])['usd']), 0)

    def test_one_percent_mark_boundary_applies_even_to_a_real_midpoint_of_one(self):
        for price in (dec(1), dec('3.3433')):
            for ratio in ('.99', '1.01', '.98999', '1.01001'):
                ticker = dict(self.ticker, mark_price=str(price*dec(ratio)))
                with self.subTest(price=price, ratio=ratio):
                    if ratio in ('.99', '1.01'):
                        self.assertTrue(self.parse([ticker], price=price)['unit_verified'])
                    else:
                        with self.assertRaisesRegex(ValueError, '^oi_unit_unverified$'):
                            self.parse([ticker], price=price)
        self.assertTrue(self.parse([self.ticker], price=None)['unit_verified'])

    def test_missing_receipt_cannot_be_replaced_by_parse_time(self):
        with self.assertRaisesRegex(ValueError, '^oi_missing$'):
            parse_oi(self.market, [[self.ticker]], None, 1000, contract_size='10')
