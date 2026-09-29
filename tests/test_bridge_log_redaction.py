"""The bridge's DEBUG frame logs must not carry credentials.

LOG_LEVEL is a documented setting and ./logs is a mounted volume, so a
password logged at DEBUG outlives the call in a file nobody thinks of as
secret.
"""

import json
import logging
import re

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


# Name shapes whose VALUE is a credential. Kept as a module constant so the
# coverage test and the test that pins the pattern cannot disagree.
_CREDENTIAL_NAME_PATTERN = (
    r"(password|passwd|secret|token|credential|otp|passphrase"
    r"|api_key|private_key|recovery_code|(^|_)pin$|(^|_)(sid|did|device_id)$)"
)


def _credential_argument_names():
    """Every argument name across all tool schemas that looks like a secret."""
    found = set()
    for schema in _tool_schemas().values():
        input_schema = schema.input_schema or schema.inputSchema or {}
        props = input_schema.get("properties", {})
        for name in props:
            if name in _NON_SECRET_NAMES:
                continue
            # The value is the secret, not a flag about one. Covers the names in
            # use today (password, passwd, chap_password, otp_code, device_id)
            # plus the shapes a new credential field would plausibly take --
            # `device_id` in particular is a live field today and would not have
            # been caught by a narrower pattern, so the net is cast by meaning
            # (a secret, a key, a token, an ID issued by DSM) rather than by the
            # handful of names that happen to exist right now.
            if re.search(_CREDENTIAL_NAME_PATTERN, name, re.IGNORECASE):
                found.add(name)
    return found


def test_the_guard_pattern_catches_plausible_future_credential_names():
    """The coverage test is only as wide as its pattern -- pin the pattern.

    A narrow pattern is worse than no test: the comment above _REDACTED_KEYS
    promises that 'the next credential field fails the suite', and that promise
    is only true for names this regex understands.
    """
    for name in ("device_id", "did", "sid", "api_key", "recovery_code",
                 "passphrase", "private_key", "user_pin", "totp_secret",
                 "password", "chap_password", "otp_code"):
        assert re.search(_CREDENTIAL_NAME_PATTERN, name, re.IGNORECASE), name


def test_redacted_keys_cover_every_credential_field_in_the_tool_schemas():
    """A credential argument must never be logged verbatim."""
    from multiclient_bridge import _REDACTED_KEYS

    missing = _credential_argument_names() - set(_REDACTED_KEYS)

    assert not missing, (
        f"tool arguments {sorted(missing)} carry secrets but are not in "
        "_REDACTED_KEYS, so DEBUG logs would write them in the clear"
    )


def test_iscsi_chap_secret_is_masked():
    """The field that was missed: synology_target_create's chap_password."""
    from multiclient_bridge import _redact

    assert _redact({"chap_password": "SECRET"})["chap_password"] == "***"
