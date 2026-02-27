# src/utils/synology_api.py - Shared API client for all Synology modules

from typing import Any, Dict, Optional

import requests


class SynologyAPIClient:
    """Shared API client for all Synology modules.

    Provides standardized API calls with timeout, SSL verification,
    and error handling across all Synology services.
    """

    def __init__(self, base_url: str, session_id: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
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
        params = {
            "api": api,
            "version": str(version),
            "method": method,
            "_sid": self.session_id,
        }
        if extra_params:
            params.update(extra_params)

        try:
            if use_post:
                resp = requests.post(self._api_url, data=params, timeout=15, verify=self.verify_ssl)
            else:
                resp = requests.get(
                    self._api_url, params=params, timeout=15, verify=self.verify_ssl
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
