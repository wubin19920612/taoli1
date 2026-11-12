"""Mandatory optimistic preconditions, durable conflict audits and atomic writes."""
import http.client
import json
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from tools.crossex_exec import api
from tools.crossex_exec.management import Management
from tools.crossex_exec.settings import Settings
from tools.crossex_exec.storage import Store
from tools.crossex_exec.universe import Universe
import test_api as previous
from test_round8_universe import native
from test_exec import setUpModule, tearDownModule


ADMIN = 'fictional-admin-round-ten'


class ManagementConcurrencyTests(previous.APIFixture, unittest.TestCase):
    start = previous.HTTPTests.start
    request = previous.HTTPTests.request

    def setUp(self):
        super().setUp()
        self.logs = []
        self.real_server = api._Server
        self.config['CROSSEX_EXEC_ADMIN_TOKEN'] = ADMIN
        self.manager = Management(self.path)
        self.rows = [native('binance', '1000SHIB'), native('bitget', 'SHIB'), native('bybit', 'SHIB')]
        self.store.set_meta('mapping', dict(instruments={i.market.key: i.dump() for i in self.rows}))
        self.store.set_meta('market_tickers', {i.market.key: dict(mid=str(p), volume='5000000', at=time.time())
                            for i, p in zip(self.rows, (1, '.001', '.001'))})
        Universe(self.store).discover()

    def write(self, service, body, path='/api/v1/config', method='PUT'):
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            conn.request(method, path, json.dumps(body), {'X-Api-Token': ADMIN, 'Content-Type': 'application/json'})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def events(self, kind):
        return [json.loads(e['value']) for e in self.store.events() if e['kind'] == kind]

    def universe_row(self, base='SHIB'):
        return next(r for r in self.manager.get('universe') if r['base'] == base)

    def test_config_requires_top_level_exact_integer_version(self):
        service = self.start()
        for value in ({'order_usd_max': 30}, {'expected_version': 0},
                      *[{'expected_version': v, 'order_usd_max': 30} for v in (True, False, '0', 0.0, None, -1)],
                      {'values': {'order_usd_max': 30}, 'expected_version': 0}):
            with self.subTest(body=value):
                self.assertEqual(self.write(service, value)[0], 400)
        self.assertEqual(Settings(self.store).read()['version'], 0)
        self.assertEqual(self.events('CONFIG_CHANGED'), [])

    def test_config_stale_writer_conflicts_and_audit_survives_restart(self):
        service = self.start()
        self.assertEqual(self.write(service, {'expected_version': 0, 'order_usd_max': 30})[0], 200)
        self.assertEqual(self.write(service, {'expected_version': 0, 'order_usd_max': 40}),
                         (409, {'error': 'version_conflict', 'current_version': 1}))
        self.assertEqual(self.write(service, {'expected_version': 2, 'order_usd_max': 40}),
                         (409, {'error': 'version_conflict', 'current_version': 1}))
        current = self.manager.get('config')
        self.assertEqual((current['version'], current['values']['order_usd_max']), (1, 30))
        self.assertEqual(len(self.events('CONFIG_CHANGED')), 1)
        self.assertEqual(len(self.events('CONFIG_CONFLICT')), 2)
        audit = Management(self.path).get('config/audit')
        self.assertEqual(len(audit), 3)
        self.assertEqual(audit[-1]['error'], 'version_conflict')
        self.assertEqual(audit[-1]['current_version'], 1)
        self.assertEqual(self.write(service, {'expected_version': 1, 'order_usd_max': 40})[1]['version'], 2)

    def test_two_processes_competing_for_same_version_only_one_commits(self):
        script = '''import json,sys
from tools.crossex_exec.management import Management
print('ready',flush=True)
sys.stdin.readline()
try:
    result=Management(sys.argv[1]).write(('config',),{'expected_version':0,'order_usd_max':int(sys.argv[2])})
    print(json.dumps({'ok':result}))
except ValueError as e:
    print(json.dumps({'error':str(e)}))
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(self.path), str(amount)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for amount in (30, 40)]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
            for process in processes:
                process.stdin.write('go\n')
                process.stdin.flush()
            results = []
            for process in processes:
                out, err = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, err)
                results.append(json.loads(out))
            self.assertEqual(sum('ok' in r for r in results), 1)
            self.assertEqual([r['error'] for r in results if 'error' in r], ['version_conflict'])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(Settings(self.store).read()['version'], 1)
        self.assertEqual(len(self.events('CONFIG_CHANGED')), 1)
        self.assertEqual(len(self.events('CONFIG_CONFLICT')), 1)

    def test_mapping_both_actions_require_and_compare_exact_strings(self):
        service = self.start()
        mapping = self.manager.get('mappings')[0]
        path = '/api/v1/mappings/'+mapping['id']+'/'
        for action in ('confirm', 'reject'):
            for body in ({}, {'expected_status': 'pending'}, {'expected_multiplier': '1000'},
                         {'expected_status': 'pending', 'expected_multiplier': 1000},
                         {'expected_status': None, 'expected_multiplier': '1000'}):
                self.assertEqual(self.write(service, body, path+action, 'POST')[0], 400)
        self.assertEqual(mapping['multiplier'], '1000')
        for action in ('confirm', 'reject'):
            for status, multiplier in (('confirmed', '1000'), ('pending', '1000.0'), ('pending', '1')):
                code, body = self.write(service, dict(expected_status=status, expected_multiplier=multiplier), path+action, 'POST')
                self.assertEqual(code, 409)
                self.assertEqual(body, {'error': 'mapping_conflict', 'current_status': 'pending', 'current_multiplier': '1000'})
        code, body = self.write(service, dict(expected_status='pending', expected_multiplier='1000'), path+'confirm', 'POST')
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'confirmed')
        self.assertEqual(self.write(service, dict(expected_status='pending', expected_multiplier='1000'), path+'reject', 'POST')[0], 409)
        self.assertEqual(self.write(service, dict(expected_status='confirmed', expected_multiplier='1000'), path+'reject', 'POST')[1]['status'], 'rejected')
        self.assertEqual(len(self.events('MAPPING_CONFLICT')), 7)
        self.assertEqual(len(self.events('MAPPING_CHANGED')), 2)

    def test_mapping_changed_candidate_multiplier_is_compared_before_update(self):
        service = self.start()
        row = self.manager.get('mappings')[0]
        stored = self.store.meta('symbol_mappings')
        stored[row['id']]['legs'][0]['quantity_multiplier'] = '10000'
        self.store.set_meta('symbol_mappings', stored)
        for action in ('confirm', 'reject'):
            code, body = self.write(service, dict(expected_status=row['status'], expected_multiplier=row['multiplier']),
                                    '/api/v1/mappings/'+row['id']+'/'+action, 'POST')
            self.assertEqual(code, 409)
            self.assertEqual(body['current_multiplier'], '10000')
        self.assertEqual(self.events('MAPPING_CHANGED'), [])

    def compete(self, requests):
        script = '''import json,sys
