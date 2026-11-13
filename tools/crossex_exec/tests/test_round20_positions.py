"""Observed UNITREE position shapes; synthetic amounts and offline I/O only."""
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.fill_sim.core import dec
from tools.crossex_exec import api, __main__ as cli
from tools.crossex_exec.accounts import Accounts, remote_positions
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.external import ExternalAdmin, ExternalRegistry
from tools.crossex_exec.position_values import value_snapshot
from tools.crossex_exec.storage import Store
from round18_fixtures import OfflineAccountHTTP
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import identity, rule
from test_round12_fx import quotes
from test_round18_lifecycle import OfflineMarket
import test_round18_lifecycle as lifecycle


HL = 'HYPERLIQUID_FUTURE_UNITREE_USDC'
ASTER = 'ASTER_FUTURE_UNITREE_USDT'


def unitree(symbol=HL, **changes):
    return dict(dict(symbol=symbol, margin_mode='ISOLATED', position_side='LONG',
        leverage='5', position_qty='2', mark_price='100', entry_price='100', upnl='0',
        position_id='offline-unitree', isolated_margin='40', initial_margin='40',
        maintenance_margin='2', funding_fee='0', funding_time='0', fee='0', closed_pnl='0',
        create_time='1794441600000'), **changes)


def catalog():
    return [dict(rule(HL), tag='xyz', support_cross='false'), rule(ASTER),
            dict(rule('HYPERLIQUID_FUTURE_xyz:UNITREE_USDC'), support_cross='false'),
            dict(rule('HYPERLIQUID_FUTURE_para:UNITREE_USDC'), support_cross='false')]


def hl_identity():
    return dict(identity(HL), dex='xyz')


class PositionFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'engine.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.http = OfflineAccountHTTP()
        self.http.responses['/api/v4/crossex/positions'] = [unitree()]
        self.now = 1794528001.0
        self.accounts = Accounts(CrossEx(self.http), Bitget(self.http), clock=lambda:self.now)
        self.market = OfflineMarket()
        self.market.spot_fx = lambda currencies:{c:quotes(self.now, coin=c) for c in currencies}
        self.engine = Engine(self.store, self.market, self.accounts, clock=lambda:self.now,
                             stop_file=Path(self.tmp.name)/'STOP')
        self.addCleanup(self.engine.close)
        self.store.set_meta('external_catalog', dict(at=self.now, rules=catalog()))

    def register(self, value=None, source='cli'):
        return ExternalAdmin(self.store, self.accounts, clock=lambda:self.now).apply(
            identity=value or hl_identity(), registered=True, reason='offline_ownership',
            expected_status=ExternalRegistry(self.store,lambda:self.now).status(), source=source)


