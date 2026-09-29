"""The Xiaozhi reconnect loop must survive a disconnect."""

import asyncio

import pytest


class _FakeConnection:
    """An asyncio-style websockets connection: no legacy `.closed` attribute.

    websockets removed `.closed` when `connect()` moved to the asyncio
    implementation in v14; this project pins >=17.0.1, so the real object has
    not had it for several majors. Omitting it here is the whole point of the
    fixture -- with the old `not websocket.closed` guard this raises
    AttributeError from inside a `finally`, which escapes the reconnect loop.
    """

    def __init__(self, on_ping):
        self.close_calls = 0
        self._on_ping = on_ping

    async def ping(self):
        self._on_ping()

        async def _pong():
            return None

        return _pong()

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self, code=None, reason=None):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_xiaozhi_client_closes_connection_and_exits_cleanly(monkeypatch):
    """A disconnect must run cleanup and return, not raise out of the loop."""
    import multiclient_bridge as mod

    bridge = mod.MCPBridge("wss://endpoint.test/mcp/", "tok")
    connections = []

    async def _fake_connect(url, **kwargs):
        # Stop after this one iteration; the loop re-checks the event after
        # its cleanup runs, which is exactly the path under test.
        conn = _FakeConnection(on_ping=bridge.shutdown_event.set)
        connections.append(conn)
        return conn

    monkeypatch.setattr(mod.websockets, "connect", _fake_connect)

    await asyncio.wait_for(bridge._xiaozhi_client(), timeout=5)

    assert len(connections) == 1, "should not have reconnected after shutdown"
    assert connections[0].close_calls == 1, "connection was not closed on the way out"


@pytest.mark.asyncio
async def test_xiaozhi_client_reconnects_after_an_unrequested_disconnect(monkeypatch):
    """A drop the caller did not ask for must lead to a second connection.

    The other test shuts the bridge down on the first connection, so it never
    reaches the half of the loop that matters most -- the backoff, the
    per-iteration `websocket = None` reset, and the second connect. A
    regression in any of those would leave the loop looking healthy while
    never recovering, which is the failure the reconnect loop exists to
    prevent. Here the first connection drops on its own and only the SECOND
    one requests shutdown.
    """
    import multiclient_bridge as mod

    bridge = mod.MCPBridge("wss://endpoint.test/mcp/", "tok")
    connections = []

    async def _fake_connect(url, **kwargs):
        index = len(connections)
        # Only the second connection asks the loop to stop.
        on_ping = bridge.shutdown_event.set if index else (lambda: None)
        conn = _FakeConnection(on_ping=on_ping)
        connections.append(conn)
        return conn

    monkeypatch.setattr(mod.websockets, "connect", _fake_connect)

    # The loop backs off ~5s between attempts via
    # `wait_for(shutdown_event.wait(), timeout=reconnect_delay)`. Make that
    # wait return immediately so the test exercises the loop, not the delay.
    async def _immediate_shutdown_check(coro, timeout=None, *args, **kwargs):
        # Only the backoff's "wait, or proceed after the delay" call is
        # shortened. A real shutdown signal must still win the race, so let
        # the event win when it is already set and otherwise yield once.
        coro.close()
        if bridge.shutdown_event.is_set():
            return True
        await _ORIGINAL_SLEEP(0)
        raise asyncio.TimeoutError

    monkeypatch.setattr(mod.asyncio, "wait_for", _immediate_shutdown_check)
    # The real wait_for -- the patch above replaces the module attribute this
    # line would otherwise resolve to.
    await _ORIGINAL_WAIT_FOR(bridge._xiaozhi_client(), timeout=5)

    assert len(connections) == 2, "the loop did not reconnect after an unrequested drop"
    assert connections[0].close_calls == 1, "the dropped connection was not closed"
    assert connections[1].close_calls == 1, "the final connection was not closed on exit"


# Captured before any monkeypatching -- patching mod.asyncio.wait_for would
# otherwise make these recurse or time out against the stand-in.
_ORIGINAL_SLEEP = asyncio.sleep
_ORIGINAL_WAIT_FOR = asyncio.wait_for
