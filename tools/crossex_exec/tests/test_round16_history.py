"""Offline large-account pagination, exact failures, and shutdown evidence."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.external_accounting import read_snapshot
from tools.crossex_exec.transport import APIError
from tools.crossex_exec.__main__ import finish_shutdown
from test_exec import intent, FakeVenue, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position, SYMBOL


class PaginationTests(unittest.TestCase):
    def test_crossex_more_than_twenty_full_pages_is_complete(self):
        http = Mock()
        http.request.side_effect = lambda m,p,q,**kw: [dict(id=str(n)) for n in
            range((q['page']-1)*1000, min(q['page']*1000, 21001))]
        rows = CrossEx(http).history('account_book', 100, 200)
        self.assertEqual(len(rows), 21001)
        self.assertEqual(http.request.call_count, 22)
        self.assertTrue(all(c.args[2]['from']==100 and c.args[2]['to']==200 for c in http.request.call_args_list))

    def test_bitget_short_page_with_cursor_is_not_terminal(self):
        http = Mock()
        http.request.side_effect = [dict(list=[dict(id='3')], cursor='3'),
                                   dict(list=[dict(id='2')], cursor='2'), dict(list=[], cursor='')]
        rows = Bitget(http).financial_records(100, 200)
        self.assertEqual([r['id'] for r in rows], ['3','2'])
        self.assertEqual(http.request.call_count, 3)
        self.assertEqual([c.args[2].get('cursor') for c in http.request.call_args_list], [None,'3','2'])

    def test_bitget_large_history_and_thirty_day_windows_cover_every_boundary(self):
        http = Mock(); day = 86400000
        http.request.return_value = dict(list=[], cursor='')
        self.assertEqual(Bitget(http).financial_records(100, 35*day), [])
        windows = [(int(c.args[2]['startTime']),int(c.args[2]['endTime'])) for c in http.request.call_args_list]
        self.assertEqual(windows[0][0],100); self.assertEqual(windows[-1][1],35*day)
        self.assertTrue(all(a[1]+1==b[0] for a,b in zip(windows,windows[1:])))
        self.assertTrue(all(b-a<=30*day for a,b in windows))
        http.reset_mock()
        http.request.side_effect = [dict(list=[dict(id=str(p*100+n)) for n in range(100)],cursor=str(p))
            for p in range(1,22)] + [dict(list=[],cursor='')]
        self.assertEqual(len(Bitget(http).financial_records(100,200)),2100)
        self.assertEqual(http.request.call_count,22)

    def test_empty_cursor_page_duplicate_and_response_shape_fail_with_context(self):
        for data, condition in ((dict(list=[],cursor='still-more'),'empty_page_with_cursor'),
                                (dict(list=None,cursor=''),'response_list_invalid'),
                                (dict(list=[dict(id='1'),dict(id='1')],cursor=''),'duplicate_id')):
            http=Mock(); http.request.return_value=data
            with self.subTest(condition=condition):
                with self.assertRaises(APIError) as caught:
                    Bitget(http).financial_records(100,200)
                diagnostic=caught.exception.diagnostic
                self.assertEqual(diagnostic['condition'],condition)
                self.assertEqual(diagnostic['endpoint'],'/api/v3/account/financial-records')
                self.assertEqual(diagnostic['page'],1)
                self.assertEqual(diagnostic['from_ms'],100)

    def test_second_page_rate_limit_preserves_endpoint_and_page_without_response_text(self):
        http=Mock(); http.request.side_effect=[[dict(id=str(n)) for n in range(1000)],APIError('rate_limited')]
        accounts=Accounts(CrossEx(http),Mock())
        accounts.snapshot=Mock(return_value=dict(raw_positions=dict(crossex=[],bitget=[]),
            raw_accounts=dict(crossex=dict(assets=[]),bitget=dict(assets=[]))))
        store=Mock(); store.meta.return_value=None
        result=read_snapshot(accounts,store,lambda:1.0)
        self.assertFalse(result['activity']['complete'])
        failure=result['activity']['failure']
        self.assertEqual(failure['endpoint'],'/api/v4/crossex/account_book')
        self.assertEqual(failure['page'],2)
        self.assertEqual(failure['error_kind'],'rate_limited')
        self.assertEqual(failure['rows_read'],1000)
        self.assertNotIn('cursor',failure)

    def test_all_history_endpoints_exhaustion_budget_is_never_a_partial_success(self):
        for route,method,key,size in (
                ('crossex','history','transaction_id',1000),
                ('crossex','position_history',None,1000),
                ('bitget','position_history','positionId',100)):
            http=Mock()
            def response(m,p,q,**kw):
                page=http.request.call_count
                rows=[dict(position_id=str(page*size+n),update_time='100',closed_type='COMPLETE_CLOSED')
                      if key is None else {key:str(page*size+n)} for n in range(size)]
                return dict(list=rows,cursor=str(page)) if route=='bitget' else rows
            http.request.side_effect=response
            adapter=CrossEx(http) if route=='crossex' else Bitget(http)
            with self.subTest(route=route,method=method),patch('tools.crossex_exec.history.MAX_PAGES',2):
                with self.assertRaises(APIError) as caught:
                    if method=='history': adapter.history('history_trades',100,200)
                    else: adapter.position_history(100,200)
                self.assertEqual(caught.exception.diagnostic['condition'],'page_budget_exhausted')
                self.assertEqual(caught.exception.diagnostic['page'],3)
                self.assertEqual(caught.exception.diagnostic['rows_read'],2*size)
                self.assertEqual(http.request.call_count,2)

    def test_real_transport_permission_code_is_retained_but_message_is_not(self):
        from tools.crossex_exec.transport import Transport
        from tools.crossex_exec.security import Credentials
        venue=FakeVenue('gate'); self.addCleanup(venue.close)
        venue.responses['/api/v4/crossex/account_book']={'label':'FORBIDDEN','message':'private-account-and-secret'}
        transport=Transport('gate',Credentials('test-key','test-secret'),base=venue.url,local_test=True)
        with self.assertRaises(APIError) as caught:
            CrossEx(transport).history('account_book',100,200)
        diagnostic=caught.exception.diagnostic
        self.assertEqual(diagnostic['http_status'],200)
        self.assertEqual(diagnostic['api_code'],'FORBIDDEN')
        self.assertEqual(diagnostic['endpoint'],'/api/v4/crossex/account_book')
        self.assertNotIn('private-account-and-secret',json.dumps(diagnostic))
        self.assertEqual([c[0] for c in venue.calls],['GET'])


class DiagnosticShutdownTests(ExternalFixture,unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.change()
        self.rows=[position(),position(side='SHORT')]

    def test_registered_external_only_shutdown_is_zero_even_income_unverifiable(self):
        self.activity_complete=False
        self.store.set_meta('external_income',dict(verified=False,reason='external_income_unverifiable'))
        self.engine.risk='STOP'
        with patch('tools.crossex_exec.__main__.emit') as emit:
            self.assertEqual(finish_shutdown(self.engine),0)
        status=self.store.meta('flatten_status')
        self.assertTrue(status['program_flat']); self.assertFalse(status['account_flat'])
        self.assertEqual(len(status['external_positions']),2)
        self.assertEqual(self.store.all_orders(),[])
        emit.assert_called_once_with('shutdown_positions',**status)

    def test_unregistered_position_nonzero_also_logs_fresh_classification(self):
        self.rows.append(position('BINANCE_FUTURE_BTC_USDT'))
        with patch('tools.crossex_exec.__main__.emit') as emit:
            self.assertEqual(finish_shutdown(self.engine),1)
        status=self.store.meta('flatten_status')
        self.assertTrue(status['verified']); self.assertFalse(status['program_flat'])
        self.assertEqual(len(status['external_positions']),2)
        self.assertEqual(len(status['remaining_positions']),1)
        self.assertIn(unittest.mock.call('shutdown_positions',**status),emit.call_args_list)

    def test_pending_write_cannot_exit_zero_with_only_external_remote_positions(self):
        self.engine.executor.pending_writes['pending']=(dict(intent('pending'),state='UNKNOWN'),None)
        with patch.object(self.engine.executor,'recover'), patch('tools.crossex_exec.__main__.emit'):
            self.assertEqual(finish_shutdown(self.engine),1)
        self.assertFalse(self.store.meta('flatten_status')['program_flat'])

    def test_activity_exception_has_diagnostic_in_snapshot_and_alert(self):
        self.anchor()
        self.accounts.activity=Mock(side_effect=APIError('rate_limited'))
        self.engine.reconcile()
        snapshot=self.store.meta('external_failed_snapshot')
        self.assertEqual(snapshot['activity']['failure']['error_kind'],'rate_limited')
        alerts=[json.loads(r['value']) for r in self.store.events() if r['kind']=='ALERT'
                and json.loads(r['value']).get('reason')=='external_income_unverifiable']
        self.assertTrue(alerts)
        self.assertEqual(alerts[-1]['activity_failure'],snapshot['activity']['failure'])

    def test_checkpoint_rebaseline_is_audited_and_rollback_keeps_it(self):
        self.anchor(); self.engine.reconcile()
        before=self.store.meta('funding_checkpoint')
        audit=self.anchor()
        self.assertEqual(audit['old']['funding_checkpoint'],before)
        current=self.store.meta('funding_checkpoint')
        self.assertEqual(current['equity_revision'],self.store.meta('equity_baseline_revision'))
        self.assertEqual(current['captured_at'],self.store.meta('external_equity_anchor')['captured_at'])
        with patch.object(self.store,'event',side_effect=sqlite3.OperationalError('test_audit_failure')):
            with self.assertRaises(sqlite3.OperationalError): self.anchor()
        self.assertEqual(self.store.meta('funding_checkpoint'),current)

    def test_large_real_adapter_activity_reaches_verified_external_income(self):
        self.rows=[position('BINANCE_FUTURE_BTC_USDT')]
        self.change(symbol='BINANCE_FUTURE_BTC_USDT')
        self.anchor()
        self.now+=1
        at=str(int(self.now*1000))
        cross_http,bg_http=Mock(),Mock()
        def response(m,p,q,**kw):
            if not p.endswith('/account_book'): return []
            return [dict(id=str(n),business_id=str(n),statement_type='TRANSACTION',change='0',
                coin='USDT',symbol='BINANCE_FUTURE_BTC_USDT',exchange_type='BINANCE',create_time=at)
                for n in range((q['page']-1)*1000,min(q['page']*1000,21001))]
        cross_http.request.side_effect=response
        bg_http.request.return_value=dict(list=[],cursor='')
        self.accounts.activity=Accounts(CrossEx(cross_http),Bitget(bg_http)).activity
        self.engine.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])
        self.assertEqual(len(self.store.meta('external_activity')['crossex_book']),21001)
        self.assertEqual(len([c for c in cross_http.request.call_args_list if c.args[1].endswith('/account_book')]),22)

    def test_three_registered_positions_replace_previous_shutdown_failure(self):
        self.rows.append(position('BINANCE_FUTURE_BTC_USDT'))
        with patch('tools.crossex_exec.__main__.emit'):
            self.assertEqual(finish_shutdown(self.engine),1)
        old=self.store.meta('flatten_status')['attempt_id']
        self.change(symbol='BINANCE_FUTURE_BTC_USDT')
        self.activity_complete=False
        with patch('tools.crossex_exec.__main__.emit') as emit:
            self.assertEqual(finish_shutdown(self.engine),0)
        status=self.store.meta('flatten_status')
        self.assertNotEqual(status['attempt_id'],old)
        self.assertTrue(status['program_flat']); self.assertFalse(status['account_flat'])
        self.assertEqual(len(status['external_positions']),3)
        self.assertEqual(self.store.all_orders(),[])
        emit.assert_called_once_with('shutdown_positions',**status)


if __name__ == '__main__': unittest.main()