from tools.crossex_exec.management import Management
print('ready',flush=True)
sys.stdin.readline()
try:
    route,body=json.loads(sys.argv[2])
    print(json.dumps({'ok':Management(sys.argv[1]).write(tuple(route),body)}))
except ValueError as e:
    print(json.dumps({'error':str(e)}))
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, str(self.path), json.dumps(request)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for request in requests]
        try:
            for process in processes:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
            for process in processes:
                process.stdin.write('go\n')
                process.stdin.flush()
            results = []
            for process in processes:
                out, err = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, err)
                results.append(json.loads(out))
            return results
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_mapping_competing_processes_have_one_decision_and_one_conflict(self):
        row = self.manager.get('mappings')[0]
        results = self.compete([(('mappings', row['id'], action),
                                 dict(expected_status=row['status'], expected_multiplier=row['multiplier']))
                                for action in ('confirm', 'reject')])
        self.assertEqual(sum('ok' in r for r in results), 1)
        self.assertEqual([r['error'] for r in results if 'error' in r], ['mapping_conflict'])
        self.assertEqual(len(self.events('MAPPING_CHANGED')), 1)
        self.assertEqual(len(self.events('MAPPING_CONFLICT')), 1)

    def test_universe_competing_processes_have_one_decision_and_one_conflict(self):
        row = self.universe_row()
        results = self.compete([(('universe', 'SHIB', action), dict(expected_status=row['status']))
                                for action in ('exclude', 'unexclude')])
        self.assertEqual(sum('ok' in r for r in results), 1)
        self.assertEqual([r['error'] for r in results if 'error' in r], ['status_conflict'])
        self.assertEqual(len(self.events('UNIVERSE_CHANGED')), 1)
        self.assertEqual(len(self.events('UNIVERSE_CONFLICT')), 1)

    def test_discovery_serializes_with_a_cross_process_admin_decision(self):
        row = self.manager.get('mappings')[0]
        script = '''import sys
from tools.crossex_exec.management import Management
print('ready',flush=True)
sys.stdin.readline()
print('writing',flush=True)
Management(sys.argv[1]).write(('mappings',sys.argv[2],'confirm'),{'expected_status':'pending','expected_multiplier':'1000'})
print('committed',flush=True)
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(self.path), row['id']],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        original = self.store.meta
        seen = []
        def observe(key, *args):
            result = original(key, *args)
            if key == 'symbol_mappings':
                seen.append(key)
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                process.stdin.write('go\n')
                process.stdin.flush()
                self.assertEqual(process.stdout.readline().strip(), 'writing')
                # Another process has its own Python locks. SQLite must hold it
                # until discovery has committed its old pending observation.
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=.15)
            return result
        try:
            with patch.object(self.store, 'meta', side_effect=observe):
                Universe(self.store).discover()
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, err)
            self.assertIn('committed', out)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(seen, ['symbol_mappings'])
        self.assertEqual(self.store.meta('symbol_mappings')[row['id']]['status'], 'confirmed')

    def test_mapping_freshness_and_success_audit_rollback_are_preserved(self):
        service = self.start()
        row = self.manager.get('mappings')[0]
        original = Store.event
        def fail(store, kind, *args, **kwargs):
            if kind in ('MAPPING_CHANGED', 'UNIVERSE_CHANGED'):
                raise OSError('private-audit-error')
            return original(store, kind, *args, **kwargs)
        universe = self.universe_row()
        with patch.object(Store, 'event', fail):
            self.assertEqual(self.write(service, dict(expected_status=row['status'], expected_multiplier=row['multiplier']),
                                       '/api/v1/mappings/'+row['id']+'/confirm', 'POST')[0], 503)
            self.assertEqual(self.write(service, {'expected_status': universe['status']},
                                       '/api/v1/universe/SHIB/exclude', 'POST')[0], 503)
        self.assertEqual(self.store.meta('symbol_mappings')[row['id']]['status'], 'pending')
        self.assertEqual(self.universe_row()['status'], universe['status'])
        self.assertEqual(self.events('MAPPING_CHANGED'), [])
        self.assertEqual(self.events('UNIVERSE_CHANGED'), [])
        stored = self.store.meta('symbol_mappings')
        stored[row['id']]['at'] = time.time()-301
        self.store.set_meta('symbol_mappings', stored)
        self.assertEqual(self.write(service, dict(expected_status=row['status'], expected_multiplier=row['multiplier']),
                                   '/api/v1/mappings/'+row['id']+'/confirm', 'POST')[0], 400)

    def test_universe_requires_status_for_every_action_and_rejects_stale_snapshot(self):
        service = self.start()
        old = self.universe_row()
        classify = {'category': 'crypto', 'markets': [self.rows[1].market.key]}
        for action, fields in (('classify', classify), ('exclude', {}), ('unexclude', {})):
            for extra in ({}, {'expected_status': None}, {'expected_status': 1}):
                self.assertEqual(self.write(service, fields | extra, '/api/v1/universe/SHIB/'+action, 'POST')[0], 400)
        code, first = self.write(service, classify | {'expected_status': old['status']}, '/api/v1/universe/SHIB/classify', 'POST')
        self.assertEqual(code, 200)
        self.assertNotEqual(first['status'], old['status'])
        for action, fields in (('classify', classify), ('exclude', {}), ('unexclude', {})):
            self.assertEqual(self.write(service, fields | {'expected_status': old['status']}, '/api/v1/universe/SHIB/'+action, 'POST'),
                             (409, {'error': 'status_conflict', 'current_status': first['status']}))
        self.assertEqual(len(self.events('UNIVERSE_CONFLICT')), 3)
        self.assertEqual(len(self.events('UNIVERSE_CHANGED')), 1)
        for action in ('exclude', 'unexclude'):
            code, first = self.write(service, {'expected_status': first['status']}, '/api/v1/universe/SHIB/'+action, 'POST')
            self.assertEqual(code, 200)
            self.assertEqual(first['excluded'], action == 'exclude')
        self.assertEqual(Management(self.path).get('universe'), self.manager.get('universe'))

    def test_universe_status_detects_selected_markets_catalog_and_aba_changes(self):
        initial = self.universe_row()['status']
        universe = Universe(self.store)
        universe.classify('SHIB', dict(category='crypto', markets=[self.rows[1].market.key]))
        first = self.universe_row()['status']
        universe.classify('SHIB', dict(category='crypto', markets=[i.market.key for i in self.rows[1:]]))
        second = self.universe_row()['status']
        universe.classify('SHIB', dict(category='crypto', markets=[self.rows[1].market.key]))
        third = self.universe_row()['status']
        self.assertEqual(len({initial, first, second, third}), 4)
        catalog = self.store.meta('mapping')
        catalog['instruments'][self.rows[1].market.key]['category'] = 'commodity'
        self.store.set_meta('mapping', catalog)
        self.assertNotEqual(self.universe_row()['status'], third)
        service = self.start()
        self.assertEqual(self.write(service, {'expected_status': third}, '/api/v1/universe/SHIB/exclude', 'POST')[0], 409)

    def test_audit_failure_rolls_back_success_and_does_not_claim_durable_conflict(self):
        service = self.start()
        original = Store.event
        def fail(store, kind, *args, **kwargs):
            if kind in ('CONFIG_CHANGED', 'CONFIG_CONFLICT', 'MAPPING_CONFLICT', 'UNIVERSE_CONFLICT'):
                raise OSError('private-audit-error')
            return original(store, kind, *args, **kwargs)
        with patch.object(Store, 'event', fail):
            self.assertEqual(self.write(service, {'expected_version': 0, 'order_usd_max': 30})[0], 503)
            self.assertEqual(self.write(service, {'expected_version': 1, 'order_usd_max': 30})[0], 503)
            mapping = self.manager.get('mappings')[0]
            self.assertEqual(self.write(service, {'expected_status': 'rejected', 'expected_multiplier': '1000'},
                                       '/api/v1/mappings/'+mapping['id']+'/confirm', 'POST')[0], 503)
            self.assertEqual(self.write(service, {'expected_status': 'u1:'+'0'*64},
                                       '/api/v1/universe/SHIB/exclude', 'POST')[0], 503)
        self.assertEqual(Settings(self.store).read()['version'], 0)
        self.assertEqual(self.events('CONFIG_CHANGED'), [])
        self.assertEqual(self.events('CONFIG_CONFLICT'), [])
        self.assertNotIn('private-audit-error', str(self.logs))
