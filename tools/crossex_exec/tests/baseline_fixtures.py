"""Synthetic public history; never bypass the production baseline gate."""
from tools.fill_sim.core import dec


def seed_baseline(store, now, bps='0', base='BTC', venues=('binance', 'bitget'), count=361, spacing=30):
    key = '|'.join((base, *sorted(venues)))
    rows = [(key, int((now-n*spacing)//30), now-n*spacing, str(dec(bps))) for n in range(count)]
    store.add_baseline_samples(rows, now)

