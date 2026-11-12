"""Real response shapes through every account/order pagination entry point."""
import json
import unittest
from unittest.mock import patch

from tools.crossex_exec.adapters import Bitget, CrossEx
from tools.crossex_exec.security import Credentials
from tools.crossex_exec.transport import APIError, Transport
from tools.fill_sim.core import dec
from test_exec import FakeVenue, intent, setUpModule, tearDownModule
from round18_fixtures import BITGET_PATHS, OPEN, FILLS, POSITIONS, FINANCIAL, OBSERVED, Pages, row


def read_bitget(http, path, count=0):
    adapter = Bitget(http)
    if path == OPEN:
        return adapter.open_orders()
    if path == FILLS:
        result = adapter.fills(dict(intent(ex='bitget'), oid='offline-order', filled=str(count)))
        if dec(result['fee']) != count*dec('.02'):
            raise AssertionError('fixture_fees_not_reconciled')
        return result['trade_ids']
    if path == POSITIONS:
        return adapter.position_history(100, 200)
    return adapter.financial_records(100, 200)


class RealShapeTests(unittest.TestCase):
    def test_observed_empty_orders_null_cursor_is_terminal(self):
        http = Pages([OBSERVED[OPEN]])
        self.assertEqual(Bitget(http).open_orders(), [])
        self.assertEqual(http.calls, [(OPEN, dict(category='USDT-FUTURES', limit=100))])

    def test_empty_array_with_null_or_string_terminal_on_all_bitget_routes(self):
        for path in BITGET_PATHS:
            for cursor in (None, ''):
                with self.subTest(path=path, cursor=cursor):
                    http = Pages([dict(list=[], cursor=cursor)])
                    self.assertEqual(read_bitget(http, path), [])
                    self.assertEqual(len(http.calls), 1)

    def test_full_string_cursor_pages_continue_to_explicit_null_terminal(self):
        for path in BITGET_PATHS:
            with self.subTest(path=path):
                full = (OBSERVED[path] if path in (FILLS, POSITIONS) else
                        dict(list=[row(path, n) for n in range(100)], cursor='offline-next'))
                terminal = OBSERVED[FINANCIAL] if path == FINANCIAL else dict(list=[], cursor=None)
                http = Pages([full, terminal])
                self.assertEqual(len(read_bitget(http, path, 100)), 100)
                self.assertEqual([q.get('cursor') for _, q in http.calls], [None, 'offline-next'])
                self.assertTrue(all(q['category']=='USDT-FUTURES' for _, q in http.calls))
                self.assertTrue(all(q['limit']==100 for _, q in http.calls))
                if path == FILLS:
                    self.assertTrue(all(q['orderId']=='offline-order' for _, q in http.calls))
                if path in (POSITIONS, FINANCIAL):
                    self.assertTrue(all((q['startTime'],q['endTime'])==('100','200') for _,q in http.calls))

    def test_short_string_cursor_never_truncates_any_bitget_route(self):
        for path in BITGET_PATHS:
            for terminal in (dict(list=[], cursor=None), dict(list=[], cursor='')):
                with self.subTest(path=path, terminal=terminal):
                    http = Pages([dict(list=[row(path, 1)], cursor='first'),
                                  dict(list=[row(path, 2)], cursor='second'), terminal])
                    self.assertEqual(len(read_bitget(http, path, 2)), 2)
                    self.assertEqual([q.get('cursor') for _,q in http.calls], [None,'first','second'])
            http = Pages([dict(list=[row(path, 1)], cursor='first'),
                          dict(list=[row(path, 2)], cursor='')])
            self.assertEqual(len(read_bitget(http, path, 2)), 2)
            self.assertEqual(len(http.calls), 2)

    def test_null_list_exception_stays_financial_only_and_requires_both_keys(self):
        for path in BITGET_PATHS:
            with self.subTest(path=path):
                http = Pages([dict(list=None, cursor=None)])
                if path == FINANCIAL:
                    self.assertEqual(read_bitget(http, path), [])
                else:
                    with self.assertRaises(APIError): read_bitget(http, path)

    def test_malformed_pages_are_rejected_on_every_bitget_route(self):
        for path in BITGET_PATHS:
            bad = (None, [], {}, {'list':[]}, {'cursor':None},
                   dict(list=None, cursor=''), dict(list=None, cursor='more'),
                   dict(list={}, cursor=None), dict(list=False, cursor=None),
                   dict(list=[], cursor='more'), dict(list=[], cursor=False),
                   dict(list=[], cursor=1), dict(list=[], cursor={}),
                   dict(list=[row(path, 1)], cursor=None),
                   dict(list=[row(path, n) for n in range(100)], cursor=None),
                   dict(list=[row(path, n) for n in range(100)], cursor=''),
                   dict(list=[row(path, n) for n in range(101)], cursor='next'))
            for data in bad:
                with self.subTest(path=path, shape=str(data)[:70]):
                    with self.assertRaises(APIError): read_bitget(Pages([data]), path)

    def test_repeated_cursor_and_page_budget_still_fail_closed(self):
        for path in BITGET_PATHS:
            with self.subTest(path=path):
                http = Pages([dict(list=[row(path, 1)], cursor='repeat'),
                              dict(list=[row(path, 2)], cursor='repeat')])
                with self.assertRaises(APIError): read_bitget(http, path, 2)
                self.assertEqual(len(http.calls), 2)
                pages = 20 if path in (OPEN,FILLS) else 2
                http = Pages([dict(list=[row(path, n)], cursor=str(n)) for n in range(pages)])
                with patch('tools.crossex_exec.history.MAX_PAGES', 2):
                    with self.assertRaises(APIError): read_bitget(http, path, pages)
                self.assertEqual(len(http.calls), pages)

    def test_positive_fill_cannot_be_accounted_from_an_empty_page(self):
        with self.assertRaises(APIError):
            Bitget(Pages([OBSERVED[OPEN]])).fills(dict(intent(ex='bitget'), oid='offline-order', filled='1'))

    def test_transport_requires_http_success_and_code_before_empty_normalization(self):
        venue = FakeVenue('bitget'); self.addCleanup(venue.close)
        http = Transport('bitget', Credentials('test-key','test-secret','test-pass'),
                         base=venue.url, local_test=True)
        status = 200
        def reply(handler, unused_status, data):
            raw = json.dumps(data).encode()
            handler.send_response(status)
            handler.send_header('Content-Length', str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)
        with patch.object(venue.server.RequestHandlerClass, 'reply', reply):
            for path in BITGET_PATHS:
                data = OBSERVED[FINANCIAL] if path == FINANCIAL else dict(list=[], cursor=None)
                envelope = dict(code='00000', data=data)
                with self.subTest(path=path):
                    status = 200; venue.responses[path] = envelope
                    self.assertEqual(read_bitget(http, path), [])
                    for status, invalid in ((403,envelope), (200,dict(code='40009',data=data)),
                                            (200,dict(data=data)), (200,dict(code='00000'))):
                        venue.responses[path] = invalid
                        with self.assertRaises(APIError): read_bitget(http, path)
        self.assertTrue(all(c[0]=='GET' for c in venue.calls))


