# src/utils/synology_api.py - Shared API client for all Synology modules

import logging
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# DSM's published common error codes (SYNO.API "Common Error Codes" table in the
# Login Web API Guide). Surfacing the number alone is unactionable: a caller
# cannot tell 105 (no permission) from 106 (session timeout) from 119 (dead
# SID), and every one of those wants a different response.
DSM_COMMON_ERRORS: Dict[int, str] = {
    100: "Unknown error",
    101: "No parameter of API, method or version",
    102: "The requested API does not exist",
    103: "The requested method does not exist",
    104: "The requested version does not support the functionality",
    105: "The logged-in session does not have permission",
    106: "Session timeout",
    107: "Session interrupted by duplicated login",
    108: "Failed to upload the file",
    109: "The network connection is unstable or the system is busy",
    110: "The network connection is unstable or the system is busy",
    111: "The network connection is unstable or the system is busy",
    114: "Lost parameters for this API",
    115: "Not allowed to upload a file",
    116: "Not allowed to perform for a demo site",
    117: "The network connection is unstable or the system is busy",
    118: "The network connection is unstable or the system is busy",
    119: "Invalid session (SID not found)",
}

# SAN Manager (SYNO.Core.ISCSI.*) codes. Synology publishes no table for these,
# so this holds ONLY codes whose meaning was established by experiment against
# DSM 7.3.2-86009 Update 4 on 2026-09-08 — each one reproduced deliberately.
# An unmapped code still gets `api`/`method`/`version` attached, which is the
# part that makes it searchable; inventing a description would not.
DSM_ISCSI_ERRORS: Dict[int, str] = {
    # Returned by SYNO.Core.ISCSI.Target get/set/delete for an absent target AND
    # for a target_id that is not JSON-quoted. Both were reproduced: `target_id=1`
    # fails, `target_id="1"` (with the quote characters) succeeds on the same
    # target. See `_json_str` in iscsi/synology_iscsi.py.
    18990710: "iSCSI target not found, or target_id was not sent as a JSON-quoted string",
}

# Error codes meaning "your session is no longer usable, log in again".
#
# Only 119 was handled here originally, and that is why a DSM 106 surfaced to
# the caller as a bare `{"error": {"code": 106}}` with no recovery attempted.
# All three are session-lifecycle codes per the table above and all three are
# fixed by exactly the same action, so they share one recovery path.
SESSION_EXPIRED_CODES = frozenset({106, 107, 119})

# Seconds to wait for a DSM response. Fine for reads and for most writes.
#
# It is NOT enough for every write, and the failure is expensive rather than
# merely slow: a client-side timeout does not cancel the request DSM is already
# executing. On 2026-09-08 a SYNO.Core.ISCSI.LUN/create returned a read timeout
# to the caller and created the LUN anyway, leaving a LUN whose uuid nobody had
# -- invisible to the caller, and found only by listing. A caller doing a write
# that DSM performs slowly should raise this rather than retry, because a retry
# after a timeout can create a SECOND one.
DEFAULT_TIMEOUT = 15

# Provisioning a LUN allocates and initialises storage; on a busy volume it can
# take well over the default. Deleting one reclaims it and is likewise not
# instant.
LUN_WRITE_TIMEOUT = 120


def describe_error_code(code: Any) -> Optional[str]:
    """Human-readable description for a DSM error code, or None if unmapped."""
    if not isinstance(code, int):
        return None
    return DSM_ISCSI_ERRORS.get(code) or DSM_COMMON_ERRORS.get(code)


def annotate_error(
    result: Dict[str, Any], api: str, method: str, version: int
) -> Dict[str, Any]:
    """Attach the failing API/method/version, and a message, to a failed response.

    DSM answers a failure with nothing but a number. Which API produced it is
    known only here, at the call site, and it is the single most useful fact for
    acting on the error — so it is recorded on the way out rather than left for
    the caller to guess from context.

    Mutates and returns `result`. A successful response is returned untouched.
    """
    if result.get("success"):
        return result
    error = result.get("error")
    if not isinstance(error, dict):
        # A failure with no error object at all still gets the call recorded.
        error = {}
        result["error"] = error
    error["api"] = api
    error["method"] = method
    error["version"] = version
    if "message" not in error:
        description = describe_error_code(error.get("code"))
        if description:
            error["message"] = description
    return result


