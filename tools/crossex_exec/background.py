"""Bounded daemon refreshers; opening workers only consume an immutable cache."""
import copy
import threading
import time


class SnapshotCache:
    def __init__(self, clock=time.time):
        self.clock=clock
        self.lock=threading.Lock()
        self.value=None
        self.at=None

    def publish(self, value, at=None):
        with self.lock:
            self.value=copy.deepcopy(value)
            self.at=self.clock() if at is None else at

    def read(self):
        with self.lock:
            if self.value is None or not 0<=self.clock()-self.at<=10:
                raise ValueError('account_snapshot_stale')
            return copy.deepcopy(self.value)


class PeriodicWorker:
    def __init__(self, callback, interval, name):
        self.callback,self.interval=callback,interval
        self.stopped=threading.Event()
        self.thread=threading.Thread(target=self.run,name=name,daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            started=time.monotonic()
            try:
                self.callback()
            except Exception:
                # The callback records a sanitized failure. Never kill the manager.
                pass
            self.stopped.wait(max(.2,self.interval-(time.monotonic()-started)))

    def close(self):
        self.stopped.set()
        self.thread.join(.1)