class PositionTests(PositionFixture, unittest.TestCase):
    def test_observed_isolated_row_preserves_quantity_mode_and_raw_evidence(self):
        row = unitree()
        key = 'crossex|'+HL+'|long'
        self.assertEqual(remote_positions([row], []), {key:dec(2)})
        for snapshot in (self.accounts.snapshot(), self.accounts.position_snapshot()):
            self.assertEqual(snapshot['positions'], {key:dec(2)})
            self.assertEqual(snapshot['position_attributes'][key]['margin_mode'], 'ISOLATED')
            self.assertEqual(snapshot['raw_positions']['crossex'], [row])

    def test_cli_body_and_registration_include_margin_proof_and_exact_catalog_dex(self):
        args = SimpleNamespace(**hl_identity(), expected_status=ExternalRegistry(
            self.store,lambda:self.now).status(), reason='offline_ownership')
        body = cli.external_body(args)
        record = self.register(body['identity'])['record']
        self.assertEqual(record['identity'], hl_identity())
        self.assertEqual(record['rule']['tag'], 'xyz')
        self.assertEqual(record['position_attributes']['crossex|'+HL+'|long']['margin_mode'], 'ISOLATED')
        projection = self.engine.external.split(self.accounts.snapshot())
        self.assertEqual(projection['positions'], {})
        self.assertEqual(projection['external_positions'], {'crossex|'+HL+'|long':dec(2)})
        with Store(self.path) as reopened:
            registry = ExternalRegistry(reopened,lambda:self.now)
            self.assertEqual(registry.records(), self.engine.external.records())
            self.assertTrue(registry.forbidden('crossex',HL))
            self.assertFalse(registry.forbidden('crossex','HYPERLIQUID_FUTURE_xyz:UNITREE_USDC'))

    def test_missing_or_wrong_dex_gets_safe_catalog_mismatch(self):
        for dex in ('', 'para'):
            with self.subTest(dex=dex), self.assertRaisesRegex(ValueError,'dex_catalog_mismatch'):
                self.register(dict(identity(HL),dex=dex))
        self.assertEqual(self.engine.external.records(), {})

    def test_explicit_raw_identity_conflicts_and_program_history_still_refuse(self):
        for change in ({'tag':'para'}, {'dex':'para'}, {'exchange_type':'ASTER'}, {'quote':'USDT'}):
            self.http.responses['/api/v4/crossex/positions'] = [unitree(**change)]
            with self.subTest(change=change), self.assertRaisesRegex(ValueError,'external_identity_unverifiable'):
                self.register()
        self.http.responses['/api/v4/crossex/positions'] = [unitree()]
        order = dict(intent(), state='REJECTED',filled='0')
        order['instrument']['symbol'] = HL
        self.store.save_order(order)
        with self.assertRaisesRegex(ValueError,'program_ownership'):
            self.register()

    def test_unregistered_and_program_isolated_stop_without_invalidating_account(self):
        self.engine.limits.initialize(self.accounts.snapshot())
        for symbol in (HL, 'BINANCE_FUTURE_BTC_USDT'):
            with self.subTest(symbol=symbol):
                self.http.responses['/api/v4/crossex/positions'] = [unitree(symbol)]
                self.now += 1
                self.engine.reconcile()
                self.assertIsNone(self.store.meta('account_snapshot_error'))
                self.assertIn('isolated_position',self.store.meta('stop_locks'))
                self.assertFalse(self.engine.can_open())
                self.assertEqual(self.engine.account_cache.read()['positions'],
                                 {'crossex|'+symbol+'|long':dec(2)})
        self.assertEqual(self.store.all_orders(), [])

    def test_program_owned_isolated_mode_alone_blocks_even_matching_quantity(self):
        order=dict(intent(),state='REJECTED',filled='0',accounting_pending=False)
        self.store.save_order(order)
        self.http.responses['/api/v4/crossex/positions'] = [unitree(order['instrument']['symbol'])]
        snapshot=dict(self.accounts.snapshot(),captured_at=self.now)
        self.engine.observe_margin_modes(snapshot)
        self.assertIn('isolated_position',self.store.meta('stop_locks'))
        self.assertEqual(snapshot['positions'],{'crossex|BINANCE_FUTURE_BTC_USDT|long':dec(2)})

    def test_margin_fx_expiry_revokes_program_proof_even_with_zero_external_pnl(self):
        self.register()
        self.engine.limits.initialize(self.accounts.snapshot())
        self.engine.rebaseline(confirm=True,reason='offline_epoch',source='cli')
        self.market.spot_fx=lambda currencies:{c:quotes(self.now,coin=c,age=59) for c in currencies}
        self.engine.reconcile()
        self.assertTrue(self.store.meta('external_income')['verified'])
        self.engine.require_external_income()
        self.now+=2
        with self.assertRaisesRegex(ValueError,'external_income_unverifiable'):
            self.engine.require_external_income()

    def test_registered_isolated_is_never_sent_even_for_reduce_or_flatten(self):
        self.register()
        for reduce in (False, True):
            order = intent(reduce=reduce)
            order['instrument']['symbol'] = HL
            with self.assertRaisesRegex(ValueError,'external_contract_forbidden'):
                self.engine.check_order(order)
        result = self.engine.flatten_all('shutdown')
        self.assertTrue(result['program_flat'])
        self.assertFalse(result['account_flat'])
        self.assertEqual(self.store.all_orders(), [])
        self.assertTrue(all(m=='GET' for m,_,_ in self.http.calls))

    def test_isolated_collateral_and_positive_pnl_cannot_fund_cross_margin(self):
        account = self.http.responses['/api/v4/crossex/accounts']
        account.update(margin_balance='1000',maintenance_margin='200',maintenance_margin_rate='5')
        for pnl, expected in (('20','4.6994'), ('-20','4.7996')):
            self.http.responses['/api/v4/crossex/positions'] = [unitree(upnl=pnl)]
            snapshot = self.accounts.snapshot()
            # No implicit USDC/USD peg in the account-only observation.
            self.assertIsNone(snapshot['margins'][0])
            snapshot['external_fx'] = {'USDC':quotes(self.now)}
            valued = value_snapshot(snapshot,self.now)
            self.assertEqual(dec(valued['margins'][0]),dec(expected))
            self.assertEqual(valued['raw_accounts']['crossex'],account)
        for collateral in (None, '-1', 'NaN'):
            self.http.responses['/api/v4/crossex/positions'] = [unitree(isolated_margin=collateral)]
            self.assertIsNone(self.engine.read_account_snapshot()['margins'][0])

    def test_unknown_isolated_coverage_does_not_hide_known_physical_margin_breach(self):
        account = self.http.responses['/api/v4/crossex/accounts']
        account.update(margin_balance='100',maintenance_margin='100',maintenance_margin_rate='1')
        self.http.responses['/api/v4/crossex/positions'] = [unitree(isolated_margin=None)]
        self.assertEqual(dec(self.accounts.snapshot()['margins'][0]),1)


