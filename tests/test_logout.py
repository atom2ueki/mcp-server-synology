"""Logout cache-eviction tests (regression for issue #47)."""

from unittest.mock import MagicMock

import pytest

# The canonical shape, and the ONLY one self.sessions ever holds: every path
# that stores a session key normalizes it first -- _login_nas and _handle_login
# both rstrip("/"), and _resync_session_after_relogin is handed
# SynologyAuth.base_url, which __init__ has already rstripped.
#
# This constant used to carry a trailing slash. _handle_logout resolves its
# argument through _get_base_url, which rstrips it, so the lookup went to
# "...:5000" while the fixture had seeded "...:5000/" -- the handler reported
# "no active session", evicted nothing, and every assertion below failed. The
# tests were describing a state the server cannot produce.
BASE_URL = "http://nas.example.com:5000"

# What a caller may actually type. _get_base_url normalizes it back to
# BASE_URL, and logout still has to find and evict the session -- so the
# trailing slash is now exercised as INPUT, which is the only place it can
# legitimately appear.
BASE_URL_TRAILING_SLASH = BASE_URL + "/"

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


@pytest.mark.asyncio
async def test_logout_accepts_a_trailing_slash_in_the_caller_url():
    """A caller-supplied trailing slash must still reach the stored session.

    Guards the LOOKUP side specifically: `_get_base_url` must keep rstripping
    the caller's argument, or it looks up a key the session was never stored
    under and logout silently evicts nothing. Verified by removing that rstrip
    -- this test fails and no other does.

    The storage side is not covered here: the fixture seeds
    `server.sessions[BASE_URL]` directly rather than going through
    `_login_nas`/`_handle_login`, so a regression in their normalization would
    leave this test green. Closing that needs a test that logs in first.
    """
    server = _server_with_active_session({"success": True})

    await server._handle_logout({"base_url": BASE_URL_TRAILING_SLASH})

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
