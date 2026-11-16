"""Opening administration uses the existing STOP gate and bounded HTTP writer."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import unittest
from unittest.mock import patch

from tools.crossex_exec import api, management
from tools.crossex_exec.limits import DEFAULT_VAR
from tools.crossex_exec.management import Management, WriteConflict
from tools.crossex_exec.storage import Store
import test_api as previous
import test_engine as engine_fixture
from test_exec import setUpModule, tearDownModule


ADMIN = 'fictional-opening-admin-token'


class OpeningHTTPFixture:
    start = previous.HTTPTests.start
    request = previous.HTTPTests.request

    def write(self, service, action, body, *, token=ADMIN, raw=None, content_type='application/json'):
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            conn.request('POST', '/api/v1/opening/'+action,
                         json.dumps(body) if raw is None else raw,
                         {'X-Api-Token': token, 'Content-Type': content_type})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def summary(self, service):
        code, body, _ = self.request(service)
        self.assertEqual(code, 200)
        return json.loads(body)

    def events(self, kind):
        return [json.loads(row['value']) for row in self.store.events() if row['kind'] == kind]


class OpeningAPITests(OpeningHTTPFixture, previous.APIFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.logs = []
        self.real_server = api._Server
        self.config['CROSSEX_EXEC_ADMIN_TOKEN'] = ADMIN
        self.stop = Path(self.config['CROSSEX_STOP_FILE'])

    def test_both_routes_require_admin_and_respect_disabled_admin(self):
        service = self.start()
        for action in ('pause', 'resume'):
            self.assertEqual(self.write(service, action, {'expected_paused': False}, token=previous.TOKEN),
                             (403, {'error': 'admin_required'}))
            self.assertEqual(self.write(service, action, {'expected_paused': False}, token='wrong')[0], 401)
        self.config.pop('CROSSEX_EXEC_ADMIN_TOKEN')
        disabled = self.start()
        for action in ('pause', 'resume'):
            self.assertEqual(self.write(disabled, action, {'expected_paused': False}, token=previous.TOKEN)[0], 403)
        self.assertFalse(self.stop.exists())
        self.assertEqual(self.store.events(), [])

    def test_expected_paused_is_required_and_exactly_boolean(self):
        service = self.start()
        for action in ('pause', 'resume'):
            for body in ({}, {'expected_paused': False, 'extra': True},
                         *[{'expected_paused': value} for value in (None, 0, 1, -1, 0.0, 1.0, 'true', 'false', [], {})]):
                with self.subTest(action=action, body=body):
                    self.assertEqual(self.write(service, action, body), (400, {'error': 'invalid_update'}))
        self.assertFalse(self.stop.exists())
        self.assertEqual(self.store.events(), [])

    def test_routes_reuse_strict_json_body_and_length_checks(self):
        service = self.start()
        for action in ('pause', 'resume'):
            for raw in ('', '[]', 'null', '{', '{"expected_paused":false,"expected_paused":true}',
                        '{"expected_paused":NaN}', ' '*8193):
                with self.subTest(action=action, raw=raw[:70]):
                    self.assertEqual(self.write(service, action, {}, raw=raw)[0], 400)
            self.assertEqual(self.write(service, action, {'expected_paused': False}, content_type='text/plain')[0], 400)
        self.assertFalse(self.stop.exists())
        self.assertEqual(self.store.events(), [])

    def test_stale_and_noop_requests_conflict_with_current_state_and_audit(self):
        service = self.start()
        for paused, attempts in ((False, (('pause', True), ('resume', True), ('resume', False))),
                                 (True, (('resume', False), ('pause', False), ('pause', True)))):
            if paused:
                self.assertEqual(self.write(service, 'pause', {'expected_paused': False})[0], 200)
            for action, expected in attempts:
                with self.subTest(paused=paused, action=action, expected=expected):
                    self.assertEqual(self.write(service, action, {'expected_paused': expected}),
                                     (409, {'error': 'opening_conflict', 'paused': paused}))
                    self.assertEqual(self.stop.exists(), paused)
                    audit = self.events('OPENING_CONFLICT')[-1]
                    self.assertEqual((audit['source'], audit['action'], audit['expected_paused'], audit['paused']),
                                     ('admin_api', action, expected, paused))
        self.assertEqual(len(self.events('OPENING_PAUSED')), 1)
        self.assertEqual(len(self.events('OPENING_CONFLICT')), 6)
        self.assertEqual(self.events('OPENING_RESUMED'), [])

    def test_pause_atomically_publishes_complete_private_json_and_audits_both_transitions(self):
        service = self.start()
        replace = os.replace
        published = []

        def inspect(source, destination):
            source, destination = Path(source), Path(destination)
            self.assertEqual(source.parent, self.stop.parent)
            self.assertEqual(destination, self.stop)
            self.assertNotEqual(source, destination)
            self.assertFalse(destination.exists())
            payload = json.loads(source.read_text(encoding='utf-8'))
            self.assertEqual(set(payload), {'at', 'source'})
            self.assertEqual(payload['source'], 'admin_api')
            self.assertIsInstance(payload['at'], (int, float))
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
            published.append((source, payload))
            return replace(source, destination)

        with patch.object(management.os, 'replace', side_effect=inspect), patch.object(
                management.os, 'chmod', wraps=os.chmod) as chmod:
            self.assertEqual(self.write(service, 'pause', {'expected_paused': False}), (200, {'paused': True}))
        self.assertEqual(len(published), 1)
        temporary, payload = published[0]
        chmod.assert_called_once_with(temporary, 0o600)
        self.assertFalse(temporary.exists())
        self.assertEqual(json.loads(self.stop.read_text(encoding='utf-8')), payload)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.stop.stat().st_mode), 0o600)
        self.assertEqual(self.events('OPENING_PAUSED'), [dict(payload, paused=True)])
        self.assertEqual(self.write(service, 'resume', {'expected_paused': True}), (200, {'paused': False}))
        self.assertFalse(self.stop.exists())
        resumed, = self.events('OPENING_RESUMED')
        self.assertEqual((resumed['paused'], resumed['source']), (False, 'admin_api'))
        self.assertGreaterEqual(resumed['at'], payload['at'])
        with Store(self.path) as reopened:
            self.assertEqual([row['kind'] for row in reopened.events()], ['OPENING_PAUSED', 'OPENING_RESUMED'])

    def test_pause_filesystem_errors_clean_temporary_files_and_keep_api_alive(self):
        service = self.start()
        before = set(self.stop.parent.iterdir())

        def partial_write(value, stream, **kwargs):
            stream.write('{"at":')
            raise OSError('private-filesystem-details')

        for target, effect in (('tempfile.NamedTemporaryFile', OSError('private-filesystem-details')),
                               ('os.chmod', OSError('private-filesystem-details')),
                               ('json.dump', partial_write),
                               ('os.fsync', OSError('private-filesystem-details')),
                               ('os.replace', OSError('private-filesystem-details'))):
            with self.subTest(stage=target), patch('tools.crossex_exec.management.'+target, side_effect=effect):
                self.assertEqual(self.write(service, 'pause', {'expected_paused': False}),
                                 (503, {'error': 'temporarily_unavailable'}))
            self.assertEqual(set(self.stop.parent.iterdir()), before)
            self.assertEqual(self.events('OPENING_PAUSED'), [])
            self.assertFalse(self.summary(service)['opening_paused'])
        self.assertTrue(any(kind == 'api_read_failed' for kind, _ in self.logs))
        self.assertNotIn('private-filesystem-details', str(self.logs))
        self.assertEqual(self.write(service, 'pause', {'expected_paused': False})[0], 200)

    def test_resume_unlink_error_preserves_stop_and_returns_fixed_error(self):
        service = self.start()
        self.stop.write_text('operator stop', encoding='utf-8')
        with patch.object(Path, 'unlink', side_effect=PermissionError('private-filesystem-details')):
            self.assertEqual(self.write(service, 'resume', {'expected_paused': True}),
                             (503, {'error': 'temporarily_unavailable'}))
        self.assertEqual(self.stop.read_text(encoding='utf-8'), 'operator stop')
        resumed, = self.events('OPENING_RESUMED')
        failed, = self.events('OPENING_RESUME_FAILED')
        self.assertEqual((resumed['paused'], failed['paused'], failed['source'], failed['error']),
                         (False, True, 'admin_api', 'stop_file_unlink_failed'))
        self.assertGreaterEqual(failed['at'], resumed['at'])
        self.assertNotIn('private-filesystem-details', str(self.store.events()))
        self.assertTrue(self.summary(service)['opening_paused'])
        self.assertEqual(self.write(service, 'resume', {'expected_paused': True})[0], 200)
        self.assertNotIn('private-filesystem-details', str(self.logs))

    def test_commit_failure_keeps_stop_for_pause_and_resume(self):
        service = self.start()
        transactions = []

        def authorize(operation, argument, _second, _database, _trigger):
            if operation == sqlite3.SQLITE_TRANSACTION:
                transactions.append(argument)
                if argument == 'COMMIT':
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        class CommitFailingStore(Store):
            def __enter__(self):
                self.db.set_authorizer(authorize)
                return super().__enter__()

        for action, expected in (('pause', False), ('resume', True)):
            with self.subTest(action=action):
                transactions.clear()
                if expected:
                    self.stop.write_text('operator stop', encoding='utf-8')
                with patch.object(management, 'Store', CommitFailingStore):
                    self.assertEqual(self.write(service, action, {'expected_paused': expected}),
                                     (503, {'error': 'temporarily_unavailable'}))
                self.assertEqual(transactions, ['BEGIN', 'COMMIT', 'ROLLBACK'])
                self.assertTrue(self.stop.exists())
                if expected:
                    self.assertEqual(self.stop.read_text(encoding='utf-8'), 'operator stop')
                self.assertEqual(self.store.events(), [])
                self.assertTrue(self.summary(service)['opening_paused'])
        self.assertEqual(self.write(service, 'resume', {'expected_paused': True})[0], 200)

    def test_resume_audit_is_durable_before_unlink(self):
        service = self.start()
        self.stop.write_text('operator stop', encoding='utf-8')
        unlink = Path.unlink
        removed = []

        def inspect(path):
            self.assertEqual(path, self.stop)
            # The fixture reads through a separate SQLite connection.
            resumed, = self.events('OPENING_RESUMED')
            self.assertEqual((resumed['paused'], resumed['source']), (False, 'admin_api'))
            removed.append(path)
            return unlink(path)

        with patch.object(Path, 'unlink', new=inspect):
            self.assertEqual(self.write(service, 'resume', {'expected_paused': True}), (200, {'paused': False}))
        self.assertEqual(removed, [self.stop])
        self.assertFalse(self.stop.exists())

    def test_resume_failure_audit_is_best_effort(self):
        service = self.start()
        self.stop.write_text('operator stop', encoding='utf-8')
        event = Store.event
        attempted = []

        def record(store, kind, value=None, cid=None):
            attempted.append(kind)
            if kind == 'OPENING_RESUME_FAILED':
                raise sqlite3.OperationalError('private-audit-details')
            return event(store, kind, value, cid)

        with patch.object(Path, 'unlink', side_effect=PermissionError('private-filesystem-details')), \
                patch.object(Store, 'event', new=record):
            self.assertEqual(self.write(service, 'resume', {'expected_paused': True}),
                             (503, {'error': 'temporarily_unavailable'}))
        self.assertEqual(attempted, ['OPENING_RESUMED', 'OPENING_RESUME_FAILED'])
        self.assertEqual(self.stop.read_text(encoding='utf-8'), 'operator stop')
        self.assertEqual(len(self.events('OPENING_RESUMED')), 1)
        self.assertEqual(self.events('OPENING_RESUME_FAILED'), [])
        self.assertTrue(self.summary(service)['opening_paused'])
        self.assertNotIn('private-audit-details', str(self.logs))
        self.assertNotIn('private-filesystem-details', str(self.logs))
        self.assertEqual(self.write(service, 'resume', {'expected_paused': True})[0], 200)

    def test_audit_insert_failure_cannot_change_gate_or_report_durable_conflict(self):
        service = self.start()
        for paused, action in ((False, 'pause'), (True, 'resume')):
            if paused:
                self.stop.write_text('operator stop', encoding='utf-8')
            with patch.object(Store, 'event', side_effect=OSError('private-audit-details')):
                self.assertEqual(self.write(service, action, {'expected_paused': paused})[0], 503)
                self.assertEqual(self.write(service, action, {'expected_paused': not paused})[0], 503)
            self.assertEqual(self.stop.exists(), paused)
        self.assertEqual(self.store.events(), [])
        self.assertNotIn('private-audit-details', str(self.logs))

    def test_competing_managers_have_one_transition_and_one_conflict(self):
        managers = [Management(self.path, stop_file=self.stop) for _ in range(2)]
        for paused, action in ((False, 'pause'), (True, 'resume')):
            barrier = threading.Barrier(2)

            def write(manager):
                barrier.wait(timeout=3)
                try:
                    return 200, manager.write(('opening', action), {'expected_paused': paused})
                except WriteConflict as error:
                    return 409, error.response

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(write, managers))
            self.assertEqual(sorted(code for code, _ in results), [200, 409])
            self.assertEqual(self.stop.exists(), not paused)
        self.assertEqual(len(self.events('OPENING_PAUSED')), 1)
        self.assertEqual(len(self.events('OPENING_RESUMED')), 1)
        self.assertEqual(len(self.events('OPENING_CONFLICT')), 2)

    def test_default_and_custom_paths_are_shared_with_summary(self):
        config = dict(self.config)
        config.pop('CROSSEX_STOP_FILE')
        service = api.APIService(config, self.path, None)
        self.assertEqual(service.management.stop_file, Path(DEFAULT_VAR)/'STOP')
        self.assertEqual(service.management.stop_file, service.view.stop_file)
        custom = self.stop.parent/'custom-stop'
        custom.mkdir()
        self.config['CROSSEX_STOP_FILE'] = str(custom/'PAUSED')
        service = self.start()
        self.assertEqual(service.management.stop_file, service.view.stop_file)
        self.assertEqual(self.write(service, 'pause', {'expected_paused': False})[0], 200)
        self.assertTrue((custom/'PAUSED').exists())
        self.assertFalse(self.stop.exists())
        self.assertTrue(self.summary(service)['opening_paused'])

    def test_summary_retains_mode_and_existing_fields_and_only_tracks_stop_file(self):
        for enabled, mode in (('0', 'shadow'), ('1', 'live')):
            self.config['CROSSEX_LIVE_ENABLED'] = enabled
            service = self.start()
            for paused, action in ((False, None), (True, 'pause'), (False, 'resume')):
                if action:
                    self.assertEqual(self.write(service, action, {'expected_paused': not paused})[0], 200)
                summary = self.summary(service)
                self.assertEqual(set(summary), previous.SUMMARY)
                self.assertIs(summary['opening_paused'], paused)
                self.assertIs(summary['stop_file'], paused)
                self.assertEqual(summary['mode'], mode)
        self.store.set_meta('state', 'HALTED')
        self.store.set_meta('live_mode', False)
        summary = self.summary(service)
        self.assertFalse(summary['opening_paused'])
        self.assertTrue(summary['halted'])
        self.assertTrue(summary['opening_paused_reason'])
        self.assertEqual(summary['mode'], 'shadow')


class OpeningEngineTests(OpeningHTTPFixture, unittest.TestCase):
    def setUp(self):
        engine_fixture.EngineTests.setUp(self)
        self.path = Path(self.tmp.name)/'exec.db'
        self.logs = []
        self.real_server = api._Server
        self.config = {'CROSSEX_EXEC_API_TOKEN': previous.TOKEN, 'CROSSEX_EXEC_ADMIN_TOKEN': ADMIN,
                       'CROSSEX_EXEC_API_BIND': '127.0.0.1', 'CROSSEX_STOP_FILE': str(self.engine.stop_file),
                       'CROSSEX_LIVE_ENABLED': '1'}

    def tearDown(self):
        # API cleanup must precede closing the engine journal on Windows.
        self.doCleanups()
        engine_fixture.EngineTests.tearDown(self)

    def test_pause_blocks_opening_and_resume_restores_gate_without_changing_mode(self):
        service = self.start()
        self.assertTrue(self.engine.can_open())
        self.assertEqual(self.write(service, 'pause', {'expected_paused': False})[0], 200)
        self.assertFalse(self.engine.can_open())
        self.assertIsNone(self.engine.ingest(engine_fixture.trigger('paused')))
        self.assertEqual(self.store.all_orders(), [])
        self.assertEqual(self.write(service, 'resume', {'expected_paused': True})[0], 200)
        self.assertTrue(self.engine.can_open())
        self.assertTrue(self.engine.live)
        self.assertTrue(self.engine.executor.live)
        self.assertEqual(self.summary(service)['mode'], 'live')

    def test_paused_existing_position_can_reconcile_and_close(self):
        service = self.start()
        pair = self.engine.ingest(engine_fixture.trigger())
        self.assertEqual(pair['state'], 'OPEN')
        self.assertEqual(self.write(service, 'pause', {'expected_paused': False})[0], 200)
        self.engine.reconcile()
        self.assertFalse(self.engine.can_open())
        self.engine.close_pair(pair, 'manual_stop_test')
        self.assertEqual(pair['state'], 'CLOSED')
        closes = [order for order in self.store.all_orders() if order['reduce_only']]
        self.assertEqual(len(closes), 2)
        self.assertTrue(all(order['state'] == 'TERMINAL' for order in closes))
        self.assertTrue(self.engine.stop_file.exists())

    def test_resume_does_not_clear_other_risk_gates(self):
        service = self.start()
        self.engine.risk = 'STOP'
        self.store.set_meta('state', 'HALTED')
        self.store.set_meta('flatten_requested', True)
        self.assertEqual(self.write(service, 'pause', {'expected_paused': False})[0], 200)
        self.assertEqual(self.write(service, 'resume', {'expected_paused': True})[0], 200)
        self.assertFalse(self.engine.can_open())
        self.assertEqual(self.engine.risk, 'STOP')
        self.assertEqual(self.store.meta('state'), 'HALTED')
        self.assertTrue(self.store.meta('flatten_requested'))
