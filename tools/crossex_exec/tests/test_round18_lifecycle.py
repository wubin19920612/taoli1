"""File-backed Engine lifecycle, real account adapters and safe CLI failures.

Only the remote I/O/public market and POSIX signal/lock plumbing are replaced.
No mock startup snapshot, in-memory DB, credentials, external sockets or orders.
"""
from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.crossex_exec import __main__ as cli
from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import Bitget, CrossEx
from tools.crossex_exec.engine import Engine
from tools.crossex_exec.storage import Store
from test_exec import intent, setUpModule, tearDownModule
from round18_fixtures import OfflineAccountHTTP, OPEN, POSITIONS, FINANCIAL


PRIVATE_TEXT = 'offline-message-body-and-chain-must-never-be-logged'


def runtime_failure(*args):
    raise RuntimeError(PRIVATE_TEXT)


class OfflineMarket:
    def __init__(self):
        self.mapping = {}
        self.sampled = threading.Event()
        self.closed = False

    def bootstrap(self, bases=()):
        return self.mapping

    def sample_quotes(self, minimum):
        self.sampled.set()
        return {}, ()

    def close(self):
        self.closed = True


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'engine.db'
        self.http = OfflineAccountHTTP()
        self.market = OfflineMarket()
        self.config = dict(CROSSEX_DB=str(self.path), CROSSEX_STOP_FILE=str(Path(self.tmp.name)/'STOP'),
                           BITGET_RISK_RATE_CONFIRMED='1')
        self.engines = []

    def stop_workers(self, engine):
        for worker in engine.workers:
            worker.stopped.set()
        for worker in engine.workers:
            worker.thread.join(2)
            self.assertFalse(worker.thread.is_alive())

    def test_real_startup_tick_shutdown_with_observed_empty_orders(self):
        with Store(self.path) as store:
            accounts = Accounts(CrossEx(self.http), Bitget(self.http), risk_confirmed=True)
            engine = Engine(store, self.market, accounts, config=self.config,
                            stop_file=self.config['CROSSEX_STOP_FILE'])
            try:
                engine.startup()
                self.assertTrue(self.market.sampled.wait(2))
                self.assertEqual(len(engine.workers), 3)
                # Let actual workers complete, then take a deterministic fresh
                # account observation through exactly the same production path.
                self.stop_workers(engine)
                engine.refresh_accounts()
                engine.tick()
                self.assertTrue(engine.limits.program_scope)
                self.assertEqual(engine.snapshot['positions'], {})
                self.assertTrue(engine.snapshot['activity']['complete'])
                self.assertTrue(store.meta('external_income')['verified'])
                self.assertTrue(store.meta('external_income')['funding_daily_verified'])
                self.assertGreater(engine.last_reconcile, 0)
                with patch.object(cli, 'emit') as emit:
                    self.assertEqual(cli.finish_shutdown(engine), 0)
                status = store.meta('flatten_status')
                self.assertTrue(status['verified'])
                self.assertTrue(status['program_flat'])
                self.assertTrue(status['account_flat'])
                emit.assert_called_once_with('shutdown_positions', **status)
                self.assertEqual(store.all_orders(), [])
                self.assertEqual(store.pairs(active=True), [])
                self.assertFalse(any(r['kind']=='BACKGROUND_FAILED' for r in store.events()))
            finally:
                engine.close()
                self.stop_workers(engine)
        paths = {p for _,p,_ in self.http.calls}
        self.assertIn(OPEN,paths)
        self.assertTrue({FINANCIAL,'/api/v4/crossex/account_book','/api/v4/crossex/history_trades'}.isdisjoint(paths))
        self.assertTrue(all(method=='GET' for method,_,_ in self.http.calls))
        with Store(self.path) as reopened:
            self.assertTrue(reopened.meta('flatten_status')['program_flat'])
            # POSITIONS is Bitget's history-position endpoint. Keep the empty
            # program assertions above, then exercise it through an owned,
            # confirmed zero-fill lifecycle (still no remote orders/sends).
            reopened.save_order(dict(intent('history-owner', ex='bitget'), leg=0,
                state='TERMINAL', filled='0', fee='0', vwap=None, fill_ms=None, accounting_pending=False))
            engine = Engine(reopened, self.market, accounts, config=self.config,
                            stop_file=self.config['CROSSEX_STOP_FILE'])
            try:
                engine.startup()
                self.stop_workers(engine)
                engine.refresh_accounts()
                engine.tick()
                self.assertTrue(reopened.meta('external_income')['verified'])
                with patch.object(cli, 'emit'):
                    self.assertEqual(cli.finish_shutdown(engine), 0)
                self.assertTrue(reopened.meta('flatten_status')['program_flat'])
            finally:
                engine.close()
                self.stop_workers(engine)
        paths = {p for _,p,_ in self.http.calls}
        self.assertIn(POSITIONS,paths)
        self.assertTrue(all(method=='GET' for method,_,_ in self.http.calls))

    @contextmanager
    def offline_cli(self, sleep=None):
        handlers = {}
        def construct(*args, **kwargs):
            engine = Engine(*args, **kwargs)
            self.engines.append(engine)
            return engine
        def stop_after_tick(seconds):
            self.assertEqual(seconds, .2)
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        fake_fcntl = SimpleNamespace(flock=lambda *args:None, LOCK_EX=1, LOCK_NB=2)
        clock = SimpleNamespace(time=time.time, sleep=sleep or stop_after_tick)
        with (patch.dict(sys.modules, {'fcntl':fake_fcntl}),
              patch.object(cli,'load_env',return_value=self.config),
              patch.object(cli,'Transport',side_effect=lambda *a,**kw:self.http),
              patch.object(cli,'MarketData',return_value=self.market),
              patch.object(cli,'Engine',side_effect=construct),
              patch.object(cli,'ShadowAPI') as source,
              patch.object(cli.signal,'signal',side_effect=lambda signum,handler:handlers.update({signum:handler})),
              patch.object(cli,'time',clock)):
            source.return_value.poll.return_value = []
            try:
                yield
            finally:
                for engine in self.engines:
                    self.stop_workers(engine)

    def run_cli(self):
        output = io.StringIO()
        with redirect_stdout(output):
            # The baseline has only main; errors used to be caught without
            # diagnostics in the module guard. New entrypoint is testable too.
            code = getattr(cli,'entrypoint',cli.main)(['run','--env-file','offline-unused'])
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_cli_real_lifecycle_tick_and_signal_shutdown(self):
        with self.offline_cli():
            code, logs = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.engines), 1)
        self.assertTrue(self.engines[0]._closed)
        self.assertGreater(self.engines[0].last_accounting_retry, 0)  # actual tick ran
        self.assertTrue(self.market.closed)
        self.assertEqual(len([r for r in logs if r['kind']=='shutdown_positions']), 1)
        self.assertFalse(any(r['kind'] in ('fatal','alert') for r in logs))
        self.assertTrue(all(method=='GET' for method,_,_ in self.http.calls))
        with Store(self.path) as store:
            self.assertTrue(store.meta('flatten_status')['program_flat'])
            self.assertEqual(store.all_orders(), [])

    def assert_fatal(self, code, logs, error_type, module, *, journal=True):
        self.assertEqual(code, 1)
        fatals = [r for r in logs if r['kind']=='fatal']
        self.assertEqual(len(fatals), 1)
        fatal = fatals[0]
        self.assertEqual(fatal['reason'], 'startup_or_runtime_failed_closed')
        self.assertEqual(fatal['error_type'], error_type)
        self.assertRegex(fatal['location'], '^'+module.replace('.',r'\.')+r':[1-9][0-9]*$')
        self.assertEqual(set(fatal), {'kind','at','reason','error_type','location'})
        self.assertNotIn(PRIVATE_TEXT, json.dumps(logs))
        self.assertNotIn(str(self.tmp.name), json.dumps(logs))
        if journal:
            with Store(self.path) as store:
                events = [json.loads(r['value']) for r in store.events() if r['kind']=='FATAL']
                self.assertEqual(events, [{k:fatal[k] for k in ('reason','error_type','location')}])
                self.assertEqual(store.all_orders(), [])
        return fatal

    def test_startup_invalid_orders_has_safe_fatal_log_and_durable_event(self):
        self.http.responses[OPEN] = dict(list=[], cursor={'private':PRIVATE_TEXT})
        with self.offline_cli():
            code, logs = self.run_cli()
        self.assert_fatal(code, logs, 'APIError', 'tools.crossex_exec.adapters')
        self.assertTrue(self.market.closed)
        self.assertTrue(self.engines[0]._closed)

    def test_runtime_fatal_reports_innermost_module_and_line(self):
        # Failure outside the recoverable tick catch must still exit nonzero.
        self.http.responses[OPEN] = dict(list=[], cursor='')
        self.http.responses[POSITIONS] = dict(list=[], cursor='')
        with self.offline_cli(sleep=runtime_failure):
            code, logs = self.run_cli()
        fatal = self.assert_fatal(code, logs, 'RuntimeError', __name__)
        self.assertEqual(fatal['location'], __name__+':'+str(runtime_failure.__code__.co_firstlineno+1))
        self.assertGreater(self.engines[0].last_accounting_retry, 0)
        self.assertTrue(self.market.closed)

    def test_failed_fatal_event_write_does_not_mask_original_failure(self):
        self.http.responses[OPEN] = dict(list=[], cursor='')
        self.http.responses[POSITIONS] = dict(list=[], cursor='')
        original = Store.event
        def event(store, kind, *args, **kwargs):
            if kind == 'FATAL':
                raise OSError(PRIVATE_TEXT)
            return original(store,kind,*args,**kwargs)
        with self.offline_cli(sleep=runtime_failure), patch.object(Store,'event',event):
            code, logs = self.run_cli()
        self.assert_fatal(code, logs, 'RuntimeError', __name__, journal=False)

    def test_pre_store_failure_still_has_safe_fatal_stdout(self):
        with self.offline_cli(), patch.object(cli,'load_env',side_effect=runtime_failure):
            code, logs = self.run_cli()
        self.assert_fatal(code, logs, 'RuntimeError', __name__, journal=False)
        self.assertFalse(self.path.exists())

    def test_exception_body_and_context_are_never_formatted(self):
        class UnprintableError(RuntimeError):
            def __str__(self):
                raise AssertionError('exception_text_accessed')
        def fail(*args):
            try:
                raise ValueError(PRIVATE_TEXT)
            except ValueError as cause:
                raise UnprintableError(PRIVATE_TEXT) from cause
        self.http.responses[OPEN] = dict(list=[], cursor='')
        self.http.responses[POSITIONS] = dict(list=[], cursor='')
        with self.offline_cli(sleep=fail):
            code, logs = self.run_cli()
        self.assert_fatal(code, logs, 'UnprintableError', __name__)


if __name__ == '__main__': unittest.main()
