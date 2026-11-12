"""Empty queries must work on Python 3.10 without relaxing nonempty parsing."""
import json
import unittest
from unittest.mock import patch

from tools.crossex_exec import api
import test_api as previous
from test_exec import setUpModule, tearDownModule


class EmptyQueryTests(previous.APIFixture, unittest.TestCase):
    start = previous.HTTPTests.start
    request = previous.HTTPTests.request

    def setUp(self):
        super().setUp()
        self.logs = []
        self.real_server = api._Server

    def test_empty_query_bypasses_strict_parser_for_all_read_endpoints(self):
        service = self.start()
        with patch.object(api, 'parse_qs', side_effect=ValueError('bad query field')) as parser:
            for endpoint in ('summary', 'positions', 'trades', 'stats'):
                for suffix in ('', '?'):
                    with self.subTest(endpoint=endpoint, suffix=suffix):
                        status, body, _ = self.request(service, '/api/v1/'+endpoint+suffix)
                        self.assertEqual(status, 200)
                        json.loads(body)
            parser.assert_not_called()

    def test_nonempty_query_keeps_strict_validation(self):
        service = self.start()
        for query in ('limit', 'limit=2&&', 'limit=2&limit=3', '=2', 'limit='):
            with self.subTest(query=query):
                self.assertEqual(self.request(service, '/api/v1/trades?'+query)[0], 400)
        self.assertEqual(self.request(service, '/api/v1/trades?limit=2')[0], 200)
