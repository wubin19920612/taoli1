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

    def test_three_volume_venues_respect_stock_selection_and_mapping_confirmation(self):
        from tools.crossex_exec.settings import Settings
        settings=Settings(self.store).read()['values']
        rows=[native(ex,'SKHYNIX','listed_equity') for ex in ('binance','bitget','bybit')]
        self.publish(rows,[100]*3)
        self.universe.classify('SKHYNIX',dict(category='listed_equity',underlying_market='KRX',markets=[i.market.key for i in rows[:2]]))
        self.assertEqual(self.universe.volume_venues(rows[:2],settings),{'binance','bitget'})
        self.universe.classify('SKHYNIX',dict(category='listed_equity',underlying_market='KRX',markets=[i.market.key for i in rows]))
        self.assertEqual(self.universe.volume_venues(rows[:2],settings),{'binance','bitget','bybit'})
        rows=[native('binance','1000SHIB'),native('bitget','SHIB'),native('bybit','SHIB')]
        self.publish(rows,[1000,1,1])
        mappings=self.universe.mappings()
        first=next(r for r in mappings if rows[1].market.key in {leg['market_key'] for leg in r['legs']})
        self.universe.decide_mapping(first['id'],'confirmed')
        resolved=self.universe.resolve(rows[:2])
        self.assertEqual(self.universe.volume_venues(resolved,settings),{'binance','bitget'})
        for row in mappings:
            self.universe.decide_mapping(row['id'],'confirmed')
        self.assertEqual(self.universe.volume_venues(resolved,settings),{'binance','bitget','bybit'})

    def test_manual_nontrading_categories_and_known_preipo_names_stay_disabled(self):
        rows=[native(ex,'NEW') for ex in ('binance','bitget')]
        self.publish(rows,[1,1])
        for category in ('commodity','pre_ipo'):
            self.universe.classify('NEW',dict(category=category,markets=[i.market.key for i in rows]))
            with self.assertRaisesRegex(ValueError,'category_unconfirmed'):
                self.universe.resolve(rows)
        self.assertEqual(self.universe.describe(),[])
        for base in ('OPENAI','ANTHROPIC'):
            rows=[native('binance',base),native('bitget','1000'+base)]
            self.publish(rows,[1,1000])
            self.assertEqual(self.universe.mappings(),[])
            self.assertEqual(self.universe.describe(),[])

    def test_stock_selected_markets_do_not_fall_back_to_auto_crypto(self):
        rows=[native(ex,'SAME') for ex in ('binance','bitget','bybit')]
        self.publish(rows,[1]*3)
        self.universe.classify('SAME',dict(category='listed_equity',underlying_market='US',markets=[i.market.key for i in rows[:2]]))
        with self.assertRaisesRegex(ValueError,'category_unconfirmed'):
            self.universe.resolve([rows[0],rows[2]])

    def test_mapping_ratio_follows_sorted_leg_order(self):
        rows=[native('bitget','SHIB'),native('binance','1000SHIB')]
        self.publish(rows,[1,1000])
        row,=self.universe.mappings()
        self.assertEqual(row['legs'][0]['market_key'],rows[1].market.key)
        self.assertEqual(dec(row['ratio']),1000)


class ClassificationTests(unittest.TestCase):
    def test_unknown_and_noncrypto_metadata_fail_closed(self):
        from tools.crossex_exec.universe import category
        for ex in ('binance', 'aster', 'bybit', 'okx', 'gate', 'bitget'):
            self.assertEqual(category(ex, {}), 'unconfirmed')
        cases = [('binance', {'underlyingType': 'COIN', 'underlyingSubType': ['PREMARKET']}, 'pre_ipo'),
                 ('binance', {'underlyingType': 'COIN'}, 'unconfirmed'),
                 ('binance', {'underlyingType': 'COIN', 'underlyingSubType': [], 'baseAsset':'PAXG'}, 'commodity'),
                 ('binance', {'underlyingType': 'COIN', 'underlyingSubType': ['RWA']}, 'unconfirmed'),
                 ('binance', {'underlyingType': 'COIN', 'underlyingSubType': []}, 'crypto'),
                 ('okx', {'instCategory': '1'}, 'crypto'), ('okx', {'instCategory': '3'}, 'listed_equity'),
                 ('okx', {'instCategory': '4'}, 'commodity'), ('bitget', {'isRwa': 'NO'}, 'crypto'),
                 ('bitget', {'isRwa': 'YES'}, 'unconfirmed'), ('bybit', {'isPreListing': True}, 'pre_ipo'),
                 ('bybit', {'symbolType': 'ETF'}, 'listed_equity')]
        for ex, row, expected in cases:
            self.assertEqual(category(ex, row), expected)
