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

from utils.synology_api import SynologyAPIClient, _scrub_secrets

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
    scrubbed = _scrub_secrets(_connection_error(url))

    assert "LIVE_SID_abc123DEF456" not in scrubbed
    assert "hunter2" not in scrubbed
    assert "123456" not in scrubbed


def test_scrub_secrets_keeps_the_diagnosis():
    """Masking must not turn a useful error into a useless one."""
    scrubbed = _scrub_secrets(
        _connection_error("/webapi/entry.cgi?method=info&_sid=LIVE_SID_abc123DEF456")
    )

    assert "_sid=***" in scrubbed
    assert "nas.example.com" in scrubbed
    assert "port=5000" in scrubbed
    assert "Max retries exceeded" in scrubbed


def test_network_failure_does_not_leak_the_sid_into_the_result(monkeypatch):
    """The end-to-end path: a real call to a dead port, through the handler."""
    client = SynologyAPIClient("http://127.0.0.1:9", LIVE_SID, syno_token="TOK")

    # Port 9 (discard) refuses immediately -- a deterministic connection error.
    result = client.get("SYNO.Core.System", "info")

    assert result["success"] is False
    assert result["error"]["code"] == "network_error"
    rendered = repr(result)
    assert LIVE_SID not in rendered
