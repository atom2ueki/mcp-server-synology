# src/nfs/synology_nfs.py - Synology NFS service and share management

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from filelock import FileLock, Timeout

from utils.synology_api import SynologyAPIClient


class SynologyNFS:
    """Manage NFS service and share-level NFS permissions on Synology DSM."""

    _lock_directory = Path(tempfile.gettempdir()) / "synology-mcp-nfs-locks"
    _lock_timeout_seconds = 30

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
        self._api = SynologyAPIClient(base_url, session_id, verify_ssl, syno_token=syno_token)

    def _api_call(
        self,
        api: str,
        method: str,
        version: int = 1,
        extra_params: Optional[Dict] = None,
        use_post: bool = False,
    ) -> Dict[str, Any]:
        """Make an authenticated call to /webapi/entry.cgi."""
        if use_post:
            return self._api.post(api, method, version, extra_params)
        return self._api.get(api, method, version, extra_params)

    def _permission_lock(self, share_name: str) -> FileLock:
        """Return a host-wide lock for one NAS/share without leaking its name."""
        identity = f"{self.base_url}\0{share_name}".encode("utf-8")
        lock_name = f"{hashlib.sha256(identity).hexdigest()}.lock"
        self._lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return FileLock(
            str(self._lock_directory / lock_name),
            timeout=self._lock_timeout_seconds,
        )

    def _save_nfs_permission(
        self, share_name: str, client_ip: str, nfs_rule: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Load, merge, and save one NFS rule while the share lock is held."""
        api = "SYNO.Core.FileServ.NFS.SharePrivilege"
        current = self._api_call(api, "load", extra_params={"share_name": share_name})
        if not current.get("success"):
            return current

        data = current.get("data")
        rules = data.get("rule") if isinstance(data, dict) else None
        if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
            return {
                "success": False,
                "error": {
                    "code": "invalid_response",
                    "message": "DSM returned malformed NFS rule data; no changes were saved",
                },
            }

        rules = [rule for rule in rules if rule.get("client") != client_ip]
        rules.append(nfs_rule)
        return self._api_call(
            api,
            "save",
            extra_params={"share_name": share_name, "rule": json.dumps(rules)},
            use_post=True,
        )

    # ------------------------------------------------------------------
    # NFS Service
    # ------------------------------------------------------------------

    def nfs_status(self) -> Dict[str, Any]:
        """Get NFS service status and configuration."""
        return self._api_call("SYNO.Core.FileServ.NFS", "get")

    def nfs_enable(self, enable: bool = True, nfs_v4: bool = False) -> Dict[str, Any]:
        """Enable or disable the NFS service.

        Args:
            enable: True to enable, False to disable.
            nfs_v4: Enable NFSv4 support (only when enabling).
        """
        params = {"enable_nfs": "true" if enable else "false"}
        if enable and nfs_v4:
            params["nfs4_enable"] = "true"
        return self._api_call("SYNO.Core.FileServ.NFS", "set", extra_params=params, use_post=True)

    # ------------------------------------------------------------------
    # Shares
    # ------------------------------------------------------------------

    def list_shares(self) -> Dict[str, Any]:
        """List all shared folders with their NFS permissions."""
        return self._api_call(
            "SYNO.Core.Share", "list", extra_params={"additional": '["nfs_privilege"]'}
        )

    def get_share(self, name: str) -> Dict[str, Any]:
        """Get detailed info for a specific shared folder."""
        return self._api_call("SYNO.Core.Share", "get", extra_params={"name": name})

    def create_share(
        self,
        name: str,
        vol_path: str,
        desc: str = "",
        enable_recycle_bin: bool = True,
        recycle_bin_admin_only: bool = True,
        enable_share_cow: bool = False,
        enable_share_compress: bool = False,
    ) -> Dict[str, Any]:
        """Create a new shared folder on the NAS.

        Args:
            name: Share name (e.g. 'rag-corpus').
            vol_path: Volume path (e.g. '/volume2').
            desc: Optional description.
            enable_recycle_bin: Enable recycle bin (default True).
            recycle_bin_admin_only: Restrict recycle bin access to admins (default True).
            enable_share_cow: Enable copy-on-write (default False).
            enable_share_compress: Enable share compression (default False).
        """
        # DSM 7.3.2 rejects flat params on SYNO.Core.Share.create with code 403.
        # The web UI sends a JSON-encoded `shareinfo` envelope plus a top-level
        # `name` (also JSON-encoded — DSM expects the quoted form here). The
        # X-SYNO-TOKEN header is added by SynologyAPIClient when a token is set.
        # Verified against DSM 7.3.2-86009 Update 3 by capturing the live web UI
        # request — no Synology Noise/__cIpHeRtExT encryption is involved.
        shareinfo = {
            "name": name,
            "vol_path": vol_path,
            "desc": desc,
            "enable_recycle_bin": enable_recycle_bin,
            "recycle_bin_admin_only": recycle_bin_admin_only,
            "enable_share_cow": enable_share_cow,
            "enable_share_compress": enable_share_compress,
            "name_org": "",
        }
        params = {
            "name": json.dumps(name),
            "shareinfo": json.dumps(shareinfo),
        }
        return self._api_call("SYNO.Core.Share", "create", extra_params=params, use_post=True)

    def set_nfs_permission(
        self,
        share_name: str,
        client_ip: str,
        privilege: str = "readwrite",
        squash: str = "root_squash",
        security: str = "sys",
    ) -> Dict[str, Any]:
        """Set NFS client access permissions on a shared folder.

        Args:
            share_name: Name of the shared folder (e.g. "media").
            client_ip: Client IP or subnet (e.g. "192.168.1.0/24" or "10.0.0.5").
            privilege: Access level — "readonly" or "readwrite".
            squash: Squash option — "root_squash", "no_root_squash", or "all_squash".
            security: Security mode — "sys" (AUTH_SYS), "krb5", "krb5i", or "krb5p".
        """
        privilege_map = {"readonly": "ro", "readwrite": "rw"}
        squash_map = {
            "no_root_squash": "root",
            "root_squash": "guest",
            "all_squash": "all_guest",
        }
        security_map = {
            "sys": "sys",
            "krb5": "kerberos",
            "krb5i": "kerberos_integrity",
            "krb5p": "kerberos_privacy",
        }
        if privilege not in privilege_map or squash not in squash_map or security not in security_map:
            return {
                "success": False,
                "error": {
                    "code": "invalid_parameter",
                    "message": "Invalid NFS privilege, squash, or security value",
                },
            }

        security_flavor = {
            "sys": False,
            "kerberos": False,
            "kerberos_integrity": False,
            "kerberos_privacy": False,
        }
        security_flavor[security_map[security]] = True
        nfs_rule = {
            "client": client_ip,
            "privilege": privilege_map[privilege],
            "root_squash": squash_map[squash],
            "async": True,
            "insecure": False,
            "crossmnt": False,
            "security_flavor": security_flavor,
        }

        try:
            with self._permission_lock(share_name):
                return self._save_nfs_permission(share_name, client_ip, nfs_rule)
        except Timeout:
            return {
                "success": False,
                "error": {
                    "code": "nfs_update_busy",
                    "message": "Timed out waiting for another NFS rule update on this share",
                },
            }
