"""Native-coin debt attribution tolerates read skew, never missing coverage."""
import copy
import json
import unittest

from tools.fill_sim.core import dec
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.ownership import ProgramOwnership
from tools.crossex_exec.program_risk import debt_attribution
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, SYMBOL, position


class MarginSkewFixture(ExternalFixture):
    def setUp(self):
        super().setUp()
        self.coin = 'USDC'
        self.asset_margin = '266.5940728'
        self.rows = [dict(position(SYMBOL, side='SHORT'), initial_margin='266.6139872')]
        self.liabilities = [dict(route='crossex', exchange_type='HYPERLIQUID',
                                 coin=self.coin, liability='146.85', source_field='liability')]
        self.notifications = []
        self.engine.alert_sink = self.notifications.append

    def snapshot(self):
        result = super().snapshot()
        if hasattr(self, 'asset_margin'):
            result['raw_accounts']['crossex']['assets'] = [dict(
                exchange_type='HYPERLIQUID', coin=self.coin, balance='1000',
                liability=self.liabilities[0]['liability'] if self.liabilities else '0',
                futures_initial_margin=self.asset_margin)]
        return result

    def classify(self, snapshot=None):
        return debt_attribution(self.store, self.snapshot() if snapshot is None else snapshot, self.now)

    def allowed(self, expected_margin, snapshot=None):
        snapshot = self.snapshot() if snapshot is None else snapshot
        result = self.classify(snapshot)
        self.assertEqual(result['blocking'], [])
        self.assertEqual(result['external'], [dict(snapshot['liabilities'][0],
                                                  external_initial_margin=str(expected_margin))])

    def blocked(self, snapshot=None):
        snapshot = self.snapshot() if snapshot is None else snapshot
        self.assertEqual(self.classify(snapshot), dict(external=[], blocking=snapshot['liabilities']))

    def usdt_group(self):
        self.coin = 'USDT'
        self.rows[0]['symbol'] = SYMBOL.replace('_USDC', '_USDT')
        self.liabilities[0]['coin'] = self.coin

    def program_order(self, state='INTENT', filled='0'):
        row = dict(intent('program', ex='hyperliquid'), state=state, filled=filled,
                   leg=0, fee='0', vwap='100', accounting_pending=False)
        row['instrument']['symbol'] = 'HYPERLIQUID_FUTURE_BTC_USDT'
        return row

    def observe(self):
        self.now += 2
        snapshot = self.snapshot()
        self.engine.limits.check_equity(snapshot)
        self.engine.observe_account_snapshot(snapshot, self.now)
        return snapshot

    def restart(self):
        self.engine.close()
        self.store.close()
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.engine = Engine(self.store, self.market, self.accounts, clock=lambda:self.now,
                             live=True, stop_file=self.path.parent/'STOP',
                             alert_sink=self.notifications.append)
        self.addCleanup(self.engine.close)


