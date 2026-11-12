"""Offline real-engine account workload, no credentials or external sockets.

5 Hz coordinator, 5s fresh accounts, three external positions, 12,863 rules;
real adapters/pagination, SQLite, ownership and program income checks. Run from
the repo with PYTHONPATH=tools/crossex_exec/tests;. . JSON goes only to stdout.
"""
import argparse
import base64
import copy
import cProfile
import io
import hashlib
import json
import pstats
import time
import unittest
from types import SimpleNamespace

from tools.crossex_exec.accounts import Accounts
from tools.crossex_exec.adapters import CrossEx, Bitget
from tools.crossex_exec.benchmark_sampling import memory
from tools.crossex_exec.security import REDACTOR
from test_exec import setUpModule, tearDownModule
from test_round11_external import ExternalFixture, position, rule, SYMBOL


class Workload(ExternalFixture, unittest.TestCase):
    def prepare(self, rows):
        self.setUp()
        self.change()
        self.change(symbol='BINANCE_FUTURE_BTC_USDT')
        self.rows = [position(), position(side='SHORT'), position('BINANCE_FUTURE_BTC_USDT')]
        self.store.set_meta('external_catalog', dict(at=self.now, rules=[rule(SYMBOL),
            rule('BINANCE_FUTURE_BTC_USDT')] + [rule('BINANCE_FUTURE_COIN'+str(n)+'_USDT')
                                               for n in range(12861)]))
        self.anchor()
        self.now += 1
        self.book = [dict(id=str(n), business_id=str(n), statement_type='TRANSACTION',change='0',
            coin='USDT',symbol='BINANCE_FUTURE_BTC_USDT',exchange_type='BINANCE',
            create_time=str(int(self.now*1000))) for n in range(rows)]
        self.requests = 0
        self.returned = 0
        def response(method, path, params, **kw):
            self.requests += 1
            if path == '/api/v4/crossex/account_book':
                data = [r for r in self.book if params['from']<=int(r['create_time'])<=params['to']]
                page = data[(params['page']-1)*1000:params['page']*1000]
                self.returned += len(page)
                return copy.deepcopy(page)
            return [] if path.startswith('/api/v4/') else dict(list=[],cursor='')
        http = SimpleNamespace(request=response)
        self.reader = Accounts(CrossEx(http),Bitget(http))
        self.accounts.activity = self.reader.activity
        # Worker's publication is synchronous here; real cache and tick are used.
        self.engine.workers = [SimpleNamespace(stop=lambda:None)]
        self.engine.next_catalog = self.now+21600

    def refresh(self):
        self.engine.refresh_accounts()
        self.engine.reconcile()
        value = self.store.meta('external_income')
        if not value or not value['verified']:
            raise AssertionError(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=65)
    parser.add_argument('--rows', type=int, default=2000)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--signatures', type=int, default=0,
                        help='synthetic signing-ring entries, no authentication')
    args = parser.parse_args()
    setUpModule()
    world = Workload()
    try:
        world.prepare(args.rows)
        for n in range(args.signatures):
            token = hashlib.sha512(('offline-signature-'+str(n)).encode()).digest()
            REDACTOR.signature(token.hex() if n%2 else base64.b64encode(token[:32]).decode())
        world.refresh()
        base = world.now
        profile = cProfile.Profile()
        if args.profile: profile.enable()
        start, cpu = time.perf_counter(), time.process_time()
        next_refresh = 0
        ticks = samples = 0
        rss = []
        while time.perf_counter()-start < args.seconds:
            tick_start = time.perf_counter()
            elapsed = tick_start-start
            world.now = base+elapsed
            if elapsed >= next_refresh:
                world.refresh()
                next_refresh = elapsed+5
                samples += 1
            world.engine.tick()
            ticks += 1
            rss.append(memory()['rss_bytes'])
            time.sleep(max(0, .2-(time.perf_counter()-tick_start)))
        elapsed, cpu_seconds = time.perf_counter()-start, time.process_time()-cpu
        if args.profile: profile.disable()
        output = dict(seconds=elapsed,cpu_seconds=cpu_seconds,cpu_one_core_percent=cpu_seconds/elapsed*100,
            rss_bytes=memory()['rss_bytes'],rss_max_bytes=max(rss),ticks=ticks,account_samples=samples,
            history_requests=world.requests,rows_returned=world.returned,history_rows=args.rows,
            catalog_rules=12863,external_positions=3,program_scope=world.engine.limits.program_scope,
            synthetic_signatures=args.signatures,
            income_verified=world.store.meta('external_income')['verified'],memory=memory())
        if args.profile:
            stream = io.StringIO()
            pstats.Stats(profile,stream=stream).sort_stats('cumulative').print_stats(25)
            output['profile'] = stream.getvalue()
        print(json.dumps(output,indent=2))
    finally:
        world.doCleanups()
        tearDownModule()


if __name__ == '__main__': main()
