"""2026-11-12 user-reported response SHAPES; every row/cursor here is synthetic.

No captured account data, credentials or remote requests. Only financial-records
has observed list=null/cursor=null evidence; other null lists remain invalid.
"""
import copy
import threading

OPEN = '/api/v3/trade/unfilled-orders'
FILLS = '/api/v3/trade/fills'
POSITIONS = '/api/v3/position/history-position'
FINANCIAL = '/api/v3/account/financial-records'
BITGET_PATHS = (OPEN, FILLS, POSITIONS, FINANCIAL)


def row(path, index):
    identity = str(index)
    if path == OPEN:
        return dict(orderId=identity, clientOid='offline-'+identity)
    if path == FILLS:
        return dict(execId=identity, orderId='offline-order', symbol='BTCUSDT',
                    execQty='1', execPrice='100', createdTime='1794441600000',
                    feeDetail=[dict(feeCoin='USDT', fee='.02')])
    if path == POSITIONS:
        return dict(positionId=identity)
    if path == FINANCIAL or path.endswith('/account_book'):
        return dict(id=identity)
    if path.endswith('/history_positions'):
        return dict(position_id=identity, update_time='1794441600000', closed_type='COMPLETE_CLOSED')
    return dict(transaction_id=identity, order_id='offline-order',
                symbol='BINANCE_FUTURE_BTC_USDT', qty='1', price='100',
                fee='.02', fee_coin='USDT', create_time='1794441600000')


OBSERVED = {
    OPEN: dict(list=[], cursor=None),
    FILLS: dict(list=[row(FILLS, n) for n in range(100)], cursor='offline-next'),
    POSITIONS: dict(list=[row(POSITIONS, n) for n in range(100)], cursor='offline-next'),
    FINANCIAL: dict(list=None, cursor=None),
}


class Pages:
    def __init__(self, pages):
        self.pages = iter(copy.deepcopy(pages))
        self.calls = []

    def request(self, method, path, params=None, **kwargs):
        if method != 'GET':
            raise AssertionError('offline_read_only')
        self.calls.append((path, copy.deepcopy(params)))
        return next(self.pages)


class OfflineAccountHTTP:
    """Wire-shaped accounts consumed by the real Accounts and venue adapters."""
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.responses = {
            '/api/v4/crossex/accounts': dict(position_mode='DUAL', account_mode='CROSS_EXCHANGE',
                margin_balance='1000', maintenance_margin='0', maintenance_margin_rate='0',
                assets=[dict(coin='USDT', exchange_type='BINANCE', balance='1000', liability='0')]),
            '/api/v4/crossex/positions': [],
            '/api/v4/crossex/open_orders': [],
            '/api/v4/crossex/account_book': [],
            '/api/v4/crossex/history_trades': [],
            '/api/v4/crossex/history_positions': [],
            '/api/v4/crossex/fee': [],
            '/api/v4/crossex/positions/leverage': {},
            '/api/v3/account/assets': dict(usdtEquity='1000', effEquity='1000', mmr='0',
                assets=[dict(coin='USDT', balance='1000', debt='0')]),
            '/api/v3/account/settings': dict(accountMode='unified', holdMode='hedge_mode',
                assetMode='multi_assets', symbolConfigList=[]),
            '/api/v3/position/current-position': dict(list=[]),
            OPEN: copy.deepcopy(OBSERVED[OPEN]),
            FINANCIAL: copy.deepcopy(OBSERVED[FINANCIAL]),
            POSITIONS: dict(list=[], cursor=None),
        }

    def request(self, method, path, params=None, **kwargs):
        if method != 'GET':
            raise AssertionError('offline_account_write_forbidden')
        with self.lock:
            self.calls.append((method, path, copy.deepcopy(params)))
            return copy.deepcopy(self.responses[path])
