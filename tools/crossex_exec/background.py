"""Bounded daemon refreshers; opening workers only consume an immutable cache."""
import copy
import logging
import re
import threading
import time


def exception_location(error):
    """Innermost module/line only: no source, paths, locals or exception text."""
    trace = error.__traceback__
    if trace is None:
        return 'unknown:0'
    while trace.tb_next is not None:
        trace = trace.tb_next
    module = trace.tb_frame.f_globals.get('__name__', 'unknown')
    if not isinstance(module, str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,200}', module):
        module = 'unknown'
    return module+':'+str(trace.tb_lineno)


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
                location=exception_location(error)
                identity=(kind,location)
                now=time.monotonic()
                if now-self.last_errors.get(identity,float('-inf'))>=60:
                    self.last_errors[identity]=now
                    logging.getLogger(__name__).error('background_callback_failed worker=%s type=%s location=%s',self.name,kind,location)
                    if self.on_error:
                        try:
                            self.on_error(self.name,kind,location)
                        except Exception:
                            logging.getLogger(__name__).error('background_failure_journal_unavailable worker=%s',self.name)
            self.stopped.wait(max(.2,self.interval-(time.monotonic()-started)))

    def close(self):
        self.stopped.set()
        self.thread.join(.1)
