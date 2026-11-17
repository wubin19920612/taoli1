"""Exact-base category inheritance and opening identity checks, offline only."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools.fill_sim.core import Book, dec
from tools.crossex_exec.accounts import FeeSnapshot
from tools.crossex_exec.admission import SKIP_REASONS
from tools.crossex_exec.api_data import JournalView
from tools.crossex_exec.policy import Instrument
from tools.crossex_exec.universe import Universe
from test_exec import setUpModule, tearDownModule
import test_round7 as admission_fixtures
import test_round8_universe as universe_fixtures
import test_merge_final_review as release_fixtures


native = universe_fixtures.native


class CategoryInheritanceTests(unittest.TestCase):
    setUp = universe_fixtures.UniverseTests.setUp

    def publish(self, instruments, prices):
        mapping_at = self.store.meta('mapping', {}).get('at', 999)+1
        self.store.set_meta('mapping', dict(at=mapping_at, instruments={i.market.key: i.dump() for i in instruments}))
        self.store.set_meta('market_tickers', {i.market.key: dict(mid=str(p), volume='5000000', at=1000)
                            for i, p in zip(instruments, prices) if i.market.market_type == 'future'})
        self.universe.discover()
        # A republish models a refresh observed after the 5s TTL (TTL tests cover the bound).
        self.universe._inheritance_cache = None

    def test_gate_bitget_and_two_unknown_legs_inherit_without_persisting(self):
        gate, bybit, bitget = [native(ex, 'BLESS', kind) for ex, kind in
                              (('gate', 'unconfirmed'), ('bybit', 'unconfirmed'), ('bitget', 'crypto'))]
        self.publish([gate, bybit, bitget], [1, 1, 1])
        before = {name: self.store.meta(name) for name in ('mapping', 'universe_decisions', 'symbol_mappings')}
        changes = self.store.db.total_changes
        for legs in ([gate, bitget], [gate, bybit], [bybit, gate]):
            self.assertEqual(self.universe.resolve(legs), legs)
            self.assertEqual([self.universe.eligibility(i) for i in legs], [('crypto', None)]*2)
        row, = self.universe.describe()
        markets = {r['exchange']: r for r in row['markets']}
        for ex in ('gate', 'bybit'):
            self.assertEqual(markets[ex]['category'], 'unconfirmed')
            self.assertEqual(markets[ex]['effective_category'], 'crypto')
            self.assertEqual(markets[ex]['category_source'], 'inherited:bitget')
            self.assertTrue(markets[ex]['eligible'])
        self.assertEqual(markets['bitget']['category_source'], 'metadata')
        self.assertEqual(Universe(self.store).eligibility(gate), ('crypto', None))
        self.assertEqual(self.store.db.total_changes, changes)
        self.assertEqual({name: self.store.meta(name) for name in before}, before)
        self.assertEqual([gate.category, bybit.category], ['unconfirmed']*2)

    def test_only_okx_and_bitget_futures_can_supply_metadata_crypto(self):
        target = native('gate', 'BLESS', 'unconfirmed')
        for ex in ('okx', 'bitget', 'binance', 'bybit', 'aster'):
            for market_type in ('future', 'spot'):
                donor = native(ex, 'BLESS')
                donor = replace(donor, market=replace(donor.market, market_type=market_type))
                self.publish([target, donor], [1, 1])
                with self.subTest(exchange=ex, market_type=market_type):
                    if ex in ('okx', 'bitget') and market_type == 'future':
                        self.assertEqual(self.universe.eligibility(target), ('crypto', None))
                    else:
                        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                            self.universe.eligibility(target)

    def test_unknown_gate_and_bybit_without_trusted_metadata_stay_unconfirmed(self):
        rows = [native(ex, 'BLESS', 'unconfirmed') for ex in ('gate', 'bybit', 'bitget')]
        self.publish(rows, [1]*3)
        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
            self.universe.resolve(rows[:2])

    def test_any_conflicting_market_blocks_inheritance_even_spot_or_blocked(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        for kind in ('listed_equity', 'commodity', 'pre_ipo'):
            for market_type in ('future', 'spot'):
                conflict = native('okx', 'BLESS', kind)
                conflict = replace(conflict, market=replace(conflict.market, market_type=market_type),
                                   rule_type='unknown_new_rule')
                self.publish([target, donor, conflict], [1]*3)
                with self.subTest(category=kind, market_type=market_type):
                    with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                        self.universe.eligibility(target)
                    with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                        self.universe.eligibility(replace(target, category=kind))

    def test_blocked_donor_cannot_supply_crypto(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        # Today only OKX rules implement this property. Exercise the required
        # Bitget blocked contract without changing the production policy.
        with patch.object(Instrument, 'trading_blocked', property(lambda i: i.market.key == donor.market.key)):
            with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(target)
        blocked_okx = replace(native('okx', 'BLESS'), rule_type='unknown_new_rule')
        self.publish([target, blocked_okx], [1, 1])
        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
            self.universe.eligibility(target)

    def test_admin_fields_block_inheritance_including_explicit_unexclude(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        self.assertEqual(self.universe.eligibility(target), ('crypto', None))
        for decision, reason in (({'excluded': True}, 'excluded'),
                                 ({'excluded': False}, 'category_unconfirmed'),
                                 ({'category': 'listed_equity'}, 'category_unconfirmed'),
                                 ({'category': 'crypto', 'markets': [donor.market.key]}, 'category_unconfirmed')):
            self.store.set_meta('universe_decisions', {'BLESS': decision})
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, '^'+reason+'$'):
                self.universe.eligibility(target)
        self.store.set_meta('universe_decisions', {})
        self.assertEqual(self.universe.eligibility(target), ('crypto', None))

    def test_selected_admin_markets_keep_priority_and_source(self):
        rows = [native(ex, 'BLESS', 'unconfirmed') for ex in ('gate', 'bybit')]
        self.publish(rows+[native('bitget', 'BLESS', 'listed_equity')], [1]*3)
        for kind, underlying in (('crypto', None), ('listed_equity', 'US')):
            self.universe.classify('BLESS', dict(category=kind, underlying_market=underlying,
                                                markets=[i.market.key for i in rows]))
            self.assertEqual(self.universe.resolve(rows), rows)
            self.assertEqual(self.universe.eligibility(rows[0]), (kind, underlying))
            row, = self.universe.describe()
            for market in row['markets'][:2]:
                self.assertEqual(market['category_source'], 'admin')
                self.assertEqual(market['effective_category'], kind)
                self.assertEqual(market['underlying_market'], underlying)
        self.universe.exclude('BLESS', True)
        with self.assertRaisesRegex(ValueError, '^excluded$'):
            self.universe.resolve(rows)

    def test_forbidden_categories_aliases_and_blocked_targets_remain_rejected(self):
        for base in ('OPENAI', 'ANTHROPIC', 'PAXG', 'XAUT', 'XAU', 'XAG', '1000OPENAI', '1000PAXG'):
            rows = [native('gate', base, 'unconfirmed'), native('bitget', base)]
            self.publish(rows, [1, 1])
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(rows[0])
        target = replace(native('okx', 'BLESS', 'unconfirmed'), rule_type='unknown_new_rule')
        donor = native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        for decision in ({}, {'category': 'crypto', 'markets': [target.market.key]},
                         {'category': 'commodity'}, {'category': 'pre_ipo'}):
            self.store.set_meta('universe_decisions', {'BLESS': decision})
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(target)

    def test_aliases_and_confirmed_canonical_mappings_never_supply_inheritance(self):
        for bases, prices in ((('1000PEPE', 'PEPE'), (1000, 1)), (('PEPE', '1000PEPE'), (1, 1000))):
            rows = [native('gate', bases[0], 'unconfirmed'), native('bitget', bases[1])]
            self.publish(rows, prices)
            proposal, = [r for r in self.universe.mappings() if r.get('available', True)]
            self.universe.decide_mapping(proposal['id'], 'confirmed')
            with self.subTest(bases=bases):
                with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                    self.universe.resolve(rows)
                with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                    self.universe.eligibility(replace(rows[0], canonical_base=bases[1]))

    def test_current_catalog_is_rechecked_and_unrelated_bases_do_not_conflict(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        other = native('okx', 'OTHER', 'listed_equity')
        self.publish([target, donor, other], [1]*3)
        self.assertEqual(self.universe.eligibility(target), ('crypto', None))
        for rows in ([target], [target, replace(donor, category='unconfirmed')],
                     [target, donor, native('bybit', 'BLESS', 'listed_equity')]):
            self.publish(rows, [1]*len(rows))
            with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(target)

    def test_existing_metadata_crypto_eligibility_is_unchanged(self):
        rows = [native(ex, 'BLESS') for ex in ('okx', 'bitget', 'binance')]
        self.publish(rows+[native('bybit', 'BLESS', 'listed_equity')], [1]*4)
        for i in rows:
            self.assertEqual(self.universe.eligibility(i), ('crypto', None))
        self.assertEqual(self.universe.resolve(rows[:2]), rows[:2])

    def test_inheritance_catalog_cache_hits_within_five_seconds_without_writes(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        now = [1000]
        self.universe.clock = lambda: now[0]
        before = {name: self.store.meta(name) for name in ('mapping', 'universe_decisions', 'symbol_mappings')}
        changes = self.store.db.total_changes
        with patch.object(Instrument, 'load', wraps=Instrument.load) as load:
            self.assertEqual(self.universe.eligibility_details(target)['category_source'], 'inherited:bitget')
            self.assertEqual(load.call_count, 2)
            now[0] += 4.999
            self.assertEqual(self.universe.eligibility_details(target)['category_source'], 'inherited:bitget')
            self.assertEqual(load.call_count, 2)
        self.assertEqual(self.store.db.total_changes, changes)
        self.assertEqual({name: self.store.meta(name) for name in before}, before)

    def test_inheritance_catalog_cache_expires_at_five_seconds_without_extending_on_hits(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        now = [1000]
        self.universe.clock = lambda: now[0]
        with patch.object(Instrument, 'load', wraps=Instrument.load) as load:
            self.assertEqual(self.universe.eligibility(target), ('crypto', None))
            mapping = self.store.meta('mapping')
            mapping['instruments'][donor.market.key]['category'] = 'unconfirmed'
            self.store.set_meta('mapping', mapping)  # Same version; TTL still forces a refresh.
            now[0] = 1004.999
            self.assertEqual(self.universe.eligibility(target), ('crypto', None))
            self.assertEqual(load.call_count, 2)
            now[0] = 1005
            with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(target)
            self.assertEqual(load.call_count, 4)

    def test_mapping_version_change_is_visible_within_ttl_bound_without_rereading_blob(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        now = [1000]
        self.universe.clock = lambda: now[0]
        with patch.object(Instrument, 'load', wraps=Instrument.load) as load:
            self.assertEqual(self.universe.eligibility(target), ('crypto', None))
            self.assertEqual(load.call_count, 2)
            mapping = self.store.meta('mapping')
            mapping['at'] += 1
            mapping['instruments'][donor.market.key]['category'] = 'unconfirmed'
            self.store.set_meta('mapping', mapping)
            with patch.object(self.store, 'meta', wraps=self.store.meta) as meta:
                now[0] = 1004
                self.assertEqual(self.universe.eligibility(target), ('crypto', None))
                self.assertNotIn('mapping', [c.args[0] for c in meta.call_args_list])
            self.assertEqual(load.call_count, 2)
            now[0] = 1005
            with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(target)
            self.assertEqual(load.call_count, 4)

    def test_cached_catalog_keeps_target_category_and_trading_rules_live(self):
        target, donor = native('okx', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        self.assertEqual(self.universe.eligibility(target), ('crypto', None))
        for kind in ('listed_equity', 'commodity', 'pre_ipo'):
            with self.subTest(category=kind), self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
                self.universe.eligibility(replace(target, category=kind))
        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
            self.universe.eligibility(replace(target, rule_type='unknown_new_rule'))
        self.assertEqual(self.universe.eligibility_details(replace(target, category='crypto'))['category_source'],
                         'metadata')

    def test_explicit_catalog_overrides_inheritance_cache_without_replacing_it(self):
        target, donor = native('gate', 'BLESS', 'unconfirmed'), native('bitget', 'BLESS')
        self.publish([target, donor], [1, 1])
        self.assertEqual(self.universe.eligibility(target), ('crypto', None))
        with self.assertRaisesRegex(ValueError, '^category_unconfirmed$'):
            self.universe.eligibility_details(target, catalog={target.market.key: target})
        self.assertEqual(self.universe.eligibility(target), ('crypto', None))


class InheritedIdentityPriceTests(unittest.TestCase):
    setUp = admission_fixtures.EngineAdmissionTests.setUp
    snapshot = admission_fixtures.EngineAdmissionTests.snapshot
    trigger = admission_fixtures.EngineAdmissionTests.trigger
    after_sent = release_fixtures.MergeReleaseTests.after_sent
    change_before_gate = admission_fixtures.EngineAdmissionTests.change_before_gate

    def prepare(self, *, categories=('unconfirmed', 'crypto'), factors=('1', '1'), shadow=False):
        payload = self.trigger('inherited-price')
        rows = [replace(native(ex, 'BTC', kind), quantity_multiplier=factor)
                for ex, kind, factor in zip(('binance', 'bitget'), categories, factors)]
        for i in rows:
            i.spec = replace(i.spec, qty_step=str(dec('.001')/i.multiplier), min_qty='0', min_notional='0')
        catalog = rows+[native('okx', 'BTC')]
        self.market.mapping = {i.market.key: i for i in catalog}
        self.store.set_meta('mapping', dict(at=self.now[0], instruments={i.market.key: i.dump() for i in catalog}))
        self.accounts.fee_snapshots = {i.market.key: FeeSnapshot('0', self.now[0]) for i in catalog}
        self.engine.live = self.engine.executor.live = not shadow
        self.store.set_meta('baseline_quotes', dict(at=self.now[0], mids={
            'binance': {'BTC': '100'}, 'bitget': {'BTC': '104'}, 'okx': {'BTC': '100'}}))
        return payload

    def books(self, prices=('100', '104')):
        return [Book([[price, '10']], [[price, '10']], self.now[0]) for price in prices]

    def assert_quiet_rejection(self, stage='review'):
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'SKIP']
        self.assertEqual([(s['reason'], s['stage']) for s in skips], [('identity_price_mismatch', stage)])
        self.assertFalse(any(row[0] == 'place' for row in self.trace))
        self.assertEqual(self.store.meta('stop_locks'), {})
        self.assertFalse(self.engine.stops)
        self.assertFalse(any(e['kind'] == 'ALERT' for e in self.store.events()))
        self.sink.assert_not_called()
        self.assertIn('identity_price_mismatch', SKIP_REASONS)
        self.assertEqual(self.store.meta('opening_skip_counts')['identity_price_mismatch'], 1)
        view = JournalView(Path(self.tmp.name)/'exec.db', {}, clock=lambda: self.now[0])
        self.assertEqual(view.get('summary', {})['opening_skip_counts']['identity_price_mismatch'], 1)

    def test_six_percent_on_either_inherited_leg_rejects_before_orders_without_alert(self):
        for categories in (('unconfirmed', 'crypto'), ('crypto', 'unconfirmed')):
            payload = self.prepare(categories=categories)
            with self.subTest(categories=categories), patch.object(self.market, 'books', return_value=self.books(('100', '106'))):
                self.assertIsNone(self.engine.ingest(dict(payload, id='-'.join(categories))))
        self.assertEqual(self.store.all_orders(), [])
        skips = [json.loads(e['value']) for e in self.store.events() if e['kind'] == 'SKIP']
        self.assertEqual([s['reason'] for s in skips], ['identity_price_mismatch']*2)
        self.assertEqual(self.store.meta('opening_skip_counts')['identity_price_mismatch'], 2)
        self.assertFalse(self.engine.stops)
        self.sink.assert_not_called()

    def test_review_identity_mismatch_preserves_single_failures_without_balance_or_stop_lock(self):
        payload = self.prepare()
        self.store.set_meta('single_failures', 2)
        with patch.object(self.market, 'books', return_value=self.books(('100', '106'))), patch.object(
                self.engine, 'balance', wraps=self.engine.balance) as balance, patch.object(
                self.store, 'set_meta', wraps=self.store.set_meta) as set_meta:
            self.assertIsNone(self.engine.ingest(payload))
        balance.assert_not_called()
        self.assertEqual(self.store.all_orders(), [])
        self.assertEqual(self.store.pairs(), [])
        self.assertEqual(self.store.meta('single_failures'), 2)
        self.assertFalse(any(call.args[0] == 'single_failures' for call in set_meta.call_args_list))
        self.assertFalse(self.engine.stop_file.exists())
        self.assert_quiet_rejection()

    def test_four_and_exactly_five_percent_pass_but_above_five_is_quiet_skip(self):
        payload = self.prepare(shadow=True)
        for price in ('104', '105', '105.000001'):
            with self.subTest(price=price), patch.object(self.market, 'books', return_value=self.books(('100', price))):
                pair = self.engine.ingest(dict(payload, id=price))
                if price == '105.000001':
                    self.assertIsNone(pair)
                else:
                    self.assertEqual(pair['state'], 'SHADOW')
                    self.assertEqual(pair['instruments'][0]['category'], 'unconfirmed')
        self.assert_quiet_rejection()

    def test_common_unit_prices_are_used_once_after_multiplier_conversion(self):
        payload = self.prepare(factors=('1000', '1'), shadow=True)
        with patch.object(self.market, 'books', return_value=self.books(('100000', '104'))):
            pair = self.engine.ingest(payload)
        self.assertIsNotNone(pair)
        self.assertEqual(dec(pair['last_books'][0]['bids'][0][0]), 100)
        self.assertEqual(pair['instruments'][0]['quantity_multiplier'], '1000')
        self.assertFalse(any(e['kind'] == 'SKIP' for e in self.store.events()))

    def test_metadata_and_admin_crypto_are_not_subject_to_new_price_limit(self):
        for source in ('metadata', 'admin'):
            payload = self.prepare(categories=('crypto', 'crypto') if source == 'metadata'
                                   else ('unconfirmed', 'unconfirmed'), shadow=True)
            if source == 'admin':
                self.engine.universe.classify('BTC', dict(category='crypto', markets=[
                    'binance|future|BTCUSDT', 'bitget|future|BTCUSDT']))
            with self.subTest(source=source), patch.object(self.market, 'books', return_value=self.books(('100', '106'))):
                pair = self.engine.ingest(dict(payload, id=source))
                self.assertEqual(pair['state'], 'SHADOW')

    def test_identity_check_is_review_only_after_shared_gate_refresh(self):
        payload = self.prepare()
        self.change_before_gate(lambda: self.now.__setitem__(0, self.now[0]+1.01))
        def current_books(instruments):
            books = self.books()
            if self.now[0] != admission_fixtures.NOW:
                # Make the mid-price gap 6% without changing executable depth/size.
                books[1].asks[0][0] = '108'
            return books
        with patch.object(self.market, 'books', side_effect=current_books) as fetch, patch.object(
                self.engine.universe, 'check_identity_prices', wraps=self.engine.universe.check_identity_prices) as check:
            pair = self.engine.ingest(payload)
        self.assertEqual(fetch.call_count, 2)
        check.assert_called_once()
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual([o['state'] for o in self.store.all_orders()], ['TERMINAL', 'TERMINAL'])
        self.assertEqual(len([row for row in self.trace if row[0] == 'place']), 2)
        self.assertFalse(any(e['kind'] in ('SKIP', 'ALERT') for e in self.store.events()))
        self.assertEqual(self.store.meta('stop_locks'), {})
        self.assertFalse(self.engine.stops)
        self.sink.assert_not_called()

    def test_identity_check_is_review_only_after_sent_commit_before_release(self):
        payload = self.prepare()
        def change(rows):
            pair = self.store.get_pair(rows[0]['pair_id'])
            pair['last_books'][1]['asks'][0][0] = '108'
            self.store.pair(pair)
        self.market.books = lambda instruments: self.books()
        with patch.object(self.engine.universe, 'check_identity_prices',
                          wraps=self.engine.universe.check_identity_prices) as check:
            pair, errors = self.after_sent(change, payload)
        check.assert_called_once()
        self.assertEqual(errors, [])
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual([o['state'] for o in self.store.all_orders()], ['TERMINAL', 'TERMINAL'])
        self.assertEqual(len([row for row in self.trace if row[0] == 'place']), 2)
        self.assertFalse(any(e['kind'] in ('SKIP', 'ALERT') for e in self.store.events()))
        self.assertEqual(self.store.meta('stop_locks'), {})
        self.assertFalse(self.engine.stops)
        self.sink.assert_not_called()
