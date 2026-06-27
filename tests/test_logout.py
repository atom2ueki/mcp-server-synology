"""Logout cache-eviction tests (regression for issue #47)."""

from unittest.mock import MagicMock

import pytest

BASE_URL = "http://nas.example.com:5000/"

# Every per-domain instance cache the server keeps keyed by base_url.
INSTANCE_DICTS = (
    "filestation_instances",
    "downloadstation_instances",
    "health_instances",
    "container_instances",
    "nfs_instances",
    "usermgr_instances",
)


def _server_with_active_session(logout_result):
    """Build a server with one logged-in NAS and every instance cache populated."""
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    server.sessions[BASE_URL] = "sid_xyz"
    server.syno_tokens[BASE_URL] = "tok_abc"

    auth = MagicMock()
    auth.logout.return_value = logout_result
    server.auth_instances[BASE_URL] = auth

    # Populate each service cache with a sentinel so we can assert it gets evicted.
    for attr in INSTANCE_DICTS:
        getattr(server, attr)[BASE_URL] = object()

    return server


def _assert_fully_evicted(server):
    assert BASE_URL not in server.sessions
    assert BASE_URL not in server.syno_tokens
    for attr in INSTANCE_DICTS:
        assert BASE_URL not in getattr(server, attr), f"{attr} still holds the logged-out session"


@pytest.mark.asyncio
async def test_logout_clears_all_service_instance_caches():
    """A successful logout evicts every per-domain cache, not just file/download station."""
    server = _server_with_active_session({"success": True})

    await server._handle_logout({"base_url": BASE_URL})

    _assert_fully_evicted(server)


# DSM returns numeric session codes via response.json(); "no_session" is the
# one string code SynologyAuth.logout() emits itself. The handler must treat all
# of them as the graceful-cleanup path.
@pytest.mark.parametrize("error_code", [105, 106, "no_session"])
@pytest.mark.asyncio
async def test_expired_session_logout_clears_all_service_instance_caches(error_code):
    """The expired-session branch (105/106/no_session) also evicts every cache."""
    server = _server_with_active_session(
        {"success": False, "error": {"code": error_code, "message": "session expired"}}
    )

    await server._handle_logout({"base_url": BASE_URL})

    _assert_fully_evicted(server)
