"""The observed financial null/null is endpoint-specific, never a generic default."""
import unittest
from unittest.mock import Mock

from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.security import Credentials
from tools.crossex_exec.transport import APIError, Transport
from test_exec import FakeVenue, intent, setUpModule, tearDownModule


class EmptyHistoryTests(unittest.TestCase):
    def test_financial_explicit_null_null_is_complete_on_first_and_final_page(self):
        for prefix in ([], [dict(list=[dict(id='old')], cursor='next')]):
            http = Mock()
            http.request.side_effect = prefix + [dict(list=None, cursor=None)]
            self.assertEqual(Bitget(http).financial_records(100, 200),
                             [dict(id='old')] if prefix else [])
            self.assertEqual(http.request.call_count, len(prefix)+1)

    def test_financial_null_requires_both_keys_and_exact_terminal_shape(self):
        for value in (None, {}, {'cursor':None}, {'list':None},
                      dict(list=None,cursor=''), dict(list=None,cursor='more'),
                      dict(list=None,cursor=0), dict(list={},cursor=None),
                      dict(list=[],cursor=None), dict(list=False,cursor=None)):
            with self.subTest(value=value):
                http = Mock(); http.request.return_value = value
                with self.assertRaises(APIError): Bitget(http).financial_records(100, 200)

    def test_real_transport_success_required_before_null_normalization(self):
        venue = FakeVenue('bitget'); self.addCleanup(venue.close)
        path = '/api/v3/account/financial-records'
        transport = Transport('bitget', Credentials('test-key','test-secret','test-pass'),
                              base=venue.url, local_test=True)
        venue.responses[path] = dict(list=None, cursor=None)
        self.assertEqual(Bitget(transport).financial_records(100,200), [])
        # FakeVenue normally supplies code=00000; exercise error/missing codes
        # on the same localhost wire without bypassing Transport's validation.
        from unittest.mock import patch
        import json
        def reply(handler, status, data):
            raw = json.dumps(data).encode()
            handler.send_response(status)
            handler.send_header('Content-Length', str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
        for envelope in (dict(code='40009',data=dict(list=None,cursor=None)),
                         dict(data=dict(list=None,cursor=None)),
                         dict(code='00000'), dict(code='00000',data=None)):
            venue.responses[path] = envelope
            with self.subTest(envelope=envelope), patch.object(venue.server.RequestHandlerClass,'reply',reply):
                with self.assertRaises(APIError): Bitget(transport).financial_records(100,200)

    def test_documented_array_empty_other_history_endpoints_and_null_rejected(self):
        calls = (lambda c,b:c.history('account_book',100,200),
                 lambda c,b:c.history('history_trades',100,200),
                 lambda c,b:c.position_history(100,200),
                 lambda c,b:b.position_history(100,200),
                 lambda c,b:b.fills(dict(intent(ex='bitget'), oid='1', filled='0')))
        for index, call in enumerate(calls):
            for null in (False,True):
                http = Mock()
                http.request.return_value = ((None if null else []) if index<3 else
                    dict(list=None,cursor=None) if null else dict(list=[],cursor=''))
                with self.subTest(endpoint=index, null=null):
                    if null:
                        with self.assertRaises(APIError): call(CrossEx(http),Bitget(http))
                    else:
                        value = call(CrossEx(http),Bitget(http))
                        self.assertEqual(value if index<4 else value['trade_ids'], [])

    def test_fills_short_page_cursor_is_followed_and_malformed_terminal_refused(self):
        http = Mock(); bg = Bitget(http)
        http.request.side_effect = [dict(list=[dict(execId='x',orderId='other')],cursor='more'),
                                    dict(list=[],cursor='')]
        value = bg.fills(dict(intent(ex='bitget'), oid='1', filled='0'))
        self.assertEqual(value['trade_ids'], [])
        self.assertEqual(http.request.call_count, 2)
        for malformed in ({'list':[]}, dict(list=[],cursor='more'), dict(list=[],cursor=1)):
            http.request.side_effect = None; http.request.return_value = malformed
            with self.subTest(malformed=malformed), self.assertRaises(APIError):
                bg.fills(dict(intent(ex='bitget'),oid='1',filled='0'))


if __name__ == '__main__': unittest.main()