class LifecycleTests20(PositionFixture, unittest.TestCase):
    stop_workers = lifecycle.LifecycleTests.stop_workers

    def test_unregistered_isolated_startup_api_register_reconcile_and_shutdown(self):
        # Real startup/Accounts/adapters/threads/file DB, then a real loopback API.
        self.engine.startup()
        try:
            self.assertTrue(self.market.sampled.wait(2))
            self.assertEqual(len(self.engine.workers),3)
            self.stop_workers(self.engine)
            self.engine.refresh_accounts()
            self.engine.tick()
            self.assertFalse(self.engine.can_open())
            self.assertIn('isolated_position',self.store.meta('stop_locks'))
            config = dict(CROSSEX_EXEC_API_TOKEN='offline-read-r20',
                CROSSEX_EXEC_ADMIN_TOKEN='offline-admin-r20', CROSSEX_EXEC_API_BIND='127.0.0.1')
            real = api._Server
            with patch.object(api,'_Server',side_effect=lambda addr,h:real((addr[0],0),h)):
                service = api.start_api(config,self.path,external_admin=self.engine.external_admin,
                                        equity_admin=self.engine.rebaseline)
            self.addCleanup(service.close)
            self.assertTrue(service.ready.wait(3))
            def request(method,path,body=None):
                conn = http.client.HTTPConnection(*service.addresses[0],timeout=3)
                try:
                    conn.request(method,'/api/v1/'+path,None if body is None else json.dumps(body),
                        {'X-Api-Token':'offline-admin-r20','Content-Type':'application/json'})
                    response = conn.getresponse()
                    return response.status,json.loads(response.read())
                finally:
                    conn.close()
            body = dict(identity=identity(HL), reason='offline_ownership',
                        expected_status=self.engine.external.status())
            status, result = request('POST','external-positions/register',body)
            self.assertEqual(status,409)
            self.assertIn('dex_catalog_mismatch',result['error'])
            body['identity'] = hl_identity()
            self.assertEqual(request('POST','external-positions/register',body)[0],200)
            for _ in range(3):
                self.now += 1
                self.engine.refresh_accounts()
                self.engine.tick()
            self.assertNotIn('isolated_position',self.store.meta('stop_locks'))
            self.assertFalse(self.store.meta('position_mismatch'))
            # Registration never silently rewrites the attribution baseline.
            self.assertFalse(self.engine.can_open())
            self.assertEqual(request('POST','equity/rebaseline',dict(confirm=True,reason='offline_epoch'))[0],200)
            self.now += 1
            self.engine.refresh_accounts()
            self.engine.tick()
            self.assertTrue(self.store.meta('external_income')['verified'])
            self.assertTrue(self.engine.can_open())
            self.assertEqual(request('GET','remote-positions')[0],200)
            with patch.object(cli,'emit'):
                self.assertEqual(cli.finish_shutdown(self.engine),0)
            self.assertTrue(self.store.meta('flatten_status')['program_flat'])
            self.assertFalse(self.store.meta('flatten_status')['account_flat'])
            self.assertEqual(self.store.all_orders(),[])
        finally:
            self.engine.close()
            self.stop_workers(self.engine)


if __name__ == '__main__': unittest.main()
