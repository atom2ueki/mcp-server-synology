"""A network error must not carry a live session id back to the caller.

Every DSM request carries `_sid=<live session id>` in its query string, and
requests/urllib3 embed the full request URL in the exception text they raise
("Max retries exceeded with url: ...&_sid=..."). Handlers pass that message
straight into a tool result, so an unreachable NAS -- the most ordinary failure
there is -- would otherwise write a live session credential into the MCP
client's conversation transcript.
"""

import pytest
import requests

from utils.synology_api import SynologyAPIClient, scrub_secrets

LIVE_SID = "LIVE_SID_abc123DEF456"


def _connection_error(url_with_sid):
    """The exact shape requests raises when the host is unreachable."""
    return requests.ConnectionError(
        f"HTTPConnectionPool(host='nas.example.com', port=5000): Max retries "
        f"exceeded with url: {url_with_sid} (Caused by NewConnectionError(...))"
    )


@pytest.mark.parametrize(
    "url",
    [
        "/webapi/entry.cgi?api=SYNO.Core.System&version=1&method=info&_sid=LIVE_SID_abc123DEF456",
        "/webapi/auth.cgi?api=SYNO.API.Auth&method=login&account=alice&passwd=hunter2",
        "/webapi/entry.cgi?_sid=LIVE_SID_abc123DEF456&sid=LIVE_SID_abc123DEF456",
        "/webapi/entry.cgi?otp_code=123456&session_id=LIVE_SID_abc123DEF456",
    ],
)
def test_scrub_secrets_masks_credentials_in_url_text(url):
    scrubbed = scrub_secrets(_connection_error(url))

    assert "LIVE_SID_abc123DEF456" not in scrubbed
    assert "hunter2" not in scrubbed
    assert "123456" not in scrubbed


def test_scrub_secrets_keeps_the_diagnosis():
    """Masking must not turn a useful error into a useless one."""
    scrubbed = scrub_secrets(
        _connection_error("/webapi/entry.cgi?method=info&_sid=LIVE_SID_abc123DEF456")
    )

    assert "_sid=***" in scrubbed
    assert "nas.example.com" in scrubbed
    assert "port=5000" in scrubbed
    assert "Max retries exceeded" in scrubbed


def test_network_failure_does_not_leak_the_sid_into_the_result():
    """The end-to-end path: a real call to a dead port, through the handler."""
    client = SynologyAPIClient("http://127.0.0.1:9", LIVE_SID, syno_token="TOK")

    # Port 9 (discard) refuses immediately -- a deterministic connection error.
    result = client.get("SYNO.Core.System", "info")

    assert result["success"] is False
    assert result["error"]["code"] == "network_error"
    rendered = repr(result)
    assert LIVE_SID not in rendered


def test_auth_logout_error_message_is_scrubbed(monkeypatch):
    """auth.synology_auth builds its own messages; they must be scrubbed too.

    It does not go through SynologyAPIClient, so the scrubbing there never saw
    it. With a GET-era URL the message carries a live `_sid`, and
    _handle_logout writes both `error.message` and the whole error dict into
    the tool result -- i.e. into the client's transcript.
    """
    import requests

    from auth.synology_auth import SynologyAuth

    leaky = requests.ConnectionError(
        "HTTPConnectionPool(host='nas.example.com', port=5000): Max retries "
        "exceeded with url: /webapi/auth.cgi?api=SYNO.API.Auth&method=logout"
        f"&_sid={LIVE_SID} (Caused by NewConnectionError(...))"
    )

    def _boom(*args, **kwargs):
        raise leaky

    import auth.synology_auth as mod

    monkeypatch.setattr(mod.requests, "post", _boom)
    monkeypatch.setattr(mod.requests, "get", _boom)

    auth = SynologyAuth("http://nas.example.com:5000")
    auth.current_session_id = LIVE_SID
    result = auth.logout()

    rendered = repr(result)
    assert result["success"] is False
    assert LIVE_SID not in rendered, "live SID reached the logout error message"
    assert "_sid=***" in result["error"]["message"]
