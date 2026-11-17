"""Bounded native debt allowances, authenticated writes and durable warnings."""
import copy
from concurrent.futures import ThreadPoolExecutor
from decimal import localcontext, ROUND_DOWN, ROUND_UP
import http.client
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tools.fill_sim.core import dec
from tools.crossex_exec import api
from tools.crossex_exec.management import Management, WriteConflict
from tools.crossex_exec.storage import Store
import test_api as api_fixture
from test_exec import intent, setUpModule, tearDownModule
from test_round29_debt import MarginSkewFixture


KEY = 'external_liability_allowances'
ADMIN = 'fictional-liability-admin'


def registration(**changes):
    return dict(dict(exchange_type='HYPERLIQUID', coin='USDC', cap='300',
                     reason='User-owned native liability', expected_revision=0), **changes)


def register(path, **changes):
    return Management(path).write(('external-liabilities', 'register'), registration(**changes))


def unregister(path, revision=1, **changes):
    body = dict(exchange_type='HYPERLIQUID', coin='USDC', expected_revision=revision)
    body.update(changes)
    return Management(path).write(('external-liabilities', 'unregister'), body)


class AllowanceAPITests(api_fixture.APIFixture, unittest.TestCase):
    start = api_fixture.HTTPTests.start
    request = api_fixture.HTTPTests.request

    def setUp(self):
        super().setUp()
        self.logs = []
        self.real_server = api._Server
        self.config['CROSSEX_EXEC_ADMIN_TOKEN'] = ADMIN

    def write(self, service, body=None, *, action='register', token=ADMIN,
              raw=None, content_type='application/json'):
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            conn.request('POST', '/api/v1/external-liabilities/'+action,
                         json.dumps(registration() if body is None else body) if raw is None else raw,
                         {'X-Api-Token': token, 'Content-Type': content_type})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def read(self, service, endpoint='external-liabilities'):
        code, body, _ = self.request(service, '/api/v1/'+endpoint)
        self.assertEqual(code, 200)
        return json.loads(body)

    def test_both_writes_require_admin_and_reader_can_list(self):
        service = self.start()
        self.assertEqual(self.read(service), dict(revision=0, items=[]))
        for action in ('register', 'unregister'):
            self.assertEqual(self.write(service, action=action, token=api_fixture.TOKEN)[0], 403)
            self.assertEqual(self.write(service, action=action, token='wrong')[0], 401)
        self.config.pop('CROSSEX_EXEC_ADMIN_TOKEN')
        disabled = self.start()
        self.assertEqual(self.write(disabled, token=api_fixture.TOKEN)[0], 403)
        self.assertIsNone(self.store.meta(KEY))

    def test_body_validation_rejects_invalid_cap_reason_identity_and_revision(self):
        service = self.start()
        invalid = []
        for cap in ('1000.00000001', '0', '-1', 'NaN', 'Infinity', 'text', '',
                    ' 300 ', '3_00', 300, True, None, [], {}, '1e9999999999999999999'):
            invalid.append(registration(cap=cap))
        for reason in ('', ' ', 'x'*201, 'line\nbreak', 'null\0byte', 'delete\x7f',
                       'control\x85', None, 1, []):
            invalid.append(registration(reason=reason))
        for field, values in (
                ('exchange_type', ('', 'hyperliquid', 'HYPERLIQUID|x', '1HL', 'HL\n', None, [])),
                ('coin', ('', 'usdc', 'USDC/USDT', 'USDC\n', 'x'*200, None, [])),
                ('expected_revision', (-1, True, '0', 0.0, None, []))):
            invalid.extend(registration(**{field: value}) for value in values)
        invalid.append(registration(unknown=True))
        for field in registration():
            body = registration()
            body.pop(field)
            invalid.append(body)
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.write(service, body)[0], 400)
        for body in ({}, dict(exchange_type='HYPERLIQUID', coin='USDC', expected_revision=0, cap='300')):
            self.assertEqual(self.write(service, body, action='unregister')[0], 400)
        self.assertIsNone(self.store.meta(KEY))
        self.assertEqual(self.store.events(), [])

    def test_strict_json_and_content_type_are_shared_with_existing_admin_routes(self):
        service = self.start()
        for action in ('register', 'unregister'):
            for raw in ('', '[]', 'null', '{', '{"cap":"300","cap":"400"}',
                        '{"cap":NaN}', ' '*8193):
                with self.subTest(action=action, raw=raw[:50]):
                    self.assertEqual(self.write(service, action=action, raw=raw)[0], 400)
            self.assertEqual(self.write(service, action=action, content_type='text/plain')[0], 400)
        self.assertEqual(self.store.events(), [])

    def test_register_replace_conflict_unregister_and_exact_audit(self):
        service = self.start()
        self.assertEqual(self.write(service)[0], 200)
        saved = self.store.meta(KEY)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(len(saved['items']), 1)
        self.assertEqual(saved['items'][0]['cap'], '300')
        self.assertGreater(saved['items'][0]['registered_at'], 0)
        self.assertEqual(self.write(service)[0], 409)
        self.assertEqual(self.store.meta(KEY), saved)
        self.assertEqual(self.write(service, registration(cap='1000', expected_revision=1))[0], 200)
        self.assertEqual(self.store.meta(KEY)['revision'], 2)
        self.assertEqual([r['cap'] for r in self.store.meta(KEY)['items']], ['1000'])
        body = dict(exchange_type='HYPERLIQUID', coin='USDC', expected_revision=1)
        self.assertEqual(self.write(service, body, action='unregister')[0], 409)
        body['expected_revision'] = 2
        self.assertEqual(self.write(service, body, action='unregister')[0], 200)
        self.assertEqual(self.read(service), dict(revision=3, items=[]))
        events = [e for e in self.store.events() if e['kind'] in (
            'EXTERNAL_LIABILITY_ALLOWANCE_REGISTERED', 'EXTERNAL_LIABILITY_ALLOWANCE_UNREGISTERED')]
        self.assertEqual(len(events), 3)
        first, last = json.loads(events[0]['value']), json.loads(events[-1]['value'])
        self.assertEqual(first['source'], 'admin_api')
        self.assertEqual(first['new'], saved['items'][0])
        self.assertIsNone(first['old'])
        self.assertEqual(last['old']['cap'], '1000')
        self.assertIsNone(last['new'])

    def test_only_one_concurrent_revision_can_commit(self):
        def attempt():
            try:
                register(self.path)
                return 'ok'
            except WriteConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: attempt(), range(2))), ['ok', 'conflict'])
        self.assertEqual(self.store.meta(KEY)['revision'], 1)

    def test_write_and_audit_roll_back_together(self):
        register(self.path)
        saved = self.store.meta(KEY)
        events = self.store.events()
        for action in ('register', 'unregister'):
            with self.subTest(action=action), patch.object(Store, 'event', side_effect=sqlite3.OperationalError('audit')):
                with self.assertRaises(sqlite3.OperationalError):
                    if action == 'register':
                        register(self.path, cap='400', expected_revision=1)
                    else:
                        unregister(self.path)
            self.assertEqual(self.store.meta(KEY), saved)
            self.assertEqual(self.store.events(), events)

    def test_read_projects_liability_without_mutating_and_handles_missing_snapshot(self):
        service = self.start()
        self.assertEqual(self.write(service)[0], 200)
        saved = self.store.meta(KEY)
        for amount, within in (('274', True), ('300', True), ('300.01', False), ('0', True)):
            self.store.set_meta('account_snapshot', dict(raw_accounts=dict(crossex=dict(assets=[dict(
                exchange_type='HYPERLIQUID', coin='USDC', liability=amount)]))))
            row, = self.read(service)['items']
            self.assertEqual((row['liability'], row['within_cap']), (amount, within))
        self.assertEqual(self.store.meta(KEY), saved)
        view = self.read(service, 'external-positions')
        self.assertIn('status', view)
        self.assertEqual(view[KEY], self.read(service))
        for snapshot in (None, {}, {'raw_accounts': []}):
            self.store.set_meta('account_snapshot', snapshot)
            row, = self.read(service)['items']
            self.assertIsNone(row['liability'])
            self.assertIsNone(row['within_cap'])
        self.store.db.execute("UPDATE meta SET value='{' WHERE key='account_snapshot'")
        self.assertIsNone(self.read(service)['items'][0]['within_cap'])


