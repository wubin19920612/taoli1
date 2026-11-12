"""Offline, reproducible 2000-pair allocation probe; never starts the service."""
import copy
import gc
import json
import platform
import sqlite3
import sys
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_final_review import FinalExecutionTests
from tools.crossex_exec.api_data import JournalView
from tools.crossex_exec.storage import encode


def measure(name, action):
    gc.collect()
    tracemalloc.start()
    try:
        result = action()
        outcome = {'result': result}
    except Exception as error:
        outcome = {'error': type(error).__name__+':'+str(error)}
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps(dict(probe=name, peak_bytes=peak, peak_mib=round(peak/2**20, 4),
                          retained_bytes=current, **outcome), sort_keys=True))


def main():
    fixture = FinalExecutionTests(methodName='runTest')
    fixture.setUp()
    try:
        fixture.engine.live = fixture.engine.executor.live = False
        template = fixture.engine.ingest(fixture.trigger('memory'))
        template['last_books'] = [dict(b, bids=[[str(100+n/100), '10'] for n in range(50)],
                                      asks=[[str(101+n/100), '10'] for n in range(50)])
                                  for b in template['last_books']]
        orders = fixture.store.all_orders()
        with fixture.store.transaction():
            fixture.store.db.execute('DELETE FROM orders')
            fixture.store.db.execute('DELETE FROM pairs')
            for n in range(2000):
                pair = dict(template, id='history-'+str(n), trigger_id='history-'+str(n))
                fixture.store.db.execute('INSERT INTO pairs VALUES (?,?)', (pair['id'], encode(pair)))
                for leg, original in enumerate(orders):
                    order = dict(original, cid=f'history-{n}-{leg}', pair_id=pair['id'])
                    fixture.store.db.execute('INSERT INTO orders VALUES (?,?)', (order['cid'], encode(order)))
        print(json.dumps(dict(platform=platform.platform(), python=platform.python_version(),
                              sqlite=sqlite3.sqlite_version, pairs=2000, orders=4000,
                              levels_per_side_per_leg=50, metric='tracemalloc Python allocations, not Linux RSS')))
        view = JournalView(Path(fixture.tmp.name)/'exec.db', {}, clock=lambda:fixture.now[0])
        measure('active_pairs_0', lambda: len(fixture.store.pairs(active=True)))
        measure('executor_recover', lambda: fixture.engine.executor.recover(startup=False))
        measure('can_open', fixture.engine.can_open)
        measure('hard_exposure', lambda: str(fixture.engine.limits.exposure(fixture.snapshot(), orders[0])))
        measure('engine_totals', lambda: list(map(str, fixture.engine.totals())))
        measure('engine_tick', fixture.engine.tick)
        for endpoint in ('summary', 'positions', 'trades', 'stats'):
            measure('api_'+endpoint, lambda endpoint=endpoint: len(json.dumps(view.get(endpoint, {}))))
        # A second case verifies that selecting a few live records does not decode history.
        for n in range(3):
            fixture.store.pair(dict(copy.deepcopy(template), id='active-'+str(n), state='OPEN'))
        measure('active_pairs_3', lambda: len(fixture.store.pairs(active=True)))
        measure('api_positions_3', lambda: len(view.get('positions', {})))
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