class NativeMarginTests(MarginSkewFixture, unittest.TestCase):
    def test_reported_usdc_short_read_skew_is_attributed_without_mutating_evidence(self):
        snapshot = self.snapshot()
        snapshot['snapshot_version'] = 3460
        before = copy.deepcopy(snapshot)
        self.assertEqual(dec(self.rows[0]['initial_margin'])-dec(self.asset_margin), dec('0.0199144'))
        self.assertEqual(snapshot['positions'], {'crossex|'+SYMBOL+'|short': dec('1')})
        self.allowed('266.5940728', snapshot)
        self.assertEqual(snapshot, before)

    def test_skew_and_exact_coverage_boundaries_are_symmetric_and_inclusive(self):
        for low, high in (('100', '101'), ('0.01', '0.0101'), ('1000000', '1010000'),
                          ('266.5940728', '266.6139872')):
            for asset, positions in ((low, high), (high, low)):
                with self.subTest(asset=asset, positions=positions):
                    self.asset_margin = asset
                    self.rows[0]['initial_margin'] = positions
                    self.liabilities[0]['liability'] = low
                    self.allowed(low)

    def test_skew_beyond_one_percent_of_smaller_value_still_blocks(self):
        for low, high in (('100', '101.00000001'), ('99', '100'),
                          ('0.01', '0.010100000001'), ('266.5940728', '270')):
            for asset, positions in ((low, high), (high, low)):
                with self.subTest(asset=asset, positions=positions):
                    self.asset_margin = asset
                    self.rows[0]['initial_margin'] = positions
                    self.liabilities[0]['liability'] = str(dec(low)/2)
                    self.blocked()

    def test_debt_between_the_two_margins_cannot_borrow_the_tolerance(self):
        for asset, positions in (('266.5940728', '266.6139872'), ('266.6139872', '266.5940728')):
            with self.subTest(asset=asset):
                self.asset_margin = asset
                self.rows[0]['initial_margin'] = positions
                self.liabilities[0]['liability'] = '266.5940729'
                self.blocked()

    def test_missing_invalid_nonfinite_negative_or_zero_margin_blocks(self):
        original = self.snapshot()
        for location, field in (('asset', 'futures_initial_margin'), ('position', 'initial_margin')):
            for value in ('missing', None, '', 'not-a-number', 'NaN', 'Infinity', '-1', '0'):
                with self.subTest(location=location, value=value):
                    snapshot = copy.deepcopy(original)
                    row = (snapshot['raw_accounts']['crossex']['assets'][0] if location == 'asset'
                           else snapshot['raw_positions']['crossex'][0])
                    if value == 'missing':
                        row.pop(field)
                    else:
                        row[field] = value
                    self.blocked(snapshot)

    def test_missing_or_duplicate_account_asset_evidence_blocks(self):
        original = self.snapshot()
        for case in ('raw_positions', 'raw_accounts', 'assets', 'empty', 'duplicate'):
            with self.subTest(case=case):
                snapshot = copy.deepcopy(original)
                if case in ('raw_positions', 'raw_accounts'):
                    snapshot.pop(case)
                elif case == 'assets':
                    snapshot['raw_accounts']['crossex'].pop('assets')
                elif case == 'empty':
                    snapshot['raw_accounts']['crossex']['assets'] = []
                else:
                    snapshot['raw_accounts']['crossex']['assets'] *= 2
                self.blocked(snapshot)

    def test_debt_identity_amount_and_source_still_require_exact_asset_match(self):
        original = self.snapshot()
        for field, value in (('route', 'bitget'), ('exchange_type', 'BINANCE'),
                             ('coin', 'USDT'), ('source_field', 'balance'),
                             ('liability', '146.86'), ('liability', 'NaN'),
                             ('liability', '0'), ('liability', '-1')):
            with self.subTest(field=field, value=value):
                snapshot = copy.deepcopy(original)
                snapshot['liabilities'][0][field] = value
                self.blocked(snapshot)
        for field in original['liabilities'][0]:
            with self.subTest(missing=field):
                snapshot = copy.deepcopy(original)
                snapshot['liabilities'][0].pop(field)
                self.blocked(snapshot)
        snapshot = copy.deepcopy(original)
        snapshot['raw_accounts']['crossex']['assets'][0].pop('liability')
        self.blocked(snapshot)

    def test_raw_dex_exchange_and_quote_conflicts_still_block(self):
        for field, value in (('tag', 'xyz'), ('dex', 'xyz'), ('exchange_type', 'BINANCE'),
                             ('business_type', 'SPOT'), ('quote', 'USDT')):
            with self.subTest(field=field):
                snapshot = self.snapshot()
                snapshot['raw_positions']['crossex'][0][field] = value
                self.blocked(snapshot)

    def test_all_external_margins_sum_only_within_exact_native_group(self):
        self.rows[0]['initial_margin'] = '100'
        self.rows.append(dict(position('HYPERLIQUID_FUTURE_xyz:OAI_USDC'),
                              initial_margin='166.6139872'))
        self.allowed('266.5940728')
        for symbol in ('HYPERLIQUID_FUTURE_xyz:OAI_USDT', 'BINANCE_FUTURE_OAI_USDC'):
            with self.subTest(symbol=symbol):
                self.rows[1]['symbol'] = symbol
                # A large unrelated balance/FX quote cannot fund this USDC liability.
                snapshot = self.snapshot()
                snapshot['raw_accounts']['crossex']['assets'].append(dict(
                    exchange_type=symbol.split('_')[0], coin=symbol.rsplit('_', 1)[1],
                    balance='1000000', liability='0', futures_initial_margin='166.6139872'))
                snapshot['external_fx'] = {'USDT': {'bid': '1000', 'ask': '1000', 'at': self.now}}
                self.blocked(snapshot)

    def test_same_group_program_position_blocks_even_with_valid_external_coverage(self):
        self.usdt_group()
        self.allowed('266.5940728')
        order = self.program_order(state='TERMINAL', filled='1')
        self.store.save_order(order)
        self.rows.append(dict(position(order['instrument']['symbol']), initial_margin='1'))
        snapshot = self.snapshot()
        projection = ProgramOwnership(self.store, lambda:self.now).split(snapshot, pnl=False)
        self.assertEqual(len(projection['positions']), 1)
        self.assertEqual(len(projection['external_positions']), 1)
        self.blocked(snapshot)

    def test_each_inflight_order_state_blocks_only_its_native_group(self):
        self.usdt_group()
        self.allowed('266.5940728')
        for state in ('INTENT', 'SENT', 'ACK', 'UNKNOWN'):
            with self.subTest(state=state):
                self.store.save_order(self.program_order(state))
                snapshot = self.snapshot()
                projection = ProgramOwnership(self.store, lambda:self.now).split(snapshot, pnl=False)
                self.assertEqual(projection['positions'], {})
                self.assertEqual(len(projection['external_positions']), 1)
                self.blocked(snapshot)
        # The same Hyperliquid USDT order cannot contaminate the USDC domain.
        self.coin = 'USDC'
        self.rows[0]['symbol'] = SYMBOL
        self.liabilities[0]['coin'] = self.coin
        self.allowed('266.5940728')

    def test_unrelated_venue_orders_do_not_block_a_fully_attributable_debt(self):
        for exchange in ('binance', 'bitget'):
            self.store.save_order(dict(intent(exchange, ex=exchange), state='UNKNOWN', filled='0'))
        self.allowed('266.5940728')


