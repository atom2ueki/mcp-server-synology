"""synology_login must not echo session credentials into the tool result.

A SID and a SynoToken are live credentials for the length of the session.
The tool result goes into the MCP client's conversation transcript, which
outlives the session and travels wherever that transcript goes.
"""

import asyncio

# ---------------------------------------------------------------------------
# synology_login must not echo session material back to the MCP client
# ---------------------------------------------------------------------------


def _login_server(login_result):
    """A server whose auth instance returns `login_result` for any login."""
    from unittest.mock import MagicMock

    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    auth = MagicMock()
    auth.login.return_value = login_result
    server.auth_instances["https://nas.example.test:5001"] = auth
    return server


def test_login_output_omits_sid_synotoken_and_full_response():
    """The tool result goes into the client's transcript and stays there.

    A SID and a SynoToken are live credentials for the length of the session;
    writing them into a conversation history hands them to anyone who later
    reads it. Only a truncated SID is reported, matching what synology_logout
    already does.
    """
    server = _login_server(
        {
            "success": True,
            "data": {
                "sid": "SID_super_secret_value",
                "synotoken": "SYNOTOKEN_secret",
                "did": None,
            },
        }
    )

    result = asyncio.run(
        server._handle_login(
            {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "hunter2",
            }
        )
    )
    text = result[0].text

    assert "SID_super_secret_value" not in text
    assert "SYNOTOKEN_secret" not in text
    assert "hunter2" not in text
    # Still useful: enough SID to correlate with the server log.
    assert "SID_super_" in text
    assert "Successfully authenticated" in text


def test_bootstrap_login_surfaces_the_new_device_token():
    """The 2FA bootstrap is the one call that cannot work without it back.

    The tool description promises the token for exactly this call, and a
    legacy .env setup has no other way to obtain it.
    """
    server = _login_server(
        {"success": True, "data": {"sid": "SID_1", "did": "DID_trusted_device"}}
    )

    result = asyncio.run(
        server._handle_login(
            {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "pw",
                "otp_code": "123456",
            }
        )
    )

    assert "DID_trusted_device" in result[0].text


def test_bootstrap_login_finds_the_token_under_the_v7_field_name():
    """DSM v7 calls it `device_id`; only v6 and older call it `did`.

    The bootstrap prefers v6, but falls through to v7 when v6 fails, so both
    names have to be read. Verified on DSM 7.3.2-86009 Update 4.
    """
    server = _login_server(
        {"success": True, "data": {"sid": "SID_1", "device_id": "DEVICE_ID_v7"}}
    )

    result = asyncio.run(
        server._handle_login(
            {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "pw",
                "otp_code": "123456",
            }
        )
    )

    assert "DEVICE_ID_v7" in result[0].text


def test_ordinary_login_does_not_print_the_device_token():
    """DSM 7 returns a device token on EVERY login, 2FA or not.

    Printing it whenever DSM sends one would drop a long-lived credential
    into the transcript on every call, reinstating most of the leak.
    """
    server = _login_server(
        {
            "success": True,
            "data": {
                "sid": "SID_1",
                "synotoken": "TOK",
                "device_id": "DEVICE_ID_should_not_appear",
            },
        }
    )

    result = asyncio.run(
        server._handle_login(
            {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "pw",
            }
        )
    )

    assert "DEVICE_ID_should_not_appear" not in result[0].text


def test_returning_trusted_device_login_does_not_reprint_the_token():
    """A caller that already holds the token does not need it read back."""
    server = _login_server(
        {"success": True, "data": {"sid": "SID_1", "device_id": "DEVICE_ID_known"}}
    )

    result = asyncio.run(
        server._handle_login(
            {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "pw",
                "device_id": "DEVICE_ID_known",
            }
        )
    )

    assert "DEVICE_ID_known" not in result[0].text


def test_failed_login_reports_the_code_without_the_request_body():
    """A failure still has to be diagnosable: 400 and 403 want different actions."""
    server = _login_server({"success": False, "error": {"code": 403}})

    result = asyncio.run(
        server._handle_login(
            {
                "base_url": "https://nas.example.test:5001",
                "username": "alice",
                "password": "hunter2",
            }
        )
    )
    text = result[0].text

    assert "403" in text
    assert "hunter2" not in text


# ---------------------------------------------------------------------------
# The failure renderer must decode DSM's number AND keep any message that is
# written for the operator. A string code is one this codebase raised itself.
# ---------------------------------------------------------------------------


def test_redirect_refusal_message_reaches_the_caller():
    """`unexpected_redirect` carries the fix; dropping it wastes the diagnosis.

    SynologyAuth raises this one locally when auth.cgi answers 3xx, and its
    message tells the operator to point at the final https:// URL. Rendering it
    as a bare "DSM error code unexpected_redirect" both hides that and
    mislabels a local code as a DSM one.
    """
    from mcp_server import _login_failure_detail

    detail = _login_failure_detail(
        {
            "success": False,
            "error": {
                "code": "unexpected_redirect",
                "message": "returned HTTP 302 (a redirect), which was refused. "
                "Configure the final https:// URL directly.",
            },
        }
    )

    assert "Configure the final https:// URL directly." in detail
    assert "DSM error code" not in detail


def test_exhausted_login_explains_itself_instead_of_saying_unknown():
    """`unknown` is what every version failing collapses to -- say why.

    It is a network or wrong-host symptom far more often than a credential one,
    and a code table with no `unknown` entry sends the caller the wrong way.
    """
    from mcp_server import _login_failure_detail

    detail = _login_failure_detail(
        {"success": False, "error": {"code": "unknown", "message": "Authentication failed"}}
    )

    assert "none replied" in detail
    assert "reachable" in detail


def test_numeric_dsm_code_still_gets_the_table():
    """The decoding table is the useful part for a bare DSM number."""
    from mcp_server import _login_failure_detail

    for code in (400, "400", 403, 404):
        detail = _login_failure_detail({"success": False, "error": {"code": code}})
        assert "Common codes:" in detail
        assert "403 2FA required" in detail


def test_malformed_failure_body_does_not_raise():
    """Whatever DSM (or a proxy) returns, rendering it must not blow up."""
    from mcp_server import _login_failure_detail

    for payload in ({"success": False}, {"success": False, "error": None},
                    {"success": False, "error": "boom"}, "not-a-dict", None):
        assert isinstance(_login_failure_detail(payload), str)
