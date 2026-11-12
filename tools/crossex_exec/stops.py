"""Independent, owner-scoped opening locks. stop_reason is only a UI projection."""


class StopLocks:
    def __init__(self, store):
        self.store = store
        # One journal-scoped lock protects persistence AND in-memory latches.
        # A second lock here would invert Store -> StopLocks in the send barrier.
        self.lock = store.lock
        saved = store.meta('stop_locks')
        pairs=store.pairs(active=True)
        self.reasons = {k:set(v) for k,v in (saved or {}).items()}
        if saved is None and store.meta('stop_reason'):
            legacy=store.meta('stop_reason')
            owners={p['id'] for p in pairs if p.get('repair_stopped')} if legacy in ('unhedged_position','repair_stopped') else set()
            self.reasons[legacy] = owners or {'global'}
        if store.meta('position_mismatch'):
            self.reasons.setdefault('position_mismatch', set()).add('global')
        for pair in pairs:
            if pair.get('repair_stopped'):
                for reason in ('repair_stopped', 'unhedged_position'):
                    self.reasons.setdefault(reason, set()).add(pair['id'])
        self.dirty = True
        self.flush()

    def flush(self, preferred=None):
        with self.lock:
            if not self.dirty:
                return
            try:
                self.store.set_meta('stop_locks', {k:sorted(v) for k,v in self.reasons.items()})
                current = self.store.meta('stop_reason')
                reason = preferred if preferred in self.reasons else current
                if reason not in self.reasons:
                    reason = next(iter(self.reasons), None)
                self.store.set_meta('stop_reason', reason)
                self.dirty = False
            except Exception:
                # The in-memory latch blocks admission even if persistence fails.
                self.dirty = True

    def add(self, reason, owner='global'):
        with self.lock:
            if owner in self.reasons.get(reason, ()):
                if self.store.meta('stop_reason')!=reason:
                    self.dirty=True
                self.flush(reason)
                return
            self.reasons.setdefault(reason, set()).add(owner)
            self.dirty = True
            self.flush(reason)

    def discard(self, reason, owner='global'):
        with self.lock:
            owners = self.reasons.get(reason, set())
            if owner not in owners:
                return
            owners.remove(owner)
            if not owners:
                self.reasons.pop(reason, None)
            self.dirty = True
            self.flush()

    def __bool__(self):
        with self.lock:
            return bool(self.reasons) or self.dirty

    def recovered(self, reason, event_kind, values):
        """A recovery event and its owner-scoped unlock commit together."""
        with self.lock:
            if 'global' not in self.reasons.get(reason,()):
                return
            before={k:set(v) for k,v in self.reasons.items()}
            try:
                with self.store.transaction():
                    self.store.event(event_kind,values)
                    self.discard(reason)
                    if self.dirty:
                        raise OSError('stop_lock_persistence_failed')
            except Exception:
                self.reasons=before
                self.dirty=True
                raise
