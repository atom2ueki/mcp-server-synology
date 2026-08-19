# src/usermanagement/synology_users.py - Synology user and group management
# Supports both DSM 6 and DSM 7 APIs.

import json
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.synology_api import SynologyAPIClient

# SYNO.Core.User.Group join is an async batch task: DSM returns a task_id and
# applies the change out-of-band, so "success" only means "queued". These
# control how long add_user_to_group/remove_user_from_group poll the group
# listing before giving up and flagging the change as unverified.
GROUP_WRITE_VERIFY_ATTEMPTS = 6
GROUP_WRITE_VERIFY_INTERVAL = 1.0


class SynologyUserManager:
    """Manage users and groups on Synology DSM via the SYNO.Core.User/Group APIs."""

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
        """List members of a group.

        Note: SYNO.Core.Group.Member only enumerates DSM user accounts.
        Non-user members (e.g. the 'Virtualization' service account visible
        in /etc/group) are not included — the MCP tool description and skill
        docs surface this limitation to callers.
        """
        return self._api_call(
            "SYNO.Core.Group.Member",
            "list",
            extra_params={
                "group": group,
                "ingroup": "true",
            },
        )

    def _group_member_names(self, group: str) -> Optional[Set[str]]:
        """Return the set of user names currently in a group, or None if unreadable.

        SYNO.Core.Group.Member's response shape has drifted across DSM
        versions (data.users vs data.members vs a bare list), so every known
        shape is tolerated here.
        """
        result = self._api_call(
            "SYNO.Core.Group.Member",
            "list",
            extra_params={
                "group": group,
                "ingroup": "true",
            },
        )
        if not result.get("success"):
            return None
        data = result.get("data")
        entries: Any = None
        if isinstance(data, dict):
            entries = data.get("users", data.get("members"))
        elif isinstance(data, list):
            entries = data
        # An unrecognized payload is unreadable, not an empty group: returning
        # an empty set here would let a removal "verify" absence the response
        # never actually showed.
        if not isinstance(entries, list):
            return None
        names: Set[str] = set()
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("name"):
                names.add(entry["name"])
            elif isinstance(entry, str):
                names.add(entry)
        return names

    def _verify_membership_change(
        self, username: str, groups: List[str], expect_member: bool
    ) -> Tuple[bool, List[str]]:
        """Poll group listings until a join/leave is visible, or give up.

        Returns (verified, unverified_groups). A group whose listing can't be
        read counts as unverified — indistinguishable from not-yet-applied.
        Groups confirmed on an earlier attempt drop out of the working set.
        """
        pending = list(groups)
        for attempt in range(GROUP_WRITE_VERIFY_ATTEMPTS):
            still_pending = []
            for group in pending:
                names = self._group_member_names(group)
                if names is None or (username in names) != expect_member:
                    still_pending.append(group)
            if not still_pending:
                return True, []
            pending = still_pending
            if attempt < GROUP_WRITE_VERIFY_ATTEMPTS - 1:
                time.sleep(GROUP_WRITE_VERIFY_INTERVAL)
        return False, pending

    def _apply_group_change(
        self,
        username: str,
        groups: List[str],
        param_name: str,
        expect_member: bool,
    ) -> Dict[str, Any]:
        """Issue a SYNO.Core.User.Group join and verify it actually landed.

        DSM executes join as an async batch task and returns a task_id; a
        bare passthrough would report success while the membership may never
        apply (issue #88). After a queued success, poll the group listings
        and annotate the response with the verification outcome so callers
        can tell "applied" from "queued".
        """
        result = self._api_call(
            "SYNO.Core.User.Group",
            "join",
            extra_params={
                "name": username,
                param_name: json.dumps(groups),
            },
            use_post=True,
        )
        if not result.get("success") or not groups:
            return result
        verified, unverified = self._verify_membership_change(
            username, groups, expect_member=expect_member
        )
        result["verified"] = verified
        if not verified:
            result["unverified_groups"] = unverified
            task_id = (result.get("data") or {}).get("task_id")
            result["warning"] = (
                f"DSM accepted the request (async batch task {task_id}) but the "
                f"membership change was not visible in the group listing after "
                f"{GROUP_WRITE_VERIFY_ATTEMPTS} checks — treat this as 'queued', "
                f"not 'applied'. Re-check with synology_list_group_members."
            )
        return result

    def add_user_to_group(self, username: str, groups: List[str]) -> Dict[str, Any]:
        """Add a user to one or more groups.

        Uses SYNO.Core.User.Group (user-side join) which works on both DSM 6 and 7.
        The response is annotated with `verified` (bool); on verification
        failure it carries `unverified_groups` and a `warning`.

        Args:
            username: The user to modify.
            groups: List of group names to join.
        """
        return self._apply_group_change(
            username, groups, param_name="join_groups", expect_member=True
        )

    def remove_user_from_group(self, username: str, groups: List[str]) -> Dict[str, Any]:
        """Remove a user from one or more groups.

        Uses SYNO.Core.User.Group (user-side leave). The response is annotated
        with `verified` (bool); on verification failure it carries
        `unverified_groups` and a `warning`.

        Args:
            username: The user to modify.
            groups: List of group names to leave.
        """
        return self._apply_group_change(
            username, groups, param_name="leave_groups", expect_member=False
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
