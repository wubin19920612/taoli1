"""Bounded daemon refreshers; opening workers only consume an immutable cache."""
import copy
import logging
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
            return dict(copy.deepcopy(self.value),captured_at=self.at)


class PeriodicWorker:
    def __init__(self, callback, interval, name, on_error=None):
        self.callback,self.interval=callback,interval
        self.name,self.on_error=name,on_error
        self.last_errors={}
        self.stopped=threading.Event()
        self.thread=threading.Thread(target=self.run,name=name,daemon=True)
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            started=time.monotonic()
            try:
                self.callback()
            except Exception as error:
                kind=type(error).__name__
                now=time.monotonic()
                if now-self.last_errors.get(kind,float('-inf'))>=60:
                    self.last_errors[kind]=now
                    # Never log exception text, traceback, request or response.
                    logging.getLogger(__name__).error('background_callback_failed worker=%s type=%s',self.name,kind)
                    if self.on_error:
                        try:
                            self.on_error(self.name,kind)
                        except Exception:
                            logging.getLogger(__name__).error('background_failure_journal_unavailable worker=%s',self.name)
            self.stopped.wait(max(.2,self.interval-(time.monotonic()-started)))

    def close(self):
        self.stopped.set()
        self.thread.join(.1)