class AllowanceFixture(MarginSkewFixture):
    def setUp(self):
        super().setUp()
        self.rows = []
        self.liabilities[0]['liability'] = '274'

    def grant(self, **changes):
        return register(self.path, **changes)

    def reconcile(self):
        self.now += 2
        self.engine.reconcile()


class AllowanceRiskTests(AllowanceFixture, unittest.TestCase):
    def test_exact_cap_and_native_amount_require_no_external_position(self):
        self.grant()
        for amount in ('0.00000001', '274', '300', '300.00000000'):
            self.liabilities[0]['liability'] = amount
            snapshot = self.snapshot()
            before = copy.deepcopy(snapshot)
            self.assertEqual(self.classify(snapshot), dict(blocking=[], external=[
                dict(snapshot['liabilities'][0], allowance_cap='300')]))
            self.assertEqual(snapshot, before)
        self.liabilities[0]['liability'] = '300.01'
        self.blocked()

    def test_grant_recovers_existing_lock_after_three_fresh_snapshots_and_permits_opening(self):
        self.reconcile()
        self.assertIn('account_liability', self.engine.stops.reasons)
        self.assertFalse(self.engine.can_open())
        self.grant()
        for index in range(3):
            self.reconcile()
            self.assertEqual('account_liability' in self.engine.stops.reasons, index < 2)
        self.assertTrue(self.engine.can_open(), self.engine.stops.reasons)
        self.engine.check_order(intent(ex='bitget'))
        self.engine.check_new_risk(self.engine.risk_settings())
        self.assertEqual(sum(e['kind'] == 'ACCOUNT_LIABILITY_RECOVERED' for e in self.store.events()), 1)
        self.assertEqual(dec(self.store.meta('account_loss_current')['total']), dec(0))
        self.anchor()  # Equity rebaseline uses the same blocking projection.

    def test_excess_and_unregistration_restore_blocking_and_prevent_new_risk(self):
        self.grant()
        self.reconcile()
        self.assertTrue(self.engine.can_open())
        self.liabilities[0]['liability'] = '300.01'
        self.reconcile()
        self.assertIn('account_liability', self.engine.stops.reasons)
        with self.assertRaisesRegex(ValueError, 'account_liability'):
            self.engine.check_new_risk(self.engine.risk_settings())
        self.liabilities[0]['liability'] = '274'
        for _ in range(3):
            self.reconcile()
        self.assertTrue(self.engine.can_open())
        unregister(self.path)
        self.reconcile()
        self.blocked()
        self.assertIn('account_liability', self.engine.stops.reasons)
        self.assertFalse(self.engine.can_open())

    def test_allowance_does_not_relax_other_opening_guards_or_dedicated_scope(self):
        self.grant()
        self.reconcile()
        self.engine.stops.add('manual_other')
        self.assertFalse(self.engine.can_open())
        self.margin = '2.9'
        self.reconcile()
        with self.assertRaisesRegex(ValueError, 'margin_open_limit'):
            self.engine.limits.check_order(intent(ex='bitget'))
        self.engine.limits.scope = 'dedicated_account'
        self.assertEqual(self.engine.limits.liabilities(self.snapshot())['blocking'], self.liabilities)

    def test_same_domain_program_position_and_each_inflight_state_keep_allowance(self):
        from test_round11_external import position
        self.grant(coin='USDT')
        self.coin = self.liabilities[0]['coin'] = 'USDT'
        for state, filled in (('TERMINAL', '1'), ('INTENT', '0'), ('SENT', '0'), ('ACK', '0'), ('UNKNOWN', '0')):
            with self.subTest(state=state):
                row = self.program_order(state=state, filled=filled)
                self.store.save_order(row)
                self.rows = [dict(position(row['instrument']['symbol']), initial_margin='100')] if filled == '1' else []
                self.assertEqual(self.classify()['blocking'], [])
                self.assertEqual(self.classify()['external'][0]['allowance_cap'], '300')

    def test_route_source_amount_and_unique_exact_asset_are_still_required(self):
        self.grant()
        original = self.snapshot()
        for field, value in (('route', 'bitget'), ('source_field', 'debt'), ('coin', 'USDT'),
                             ('exchange_type', 'BINANCE'), ('liability', '273'),
                             ('liability', 'NaN'), ('liability', '0'), ('liability', '-1')):
            with self.subTest(field=field, value=value):
                snapshot = copy.deepcopy(original)
                snapshot['liabilities'][0][field] = value
                self.blocked(snapshot)
        for assets in ([], None, {}, original['raw_accounts']['crossex']['assets']*2,
                       [dict(exchange_type='HYPERLIQUID', coin='USDC')]):
            snapshot = copy.deepcopy(original)
            snapshot['raw_accounts']['crossex']['assets'] = assets
            self.blocked(snapshot)

    def test_corrupt_or_unreadable_registry_never_grants_partial_allowances(self):
        self.grant()
        valid = self.store.meta(KEY)
        bad_records = []
        for field, value in (('cap', '1001'), ('cap', 'NaN'), ('cap', '-1'), ('cap', 300),
                             ('coin', 'usdc'), ('reason', ''), ('registered_at', None)):
            row = dict(valid['items'][0], **{field: value})
            bad_records.append(dict(revision=1, items=[row]))
        bad_records += [None, [], {}, dict(revision=True, items=valid['items']),
                        dict(revision=-1, items=valid['items']), dict(revision=1, items={}),
                        dict(valid, items=valid['items']*2), dict(valid, extra=True),
                        dict(valid, items=valid['items']+[{}])]
        for data in bad_records:
            with self.subTest(data=data):
                self.store.set_meta(KEY, data)
                self.blocked()
        self.store.db.execute("UPDATE meta SET value='{' WHERE key=?", (KEY,))
        self.blocked()
        original = self.store.meta
        def read(key, default=None):
            if key == KEY:
                raise sqlite3.OperationalError('unreadable')
            return original(key, default)
        with patch.object(self.store, 'meta', side_effect=read):
            self.blocked()

    def test_existing_margin_attribution_wins_and_survives_bad_allowance_data(self):
        from test_round11_external import position, SYMBOL
        self.rows = [dict(position(SYMBOL), initial_margin='266.6139872')]
        self.liabilities[0]['liability'] = '146.85'
        self.grant()
        self.allowed('266.5940728')
        self.store.set_meta(KEY, {'invalid': True})
        self.allowed('266.5940728')


