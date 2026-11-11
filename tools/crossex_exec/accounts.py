"""Read-only startup validation and independent account risk measures."""
import time
from dataclasses import dataclass
from tools.fill_sim.core import dec
from .policy import Instrument


def gate_margin(data,mode):
    if data['position_mode']!=mode or data['account_mode']!='CROSS_EXCHANGE' or data.get('exchange_type','CROSSEX')!='CROSSEX':
        raise ValueError('crossex_account_mode_mismatch')
    balance,mm=dec(data['margin_balance']),dec(data['maintenance_margin'])
    if mm<0:
        raise ValueError('crossex_equity_invalid')
    if not mm:
        return dec('1e30')
    ratio=balance/mm
    if abs(ratio-dec(data['maintenance_margin_rate']))>max(dec('.01'),ratio*dec('.001')):
        raise ValueError('crossex_margin_unit_mismatch')
    return dec(data['maintenance_margin_rate'])


def bitget_margin(data):
    # UTA account mmr is a USD amount (position mmr is a different field/unit).
    equity,maintenance=dec(data['effEquity']),dec(data['mmr'])
    if maintenance<0:
        raise ValueError('bitget_margin_invalid')
    return equity/maintenance if maintenance else dec('1e30')


def bitget_settings(data,mode):
    if data['accountMode']!='unified' or data['holdMode']!=mode or data['assetMode']!='multi_assets':
        raise ValueError('bitget_account_mode_mismatch')


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
                side=r['posSide']
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


@dataclass(frozen=True)
class FeeSnapshot:
    rate: str
    captured_at: float


class Accounts:
    def __init__(self,cross,bitget,*,risk_confirmed=False,clock=time.time):
        self.cross,self.bitget,self.risk_confirmed=cross,bitget,risk_confirmed
        self.clock=clock
        self.fee_snapshots={}
        self.leverages={}

    @property
    def fees_by_market(self):
        return {k:v.rate for k,v in self.fee_snapshots.items()}

    @property
    def fee_times_by_market(self):
        return {k:v.captured_at for k,v in self.fee_snapshots.items()}

    def fees(self,instruments):
        # A failed refresh must revoke stale rates, even if the caller catches it.
        instruments=tuple(instruments)
        allowed={i.market.key for i in instruments}
        self.fee_snapshots={k:v for k,v in self.fee_snapshots.items() if k in allowed}
        if not instruments:
            return {}
        cross_at=self.clock()
        try:
            cross=self.cross.fees()
        except Exception:
            cross=[]
        mapped={}
        for i in instruments:
            captured_at=self.clock() if i.market.exchange=='bitget' else cross_at
            try:
                if i.market.exchange=='bitget':
                    rate=self.bitget.fee(i.symbol)
                else:
                    rows=[r for r in cross if r['exchange_type']==i.market.exchange.upper()]
                    if len(rows)!=1:
                        raise ValueError('fee_market_missing')
                    specials=[r for r in rows[0].get('special_fee_list',[]) if r['symbol']==i.symbol]
                    if len(specials)>1:
                        raise ValueError('fee_market_ambiguous')
                    rate=specials[0]['taker_fee_rate'] if specials else rows[0]['future_taker_fee']
                if 0<=dec(rate)<dec('.02'):
                    mapped[i.market.key]=str(rate)
            except Exception:
                pass
            # Publish each contract independently; one slow/missing rate cannot
            # revoke already verified fees for a different contract.
            current=dict(self.fee_snapshots)
            current.pop(i.market.key,None)
            if i.market.key in mapped:
                current[i.market.key]=FeeSnapshot(mapped[i.market.key],captured_at)
            # One publication carries both fields; readers retain that version.
            self.fee_snapshots=current
        try:
            self.leverages=self.cross.leverage([])
        except Exception:
            self.leverages={}
        return mapped

    def validate_startup(self):
        # Empty positions never imply a mode. Failure propagates to abort startup.
        snapshot=self.snapshot()
        snapshot['open_orders']=self.bitget.open_orders()
        return snapshot

    def position_snapshot(self):
        # Recovery must not depend on balances, margin units, settings or fees.
        started=self.clock()
        gate,bitget=self.cross.positions(),self.bitget.positions()
        result=dict(positions=remote_positions(gate,bitget),captured_at=started)
        floating={}
        try:
            for rows,is_gate in ((gate,True),(bitget,False)):
                for row in rows:
                    keys=remote_positions([row],[]) if is_gate else remote_positions([],[row])
                    for key in keys:
                        floating[key]=str(dec(row['upnl' if is_gate else 'unrealisedPnl']))
        except (KeyError,ValueError,TypeError):
            return result  # Unknown PnL must not obstruct known-quantity reduction.
        return dict(result,upnl=str(sum(map(dec,floating.values()),dec(0))),upnl_by_position=floating)

    def snapshot(self):
        a,b=self.cross.account(),self.bitget.account()
        bitget_settings(self.bitget.settings(),self.bitget.mode)
        margins=[gate_margin(a,self.cross.mode),bitget_margin(b)]
        ga,ba=self.cross.positions(),self.bitget.positions()
        positions=remote_positions(ga,ba)
        upnl=sum((dec(r['upnl']) for r in ga),dec(0))+sum((dec(r['unrealisedPnl']) for r in ba),dec(0))
        notionals,upnl_by_position={},{}
        for venue,rows in (('crossex',ga),('bitget',ba)):
            for row in rows:
                normalized=remote_positions([row],[]) if venue=='crossex' else remote_positions([],[row])
                for key,qty in normalized.items():
                    price=dec(row['mark_price' if venue=='crossex' else 'markPrice'])
                    if price<=0:
                        raise ValueError('position_mark_missing')
                    notionals[key]=str(qty*price)
                    upnl_by_position[key]=str(dec(row['upnl' if venue=='crossex' else 'unrealisedPnl']))
        equities=dict(crossex=str(dec(a['margin_balance'])),bitget=str(dec(b['usdtEquity'])))
        liabilities=[{k:r[k] for k in ('coin','exchange_type','liability')} for r in a['assets'] if dec(r['liability'])>0]
        return dict(margins=[str(m) if m is not None else None for m in margins],positions=positions,upnl=str(upnl),
                    equity=str(sum(map(dec,equities.values()))),equity_by_venue=equities,
                    notionals=notionals,liabilities=liabilities,upnl_by_position=upnl_by_position)

    def check_instruments(self,instruments):
        for i in instruments:
            if i.market.key not in self.fees_by_market:
                raise ValueError('actual_fee_missing')
            if i.market.exchange=='bitget':
                data=self.bitget.settings()
                bitget_settings(data,self.bitget.mode)
                rows=[r for r in data['symbolConfigList'] if r['symbol']==i.symbol and r['category']=='USDT-FUTURES']
                if len(rows)!=1 or rows[0]['marginMode']!='crossed' or dec(rows[0]['leverage'])!=3:
                    raise ValueError('bitget_symbol_config_mismatch')
                if not self.risk_confirmed:
                    raise ValueError('bitget_risk_units_unconfirmed')
            elif dec(self.leverages.get(i.symbol,0))!=3:
                raise ValueError('crossex_leverage_mismatch')
