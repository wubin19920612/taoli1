"""Management API has a separate credential and strict bounded request bodies."""
import http.client
from email.message import Message
from io import BytesIO
import json
from types import SimpleNamespace
import unittest

from tools.crossex_exec import api
from tools.crossex_exec.settings import SCHEMA
import test_api as previous
from test_exec import setUpModule, tearDownModule


ADMIN = 'fictional-admin-token-round-eight'


class AdminTests(previous.APIFixture, unittest.TestCase):
    start = previous.HTTPTests.start
    request = previous.HTTPTests.request

    def setUp(self):
        super().setUp()
        self.logs = []
        self.real_server = api._Server
        self.config['CROSSEX_EXEC_ADMIN_TOKEN'] = ADMIN

    def write(self, service, value, path='/api/v1/config', method='PUT', token=ADMIN, raw=None, headers=None):
        # Round ten adds mandatory preconditions. Preserve every original
        # boundary/auth assertion; this legacy helper represents a fresh reader.
        if raw is None:
            if path == '/api/v1/config':
                from tools.crossex_exec.settings import Settings
                value = dict(value, expected_version=Settings(self.store).read()['version'])
            elif path.startswith('/api/v1/universe/'):
                from tools.crossex_exec.universe import Universe
                value = dict(value, expected_status=Universe(self.store).administrative_status(path.split('/')[4]))
        conn = http.client.HTTPConnection(*service.addresses[0], timeout=3)
        try:
            body = json.dumps(value) if raw is None else raw
            conn.request(method, path, body=body, headers=headers or {'X-Api-Token': token, 'Content-Type': 'application/json'})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_each_setting_http_boundaries_and_atomic_bad_input(self):
        service = self.start()
        self.assertEqual(self.request(service, '/api/v1/config')[0], 200)
        for name, rule in SCHEMA.items():
            self.assertEqual(self.write(service, {name: rule['default']})[0], 200)
            status, before, _ = self.request(service, '/api/v1/config')
            self.assertEqual(status, 200)
            for value in (True, str(rule['default']), rule['min']-1):
                self.assertEqual(self.write(service, {name: value})[0], 400)
            if rule['max'] is not None:
                self.assertEqual(self.write(service, {name: rule['max']+1})[0], 400)
            self.assertEqual(self.request(service, '/api/v1/config')[1], before)
        status, audit, _ = self.request(service, '/api/v1/config/audit')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(audit)), len(SCHEMA))

    def test_reader_cannot_write_and_tokens_never_echo(self):
        service = self.start()
        for path, method, value in [('/api/v1/config', 'PUT', {'order_usd_max': 30}),
                                    ('/api/v1/universe/BTC/exclude', 'POST', {}),
                                    ('/api/v1/mappings/'+'a'*24+'/confirm', 'POST', {})]:
            self.assertEqual(self.write(service, value, path, method, previous.TOKEN)[0], 403)
            self.assertEqual(self.write(service, value, path, method, 'wrong')[0], 401)
        self.assertEqual(self.write(service, {}, '/api/v1/universe/BTC/exclude', 'POST')[0], 200)
        for endpoint in ('universe', 'mappings'):
            status, body, _ = self.request(service, '/api/v1/'+endpoint)
            self.assertEqual(status, 200)
            self.assertIsInstance(json.loads(body), list)
        self.assertNotIn(ADMIN, json.dumps(self.logs))

    def test_duplicate_json_nonfinite_arrays_and_oversize_are_rejected(self):
        service = self.start()
        for raw in ('[]', 'null', '{"order_usd_max":30,"order_usd_max":40}',
                    '{"order_usd_max":NaN}', '{"order_usd_max":Infinity}', ' '*8193):
            self.assertEqual(self.write(service, {}, raw=raw)[0], 400)
        self.assertEqual(self.write(service, {}, '/api/v1/universe/BTC/exclude', 'POST', raw='{"extra":true}')[0], 400)

    def test_admin_disabled_without_token_and_same_token_is_not_accepted(self):
        self.config.pop('CROSSEX_EXEC_ADMIN_TOKEN')
        service = self.start()
        self.assertEqual(self.write(service, {'order_usd_max': 30}, token=previous.TOKEN)[0], 403)
        with self.assertRaises(ValueError):
            api.APIService(dict(self.config, CROSSEX_EXEC_ADMIN_TOKEN=previous.TOKEN), self.path, None)

    def test_denied_write_consumes_bounded_body_before_closing(self):
        handler = api._Handler.__new__(api._Handler)
        handler.server = SimpleNamespace(service=SimpleNamespace(clean=lambda value: value))
        handler.command, handler.request_version = 'PUT', 'HTTP/1.1'
        handler.requestline = 'PUT /api/v1/config HTTP/1.1'
        handler.headers = Message()
        handler.headers['Content-Length'] = '2'
        handler.rfile, handler.wfile = BytesIO(b'{}'), BytesIO()
        handler.reply(403, {'error': 'admin_required'})
        self.assertEqual(handler.rfile.tell(), 2)
        self.assertIn(b'403', handler.wfile.getvalue())