class AllowanceAlertTests(AllowanceFixture, unittest.TestCase):
    def count(self):
        return sum(e['kind'] == 'EXTERNAL_LIABILITY' for e in self.store.events())

    def assert_risk_protected(self):
        self.engine.stops.discard('account_liability')
        with patch.object(self.engine, 'flatten_all') as flatten:
            self.reconcile()
        flatten.assert_called_once_with('circuit_breaker')
        attribution = self.store.meta('liability_attribution')
        self.assertEqual(attribution['blocking'], [self.liabilities[1]])
        self.assertEqual(attribution['external'][0]['allowance_cap'], '400')
        self.assertIn('account_liability', self.engine.stops.reasons)
        self.assertFalse(self.engine.can_open())

    def prepare_margin_and_blocking_debt(self):
        self.grant(cap='400')
        self.margin = '1.5'
        self.liabilities.append(dict(route='bitget', exchange_type='BITGET', coin='USDT',
                                     liability='1', source_field='liability'))

    def assert_all_protections(self):
        # First prove that margin alone still reaches the closing coordinator.
        self.assert_risk_protected()
        self.assertNotEqual(self.store.meta('state'), 'HALTING')
        # Isolate a certified equity breach from the accounting fixtures; the
        # real limits, stop locks, alert persistence and margin path still run.
        with patch('tools.crossex_exec.limits.require_program_proof',
                   return_value=dict(program_total='-200', snapshot_version=1)):
            self.assert_risk_protected()
        self.assertEqual(self.store.meta('state'), 'HALTING')
        self.assertEqual(self.store.meta('halt_reason'), 'account_equity_drop')
        self.assertIn('account_equity_drop', self.engine.stops.reasons)

    def test_warning_state_write_failure_preserves_all_protections(self):
        self.prepare_margin_and_blocking_debt()
        save = self.store.set_meta
        for key in ('external_liability_status', 'external_liability_active', 'alert_states'):
            with self.subTest(key=key):
                self.store.set_meta('state', None)
                def failed(name, value):
                    if name == key and (key != 'alert_states' or 'external_liability|global' in value):
                        raise sqlite3.OperationalError('injected warning state failure')
                    return save(name, value)
                with patch.object(self.store, 'set_meta', side_effect=failed):
                    self.assert_all_protections()
        errors = [e for e in self.store.events() if e['kind'] == 'ALERT_INTERNAL_ERROR']
        self.assertTrue(errors)
        self.assertTrue(all(json.loads(e['value'])['reason'] == 'external_liability' for e in errors))
        self.assertEqual(self.count(), 0)

    def test_nonfinite_timestamp_in_another_alert_preserves_all_protections(self):
        self.prepare_margin_and_blocking_debt()
        # Valid JSON syntax overflows to infinity on read, then strict journal
        # serialization rejects it when a different alert updates the map.
        self.store.db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)',
            ('alert_states', '{"unrelated|global":{"state_hash":"old","at":1e9999},'
                             '"healthy|global":{"state_hash":"kept","at":0}}'))
        self.assert_all_protections()
        self.assertEqual(self.store.meta('alert_states')['healthy|global'], dict(state_hash='kept', at=0))

    def test_warning_audit_and_error_reporting_failures_preserve_all_protections(self):
        self.prepare_margin_and_blocking_debt()
        event = self.store.event
        for broken in ('EXTERNAL_LIABILITY', 'ALERT', 'ALERT_INTERNAL_ERROR'):
            with self.subTest(broken=broken):
                self.store.set_meta('state', None)
                def failed(kind, value=None, cid=None):
                    failures = (('EXTERNAL_LIABILITY', broken) if broken == 'ALERT_INTERNAL_ERROR' else (broken,))
                    if (kind in failures
                            and (kind != 'ALERT' or value.get('reason') == 'external_liability')):
                        raise sqlite3.OperationalError('injected audit failure')
                    return event(kind, value, cid)
                with patch.object(self.store, 'event', side_effect=failed):
                    self.assert_all_protections()
        self.assertEqual(self.count(), 0)

    def test_clearing_warning_state_failure_preserves_blocking_equity_and_margin(self):
        self.prepare_margin_and_blocking_debt()
        self.observe()
        self.liabilities[0]['liability'] = '400.01'
        save = self.store.set_meta
        for key in ('external_liability_active', 'external_liability_status'):
            with self.subTest(key=key):
                self.store.set_meta('state', None)
                self.engine.stops.discard('account_liability')
                def failed(name, value):
                    if name == key:
                        raise OSError('injected clear failure')
                    return save(name, value)
                with (patch.object(self.store, 'set_meta', side_effect=failed),
                      patch('tools.crossex_exec.limits.require_program_proof',
                            return_value=dict(program_total='-200', snapshot_version=1)),
                      patch.object(self.engine, 'flatten_all') as flatten):
                    self.reconcile()
                flatten.assert_called_once_with('circuit_breaker')
                self.assertEqual(self.store.meta('liability_attribution')['blocking'], self.liabilities)
                self.assertIn('account_liability', self.engine.stops.reasons)
                self.assertEqual(self.store.meta('state'), 'HALTING')
                self.assertTrue(self.store.meta('external_liability_active'))

    def test_corrupt_warning_status_cannot_interrupt_blocking_or_flatten(self):
        self.prepare_margin_and_blocking_debt()
        valid = ['crossex', 'HYPERLIQUID', 'USDC', '400', True]
        for status in (None, 'broken', 1, {}, [None], [1], [{}], ['abcde'],
                       [valid[:4]], [valid[:4]+['true']], [valid[:3]+[{}, True]],
                       [[[], 'HYPERLIQUID', 'USDC', '400', True]], [valid, None]):
            with self.subTest(status=status):
                self.store.set_meta('external_liability_active', True)
                self.store.set_meta('external_liability_status', status)
                before = self.notifications.count('external_liability')
                self.assert_risk_protected()
                self.assertEqual(self.notifications.count('external_liability'), before+1)
                self.assertEqual(self.store.meta('external_liability_status'), [valid[:4]+[False]])

    def test_unreadable_warning_metadata_cannot_interrupt_blocking_or_flatten(self):
        self.prepare_margin_and_blocking_debt()
        read = self.store.meta
        for key in ('external_liability_active', 'external_liability_status', 'alert_states'):
            for error in (sqlite3.OperationalError('read failed'), ValueError('bad JSON'),
                          RuntimeError('unexpected read failure')):
                with self.subTest(key=key, error=type(error).__name__):
                    self.store.set_meta('external_liability_active', True)
                    def failed(name, default=None):
                        if name == key:
                            raise error
                        return read(name, default)
                    with patch.object(self.store, 'meta', side_effect=failed):
                        self.assert_risk_protected()

    def test_corrupt_dedup_metadata_cannot_interrupt_blocking_or_flatten(self):
        self.prepare_margin_and_blocking_debt()
        for states in (None, 'broken', [], {'external_liability|global': 'broken'},
                       {'account_liability|global': {'state_hash': 'broken', 'at': None}}):
            with self.subTest(states=states):
                self.store.set_meta('alert_states', states)
                self.assert_risk_protected()

    def test_warning_calculation_failure_cannot_interrupt_blocking_or_flatten(self):
        self.prepare_margin_and_blocking_debt()
        def failed(value):
            if value == '400':
                raise ArithmeticError('injected warning calculation failure')
            return dec(value)
        with patch('tools.crossex_exec.limits.dec', side_effect=failed):
            self.assert_risk_protected()
        with patch('tools.crossex_exec.alerts.digest', side_effect=ValueError('bad status')):
            self.assert_risk_protected()

    def test_failed_notification_retries_initial_state_and_threshold_after_restart(self):
        self.grant(cap='400')
        saved = (None, None)
        keys = ('external_liability_status', 'external_liability_active')
        for amount in ('274', '320.01'):
            with self.subTest(amount=amount):
                self.liabilities[0]['liability'] = amount
                sink = Mock(side_effect=OSError('delivery failed'))
                self.engine.alert_sink = sink
                self.observe()
                failed_at = self.now
                state = self.store.meta('alert_states')['external_liability|global']
                self.assertEqual(state['retry_after'], failed_at+300)
                events = self.store.events()
                self.observe()
                self.assertEqual(sink.call_count, 1)
                self.assertEqual(self.store.events(), events)
                self.assertEqual(tuple(self.store.meta(key) for key in keys), saved)
                self.restart()
                before = len(self.notifications)
                self.now = failed_at+297
                self.observe()
                self.assertEqual(len(self.notifications), before)
                self.assertEqual(self.store.events(), events)
                self.now = failed_at+298
                self.observe()
                self.assertEqual(self.notifications[before:], ['external_liability'])
                self.assertNotIn('retry_after', self.store.meta('alert_states')['external_liability|global'])
                saved = tuple(self.store.meta(key) for key in keys)
                self.now += 3700
                self.observe()
                self.assertEqual(len(self.notifications), before+1)
        self.assertEqual(self.count(), 4)
        self.assertEqual(sum(e['kind'] == 'ALERT_SINK_FAILED' for e in self.store.events()), 2)

    def test_repeated_failure_rearms_retry_and_changed_signature_sends_immediately(self):
        self.grant(cap='400')
        sink = Mock(side_effect=OSError('delivery failed'))
        self.engine.alert_sink = sink
        for amount, calls in (('274', 1), ('275', 1), ('320.01', 2), ('321', 2)):
            self.liabilities[0]['liability'] = amount
            self.observe()
            self.assertEqual(sink.call_count, calls)
        failed_state = self.store.meta('alert_states')['external_liability|global']
        self.now = failed_state['retry_after']-2
        self.observe()
        self.assertEqual(sink.call_count, 3)
        self.assertEqual(self.count(), 3)
        events = self.store.events()
        self.observe()
        self.assertEqual(sink.call_count, 3)
        self.assertEqual(self.store.events(), events)
        retried_state = self.store.meta('alert_states')['external_liability|global']
        self.assertEqual(retried_state['retry_after'], failed_state['retry_after']+300)
        sink.side_effect = None
        self.now = retried_state['retry_after']-2
        self.observe()
        self.assertEqual(sink.call_count, 4)
        self.observe()
        self.assertEqual(sink.call_count, 4)
        kinds = [e['kind'] for e in self.store.events()]
        self.assertEqual(kinds.count('EXTERNAL_LIABILITY'), 4)
        self.assertEqual(kinds.count('ALERT'), 4)
        self.assertEqual(kinds.count('ALERT_SINK_FAILED'), 3)

    def test_other_alerts_keep_existing_failure_and_hourly_dedup_semantics(self):
        from tools.crossex_exec.alerts import emit
        sink = Mock(side_effect=OSError('delivery failed'))
        def send(**kwargs):
            emit(self.store, lambda:self.now, 'other_reason', {}, sink=sink, **kwargs)
        send()
        send()
        self.assertEqual(sink.call_count, 1)
        self.now += 3600
        send()
        self.assertEqual(sink.call_count, 2)
        send(new_incident=True)
        self.assertEqual(sink.call_count, 3)
        self.assertNotIn('retry_after', self.store.meta('alert_states')['other_reason|global'])
        self.assertEqual(sum(e['kind'] == 'ALERT' for e in self.store.events()), 3)
        self.assertEqual(sum(e['kind'] == 'ALERT_SINK_FAILED' for e in self.store.events()), 3)

    def test_retry_throttle_is_per_key_and_retains_other_alert_states(self):
        from tools.crossex_exec.alerts import emit
        def failed(reason):
            if reason == 'external_liability':
                emit(self.store, lambda:self.now, 'other_reason', {}, status='unchanged')
            raise OSError('delivery failed')
        sink = Mock(side_effect=failed)
        for reason in ('external_liability', 'independent', 'external_liability', 'independent'):
            emit(self.store, lambda:self.now, reason, {}, sink=sink, status='unchanged',
                 new_incident=True, repeat_after=None, retry_on_failure=True)
        self.assertEqual(sink.call_count, 2)
        states = self.store.meta('alert_states')
        self.assertEqual(set(states), {'external_liability|global', 'independent|global', 'other_reason|global'})
        self.assertNotIn('retry_after', states['other_reason|global'])
        self.assertEqual(sum(e['kind'] == 'ALERT' for e in self.store.events()), 3)
        self.assertEqual(sum(e['kind'] == 'ALERT_SINK_FAILED' for e in self.store.events()), 2)

    def test_high_precision_threshold_is_exact_under_small_decimal_contexts(self):
        cap = '400.00000000000000000000000000000000000001'
        self.grant(cap=cap)
        for precision in (6, 28):
            for rounding in (ROUND_DOWN, ROUND_UP):
                with self.subTest(precision=precision, rounding=rounding):
                    self.store.set_meta('external_liability_active', False)
                    before = self.count()
                    for amount, crossed, extra in (
                            ('320.000000000000000000000000000000000000007', False, 1),
                            ('320.000000000000000000000000000000000000008', False, 1),
                            ('320.000000000000000000000000000000000000009', True, 2),
                            ('320.000000000000000000000000000000000000007', True, 2)):
                        self.liabilities[0]['liability'] = amount
                        snapshot = self.snapshot()
                        with localcontext() as context:
                            context.prec = precision
                            context.rounding = rounding
                            self.engine.limits.check_equity(snapshot)
                        self.assertEqual(self.count(), before+extra)
                        self.assertEqual(self.store.meta('external_liability_status'),
                                         [['crossex', 'HYPERLIQUID', 'USDC', cap, crossed]])

    def test_distinct_high_precision_caps_preserve_raw_status_and_notify(self):
        for revision, cap in enumerate(('400.00000000000000000000000000000000000001',
                                        '400.00000000000000000000000000000000000002')):
            self.grant(cap=cap, expected_revision=revision)
            self.observe()
            self.assertEqual(self.count(), revision+1)
            self.assertEqual(self.store.meta('external_liability_status')[0][3], cap)

    def test_amount_jitter_hour_and_restart_do_not_repeat_allowance_warning(self):
        self.grant(cap='400')
        for index in range(12):
            if index == 6:
                self.restart()
            self.now += 3700
            self.liabilities[0]['liability'] = str(dec('274')+dec(index)/100)
            self.observe()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.notifications, ['external_liability'])

    def test_eighty_percent_first_crossing_is_latched_until_allowance_incident_changes(self):
        self.grant(cap='400')
        for amount, count in (('274', 1), ('320', 1), ('320.0001', 2), ('321', 2),
                              ('319.99', 2), ('320.01', 2)):
            self.liabilities[0]['liability'] = amount
            self.observe()
            self.assertEqual(self.count(), count)
        self.restart()
        self.liabilities[0]['liability'] = '319'
        self.observe()
        self.assertEqual(self.count(), 2)
        self.grant(cap='500', expected_revision=1)
        self.observe()
        self.assertEqual(self.count(), 3)
        self.liabilities[0]['liability'] = '400.01'
        self.observe()
        self.assertEqual(self.count(), 4)

    def test_clear_unregister_and_new_coverage_each_start_new_incident(self):
        self.grant()
        self.observe()
        saved = self.liabilities
        self.liabilities = []
        self.observe()
        self.assertFalse(self.store.meta('external_liability_active'))
        self.liabilities = saved
        self.observe()
        self.assertEqual(self.count(), 2)
        unregister(self.path)
        self.observe()
        self.assertIn('account_liability', self.engine.stops.reasons)
        self.grant(expected_revision=2)
        self.observe()
        self.assertEqual(self.count(), 3)

    def test_threshold_state_and_warning_audit_are_atomic(self):
        self.grant()
        save = self.store.set_meta
        def failed(key, value):
            if key == 'external_liability_active':
                raise OSError('injected alert write failure')
            return save(key, value)
        with patch.object(self.store, 'set_meta', side_effect=failed):
            self.observe()
        self.assertEqual(self.count(), 0)
        self.assertIsNone(self.store.meta('external_liability_status'))
        self.assertIsNone(self.store.meta('alert_states'))
        self.assertEqual(sum(e['kind'] == 'ALERT_INTERNAL_ERROR' for e in self.store.events()), 1)
