"""Manual equity changes require flat, fully reconciled local AND remote state."""
import copy
import json
import unittest
from unittest.mock import patch

from tools.fill_sim.core import dec, day
from tools.crossex_exec import __main__ as cli
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from tools.crossex_exec.management import Management, write_route
from test_exec import setUpModule, tearDownModule, intent
import test_round3 as previous
import test_round8_admin as api_previous


class EquityTests(unittest.TestCase):
    setUp = previous.Round3Tests.setUp
    snapshot = previous.Round3Tests.snapshot
    pair = previous.Round3Tests.pair

    def prepare(self, halted=False):
        self.venues[0].open_orders=lambda:[]
        self.venues[1].open_orders=lambda:[]
        self.store.set_meta('equity_baseline','31000')
        self.store.set_meta('daily_equity',dict(day=day(self.now[0]),equity='31000'))
        self.data['equity_by_venue']=dict(crossex='29753',bitget='517')
        if halted:
            self.engine.alert('account_equity_drop')
            self.store.set_meta('state','HALTED')
        return copy.deepcopy(self.store.meta('account_equity_baseline'))

    def change(self, release=False, **kwargs):
        return self.engine.rebaseline(confirm='account_equity_drop' if release else True,
            release_halt=release,source='cli',reason='verified_transfer',**kwargs)

    def assert_refused(self, reason, release=False):
        before={r[0]:r[1] for r in self.store.db.execute('SELECT key,value FROM meta')}
        with self.assertRaisesRegex(ValueError,reason):
            self.change(release)
        self.assertEqual({r[0]:r[1] for r in self.store.db.execute('SELECT key,value FROM meta')},before)
        self.assertFalse(any(e['kind'] in ('EQUITY_REBASELINED','HALT_RELEASED') for e in self.store.events()))

    def test_release_and_rebaseline_audit_restart_and_running_effect(self):
        for release in (False,True):
            with self.subTest(release=release):
                old=self.prepare(release)
                self.engine.alert('manual_review')
                # A separate reason must not obscure the actual HALTED cause.
                if release:
                    self.store.set_meta('halt_reason','account_equity_drop')
                result=self.change(release)
                expected=self.data['equity_by_venue']
                self.assertEqual(self.store.meta('account_equity_baseline'),expected)
                self.assertEqual(self.store.meta('equity_baseline'),'30270')
                self.assertEqual(self.store.meta('daily_equity'),dict(day=day(self.now[0]),equity='30270'))
                events=[json.loads(e['value']) for e in self.store.events() if e['kind']==('HALT_RELEASED' if release else 'EQUITY_REBASELINED')]
                audit=events[-1]
                self.assertEqual(audit['old']['account_equity_baseline'],old)
                self.assertEqual(audit['new']['account_equity_baseline'],expected)
                self.assertEqual(audit['at'],self.now[0])
                self.assertEqual(audit['source'],'cli')
                self.assertEqual(audit['reason'],'verified_transfer')
                self.assertEqual(result['revision'],self.store.meta('equity_baseline_revision'))
                self.assertIn('manual_review',self.store.meta('stop_locks'))
                if release:
                    self.assertEqual(audit['halt_reason'],'account_equity_drop')
                    self.assertEqual(self.store.meta('state'),'RUNNING')
                    self.assertNotIn('account_equity_drop',self.store.meta('stop_locks'))
                self.assertFalse(self.engine.limits.check_equity(self.engine.account_cache.read()))
                with Store(self.tmp.name+'/exec.db') as reopened:
                    self.assertEqual(reopened.meta('account_equity_baseline'),expected)
                    self.assertEqual(reopened.meta('equity_baseline_revision'),result['revision'])

    def test_halted_confirmation_must_exactly_match_and_rebaseline_cannot_unhalt(self):
        self.prepare(True)
        for confirm in (True,False,'','wrong',' account_equity_drop'):
            with self.subTest(confirm=confirm), self.assertRaisesRegex(ValueError,'halt_confirmation'):
                self.engine.rebaseline(confirm=confirm,release_halt=True,source='cli',reason='transfer')
        with self.assertRaisesRegex(ValueError,'halted_requires_release'):
            self.change()
        self.store.set_meta('state','HALTING')
        self.assert_refused('not_halted',True)

    def test_every_local_unsettled_order_state_and_pending_accounting_refuses(self):
        self.prepare()
        for state,pending in (('INTENT',False),('SENT',False),('ACK',False),('UNKNOWN',False),('TERMINAL',True),('REJECTED',True)):
            with self.subTest(state=state,pending=pending):
                row=dict(intent(),state=state,filled='0',accounting_pending=pending)
                self.store.save_order(row)
                self.assert_refused('local_orders_pending')
                self.store.db.execute('DELETE FROM orders WHERE cid=?',(row['cid'],))

    def test_local_positions_even_closed_corrupt_pair_and_pending_memory_refuse(self):
        self.prepare()
        p=self.pair()
        self.assert_refused('local_positions')
        p['state']='CLOSED'
        self.store.pair(p)
        self.assert_refused('local_positions')

    def test_memory_observations_and_active_send_ownership_refuse(self):
        self.prepare()
        self.engine.executor.pending_writes['x']=(dict(intent(),state='UNKNOWN'),None)
        self.assert_refused('local_orders_pending')
        self.engine.executor.pending_writes.clear()
        self.engine.executor.owned['x']=1
        self.assert_refused('local_orders_pending')
        self.engine.executor.owned.clear()

    def test_each_remote_position_open_order_and_unknown_read_refuses(self):
        self.prepare()
        for n,venue in enumerate(self.venues):
            venue.quantity=dec('.001')
            self.assert_refused('remote_positions')
            venue.quantity=dec(0)
            for value in ([{'foreign':True}],None,{}):
                with self.subTest(venue=n,value=value):
                    venue.open_orders=lambda value=value:value
                    self.assert_refused('remote_orders')
            venue.open_orders=lambda:[]
        with patch.object(self.accounts,'snapshot',side_effect=OSError('unknown')):
            self.assert_refused('account_read_unavailable')

    def test_liability_stale_negative_or_missing_equity_refuse(self):
        self.prepare()
        self.data['liabilities']=[dict(coin='USDC',exchange_type='HYPERLIQUID',liability='28.5')]
        self.assert_refused('account_liability')
        self.data['liabilities']=[]
        for value in ('NaN','0','-1',None):
            self.data['equity_by_venue']['bitget']=value
            self.assert_refused('equity_invalid')
        self.data['equity_by_venue']['bitget']='517'
        def delayed():
            self.now[0]+=10.01
            return []
        self.venues[1].open_orders=delayed
        self.assert_refused('account_snapshot_stale')

    def test_position_related_locks_cannot_be_released(self):
        self.prepare(True)
        self.store.set_meta('halt_reason','account_equity_drop')
        for reason in ('position_mismatch','unhedged_position','repair_stopped','unknown_order','fills_pending'):
            with self.subTest(reason=reason):
                self.engine.stops.add(reason)
                self.assert_refused('position_or_order_lock',True)
                self.engine.stops.discard(reason)

    def test_audit_failure_rolls_back_every_baseline_state_and_memory(self):
        self.prepare(True)
        before={r[0]:r[1] for r in self.store.db.execute('SELECT key,value FROM meta')}
        locks=copy.deepcopy(self.engine.stops.reasons)
        original=self.store.event
        def failure(kind,*args,**kwargs):
            if kind=='HALT_RELEASED':
                raise OSError('disk failure')
            return original(kind,*args,**kwargs)
        with patch.object(self.store,'event',side_effect=failure), self.assertRaises(OSError):
            self.change(True)
        self.assertEqual({r[0]:r[1] for r in self.store.db.execute('SELECT key,value FROM meta')},before)
        self.assertEqual(self.engine.stops.reasons,locks)

    def test_old_inflight_refresh_cannot_apply_to_new_baseline(self):
        import threading
        self.prepare()
        entered,continue_read=threading.Event(),threading.Event()
        old=self.snapshot()
        old['equity_by_venue']=dict(crossex='1000',bitget='30000')
        def snapshot():
            if threading.current_thread().name=='old-account-read':
                entered.set()
                if not continue_read.wait(2):
                    raise TimeoutError('test')
                return old
            return self.snapshot()
        with patch.object(self.accounts,'snapshot',side_effect=snapshot):
            thread=threading.Thread(target=self.engine.refresh_accounts,name='old-account-read')
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.change()
            finally:
                continue_read.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertNotIn(self.store.meta('state'),('HALTING','HALTED'))
        self.assertEqual(self.engine.account_cache.read()['equity_by_venue'],self.data['equity_by_venue'])