def _try_relogin(
    base_url: str, stale_session_id: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Lookup the SynologyAuth registered for this URL and trigger a relogin.

    Used internally by SynologyAPIClient.request() to silently recover from a
    DSM session-lifecycle error (see SESSION_EXPIRED_CODES). Returns
    (new_session_id, new_syno_token) on
    success, (None, None) if no auth instance is registered for this URL or
    if the relogin attempt fails. `stale_session_id` is the SID that just got
    the 119; it lets concurrent recoveries collapse into a single relogin.

    Import is deferred to runtime to avoid a circular import (auth → utils).
    """
    try:
        from auth.synology_auth import get_auth_for_url
    except ImportError as exc:
        # Deferred to dodge a circular import; a real failure here (e.g. a
        # syntax error introduced in synology_auth) would otherwise silently
        # leave the caller stuck on the 119. Log it so it's observable.
        logger.warning(
            "Cannot recover from an expired DSM session — auth module unavailable: %s", exc
        )
        return (None, None)
    auth = get_auth_for_url(base_url)
    if auth is None or not auth.relogin(stale_session_id):
        return (None, None)
    return (auth.current_session_id, auth.current_syno_token)


class SynologyAPIClient:
    """Shared API client for all Synology modules.

    Provides standardized API calls with timeout, SSL verification,
    and error handling across all Synology services.

    Transparently recovers from any of DSM's session-lifecycle errors — 106
    ("session timeout"), 107 ("session interrupted by duplicated login") and
    119 ("SID not found"). When one arrives, the client looks up the
    SynologyAuth instance registered for its base_url, triggers a relogin,
    refreshes its local SID/token, and retries the call once. If no auth is
    registered (e.g. in standalone tests), the error is returned unchanged.

    Every failed response is annotated with the api/method/version that
    produced it, plus a description of the code where one is known — see
    `annotate_error`.
    """

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
        self.syno_token = syno_token
        self._api_url = f"{self.base_url}/webapi/entry.cgi"

    def request(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
        use_post: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """Make an authenticated API call to /webapi/entry.cgi.

        Args:
            api: Synology API name (e.g., 'SYNO.Core.System')
            method: API method name (e.g., 'info')
            version: API version number
            extra_params: Additional parameters for the API call
            use_post: Use POST instead of GET
            timeout: Seconds to wait for a response. See DEFAULT_TIMEOUT.

        Returns:
            Dict with API response or error information
        """
        # Capture the SID this request is about to USE, before sending it. Read
        # back afterwards instead, and two concurrent calls on a shared client
        # race: A can refresh the client from S0 to S1 while B is still in
        # flight, and B -- which failed on S0 -- would then report S1 as the
        # stale one. SynologyAuth.relogin() dedupes on the SID it is told about,
        # so it would see a SID that is already current, decide no recovery is
        # needed, and open a second session anyway. This client is now shared
        # between SynologyHealth and its SynologyISCSI delegate, which makes
        # concurrent use ordinary rather than theoretical.
        attempted_sid = self.session_id
        result = self._do_request(api, method, version, extra_params, use_post, timeout)
        # A session-lifecycle error (106 timeout, 107 displaced by a duplicate
        # login, 119 SID not found) means the server-side session is gone. Try a
        # single transparent re-auth via the SynologyAuth registered for this
        # base_url. If it succeeds, refresh local SID/token and retry once. If no
        # auth is registered, the original error is returned to the caller.
        if not result.get("success") and result.get("error", {}).get("code") in SESSION_EXPIRED_CODES:
            new_sid, new_token = _try_relogin(self.base_url, attempted_sid)
            if new_sid:
                self.session_id = new_sid
                self.syno_token = new_token
                result = self._do_request(api, method, version, extra_params, use_post, timeout)
        return annotate_error(result, api, method, version)

    def _do_request(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
        use_post: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """Internal: perform the HTTP call without any retry logic."""
        params = {
            "api": api,
            "version": str(version),
            "method": method,
            "_sid": self.session_id,
        }
        if extra_params:
            params.update(extra_params)

        # DSM 7.3.2+ enforces CSRF on mutating endpoints via X-SYNO-TOKEN.
        # Harmless to send on reads and on older DSM (header is ignored there).
        headers = {"X-SYNO-TOKEN": self.syno_token} if self.syno_token else None

        try:
            if use_post:
                resp = requests.post(
                    self._api_url,
                    data=params,
                    headers=headers,
                    timeout=timeout,
                    verify=self.verify_ssl,
                )
            else:
                resp = requests.get(
                    self._api_url,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                    verify=self.verify_ssl,
                )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            return {"success": False, "error": {"code": "network_error", "message": str(e)}}
        except Exception as e:
            return {"success": False, "error": {"code": "unknown_error", "message": str(e)}}

    def get(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """Make a GET request to the API."""
        return self.request(api, method, version, extra_params, use_post=False, timeout=timeout)

    def post(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """Make a POST request to the API."""
        return self.request(api, method, version, extra_params, use_post=True, timeout=timeout)
