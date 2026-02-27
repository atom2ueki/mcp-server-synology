# src/usermanagement/synology_users.py - Synology user and group management
# Supports both DSM 6 and DSM 7 APIs.

import json
from typing import Any, Dict, List, Optional

from utils.synology_api import SynologyAPIClient


class SynologyUserManager:
    """Manage users and groups on Synology DSM via the SYNO.Core.User/Group APIs."""

    def __init__(self, base_url: str, session_id: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
        self._api = SynologyAPIClient(base_url, session_id, verify_ssl)

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

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def list_users(self, offset: int = 0, limit: int = -1) -> Dict[str, Any]:
        """List all local users.

        Args:
            offset: Starting offset for pagination.
            limit: Max users to return (-1 for all).
        """
        return self._api_call(
            "SYNO.Core.User",
            "list",
            extra_params={
                "offset": str(offset),
                "limit": str(limit),
                "type": "local",
            },
        )

    def get_user(self, name: str) -> Dict[str, Any]:
        """Get info for a specific user."""
        return self._api_call(
            "SYNO.Core.User",
            "get",
            extra_params={
                "name": name,
            },
        )

    def create_user(
        self,
        name: str,
        password: str,
        description: str = "",
        email: str = "",
        cannot_chg_passwd: bool = False,
        passwd_never_expire: bool = True,
    ) -> Dict[str, Any]:
        """Create a new local user.

        Args:
            name: Username (required).
            password: Password (required).
            description: User description.
            email: User email address.
            cannot_chg_passwd: Prevent user from changing their password.
            passwd_never_expire: Password never expires.
        """
        params = {
            "name": name,
            "password": password,
            "description": description,
            "email": email,
            "expired": "normal",
            "cannot_chg_passwd": str(cannot_chg_passwd).lower(),
            "passwd_never_expire": str(passwd_never_expire).lower(),
            "notify_by_email": "false",
            "send_password": "false",
        }
        return self._api_call("SYNO.Core.User", "create", extra_params=params, use_post=True)

    def set_user(
        self,
        name: str,
        new_name: Optional[str] = None,
        password: Optional[str] = None,
        description: Optional[str] = None,
        email: Optional[str] = None,
        expired: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Modify an existing user.

        Args:
            name: Target username.
            new_name: Rename the user (optional).
            password: New password (optional).
            description: New description (optional).
            email: New email (optional).
            expired: Account status — "normal" (active), "now" (disabled).
        """
        params: Dict[str, str] = {"name": name}
        if new_name is not None:
            params["new_name"] = new_name
        if password is not None:
            params["password"] = password
        if description is not None:
            params["description"] = description
        if email is not None:
            params["email"] = email
        if expired is not None:
            params["expired"] = expired
        return self._api_call("SYNO.Core.User", "set", extra_params=params, use_post=True)

    def delete_user(self, name: str) -> Dict[str, Any]:
        """Delete a user."""
        return self._api_call(
            "SYNO.Core.User", "delete", extra_params={"name": name}, use_post=True
        )

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def list_groups(self) -> Dict[str, Any]:
        """List all local groups."""
        return self._api_call(
            "SYNO.Core.Group",
            "list",
            extra_params={
                "offset": "0",
                "limit": "-1",
                "type": "local",
            },
        )

    def get_group(self, name: str) -> Dict[str, Any]:
        """Get info for a specific group."""
        return self._api_call(
            "SYNO.Core.Group",
            "get",
            extra_params={
                "name": name,
            },
        )

    def list_group_members(self, group: str) -> Dict[str, Any]:
        """List members of a group."""
        return self._api_call(
            "SYNO.Core.Group.Member",
            "list",
            extra_params={
                "group": group,
                "ingroup": "true",
            },
        )

    def add_user_to_group(self, username: str, groups: List[str]) -> Dict[str, Any]:
        """Add a user to one or more groups.

        Uses SYNO.Core.User.Group (user-side join) which works on both DSM 6 and 7.

        Args:
            username: The user to modify.
            groups: List of group names to join.
        """
        return self._api_call(
            "SYNO.Core.User.Group",
            "join",
            extra_params={
                "name": username,
                "join_groups": json.dumps(groups),
            },
            use_post=True,
        )

    def remove_user_from_group(self, username: str, groups: List[str]) -> Dict[str, Any]:
        """Remove a user from one or more groups.

        Args:
            username: The user to modify.
            groups: List of group names to leave.
        """
        return self._api_call(
            "SYNO.Core.User.Group",
            "join",
            extra_params={
                "name": username,
                "leave_groups": json.dumps(groups),
            },
            use_post=True,
        )

    # ------------------------------------------------------------------
    # Shared Folder Permissions
    # ------------------------------------------------------------------

    def get_user_permissions(self, name: str) -> Dict[str, Any]:
        """Get shared folder permissions for a user."""
        return self._api_call(
            "SYNO.Core.Share.Permission",
            "list_by_user",
            extra_params={
                "name": name,
                "user_group_type": "local_user",
            },
        )

    def set_user_permissions(self, name: str, permissions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Set shared folder permissions for a user.

        Args:
            name: Username.
            permissions: List of permission dicts, e.g.:
                [{"name": "shared_folder", "is_writable": true, "is_deny": false}]
        """
        return self._api_call(
            "SYNO.Core.Share.Permission",
            "set_by_user_group",
            extra_params={
                "name": name,
                "user_group_type": "local_user",
                "permissions": json.dumps(permissions),
            },
            use_post=True,
        )
