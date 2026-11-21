"""Local users, groups, membership and shared-folder permissions.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_list_users",
        description="List all local users on the Synology NAS",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_usermgr_call",
        handler_kwargs={"method_name": "list_users"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_get_user",
        description="Get detailed information about a specific user",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Username to look up"},
            },
            "required": ["name"],
        },
        handler="_handle_usermgr_get_user",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_create_user",
        description="Create a new local user on the Synology NAS",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Username for the new account"},
                "password": {"type": "string", "description": "Password for the new account"},
                "description": {"type": "string", "description": "User description (optional)"},
                "email": {"type": "string", "description": "User email address (optional)"},
                "cannot_chg_passwd": {
                    "type": "boolean",
                    "description": "Prevent user from changing password (default: false)",
                },
                "passwd_never_expire": {
                    "type": "boolean",
                    "description": "Password never expires (default: true)",
                },
            },
            "required": ["name", "password"],
        },
        handler="_handle_usermgr_create_user",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_set_user",
        description="Modify an existing user (rename, change password, enable/disable)",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Target username to modify"},
                "new_name": {"type": "string", "description": "Rename the user (optional)"},
                "password": {"type": "string", "description": "New password (optional)"},
                "description": {"type": "string", "description": "New description (optional)"},
                "email": {"type": "string", "description": "New email (optional)"},
                "expired": {
                    "type": "string",
                    "enum": ["normal", "now"],
                    "description": "'normal' = active, 'now' = disabled",
                },
            },
            "required": ["name"],
        },
        handler="_handle_usermgr_set_user",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_delete_user",
        description="Delete a local user from the Synology NAS",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Username to delete"},
            },
            "required": ["name"],
        },
        handler="_handle_usermgr_delete_user",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_list_groups",
        description="List all local groups on the Synology NAS",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        },
        handler="_handle_usermgr_call",
        handler_kwargs={"method_name": "list_groups"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_list_group_members",
        description="List members of a specific group. Only DSM user accounts are enumerated — non-user members (e.g. service accounts like 'Virtualization' that appear in /etc/group) are omitted, so do not treat this as an authoritative privileged-group audit",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "group": {"type": "string", "description": "Group name to list members of"},
            },
            "required": ["group"],
        },
        handler="_handle_usermgr_list_group_members",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_add_user_to_group",
        description="Add a user to one or more groups. DSM applies this as an async batch task; the tool polls the group listing and reports `verified` — check it, since `success` alone only means the task was queued",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "username": {"type": "string", "description": "Username to add to groups"},
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of group names to join",
                },
            },
            "required": ["username", "groups"],
        },
        handler="_handle_usermgr_add_to_group",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_remove_user_from_group",
        description="Remove a user from one or more groups. DSM applies this as an async batch task; the tool polls the group listing and reports `verified` — check it, since `success` alone only means the task was queued",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "username": {"type": "string", "description": "Username to remove from groups"},
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of group names to leave",
                },
            },
            "required": ["username", "groups"],
        },
        handler="_handle_usermgr_remove_from_group",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_get_user_permissions",
        description="Get shared folder permissions for a user",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Username to check permissions for"},
            },
            "required": ["name"],
        },
        handler="_handle_usermgr_get_permissions",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_set_user_permissions",
        description="Set shared folder permissions for a user (read/write/deny per folder)",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from settings.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "name": {"type": "string", "description": "Username to set permissions for"},
                "permissions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Shared folder name"},
                            "is_writable": {"type": "boolean", "description": "Grant write access"},
                            "is_deny": {"type": "boolean", "description": "Deny access entirely"},
                        },
                        "required": ["name"],
                    },
                    "description": "List of folder permission objects",
                },
            },
            "required": ["name", "permissions"],
        },
        handler="_handle_usermgr_set_permissions",
        annotations=DESTRUCTIVE,
    ),
)
