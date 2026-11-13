"""Read-only proofs for transient opening locks; never send or rewrite fills."""
from tools.fill_sim.core import dec, walk
from .policy import Instrument
from .program_risk import journal_fingerprint, require_program_proof
from .ownership import journal_positions


READ_LOCKS = frozenset(('funding_settlement_accounting_required', 'funding_refresh_failed',
    'negative_local_position', 'unknown_exposure_unavailable', 'close_incomplete',
    'flatten_retry_pending', 'flatten_snapshot_unavailable', 'exit_pricing_unavailable',
    'halting_startup_account_unavailable', 'foreign_bitget_unfilled_orders', 'cycle_failed_closed'))


def recover_observed_stops(engine, snapshot, income):
    """Called only after a fresh, complete, matching journal/account evaluation."""
    store=engine.store
    reasons=READ_LOCKS.intersection(engine.stops.reasons)
    if not reasons:
        return
    journal=journal_fingerprint(store)
    pairs=list(store.iter_pairs(exclude_states=('SHADOW','SKIPPED')))
    active=[p for p in pairs if p['state']!='CLOSED']
    owned=journal_positions(store)  # a terminal over-close is still an error
    ready={reason:'EXECUTION_OBSERVATION_RECOVERED' for reason in
           reasons.intersection(('negative_local_position','unknown_exposure_unavailable','cycle_failed_closed'))}
    closing=[p for p in pairs if p.get('exit_reason')]
    if 'close_incomplete' in reasons and (not active or closing and all(p['state']=='CLOSED' for p in closing)):
        ready['close_incomplete']='CLOSE_OBSERVATION_RECOVERED'
    if not active and not owned:
        ready.update({reason:'CLOSE_OBSERVATION_RECOVERED' for reason in
                      reasons.intersection(('flatten_retry_pending','flatten_snapshot_unavailable'))})
    if ('funding_settlement_accounting_required' in reasons and income
            and income.get('funding_verified') and income.get('funding_daily_verified')
            and all(p.get('closed_at',0)<=snapshot.get('accounting_at',snapshot['captured_at'])
                    for p in pairs if p.get('funding_known') is False)):
        ready['funding_settlement_accounting_required']='FUNDING_SETTLEMENT_RECOVERED'
    if 'funding_refresh_failed' in reasons:
        try:
            # Closed pairs are intentional: track() will never visit them again.
            targets=[p for p in pairs if p.get('exit_reason')=='funding_unknown'] or pairs
            for pair in targets:
                instruments=[Instrument.load(i) for i in pair['instruments']]
                fresh=engine.market.current_funding(instruments)
                if len(fresh)!=len(instruments):
                    raise ValueError('funding_refresh_incomplete')
                for row in fresh:
                    if (not 0<=engine.clock()-row['captured_at']<=10
                            or row['next']<=engine.clock() or dec(row['interval'])<=0):
                        raise ValueError('funding_refresh_stale')
                    dec(row['rate'])
            ready['funding_refresh_failed']='FUNDING_REFRESH_RECOVERED'
        except Exception:
            pass  # an unsuccessful retry cannot manufacture recovery
    if 'exit_pricing_unavailable' in reasons:
        try:
            for pair in active:
                instruments=[Instrument.load(i) for i in pair['instruments']]
                books=engine.market.books(instruments)
                if len(books)!=len(instruments):
                    raise ValueError('exit_books_incomplete')
                for n,(instrument,book) in enumerate(zip(instruments,books)):
                    if book is None or not 0<=engine.clock()-book.received_at<=1:
                        raise ValueError('exit_book_stale')
                    quantity=engine.quantities(pair)[n]
                    if quantity:
                        common=instrument.common_book(book)
                        walk(common.bids if n==0 else common.asks,quantity=quantity)
            ready['exit_pricing_unavailable']='EXIT_PRICING_RECOVERED'
        except Exception:
            pass
    if 'halting_startup_account_unavailable' in reasons:
        try:
            engine.accounts.validate_startup()
            ready['halting_startup_account_unavailable']='STARTUP_ACCOUNT_RECOVERED'
        except Exception:
            pass
    if 'foreign_bitget_unfilled_orders' in reasons:
        try:
            if engine.accounts.bitget.open_orders()==[]:
                ready['foreign_bitget_unfilled_orders']='FOREIGN_ORDERS_RECOVERED'
        except Exception:
            pass
    with store.lock:
        if journal!=journal_fingerprint(store):
            return
        # These reads prove their own failures gone. A slow successful retry
        # may expire PnL; leaving its lock would force the same slow read forever.
        # Releasing only these reasons never renews or bypasses the PnL proof.
        for reason in ('funding_refresh_failed','halting_startup_account_unavailable','foreign_bitget_unfilled_orders'):
            if reason in ready:
                engine.stops.recovered(reason,ready.pop(reason),dict(reason=reason,completed_at=engine.clock()))
        # Network retries may outlive the original proof or race a fresh read.
        if (not 0<=engine.clock()-snapshot['captured_at']<=10
                or snapshot['snapshot_version']!=store.meta('account_snapshot_version')
                or store.meta('account_snapshot_error') or journal!=journal_fingerprint(store)):
            return
        if engine.limits.program_scope:
            require_program_proof(store,snapshot,engine.clock())
        for reason,event in ready.items():
            engine.stops.recovered(reason,event,dict(reason=reason,captured_at=snapshot['captured_at'],
                snapshot_version=snapshot['snapshot_version']))
