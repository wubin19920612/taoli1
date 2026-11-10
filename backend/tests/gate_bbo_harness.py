"""Spawn targets for offline tests; no live service or inherited monkeypatch required."""
import asyncio
import json
import socket
import time


def forbid_network():
    def blocked(*args, **kwargs):
        raise AssertionError("Gate offline child attempted external network")
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked


def fixture_child(incoming, outgoing):
    from app.exchanges.gate_bbo import GateBboWorker, child_entry

    # Set up asyncio's Windows loopback socketpair before prohibiting external I/O.
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    forbid_network()
    def guard(original):
        def connect(sock, address, *args, **kwargs):
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
                return original(sock, address, *args, **kwargs)
            raise AssertionError("Gate offline child attempted external network")
        return connect
    socket.socket.connect = guard(original_connect)
    socket.socket.connect_ex = guard(original_connect_ex)

    async def replay(self, kind):
        state = self.states[kind]
        state.update(connected=True, generation=1)
        update = 0
        while True:
            update += 1
            state["subscribed"] = len(self.subscriptions[kind])
            for symbol in self.subscriptions[kind]:
                self.handle_message(kind, json.dumps({
                    "channel": f"{'futures' if kind == 'future' else 'spot'}.book_ticker",
                    "event": "update", "result": {"t": int(time.time() * 1000), "u": update,
                        "s": symbol, "b": "100", "B": "2", "a": "101", "A": "3"},
                }))
            await asyncio.sleep(.02)
    GateBboWorker._connection = replay
    child_entry(incoming, outgoing)


def hung_child(incoming, outgoing):
    forbid_network()
    # Regression: no shutdown action may acquire a lock held by a suspended peer.
    with incoming._rlock:
        outgoing.put({"quotes": {}, "connections": {}, "cpu_seconds": 0})
        while True:
            time.sleep(1)
