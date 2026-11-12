"""Categories and mapping confirmation preserve raw market identity."""
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path

from tools.fill_sim.core import Book, Market, dec
from tools.crossex_exec.storage import Store
from test_exec import instrument, setUpModule, tearDownModule


def native(ex, base, category='crypto'):
    from tools.crossex_exec.policy import Instrument
    old = instrument(ex)
    return Instrument(Market(ex, 'future', base+'USDT'), replace(old.spec, base=base),
                      base+'USDT' if ex == 'bitget' else ex.upper()+'_FUTURE_'+base+'_USDT',
                      '.000001', category=category)


class UniverseTests(unittest.TestCase):
    def setUp(self):
        from tools.crossex_exec.universe import Universe
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'exec.db')
        self.addCleanup(self.store.close)
        self.universe = Universe(self.store, clock=lambda: 1000)

    def publish(self, instruments, prices):
        self.store.set_meta('mapping', dict(at=1000, instruments={i.market.key: i.dump() for i in instruments}))
        quotes = {i.market.key: dict(mid=str(p), volume='5000000', at=1000) for i, p in zip(instruments, prices)}
        self.store.set_meta('market_tickers', quotes)
        self.universe.discover()

    def test_unknown_requires_manual_classification_and_exclusion_survives_restart(self):
        rows = [native('binance', 'NEW', 'unconfirmed'), native('bitget', 'NEW', 'unconfirmed')]
        self.publish(rows, [100, 100])
        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
            self.universe.resolve(rows)
        self.universe.classify('NEW', dict(category='crypto', markets=[i.market.key for i in rows]))
        self.assertEqual([i.base for i in self.universe.resolve(rows)], ['NEW', 'NEW'])
        self.universe.exclude('NEW', True)
        from tools.crossex_exec.universe import Universe
        with self.assertRaisesRegex(ValueError, '^excluded$'):
            Universe(self.store).resolve(rows)
        self.universe.exclude('NEW', False)
        self.assertEqual(len(self.universe.resolve(rows)), 2)

    def test_listed_equity_requires_supported_market_and_selected_venues(self):
        rows = [native(ex, 'SKHYNIX', 'listed_equity') for ex in ('binance', 'bitget', 'bybit')]
        self.publish(rows, [100]*3)
        for fields in ({}, {'underlying_market': 'HK'}):
            with self.assertRaises(ValueError):
                self.universe.classify('SKHYNIX', dict(category='listed_equity', markets=[i.market.key for i in rows[:2]], **fields))
        self.universe.classify('SKHYNIX', dict(category='listed_equity', underlying_market='KRX', markets=[i.market.key for i in rows[:2]]))
        self.assertEqual(len(self.universe.resolve(rows[:2])), 2)
        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
            self.universe.resolve([rows[0], rows[2]])

    def test_preipo_and_commodity_never_candidates_or_manually_enabled(self):
        for base, category in [('OPENAI', 'pre_ipo'), ('GOLD', 'commodity')]:
            rows = [native('binance', base, category), native('bitget', '1000'+base, category)]
            self.publish(rows, [1, 1000])
            self.assertEqual(self.universe.mappings(), [])
            with self.assertRaises(ValueError):
                self.universe.classify(base, dict(category='crypto', markets=[rows[0].market.key]))
            with self.assertRaises(ValueError):
                self.universe.resolve(rows)

    def test_mapping_requires_confirmation_both_directions_and_live_ratio(self):
        from tools.crossex_exec.universe import check_mapping_ratio
        rows = [native('binance', '1000SHIB'), native('bitget', 'SHIB')]
        self.publish(rows, [1, '.001'])
        proposals = self.universe.mappings()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]['status'], 'pending')
        with self.assertRaisesRegex(ValueError, '^mapping_unconfirmed$'):
            self.universe.resolve(rows)
        self.universe.decide_mapping(proposals[0]['id'], 'confirmed')
        for order in (rows, rows[::-1]):
            mapped = self.universe.resolve(order)
            self.assertEqual([i.base for i in mapped], ['SHIB']*2)
            books = [Book([[str(i.multiplier), '1']], [[str(i.multiplier), '1']], 1000) for i in mapped]
            check_mapping_ratio(mapped, books)
            common = [i.common() for i in mapped]
            self.assertEqual([dec(i.spec.qty_step) for i in common], [i.multiplier*dec('.001') for i in mapped])
            books[0] = Book([[str(mapped[0].multiplier*dec('1.0101')), '1']], [[str(mapped[0].multiplier*dec('1.0101')), '1']], 1000)
            with self.assertRaisesRegex(ValueError, '^mapping_ratio_mismatch$'):
                check_mapping_ratio(mapped, books)
        self.universe.decide_mapping(proposals[0]['id'], 'rejected')
        with self.assertRaises(ValueError):
            self.universe.resolve(rows)

    def test_false_prefixes_and_price_mismatches_are_not_candidates(self):
        for bases, prices in [(('KO', 'O'), (1000, 1)), (('KAT', 'AT'), (1000, 1)),
                              (('1000SHIB', 'SHIB'), (1010.1, 1))]:
            rows = [native('binance', bases[0]), native('bitget', bases[1])]
            self.publish(rows, prices)
            self.assertEqual(self.universe.mappings(), [])


class ClassificationTests(unittest.TestCase):
    def test_unknown_and_noncrypto_metadata_fail_closed(self):
        from tools.crossex_exec.universe import category
        for ex in ('binance', 'aster', 'bybit', 'okx', 'gate', 'bitget'):
            self.assertEqual(category(ex, {}), 'unconfirmed')
        cases = [('binance', {'underlyingType': 'COIN', 'underlyingSubType': ['PREMARKET']}, 'pre_ipo'),
                 ('binance', {'underlyingType': 'COIN', 'underlyingSubType': ['RWA']}, 'unconfirmed'),
                 ('binance', {'underlyingType': 'COIN', 'underlyingSubType': []}, 'crypto'),
                 ('okx', {'instCategory': '1'}, 'crypto'), ('okx', {'instCategory': '3'}, 'listed_equity'),
                 ('okx', {'instCategory': '4'}, 'commodity'), ('bitget', {'isRwa': 'NO'}, 'crypto'),
                 ('bitget', {'isRwa': 'YES'}, 'unconfirmed'), ('bybit', {'isPreListing': True}, 'pre_ipo'),
                 ('bybit', {'symbolType': 'ETF'}, 'listed_equity')]
        for ex, row, expected in cases:
            self.assertEqual(category(ex, row), expected)
