# src/synology_auth.py - Simple Synology authentication utilities

import logging
from typing import Any, Callable, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# Module-level registry of SynologyAuth instances, keyed by base_url.
# Used by SynologyAPIClient to perform transparent session re-auth when DSM
# returns error code 119 ("SID not found"), which happens when the server-side
# session expires (typically after ~1h of inactivity for SYNO.Core.* APIs).
# Without this, the client's SID stays dead until the process restarts.
_AUTH_REGISTRY: Dict[str, "SynologyAuth"] = {}


def get_auth_for_url(base_url: str) -> Optional["SynologyAuth"]:
    """Return the SynologyAuth instance registered for `base_url`, if any.

    Used by SynologyAPIClient.request() when it needs to recover from a
    DSM error 119 (SID expired) by silently re-authenticating.
    """
    return _AUTH_REGISTRY.get(base_url.rstrip("/"))


class SynologyAuth:
    """Handles Synology NAS authentication using simple API calls."""

    def __init__(self, base_url: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.current_session_id: Optional[str] = None
        # Mirrors what login_with_session uses by default; overwritten on every login.
        self.current_session_type: str = "webui"
        self.current_syno_token: Optional[str] = None
        # Credentials cached on a successful login, used by relogin() to recover
        # from DSM session expiry without requiring a process restart.
        self._credentials: Optional[Tuple[str, str]] = None
        # Optional callback invoked after a successful relogin() with
        # (base_url, session_id, syno_token). Lets the caller (e.g. mcp_server)
        # resync any cached session state with the refreshed SID.
        self.on_relogin: Optional[Callable[[str, Optional[str], Optional[str]], None]] = None
        # Register this instance for cross-module discovery (see _AUTH_REGISTRY).
        _AUTH_REGISTRY[self.base_url] = self

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate with Synology NAS and return session info.

        Defaults to `session=webui` — the same scope the DSM web UI uses.
        Per live testing on DSM 7.3.2, session type doesn't actually
        affect which APIs are reachable (account permissions do); we
        pick `webui` for semantic alignment with the official client.
        Use `login_with_session(...)` to pick a different session type.
        """
        return self.login_with_session(username, password, "webui")

    def login_with_session(
        self, username: str, password: str, session_type: str = "webui"
    ) -> Dict[str, Any]:
        """Authenticate with Synology NAS using specific session type."""
        login_url = f"{self.base_url}/webapi/auth.cgi"

        # Try common API versions (start with newer versions)
        api_versions = ["7", "6", "3", "2"]

        for version in api_versions:
            payload = {
                "api": "SYNO.API.Auth",
                "version": version,
                "method": "login",
                "account": username,
                "passwd": password,
                "session": session_type,
                "format": "sid",
                # DSM 7.3.2+ enforces CSRF on mutating endpoints; service
                # modules send the returned token as X-SYNO-TOKEN. Older DSM
                # ignores the flag (synotoken absent → header-less requests,
                # which still work pre-7.3.2).
                "enable_syno_token": "yes",
            }

            try:
                response = requests.get(login_url, params=payload, verify=self.verify_ssl)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    # Store session info for automatic logout
                    self.current_session_id = result["data"]["sid"]
                    self.current_session_type = session_type
                    self.current_syno_token = result["data"].get("synotoken")
                    # Cache credentials so relogin() can recover from session expiry.
                    self._credentials = (username, password)
                    return result
                else:
                    error_code = result.get("error", {}).get("code", "unknown")
                    # Don't try other versions for auth errors
                    if error_code in [400, 402, 403, 404]:
                        return result
            except Exception:
                continue

        # If all versions failed, return the last result
        return {"success": False, "error": {"code": "unknown", "message": "Authentication failed"}}

    def login_download_station(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate specifically for Download Station."""
        return self.login_with_session(username, password, "DownloadStation")

    def relogin(self) -> bool:
        """Re-authenticate using credentials cached from the last successful login.

        Returns True on success, False if no credentials are cached or login fails.
        Called by SynologyAPIClient when DSM returns error code 119 ("SID not
        found"), which happens when the server-side session expires (typically
        after ~1h of inactivity for SYNO.Core.* APIs). Without this, the client's
        SID stays dead until the process restarts.
        """
        if not self._credentials:
            return False
        username, password = self._credentials
        result = self.login_with_session(username, password, self.current_session_type)
        success = bool(result.get("success"))
        if success and self.on_relogin is not None:
            # Notify the caller so it can resync cached session state with the
            # new SID. A callback failure must never break the relogin path.
            try:
                self.on_relogin(self.base_url, self.current_session_id, self.current_syno_token)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("on_relogin callback failed for %s: %s", self.base_url, exc)
        return success

    def logout(
        self, session_id: Optional[str] = None, session_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Logout from Synology NAS.

        Args:
            session_id: Session ID to logout. If None, uses current session.
            session_type: Session type to logout. If None, uses current session type.

        Returns:
            Dict with success status and any error details.
        """
        # Use provided parameters or fall back to current session
        logout_session_id = session_id or self.current_session_id
        logout_session_type = session_type or self.current_session_type

        if not logout_session_id:
            return {
                "success": False,
                "error": {"code": "no_session", "message": "No session ID provided or available"},
            }

        logout_url = f"{self.base_url}/webapi/auth.cgi"

        # Try multiple API versions for logout (same approach as login)
        api_versions = ["7", "6", "3", "2"]
        last_error = None

        for version in api_versions:
            payload = {
                "api": "SYNO.API.Auth",
                "version": version,
                "method": "logout",
                "session": logout_session_type,
                "_sid": logout_session_id,
            }

            try:
                response = requests.get(logout_url, params=payload, verify=self.verify_ssl)
                response.raise_for_status()
                result = response.json()

                if result.get("success"):
                    # Clear current session if we logged out our own session
                    if logout_session_id == self.current_session_id:
                        self.current_session_id = None
                        self.current_session_type = "webui"
                        self.current_syno_token = None
                        # Drop cached credentials when the user explicitly logs out;
                        # otherwise a subsequent 119 would silently re-auth them
                        # against their explicit intent.
                        self._credentials = None
                    return result
                else:
                    last_error = result
                    error_code = result.get("error", {}).get("code", "unknown")
                    # For certain errors, don't try other versions
                    if error_code in [105, 106]:  # Invalid session or not logged in
                        break

            except requests.RequestException as e:
                last_error = {
                    "success": False,
                    "error": {"code": "network_error", "message": f"Network error: {str(e)}"},
                }
                continue
            except Exception as e:
                last_error = {
                    "success": False,
                    "error": {"code": "unknown_error", "message": f"Unexpected error: {str(e)}"},
                }
                continue

        # If we reach here, all attempts failed
        if last_error:
            return last_error
        else:
            return {
                "success": False,
                "error": {
                    "code": "all_versions_failed",
                    "message": "Logout failed with all API versions",
                },
            }

    def is_logged_in(self) -> bool:
        """Check if there's an active session."""
        return self.current_session_id is not None

    def get_session_info(self) -> Dict[str, Any]:
        """Get current session information."""
        return {
            "session_id": self.current_session_id,
            "session_type": self.current_session_type,
            "syno_token": self.current_syno_token,
            "logged_in": self.is_logged_in(),
        }