class MarginAlertTests(MarginSkewFixture, unittest.TestCase):
    def test_tolerated_jitter_keeps_one_active_warning_across_file_restart(self):
        for index in range(20):
            if index == 10:
                self.restart()
            self.asset_margin = ('266.5940728', '266.6139872', '266.62', '267')[index % 4]
            self.liabilities[0]['liability'] = str(dec('146.85')+dec(index)/100)
            self.observe()
            self.assertNotIn('account_liability', self.engine.stops.reasons)
            self.assertTrue(self.store.meta('external_liability_active'))
        self.assertEqual(self.notifications, ['external_liability'])
        self.assertEqual(sum(e['kind'] == 'EXTERNAL_LIABILITY' for e in self.store.events()), 1)
        self.assertFalse(any(e['kind'] in ('ACCOUNT_LIABILITY', 'ACCOUNT_LIABILITY_RECOVERED',
                                          'EXTERNAL_LIABILITY_CLEARED') for e in self.store.events()))
        alerts = [json.loads(e['value'])['reason'] for e in self.store.events() if e['kind'] == 'ALERT']
        self.assertEqual(alerts, ['external_liability'])

    def test_excess_skew_still_blocks_and_requires_three_distinct_tolerated_snapshots(self):
        self.asset_margin = '270'
        self.observe()
        self.assertIn('account_liability', self.engine.stops.reasons)
        self.engine.stops.add('manual_other')
        self.asset_margin = '266.5940728'
        for index in range(3):
            snapshot = self.observe()
            self.assertEqual('account_liability' in self.engine.stops.reasons, index < 2)
            self.engine.observe_account_snapshot(snapshot, self.now)
            self.assertEqual('account_liability' in self.engine.stops.reasons, index < 2)
        self.assertIn('manual_other', self.engine.stops.reasons)
        self.assertEqual(sum(e['kind'] == 'ACCOUNT_LIABILITY_RECOVERED' for e in self.store.events()), 1)
        self.assertEqual(self.notifications, ['account_liability', 'external_liability'])

    def test_real_clear_and_new_debt_still_create_a_new_warning(self):
        self.observe()
        self.assertEqual(self.notifications, ['external_liability'])
        saved = self.liabilities
        self.liabilities = []
        self.observe()
        self.assertFalse(self.store.meta('external_liability_active'))
        self.liabilities = saved
        self.observe()
        self.assertEqual(self.notifications, ['external_liability', 'external_liability'])
        self.assertEqual(sum(e['kind'] == 'EXTERNAL_LIABILITY_CLEARED' for e in self.store.events()), 1)
