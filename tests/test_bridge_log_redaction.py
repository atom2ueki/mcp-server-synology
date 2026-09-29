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


# ---------------------------------------------------------------------------
# _REDACTED_KEYS is an allowlist of exact names, so it silently rots: a tool
# that declares a new credential field keeps logging it verbatim and nothing
# fails. These tests read the real tool schemas, so adding such a field without
# adding its name here is a test failure rather than a silent leak.
# ---------------------------------------------------------------------------

# Field names that carry a secret but are not themselves credentials.
_NON_SECRET_NAMES = {
    "password_never_expire",
    "passwd_never_expire",
    "cannot_chg_passwd",
    "send_password",
}


def _tool_schemas():
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    return {name: tool for name, (tool, _handler) in server._tool_registry.items()}


def _credential_argument_names():
    """Every argument name across all tool schemas that looks like a secret."""
    import re

    found = set()
    for schema in _tool_schemas().values():
        input_schema = schema.input_schema or schema.inputSchema or {}
        props = input_schema.get("properties", {})
        for name in props:
            if name in _NON_SECRET_NAMES:
                continue
            # password / passwd / *_password / *_passwd / *_token / *_secret /
            # otp / sid -- i.e. the value is the secret, not a flag about one.
            if re.search(r"(password|passwd|secret|token|credential|otp|_sid$|sid$)",
                         name, re.IGNORECASE):
                found.add(name)
    return found


def test_redacted_keys_cover_every_credential_field_in_the_tool_schemas():
    """A credential argument must never be logged verbatim."""
    from multiclient_bridge import _REDACTED_KEYS

    missing = _credential_argument_names() - set(_REDACTED_KEYS)

    assert not missing, (
        f"tool arguments {sorted(missing)} carry secrets but are not in "
        "_REDACTED_KEYS, so DEBUG logs would write them in the clear"
    )


@pytest.mark.parametrize("field", ["chap_password", "new_password"])
def test_iscsi_and_user_credentials_are_masked(field):
    """The two that were missed: iSCSI CHAP and set-user's new password."""
    from multiclient_bridge import _redact

    assert _redact({field: "SECRET"})[field] == "***"
