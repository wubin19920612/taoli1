"""Daemon workers and one fair queue of individual public HTTP sends."""
from concurrent.futures import Future
import heapq
import queue
import threading

from .network import RateLimited


class DaemonPool:
    # No concurrent.futures executor atexit join: guard must not wait for DNS/IO.
    def __init__(self, max_workers=4, thread_name_prefix="public-depth"):
        self.queue = queue.Queue()
        self.closed = False
        self.threads = [threading.Thread(target=self._worker, name=f"{thread_name_prefix}-{i}",
                                        daemon=True) for i in range(max_workers)]
        for thread in self.threads:
            thread.start()

    def _worker(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            future, fn, args = item
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(*args))
                except BaseException as exc:
                    future.set_exception(exc)

    def submit(self, fn, *args):
        if self.closed:
            raise RuntimeError("pool_closed")
        future = Future()
        self.queue.put((future, fn, args))
        return future

    def shutdown(self, wait=True):
        self.closed = True
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                item[0].cancel()
        for _ in self.threads:
            self.queue.put(None)
        if wait:
            for thread in self.threads:
                thread.join()


class RequestFuture(Future):
    def __init__(self, now):
        super().__init__()
        self.queued_at = now
        self.sent_at = None
        self.local_wait = 0.0
        self.checked_at = now


class RequestQueue:
    """Priority 0 tracking; 1 opening (including dependencies); 2 warmup.

    At most three non-opening sends while an opening is queued. If its venue
    is temporarily full, retain that fourth slot until it can send. No quota
    is reserved by the engine or when submitting a thread-pool job.
    """
    def __init__(self, client, clock):
        self.client, self.clock = client, clock
        self.lock = threading.RLock()
        self.heap = []
        self.sequence = 0
        self.non_open_sends = 0
        self.send_count = 0
        self.workers = []
        self.closed = False

    def submit(self, ex, url, priority=1, convert=None):
        with self.lock:
            future = RequestFuture(self.clock())
            future.priority = priority
            if self.closed:
                future.cancel()
                return future
            self.sequence += 1
            heapq.heappush(self.heap, (priority, self.sequence, ex, url, future, convert))
            return future

    def promote(self, future, priority):
        with self.lock:
            future.priority = min(future.priority, priority)
            self.heap = [(min(p, priority) if f is future else p, n, ex, url, f, convert)
                         for p, n, ex, url, f, convert in self.heap]
            heapq.heapify(self.heap)

    def _account_wait(self):
        now = self.clock()
        for _, _, ex, _, future, _ in self.heap:
            paused_until = self.client.limiter.stats[ex]["paused_until"]
            future.local_wait += max(0, now - max(future.checked_at, paused_until))
            future.checked_at = now

    def _send_one(self):
        limiter = self.client.limiter
        with self.lock, limiter.lock:
            self._account_wait()
            self.heap = [item for item in self.heap if not item[4].cancelled()]
            heapq.heapify(self.heap)
            opening_due = any(item[0] == 1 for item in self.heap)
            require_open = opening_due and self.non_open_sends >= 3
            deferred, chosen = [], None
            while self.heap:
                item = heapq.heappop(self.heap)
                priority, _, ex, _, future, _ = item
                if limiter.stats[ex]["paused_until"] > self.clock():
                    future.set_exception(RateLimited("exchange_paused"))
                    continue
                if (not require_open or priority == 1) and limiter.available(ex):
                    if not future.set_running_or_notify_cancel():
                        continue
                    limiter.admit_send(ex)
                    chosen = item
                    break
                deferred.append(item)
            for item in deferred:
                heapq.heappush(self.heap, item)
            if chosen is None:
                return
            priority, _, ex, url, future, convert = chosen
            self.non_open_sends = 0 if priority == 1 else min(3, self.non_open_sends + 1)
            self.send_count += 1
            future.send_order = self.send_count
            future.sent_at = self.clock()
        try:
            data = self.client._perform(ex, url)
            future.set_result(convert(data) if convert else data)
        except BaseException as exc:
            future.set_exception(exc)

    def pump(self, executor):
        with self.lock:
            self._account_wait()
            self.workers = [f for f in self.workers if not f.done()]
            count = min(4 - len(self.workers), len(self.heap))
        # Do not hold lock while invoking an injected synchronous executor.
        for _ in range(count):
            self.workers.append(executor.submit(self._send_one))

    def close(self):
        with self.lock:
            self.closed = True
            for item in self.heap:
                item[4].cancel()
            self.heap.clear()
