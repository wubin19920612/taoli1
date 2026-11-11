"""Read-only startup validation and independent account risk measures."""
from tools.fill_sim.core import dec
from .policy import Instrument


def gate_margin(data,mode):
    if data['position_mode']!=mode or data['account_mode']!='CROSS_EXCHANGE' or data['exchange_type']!='CROSSEX':
        raise ValueError('crossex_account_mode_mismatch')
    balance,mm=dec(data['margin_balance']),dec(data['maintenance_margin'])
    if balance<=0 or mm<0:
        raise ValueError('crossex_equity_invalid')
    if not mm:
        return dec('1e30')
    ratio=balance/mm
    if abs(ratio-dec(data['maintenance_margin_rate']))>max(dec('.01'),ratio*dec('.001')):
        raise ValueError('crossex_margin_unit_mismatch')
    return ratio


def bitget_margin(data,mode,confirmed=False):
    if data['posMode']!=mode or data['marginMode']!='crossed' or data['marginCoin']!='USDT':
        raise ValueError('bitget_account_mode_mismatch')
    if dec(data['crossedMarginLeverage'])!=3:
        raise ValueError('bitget_leverage_mismatch')
    if dec(data['accountEquity'])<=0:
        raise ValueError('bitget_equity_invalid')
    # v2 only calls crossedRiskRate a "risk ratio". Inverse convention must be
    # checked against the user's classic account; never treat it as Gate's ratio.
    if not confirmed:
        return None
    risk=dec(data['crossedRiskRate'])
    if risk<0:
        raise ValueError('bitget_margin_invalid')
    return dec(1)/risk if risk else dec('1e30')


def remote_positions(gate_rows,bitget_rows):
    result={}
    for venue,rows in (('crossex',gate_rows),('bitget',bitget_rows)):
        if not isinstance(rows,list):
            raise ValueError('positions_unknown')
        for r in rows:
            quantity=dec(r['position_qty' if venue=='crossex' else 'total'])
            if not quantity:
                continue
            if venue=='crossex':
                side=r['position_side']
                if side=='NONE':
                    side='LONG' if quantity>0 else 'SHORT'
                if side not in ('LONG','SHORT'):
                    raise ValueError('position_direction_unknown')
                side=side.lower()
                if r.get('margin_mode','CROSS')!='CROSS':
                    raise ValueError('isolated_position')
            else:
                side=r['holdSide']
                if side not in ('long','short') or quantity<0 or r['marginMode']!='crossed':
                    raise ValueError('position_direction_unknown')
            key='|'.join((venue,r['symbol'],side))
            if key in result:
                raise ValueError('duplicate_remote_position')
            result[key]=abs(quantity)
    return result


def local_positions(pairs):
    result={}
    for p in pairs:
        for idx,raw in enumerate(p['instruments']):
            q=dec(p['remaining'][idx])
            if not q:
                continue
            i=Instrument.load(raw)
            venue='bitget' if i.market.exchange=='bitget' else 'crossex'
            key='|'.join((venue,i.symbol,'long' if idx==0 else 'short'))
            result[key]=result.get(key,dec(0))+q
    return result


class Accounts:
    def __init__(self,cross,bitget,*,risk_confirmed=False):
        self.cross,self.bitget,self.risk_confirmed=cross,bitget,risk_confirmed
        self.fees_by_market={}
        self.leverages={}

    def fees(self,instruments):
        cross=self.cross.fees()
        bitget=self.bitget.fees()
        mapped={}
        for i in instruments:
            try:
                if i.market.exchange=='bitget':
                    rows=[r for r in bitget if r['symbol']==i.symbol]
                    if len(rows)!=1:
                        continue
                    rate=rows[0]['takerFeeRate']
                else:
                    rows=[r for r in cross if r['exchange_type']==i.market.exchange.upper()]
                    if len(rows)!=1:
                        continue
                    specials=[r for r in rows[0]['special_fee_list'] if r['symbol']==i.symbol]
                    if len(specials)>1:
                        continue
                    rate=specials[0]['taker_fee_rate'] if specials else rows[0]['future_taker_fee']
                if 0<=dec(rate)<dec('.02'):
                    mapped[i.market.key]=str(rate)
            except (KeyError,TypeError,ValueError):
                continue
        self.fees_by_market=mapped
        self.leverages=self.cross.leverage([])
        return mapped

    def snapshot(self):
        a,b=self.cross.account(),self.bitget.account('BTCUSDT')
        margins=[gate_margin(a,self.cross.mode),bitget_margin(b,self.bitget.mode,self.risk_confirmed)]
        ga,ba=self.cross.positions(),self.bitget.positions()
        positions=remote_positions(ga,ba)
        upnl=sum((dec(r['upnl']) for r in ga),dec(0))+sum((dec(r['unrealizedPL']) for r in ba),dec(0))
        return dict(margins=[str(m) if m is not None else None for m in margins],positions=positions,upnl=str(upnl),
                    equity=str(dec(a['margin_balance'])+dec(b['accountEquity'])))

    def check_instruments(self,instruments):
        for i in instruments:
            if i.market.key not in self.fees_by_market:
                raise ValueError('actual_fee_missing')
            if i.market.exchange=='bitget':
                data=self.bitget.account(i.symbol)
                if bitget_margin(data,self.bitget.mode,self.risk_confirmed) is None:
                    raise ValueError('bitget_risk_units_unconfirmed')
            elif dec(self.leverages.get(i.symbol,0))!=3:
                raise ValueError('crossex_leverage_mismatch')
