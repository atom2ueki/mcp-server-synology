"""The bridge's DEBUG frame logs must not carry credentials.

LOG_LEVEL is a documented setting and ./logs is a mounted volume, so a
password logged at DEBUG outlives the call in a file nobody thinks of as
secret.
"""

import json
import logging

import pytest


def test_redact_masks_credentials_at_any_depth():
    from multiclient_bridge import _redact

    frame = {
        "method": "tools/call",
        "params": {
            "name": "synology_login",
            "arguments": {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "hunter2",
                "otp_code": "123456",
                "device_id": "DID_abc",
            },
        },
    }

    masked = json.dumps(_redact(frame))

    assert "hunter2" not in masked
    assert "123456" not in masked
    assert "DID_abc" not in masked
    # Shape is preserved, so the log still shows what was called and that a
    # credential was present.
    assert "synology_login" in masked
    assert "alice" in masked
    assert masked.count("***") == 3


def test_redact_does_not_mutate_the_frame_it_was_given():
    """The redacted copy is for logging; the real frame still has to be usable."""
    from multiclient_bridge import _redact

    frame = {"params": {"arguments": {"password": "hunter2"}}}
    _redact(frame)

    assert frame["params"]["arguments"]["password"] == "hunter2"


def test_redact_masks_inside_lists():
    from multiclient_bridge import _redact

    frame = {"batch": [{"password": "a"}, {"token": "b"}]}
    masked = _redact(frame)

    assert masked["batch"] == [{"password": "***"}, {"token": "***"}]


@pytest.mark.asyncio
async def test_handle_message_debug_log_is_redacted(caplog):
    """End to end: the line that actually reaches the log file."""
    from multiclient_bridge import MCPBridge

    bridge = MCPBridge("wss://endpoint.test/mcp/", "tok")
    message = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "synology_login",
                "arguments": {"username": "alice", "password": "hunter2"},
            },
        }
    )

    with caplog.at_level(logging.DEBUG, logger="multiclient_bridge"):
        await bridge._handle_message(message, "XIAOZHI")

    assert caplog.text, "expected the DEBUG frame log to be emitted"
    assert "hunter2" not in caplog.text
