"""Shared-folder creation.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_create_share",
        description="Create a new shared folder on a Synology NAS volume",
        schema={
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from secrets.json (e.g. 'nas1', 'nas2')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                "share_name": {
                    "type": "string",
                    "description": "Name of the shared folder to create (e.g. 'rag-corpus')",
                },
                "vol_path": {
                    "type": "string",
                    "description": "Volume path where the share will be created (e.g. "
                    "'/volume1', '/volume2')",
                },
                "description": {
                    "type": "string",
                    "description": "Optional description for the shared folder",
                },
                "enable_recycle_bin": {
                    "type": "boolean",
                    "description": "Enable recycle bin for deleted files (default: true)",
                },
                "recycle_bin_admin_only": {
                    "type": "boolean",
                    "description": "Restrict recycle bin access to "
                    "administrators only (default: "
                    "true)",
                },
            },
            "required": ["share_name", "vol_path"],
        },
        handler="_handle_create_share",
        annotations=ADDITIVE,
    ),
)
