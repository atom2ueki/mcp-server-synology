# src/utils/synology_api.py - Shared API client for all Synology modules

import logging
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


def _try_relogin(
    base_url: str, stale_session_id: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Lookup the SynologyAuth registered for this URL and trigger a relogin.

    Used internally by SynologyAPIClient.request() to silently recover from
    DSM error 119 (SID expired). Returns (new_session_id, new_syno_token) on
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
        logger.warning("Cannot recover from DSM error 119 — auth module unavailable: %s", exc)
        return (None, None)
    auth = get_auth_for_url(base_url)
    if auth is None or not auth.relogin(stale_session_id):
        return (None, None)
    return (auth.current_session_id, auth.current_syno_token)


class SynologyAPIClient:
    """Shared API client for all Synology modules.

    Provides standardized API calls with timeout, SSL verification,
    and error handling across all Synology services.

    Transparently recovers from DSM error code 119 ("SID not found") — the
    error DSM returns when the server-side session has expired (typically
    after ~1h of inactivity for SYNO.Core.* APIs). When that happens, the
    client looks up the SynologyAuth instance registered for its base_url,
    triggers a relogin, refreshes its local SID/token, and retries the call
    once. If no auth is registered (e.g. in standalone tests), the 119 is
    returned to the caller unchanged.
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
    ) -> Dict[str, Any]:
        """Make an authenticated API call to /webapi/entry.cgi.

        Args:
            api: Synology API name (e.g., 'SYNO.Core.System')
            method: API method name (e.g., 'info')
            version: API version number
            extra_params: Additional parameters for the API call
            use_post: Use POST instead of GET

        Returns:
            Dict with API response or error information
        """
        result = self._do_request(api, method, version, extra_params, use_post)
        # DSM error 119 = "SID not found" → server-side session has expired.
        # Try a single transparent re-auth via the SynologyAuth registered for
        # this base_url. If it succeeds, refresh local SID/token and retry once.
        # If no auth is registered, return the original 119 to the caller.
        if not result.get("success") and result.get("error", {}).get("code") == 119:
            new_sid, new_token = _try_relogin(self.base_url, self.session_id)
            if new_sid:
                self.session_id = new_sid
                self.syno_token = new_token
                result = self._do_request(api, method, version, extra_params, use_post)
        return result

    def _do_request(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
        use_post: bool = False,
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
                    timeout=15,
                    verify=self.verify_ssl,
                )
            else:
                resp = requests.get(
                    self._api_url,
                    params=params,
                    headers=headers,
                    timeout=15,
                    verify=self.verify_ssl,
                )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            return {"success": False, "error": {"code": "network_error", "message": str(e)}}
        except Exception as e:
            return {"success": False, "error": {"code": "unknown_error", "message": str(e)}}

    def get(
        self, api: str, method: str, version: int = 1, extra_params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make a GET request to the API."""
        return self.request(api, method, version, extra_params, use_post=False)

    def post(
        self, api: str, method: str, version: int = 1, extra_params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make a POST request to the API."""
        return self.request(api, method, version, extra_params, use_post=True)
