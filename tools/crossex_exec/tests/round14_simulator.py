"""New tail episodes; original random sequence and all invariants stay intact."""
from tools.fill_sim.core import dec
from round11_simulator import SYMBOL, KEY


def step(world,index):
    n=index%24
    if world.external_scenario=='volatility' and n in (2,5,8,11):
        world.external_pnl=dec({2:'-750',5:'12000',8:'-600',11:'8000'}[n])
        world.coverage['external_violent_pnl']+=1
    if world.external_scenario=='capital' and n in (2,5,8,11):
        amount=dec({2:'5000',5:'-4800',8:'900',11:'-850'}[n])
        world.external_cash+=amount
        world.external_book.append(dict(id='capital-'+str(n),business_id='transfer-'+str(n),
            symbol='',exchange_type='HYPERLIQUID',coin='USDT',change=str(amount),
            statement_type='TRANSFER_IN' if amount>0 else 'TRANSFER_OUT',
            create_time=str(int(world.now*1000))))
        world.coverage['external_deposit' if amount>0 else 'external_withdrawal']+=1
    world.engine.reconcile()


def check(test,world,first_send):
    for sent in world.sends[first_send:]:
        test.assertNotEqual(sent['order']['instrument']['symbol'],SYMBOL,'I8 external sent')
    test.assertEqual(world.positions()['positions'].get(KEY,dec(0)),world.external_qty,'I8 external touched')
    n=world.step%24
    if 1<=n<21:
        test.assertNotIn(world.store.meta('state'),('HALTING','HALTED'),'I9 external/capital halt')
        test.assertTrue(world.engine.can_open(),'I9 external/capital stopped opening')
        loss=world.store.meta('account_loss_current')
        test.assertEqual(dec(loss['daily']),0,'I9 outside PnL counted as daily loss')
        test.assertEqual(dec(loss['total']),0,'I9 outside PnL counted as total loss')
        world.coverage['isolation_open_checks']+=1
    if world.store.meta('state')=='HALTED':
        test.assertTrue(world.store.meta('flatten_status')['program_flat'])
        test.assertFalse(world.store.meta('flatten_status')['account_flat'])
    world.coverage['round14_invariant_checks']+=1
