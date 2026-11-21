"""NFS service and per-share permissions.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_nfs_status",
        description="Get NFS service status and configuration (enabled/disabled, NFSv4 settings)",
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
        handler="_handle_nfs_call",
        handler_kwargs={"method_name": "nfs_status"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_nfs_enable",
        description="Enable or disable the NFS file service on the Synology NAS",
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
                "enable": {
                    "type": "boolean",
                    "description": "True to enable NFS, false to disable (default: true)",
                },
                "nfs_v4": {
                    "type": "boolean",
                    "description": "Enable NFSv4 support (default: false)",
                },
            },
            "required": [],
        },
        handler="_handle_nfs_enable",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_nfs_list_shares",
        description="List all shared folders with their NFS access permissions",
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
        handler="_handle_nfs_call",
        handler_kwargs={"method_name": "list_shares"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_nfs_set_permission",
        description="Set NFS client access permissions on a shared folder (IP/subnet, read/write, squash options)",
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
                "share_name": {
                    "type": "string",
                    "description": "Name of the shared folder (e.g. 'media', 'backups')",
                },
                "client_ip": {
                    "type": "string",
                    "description": "Client IP or subnet (e.g. '192.168.1.0/24', '10.0.0.5')",
                },
                "privilege": {
                    "type": "string",
                    "enum": ["readonly", "readwrite"],
                    "description": "Access level (default: readwrite)",
                },
                "squash": {
                    "type": "string",
                    "enum": ["root_squash", "no_root_squash", "all_squash"],
                    "description": "Squash option for root user mapping (default: root_squash)",
                },
                "security": {
                    "type": "string",
                    "enum": ["sys", "krb5", "krb5i", "krb5p"],
                    "description": "Security mode (default: sys/AUTH_SYS)",
                },
            },
            "required": ["share_name", "client_ip"],
        },
        handler="_handle_nfs_set_permission",
        annotations=DESTRUCTIVE,
    ),
)
