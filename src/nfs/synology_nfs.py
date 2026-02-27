# src/nfs/synology_nfs.py - Synology NFS service and share management

import json
import requests
from typing import Dict, Any, Optional, List


class SynologyNFS:
    """Manage NFS service and share-level NFS permissions on Synology DSM."""

    def __init__(self, base_url: str, session_id: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip('/')
        self.session_id = session_id
        self.verify_ssl = verify_ssl

    def _api_call(self, api: str, method: str, version: int = 1,
                  extra_params: Optional[Dict] = None,
                  use_post: bool = False) -> Dict[str, Any]:
        """Make an authenticated call to /webapi/entry.cgi."""
        url = f"{self.base_url}/webapi/entry.cgi"
        params = {
            'api': api,
            'version': str(version),
            'method': method,
            '_sid': self.session_id,
        }
        if extra_params:
            params.update(extra_params)

        try:
            if use_post:
                resp = requests.post(url, data=params, verify=self.verify_ssl, timeout=15)
            else:
                resp = requests.get(url, params=params, verify=self.verify_ssl, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            return {'success': False, 'error': {'code': 'network_error', 'message': str(e)}}
        except Exception as e:
            return {'success': False, 'error': {'code': 'unknown_error', 'message': str(e)}}

    # ------------------------------------------------------------------
    # NFS Service
    # ------------------------------------------------------------------

    def nfs_status(self) -> Dict[str, Any]:
        """Get NFS service status and configuration."""
        return self._api_call('SYNO.Core.FileServ.NFS', 'get')

    def nfs_enable(self, enable: bool = True, nfs_v4: bool = False) -> Dict[str, Any]:
        """Enable or disable the NFS service.

        Args:
            enable: True to enable, False to disable.
            nfs_v4: Enable NFSv4 support (only when enabling).
        """
        params = {'enable_nfs': 'true' if enable else 'false'}
        if enable and nfs_v4:
            params['nfs4_enable'] = 'true'
        return self._api_call('SYNO.Core.FileServ.NFS', 'set',
                              extra_params=params, use_post=True)

    # ------------------------------------------------------------------
    # Shares
    # ------------------------------------------------------------------

    def list_shares(self) -> Dict[str, Any]:
        """List all shared folders with their NFS permissions."""
        return self._api_call('SYNO.Core.Share', 'list',
                              extra_params={'additional': '["nfs_privilege"]'})

    def get_share(self, name: str) -> Dict[str, Any]:
        """Get detailed info for a specific shared folder."""
        return self._api_call('SYNO.Core.Share', 'get',
                              extra_params={'name': name})

    def create_share(self, name: str, vol_path: str,
                     desc: str = "",
                     enable_recycle_bin: bool = True,
                     recycle_bin_admin_only: bool = True) -> Dict[str, Any]:
        """Create a new shared folder on the NAS.

        Args:
            name: Share name (e.g. 'rag-corpus').
            vol_path: Volume path (e.g. '/volume2').
            desc: Optional description.
            enable_recycle_bin: Enable recycle bin (default True).
            recycle_bin_admin_only: Restrict recycle bin access to admins (default True).
        """
        params = {
            'name': name,
            'vol_path': vol_path,
            'desc': desc,
            'enable_recycle_bin': json.dumps(enable_recycle_bin),
            'recycle_bin_admin_only': json.dumps(recycle_bin_admin_only),
        }
        return self._api_call('SYNO.Core.Share', 'create',
                              extra_params=params, use_post=True)

    def set_nfs_permission(self, share_name: str, client_ip: str,
                           privilege: str = "readwrite",
                           squash: str = "root_squash",
                           security: str = "sys") -> Dict[str, Any]:
        """Set NFS client access permissions on a shared folder.

        Args:
            share_name: Name of the shared folder (e.g. "media").
            client_ip: Client IP or subnet (e.g. "192.168.1.0/24" or "10.0.0.5").
            privilege: Access level — "readonly" or "readwrite".
            squash: Squash option — "root_squash", "no_root_squash", or "all_squash".
            security: Security mode — "sys" (AUTH_SYS), "krb5", "krb5i", or "krb5p".
        """
        import json as _json

        nfs_rule = {
            'host': client_ip,
            'privilege': privilege,
            'squash': squash,
            'security': security,
        }

        params = {
            'name': share_name,
            'nfs_privilege': _json.dumps([nfs_rule]),
        }
        return self._api_call('SYNO.Core.Share', 'set',
                              extra_params=params, use_post=True)