class EquityHTTPTests(api_previous.previous.APIFixture, unittest.TestCase):
    start=api_previous.AdminTests.start
    request=api_previous.AdminTests.request
    write=api_previous.AdminTests.write

    def setUp(self):
        super().setUp()
        self.logs=[]
        self.real_server=api_previous.api._Server
        self.config['CROSSEX_EXEC_ADMIN_TOKEN']=api_previous.ADMIN

    def test_rebaseline_requires_admin_confirmation_and_runtime_handler(self):
        service=self.start()
        path='/api/v1/equity/rebaseline'
        self.assertEqual(write_route('POST',path),('equity','rebaseline'))
        self.assertEqual(self.write(service,{'confirm':True,'reason':'transfer'},path,'POST',api_previous.previous.TOKEN)[0],403)
        self.assertEqual(self.write(service,{'confirm':True,'reason':'transfer'},path,'POST','wrong')[0],401)
        self.assertEqual(self.write(service,{'confirm':True,'reason':'transfer'},path,'POST')[0],503)
        from unittest.mock import Mock
        handler=Mock(return_value={'revision':1})
        service.management.equity_admin=handler
        for body in ({},{'confirm':False},{'confirm':1},{'confirm':'true'}, {'confirm':True,'reason':''},
                     {'confirm':True,'reason':'x','extra':True}):
            self.assertEqual(self.write(service,body,path,'POST')[0],400)
        handler.assert_not_called()
        self.assertEqual(self.write(service,{'confirm':True,'reason':'transfer'},path,'POST'),(200,{'revision':1}))
        handler.assert_called_once_with(confirm=True,reason='transfer',source='admin_api')


class CLITests(unittest.TestCase):
    def test_commands_require_confirmation_and_dispatch_without_startup_or_trading(self):
        with patch.object(cli,'load_env',return_value={'CROSSEX_DB':'unused'}), patch.object(
                cli,'run_maintenance',create=True,return_value=0) as run:
            self.assertEqual(cli.main(['release-halt','--env-file','fake','--confirm','account_equity_drop']),0)
            self.assertEqual(run.call_args.args[1].confirm,'account_equity_drop')
            self.assertEqual(cli.main(['rebaseline','--env-file','fake','--confirm']),0)
            self.assertIs(run.call_args.args[1].confirm,True)
            for command in ('release-halt','rebaseline'):
                with self.assertRaises(SystemExit):
                    cli.main([command,'--env-file','fake'])
