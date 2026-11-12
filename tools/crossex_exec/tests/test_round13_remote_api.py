"""Remote positions read the durable account observation, never private I/O."""
import copy
import json
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec import api
from tools.crossex_exec.api_data import JournalView
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, SYMBOL, identity, position
from test_round12_fx import quotes
import test_api as previous


class RemoteViewTests(ExternalFixture,unittest.TestCase):
    def view(self):
        return JournalView(self.path,{},clock=lambda:self.now).get('remote-positions',{})

    def publish(self):
        self.engine.account_cache.publish(self.engine.read_account_snapshot(),self.now)

    def test_no_snapshot_is_explicitly_unavailable_not_verified_empty(self):
        result=self.view()
        self.assertFalse(result['available'])
        self.assertTrue(result['stale'])
        self.assertIsNone(result['snapshot_version'])
        self.assertEqual(result['positions'],[])

    def test_all_remote_identities_sides_native_values_usd_bounds_and_ownership(self):
        self.change()
        other='HYPERLIQUID_FUTURE_xyz:OAI_USDC'
        program='BINANCE_FUTURE_BTC_USDT'
        self.rows=[dict(position(SYMBOL,upnl='10'),entry_price='90'),
                   dict(position(SYMBOL,qty='2',upnl='-10',side='SHORT'),entry_price='95'),
                   dict(position(other,upnl='0'),entry_price='100'),
                   dict(position(program,upnl='3'),entry_price='97')]
        self.store.save_order(dict(intent(),state='TERMINAL',filled='1',fee='0',leg=0,accounting_pending=False))
        self.market.spot_fx=lambda coins:{c:quotes(self.now,('1.01','1.01'),c) for c in coins}
        self.publish()
        before=self.store.db.total_changes
        with patch.object(self.accounts,'snapshot',side_effect=AssertionError('private_read')):
            result=self.view()
        self.assertEqual(self.store.db.total_changes,before)
        self.assertTrue(result['available']); self.assertFalse(result['stale'])
        self.assertEqual(len(result['positions']),4)
        rows={(r['symbol'],r['side']):r for r in result['positions']}
        external=rows[(SYMBOL,'long')]
        for key,value in identity().items(): self.assertEqual(external[key],value)
        self.assertEqual(external['ownership'],'external_registered')
        self.assertEqual(external['entry_price'],'90')
        self.assertEqual(external['mark_price'],'100')
        self.assertEqual(external['unrealized_pnl_native'],'10')
        self.assertEqual(dec(external['unrealized_pnl_usd']),dec('10.07980'))
        short=rows[(SYMBOL,'short')]
        self.assertEqual(short['quantity'],'2')
        self.assertEqual(dec(short['unrealized_pnl_usd']),dec('-10.12020'))
        self.assertEqual(rows[(other,'long')]['ownership'],'unregistered')
        self.assertEqual(rows[(other,'long')]['dex'],'xyz')
        self.assertEqual(rows[(program,'long')]['ownership'],'program')
        for row in rows.values():
            self.assertEqual(row['snapshot_version'],result['snapshot_version'])
            self.assertEqual(row['snapshot_at'],result['snapshot_at'])
            self.assertFalse(row['stale'])

    def test_bitget_uta_avg_price_and_exact_raw_identity(self):
        snapshot=self.snapshot()
        row=dict(category='USDT-FUTURES',symbol='BTCUSDT',marginCoin='USDT',posSide='short',
                 total='2',avgPrice='105',markPrice='100',unrealisedPnl='10',marginMode='crossed',
                 privateSecret='must-not-leak')
        snapshot['raw_positions']['bitget']=[row]
        snapshot['positions']={'bitget|BTCUSDT|short':'2'}
        self.engine.account_cache.publish(snapshot,self.now)
        result=self.view()['positions'][0]
        self.assertEqual(result['route'],'bitget')
        self.assertEqual(result['exchange_type'],'BITGET')
        self.assertEqual(result['business'],'USDT-FUTURES')
        self.assertEqual(result['quote'],'USDT'); self.assertEqual(result['dex'],'')
        self.assertEqual(result['entry_price'],'105')
        self.assertEqual(result['unrealized_pnl_usd'],'10')
        self.assertNotIn('must-not-leak',json.dumps(result))

    def test_missing_prices_and_fx_do_not_hide_rows_or_invent_zero(self):
        self.rows=[position(upnl='-7')]
        self.publish()
        row=self.view()['positions'][0]
        self.assertIsNone(row['entry_price'])
        self.assertEqual(row['unrealized_pnl_native'],'-7')
        self.assertIsNone(row['unrealized_pnl_usd'])
        self.assertFalse(row['valuation_verified'])
        self.assertEqual(row['valuation_error'],'position_fx_unavailable')

    def test_snapshot_and_source_expiry_are_independent_and_reads_do_not_renew_them(self):
        self.rows=[position(upnl='1')]
        self.market.spot_fx=lambda coins:{c:quotes(self.now,age=59) for c in coins}
        self.publish()
        original=self.view()
        self.now+=1.01
        result=self.view()
        self.assertFalse(result['stale'])
        self.assertIsNone(result['positions'][0]['unrealized_pnl_usd'])
        self.now+=9.001
        result=self.view()
        self.assertTrue(result['stale']); self.assertTrue(result['positions'][0]['stale'])
        self.assertEqual(result['snapshot_at'],original['snapshot_at'])
        self.assertEqual(result['snapshot_version'],original['snapshot_version'])

    def test_cache_publication_without_external_registration_is_persisted(self):
        self.rows=[position('BINANCE_FUTURE_BTC_USDT')]
        self.engine.refresh_accounts()
        first=self.view()
        self.assertEqual(len(first['positions']),1)
        self.now+=1; self.rows=[]
        self.engine.refresh_accounts()
        result=self.view()
        self.assertGreater(result['snapshot_version'],first['snapshot_version'])
        self.assertTrue(result['available']); self.assertEqual(result['positions'],[])


class RemoteHTTPTests(previous.APIFixture,unittest.TestCase):
    start=previous.HTTPTests.start
    request=previous.HTTPTests.request

    def setUp(self):
        super().setUp()
        self.logs=[]; self.real_server=api._Server

    def test_read_token_get_only_empty_query_and_missing_snapshot(self):
        service=self.start()
        for suffix in ('','?'):
            status,body,headers=self.request(service,'/api/v1/remote-positions'+suffix)
            self.assertEqual(status,200)
            self.assertFalse(json.loads(body)['available'])
            self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.request(service,'/api/v1/remote-positions',token=None)[0],401)
        self.assertEqual(self.request(service,'/api/v1/remote-positions',method='POST')[0],405)
        self.assertEqual(self.request(service,'/api/v1/remote-positions?symbol=BTC')[0],400)