class CrossExShapeTests(unittest.TestCase):
    def read(self, http, kind, count=0):
        adapter = CrossEx(http)
        if kind == 'fills':
            return adapter.fills(dict(intent(), oid='offline-order', filled=str(count)))['trade_ids']
        if kind == 'history_positions':
            return adapter.position_history(100,200)
        if kind == 'open_orders':
            return adapter.open_orders()
        return adapter.history(kind,100,200)

    def test_top_level_array_contract_does_not_accept_bitget_wrappers(self):
        for kind in ('account_book','history_trades','history_positions','fills','open_orders'):
            with self.subTest(kind=kind):
                self.assertEqual(self.read(Pages([[]]),kind), [])
                for bad in (None, {}, dict(list=[],cursor=None), dict(list=None,cursor=None)):
                    with self.assertRaises(APIError): self.read(Pages([bad]),kind)

    def test_numbered_pages_continue_on_full_and_stop_on_short_or_empty(self):
        for kind in ('account_book','history_trades','history_positions','fills'):
            path = '/api/v4/crossex/'+('history_trades' if kind=='fills' else kind)
            full = [row(path,n) for n in range(1000)]
            for tail in ([],[row(path,1000)]):
                with self.subTest(kind=kind, tail=len(tail)):
                    http = Pages([full,tail])
                    self.assertEqual(len(self.read(http,kind,1000+len(tail))), 1000+len(tail))
                    self.assertEqual([q['page'] for _,q in http.calls], [1,2])
                    self.assertTrue(all(q['limit']==1000 for _,q in http.calls))


if __name__ == '__main__': unittest.main()
