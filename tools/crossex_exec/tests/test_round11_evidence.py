"""Documented private-read shapes, malformed evidence and last send boundaries."""
import copy
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.external import ExternalRegistry
from tools.crossex_exec.management import Management
from tools.fill_sim.core import dec
from test_exec import intent, setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position, SYMBOL, rule


class EvidenceTests(ExternalFixture,unittest.TestCase):
    def test_partial_account_read_preserves_first_venue_before_second_fails(self):
        raw=dict(assets=[dict(coin='USDC',exchange_type='HYPERLIQUID',balance='25')])
        cross=SimpleNamespace(account=lambda:copy.deepcopy(raw))
        bg=SimpleNamespace(account=Mock(side_effect=ValueError('invalid_response')))
        accounts=Accounts(cross,bg,clock=lambda:self.now)
        with self.assertRaises(ValueError) as failure:
            accounts.snapshot()
        self.assertEqual(failure.exception.account_snapshot['raw_accounts']['crossex'],raw)

    def test_accounts_retain_full_raw_response_on_missing_pnl_and_refresh_stops(self):
        self.change()
        raw=position(); raw.pop('upnl')
        cross=SimpleNamespace(mode='DUAL',account=lambda:dict(position_mode='DUAL',account_mode='CROSS_EXCHANGE',
            exchange_type='CROSSEX',margin_balance='1000',maintenance_margin='250',maintenance_margin_rate='4',assets=[]),
            positions=lambda:[raw])
        bg=SimpleNamespace(mode='hedge_mode',account=lambda:dict(effEquity='30000',mmr='7500',usdtEquity='30000'),
            settings=lambda:dict(accountMode='unified',holdMode='hedge_mode',assetMode='multi_assets'),positions=lambda:[])
        accounts=Accounts(cross,bg,clock=lambda:self.now)
        with self.assertRaises(KeyError) as failure:
            accounts.snapshot()
        self.assertEqual(failure.exception.account_snapshot['raw_positions']['crossex'],[raw])
        self.engine.accounts=accounts
        self.engine.risk='RUN'
        self.engine.refresh_accounts()
        self.assertFalse(self.engine.can_open())
        self.assertEqual(self.store.meta('external_failed_snapshot')['raw_positions']['crossex'],[raw])

    def test_registration_cannot_hide_inmemory_order_or_recovery_obligation(self):
        self.engine.executor.pending_writes['x']=(dict(intent(),state='UNKNOWN'),None)
        with self.assertRaisesRegex(ValueError,'local_orders_pending'):
            self.change()
        self.engine.executor.pending_writes.clear()
        self.engine.executor.owned['x']=1
        with self.assertRaisesRegex(ValueError,'local_orders_pending'):
            self.change()

    def test_directory_change_during_admin_io_conflicts_and_snapshot_refresh_is_fresh(self):
        expected=self.registry().status()
        original=self.accounts.snapshot
        def changed():
            value=original()
            rows=self.store.meta('external_catalog'); rows['rules'][0]['tick_size']='.2'
            self.store.set_meta('external_catalog',rows)
            return value
        self.accounts.snapshot=changed
        from tools.crossex_exec.management import WriteConflict
        with self.assertRaises(WriteConflict): self.change(expected=expected)
        self.assertEqual(self.registry().records(),{})

    def test_initial_and_shared_send_config_block_even_alias_and_same_ticker_other_dex_does_not(self):
        self.change(symbol='BINANCE_FUTURE_BTC_USDT')
        i=next(i for i in self.market.mapping.values() if i.market.exchange=='binance')
        i.canonical_base='ALIAS'
        with self.assertRaisesRegex(ValueError,'external_contract_forbidden'):
            self.engine.check_send_config({},[i])
        self.assertFalse(self.registry().forbidden('bitget','BTCUSDT'))
        self.assertFalse(self.registry().forbidden('crossex','HYPERLIQUID_FUTURE_xyz:OAI_USDC'))

    def test_external_notional_liability_and_margin_keep_original_limits(self):
        self.change(); self.rows=[position()]
        snapshot=self.snapshot(); key=next(iter(snapshot['positions']))
        snapshot['notionals'][key]='601'
        self.assertEqual(self.engine.limits.exposure(snapshot),dec(601))
        self.engine.account_cache.publish(snapshot,self.now)
        with self.assertRaisesRegex(ValueError,'total_notional_limit'):
            self.engine.limits.check_order(intent(ex='bitget'))
        self.liabilities=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='1')]
        self.engine.reconcile()
        self.assertIn('account_liability',self.store.meta('stop_locks'))
        self.assertFalse(self.engine.can_open())
        self.liabilities=[]; self.margin='1.5'
        with patch.object(self.engine,'flatten_all') as flatten:
            self.engine.reconcile()
        flatten.assert_called()

    def test_administration_without_executor_has_no_offline_write_fallback(self):
        view=Management(self.path).get('external-positions')
        from test_round11_external import identity
        with self.assertRaises(OSError):
            Management(self.path).write(('external-positions','register'),dict(
                identity=identity(),expected_status=view['status'],reason='x'))
        self.assertEqual(self.registry().records(),{})

    def test_two_processes_cannot_both_apply_same_expected_status(self):
        from test_round11_external import identity
        script='''import json,sys
from types import SimpleNamespace
from tools.crossex_exec.storage import Store
from tools.crossex_exec.external import ExternalAdmin
from tools.crossex_exec.management import WriteConflict
with Store(sys.argv[1]) as store:
    accounts=SimpleNamespace(snapshot=lambda:dict(positions={},raw_positions=dict(crossex=[],bitget=[])),
        cross=SimpleNamespace(open_orders=lambda:[]))
    admin=ExternalAdmin(store,accounts,clock=lambda:float(sys.argv[4]))
    print('ready',flush=True)
    sys.stdin.readline()
    try:
        admin.apply(identity=json.loads(sys.argv[2]),expected_status=sys.argv[3],registered=True,reason='race',source='cli')
        print('applied',flush=True)
    except WriteConflict:
        print('conflict',flush=True)
'''
        expected=self.registry().status()
        children=[subprocess.Popen([sys.executable,'-c',script,str(self.path),json.dumps(identity(symbol)),expected,str(self.now)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            for symbol in (SYMBOL,'HYPERLIQUID_FUTURE_xyz:OAI_USDC')]
        try:
            for child in children:
                self.assertEqual(child.stdout.readline().strip(),'ready')
            for child in children:
                child.stdin.write('go\n'); child.stdin.flush()
            results=[child.communicate(timeout=15) for child in children]
            self.assertEqual(sorted(r[0].strip() for r in results),['applied','conflict'])
            self.assertTrue(all(child.returncode==0 for child in children))
            self.assertEqual(len(self.registry().records()),1)
            self.assertEqual(sum(e['kind']=='EXTERNAL_CONFLICT' for e in self.store.events()),1)
        finally:
            for child in children:
                if child.poll() is None: child.kill()
                child.communicate()

    def test_both_external_sides_are_excluded_but_other_dex_same_ticker_is_not(self):
        self.change()
        self.rows=[position(side='LONG'),position(side='SHORT')]
        view=self.registry().split(self.snapshot())
        self.assertEqual(view['positions'],{})
        self.assertEqual(len(view['external_positions']),2)
        self.rows.append(position('HYPERLIQUID_FUTURE_xyz:OAI_USDC'))
        self.engine.reconcile()
        self.assertTrue(self.store.meta('position_mismatch'))
        self.assertFalse(self.engine.can_open())

    def test_tracking_before_midnight_also_freezes_program_floating_baseline(self):
        from collections import Counter
        from test_invariants import World
        from round11_simulator import enable
        world=World(True,Counter())
        self.addCleanup(world.engine.close)
        enable(world,'settlement')
        world.step=1; world.now+=1; world.references(); world.engine.reconcile(); world.open()
        pair=world.store.pairs(active=True)[0]
        world.prices=[dec(99),dec(103)]
        world.now=(int(world.now)//86400+1)*86400-1
        books=world.books(world.instruments)
        world.engine.save_tracking_upnl(pair,books,world.now)
        self.assertLess(dec(world.store.meta('program_last_upnl')['upnl']),0)


class HistoryReadTests(unittest.TestCase):
    def test_cross_history_paginates_fixed_interval_and_rejects_duplicates(self):
        http=Mock()
        http.request.side_effect=[[dict(id=str(n)) for n in range(1000)],[]]
        rows=CrossEx(http).history('account_book',100,200)
        self.assertEqual(len(rows),1000)
        self.assertEqual([c.args[2] for c in http.request.call_args_list],
                         [{'from':100,'to':200,'page':p,'limit':1000} for p in (1,2)])
        http.request.side_effect=[[dict(id=str(n)) for n in range(1000)],[dict(id='0')]]
        with self.assertRaises(Exception): CrossEx(http).history('account_book',100,200)
        self.assertTrue(all(c.args[:2]==('GET','/api/v4/crossex/account_book') for c in http.request.call_args_list))

    def test_bitget_history_uses_uta_category_and_fixed_millisecond_window(self):
        http=Mock(); http.request.return_value=dict(list=[],cursor='')
        self.assertEqual(Bitget(http).financial_records(100,200),[])
        call=http.request.call_args
        self.assertEqual(call.args[:2],('GET','/api/v3/account/financial-records'))
        self.assertEqual(call.args[2],dict(category='USDT-FUTURES',startTime='100',endTime='200',limit=100))


if __name__=='__main__': unittest.main()
