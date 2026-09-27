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
