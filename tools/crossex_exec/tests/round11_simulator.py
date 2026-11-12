"""Additional external episodes for the original invariant simulator.

Original random draws, fault schedule, assertions and the 168-round tail remain
unchanged. This adds exact-identity external positions to separate tail worlds.
"""
import copy
from tools.fill_sim.core import dec
from test_round11_external import identity, rule, position


SYMBOL='HYPERLIQUID_FUTURE_io:OAI_USDT'
KEY='crossex|'+SYMBOL+'|long'


def enable(world,scenario):
    world.external_scenario=scenario
    world.external_qty=dec(1)
    world.external_pnl=dec(0)
    world.external_cash=dec(0)
    world.external_book=[]
    world.external_trades=[]
    original_positions=world.positions
    original_snapshot=world.snapshot

    def positions():
        result=original_positions()
        raw={'crossex':[],'bitget':[]}
        for key,qty in result['positions'].items():
            venue,symbol,side=key.split('|')
            if venue=='crossex':
                raw[venue].append(position(symbol,str(qty),result['upnl_by_position'][key],side.upper()))
            else:
                raw[venue].append(dict(symbol=symbol,total=str(qty),posSide=side,marginMode='crossed'))
        if world.external_qty:
            result['positions'][KEY]=world.external_qty
            result['upnl_by_position'][KEY]=str(world.external_pnl)
            raw['crossex'].append(position(SYMBOL,str(world.external_qty),str(world.external_pnl)))
        result['upnl']=str(dec(result['upnl'])+world.external_pnl)
        result['raw_positions']=raw
        return result

    world.positions=positions

    def snapshot():
        result=original_snapshot()
        result['equity_by_venue']['crossex']=str(dec(result['equity_by_venue']['crossex'])+world.external_cash+world.external_pnl)
        result['equity']=str(sum(map(dec,result['equity_by_venue'].values()),dec(0)))
        result['raw_accounts']=dict(crossex={'assets':[
            dict(exchange_type='BINANCE',coin='USDT',balance=str(900+world.venues[0].realized)),
            dict(exchange_type='HYPERLIQUID',coin='USDT',balance=str(100+world.external_cash))]},
            bitget={'assets':[dict(coin='USDT',balance=str(30000+world.venues[1].realized))]})
        result['notionals']={KEY:str(world.external_qty*100)} if world.external_qty else {}
        if scenario=='margin' and world.step%24>=1:
            result['margins']=['1.5','4']
        return result

    def activity(start,end):
        def window(rows):
            return copy.deepcopy([r for r in rows if start<=int(r['create_time'])<=end])
        # World order fills have zero fees and deterministic realized PnL.
        # Supply matching settled cash records for the program's native contracts.
        book=window(world.external_book)
        bg=[]
        for order in world.store.iter_orders(exclude_states=('SHADOW','UNSENT')):
            if order.get('state')!='TERMINAL' or not order.get('reduce_only') or not dec(order.get('filled',0)):
                continue
            at=int(order.get('fill_ms') or 0)
            if not start<=at<=end:
                continue
            n=order['leg']
            value=dec(order['filled'])*(dec(order['vwap'])-world.entry_prices[n]*world.multipliers[n])*(1 if n==0 else -1)
            if n==0:
                book.append(dict(id=order['cid'],business_id=order['oid'],symbol=order['instrument']['symbol'],
                    exchange_type='BINANCE',statement_type='TRANSACTION',coin='USDT',change=str(value),create_time=str(at)))
            else:
                bg.append(dict(id=order['cid'],symbol=order['instrument']['symbol'],category='USDT-FUTURES',
                    coin='USDT',type='CLOSE_SHORT',amount=str(value),fee='0',ts=str(at)))
        return dict(from_ms=start,to_ms=end,complete=True,crossex_book=book,
            crossex_trades=window(world.external_trades),bitget_book=bg)

    world.snapshot=snapshot
    world.accounts.snapshot=snapshot
    world.accounts.validate_startup=snapshot
    world.accounts.position_snapshot=positions
    world.accounts.activity=activity
    for venue in world.venues:
        venue.open_orders=lambda:[]
    world.store.set_meta('external_catalog',dict(at=world.now,rules=[rule(SYMBOL)]))
    world.engine.external_admin(identity=identity(SYMBOL),registered=True,
        expected_status=world.engine.external.status(),reason='sim_external',source='cli')
    world.engine.rebaseline(confirm=True,reason='sim_external_epoch',source='cli')
    world.engine.reconcile()


def step(world,index):
    n=index%24
    scenario=world.external_scenario
    if scenario=='catastrophe' and n==2:
        world.external_pnl=dec(-250)
        world.coverage['external_catastrophe']+=1
    elif scenario=='settlement' and n==2:
        world.external_pnl=dec(-10)
        world.coverage['external_loss']+=1
    elif scenario=='settlement' and n==4:
        world.external_pnl=dec(0)
        world.external_cash=dec('-10.1')
        world.external_qty=dec(0)
        at=str(int(world.now*1000))
        world.external_trades=[dict(transaction_id='ext-trade',order_id='ext-order',symbol=SYMBOL,
            exchange_type='HYPERLIQUID',business_type='FUTURE',fee_coin='USDT',side='SELL',position_side='LONG',
            position_mode='DUAL',qty='1',price='90',rpnl='-10',fee='.1',create_time=at)]
        world.external_book=[dict(id=kind,business_id='ext-order',symbol=SYMBOL,exchange_type='HYPERLIQUID',
            coin='USDT',statement_type=kind,change=value,create_time=at)
            for kind,value in (('TRANSACTION','-10'),('TRADING_FEE','-.1'))]
        world.coverage['external_closed']+=1
    elif scenario=='catalog' and n==3:
        catalog=world.store.meta('external_catalog')
        catalog['rules'][0]['tick_size']='.1'
        world.store.set_meta('external_catalog',catalog)
        world.coverage['external_catalog_changed']+=1
    if scenario=='margin' and n==1:
        world.coverage['external_margin']+=1
    world.engine.reconcile()


def check(test,world,first_send):
    for sent in world.sends[first_send:]:
        test.assertNotEqual(sent['order']['instrument']['symbol'],SYMBOL,'I8 external contract sent')
    test.assertEqual(world.positions()['positions'].get(KEY,dec(0)),world.external_qty,'I8 external position touched')
    if world.store.meta('state')=='HALTED':
        status=world.store.meta('flatten_status')
        test.assertTrue(status['program_flat'],'I8 halted with program obligation')
        test.assertEqual(status['account_flat'],not bool(world.external_qty),'I8 falsely reported account flat')
    world.coverage['external_invariant_checks']+=1
