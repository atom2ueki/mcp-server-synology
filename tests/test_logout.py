"""Logout cache-eviction tests (regression for issue #47)."""

from unittest.mock import MagicMock

import pytest

BASE_URL = "http://nas.example.com:5000/"

# Every per-domain instance cache the server keeps keyed by base_url.
#
# DISCOVERED, not listed. This was a hardcoded tuple, and when iscsi_instances
# was added it was not added here - so the new cache was neither populated nor
# asserted, and dropping it from eviction would have left every test in this
# file green. That is the vacuous-check anti-pattern: a check whose scope
# excludes the thing it is meant to check always passes.
#
# Deriving the list from _service_instance_dicts() closes that, but only half of
# it: it cannot catch a cache that was never registered there in the first place.
# test_every_instance_cache_is_registered_for_eviction is the other half, and it
# deliberately does NOT use this function.
# Caches deliberately NOT evicted on logout, each with the reason. An exemption
# removes something from the check above, so it is named one at a time rather
# than widened into a pattern.
NOT_EVICTED_ON_LOGOUT = {
    # Holds the SynologyAuth for this base_url, which is also what
    # utils.synology_api._try_relogin looks up to recover an expired session.
    # Dropping it on logout would break transparent re-auth, and logging in
    # again reuses this instance rather than opening a second registration.
    "auth_instances",
}


def _instance_dict_attrs(server):
    registered = {id(d) for d in server._service_instance_dicts()}
    return tuple(
        attr
        for attr in vars(server)
        if attr.endswith("_instances") and id(getattr(server, attr)) in registered
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
    for attr in _instance_dict_attrs(server):
        getattr(server, attr)[BASE_URL] = object()

    return server


def _assert_fully_evicted(server):
    assert BASE_URL not in server.sessions
    assert BASE_URL not in server.syno_tokens
    for attr in _instance_dict_attrs(server):
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


def test_every_instance_cache_is_registered_for_eviction():
    """Every `*_instances` cache on the server must be in _service_instance_dicts().

    Deliberately built from `vars(server)` rather than from
    _service_instance_dicts(), because a cache that was never registered is
    exactly what the derived scope above cannot see. Adding a new service and
    forgetting this one line leaks a stale SID into the next session on that NAS.
    """
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    registered = {id(d) for d in server._service_instance_dicts()}
    unregistered = [
        attr
        for attr, value in vars(server).items()
        if attr.endswith("_instances")
        and isinstance(value, dict)
        and attr not in NOT_EVICTED_ON_LOGOUT
        and id(value) not in registered
    ]
    assert not unregistered, (
        f"instance cache(s) not evicted on logout: {unregistered}. "
        "Add them to SynologyMCPServer._service_instance_dicts()."
    )
