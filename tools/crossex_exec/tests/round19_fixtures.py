"""Observed R19 response shapes; all IDs, balances and executions are synthetic."""
from tools.fill_sim.core import dec
from round15_simulator import ProgramWorld
from test_round11_external import ExternalFixture, SYMBOL, position
from test_round12_fx import quotes


BINANCE = 'BINANCE_FUTURE_OPENAI_USDT'
CONVERT = 'HYPERLIQUID_CONVERT_USDC_USDT'


class ObservedFixture(ExternalFixture):
    def setUp(self):
        self.independent_funding = True
        self.balances = {('CROSSEX', 'USDT'): dec(1000),
                         ('HYPERLIQUID', 'USDC'): dec(1000),
                         ('LIGHTER', 'USDC'): dec(100)}
        super().setUp()
        self.market.spot_fx = lambda coins: {coin: quotes(self.now, coin=coin) for coin in coins}
        self.now += 100
        self.catalog((BINANCE, SYMBOL))
        self.change(symbol=BINANCE)
        self.change(symbol=SYMBOL)
        self.rows = [dict(position(symbol, side=side), position_id=str(n),
                          create_time=str(int(self.now*1000)-1000),
                          funding_fee='0', funding_time='0')
                     for n, (symbol, side) in enumerate(((BINANCE, 'LONG'),
                                                        (SYMBOL, 'LONG'), (SYMBOL, 'SHORT')))]
        self.anchor()
        self.now += 1

    def snapshot(self):
        result = super().snapshot()
        result['raw_accounts']['crossex']['assets'] = [
            dict(exchange_type=exchange, coin=coin, balance=str(value))
            for (exchange, coin), value in self.balances.items()]
        return result

    def cash(self, symbol, kind, value, exchange, coin, business_id=None):
        identity = 'cash-'+str(len(self.book))
        row = dict(id=identity, business_id=business_id or identity, symbol=symbol,
                   statement_type=kind, change=str(value), exchange_type=exchange,
                   coin=coin, create_time=str(int(self.now*1000)))
        self.book.append(row)
        self.balances[(exchange, coin)] += dec(value)
        return row

    def settle(self, symbol, value):
        row = next(r for r in self.rows if r['symbol'] == symbol)
        row['funding_fee'] = str(dec(row['funding_fee'])+dec(value))
        row['funding_time'] = str(int(self.now*1000))
        exchange, coin = ('CROSSEX', 'USDT') if symbol == BINANCE else ('HYPERLIQUID', 'USDC')
        return self.cash(symbol, 'FUNDING_FEE', value, exchange, coin)

    def trade(self, symbol, *, fee='.1', pnl='0'):
        exchange, coin = ('BINANCE', 'USDT') if symbol == BINANCE else ('HYPERLIQUID', 'USDC')
        oid = 'order-'+str(len(self.trades))
        self.trades.append(dict(transaction_id=oid, order_id=oid, symbol=symbol,
            exchange_type=exchange, business_type='FUTURE', fee_coin=coin, side='BUY',
            position_mode='DUAL', position_side='LONG', qty='1', price='100', rpnl=pnl,
            fee=fee, create_time=str(int(self.now*1000))))
        cash_exchange = 'CROSSEX' if coin == 'USDT' else exchange
        self.cash(symbol, 'TRANSACTION', pnl, cash_exchange, coin, oid)
        self.cash(symbol, 'TRADING_FEE', -dec(fee), cash_exchange, coin, oid)
        row = next(r for r in self.rows if r['symbol'] == symbol and r['position_side'] == 'LONG')
        row['position_qty'] = str(dec(row['position_qty'])+1)

    def convert(self):
        self.cash(CONVERT, 'TRANSACTION', '-10', 'HYPERLIQUID', 'USDC', 'conversion')
        self.cash(CONVERT, 'TRANSACTION', '9.99', 'CROSSEX', 'USDT', 'conversion')
        self.trades.append(dict(transaction_id='conversion', order_id='conversion', symbol=CONVERT,
            exchange_type='HYPERLIQUID', business_type='CONVERT', fee_coin='', position_side='',
            side='SELL', qty='10', price='.999', fee='0', rpnl='0', create_time=str(int(self.now*1000))))

    def prove(self):
        self.now += 1
        # R21 keeps the R19 cash parser as an optional diagnostic. The engine's
        # actual opening proof is separately exercised by ObservedProgramWorld.
        from legacy_cash_diagnostics import prove
        return prove(self)

    def anchor(self):
        result=super().anchor()
        from legacy_cash_diagnostics import anchor
        anchor(self)
        return result


class ObservedProgramWorld(ProgramWorld):
    def snapshot(self):
        result = super().snapshot()
        for row in result.get('raw_accounts', {}).get('crossex', {}).get('assets', []):
            row['exchange_type'] = 'CROSSEX'
        return result

    def activity(self, start, end):
        result = super().activity(start, end)
        for row in result['crossex_book']:
            row['exchange_type'] = 'CROSSEX'
        return result
