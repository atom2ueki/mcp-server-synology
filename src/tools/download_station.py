"""Download Station tools: tasks, statistics, downloaded files.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="ds_get_info",
        description="Get Download Station information and settings",
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
        handler="_handle_ds_get_info",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="ds_list_tasks",
        description="List all download tasks in Download Station",
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
                "offset": {
                    "type": "integer",
                    "description": "Starting offset for pagination (default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of tasks to return (default: -1 for all)",
                },
            },
            "required": [],
        },
        handler="_handle_ds_list_tasks",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="ds_create_task",
        description="Create a new download task from URL or magnet link",
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
                "uri": {"type": "string", "description": "Download URL or magnet link"},
                "destination": {
                    "type": "string",
                    "description": "Destination folder path (optional)",
                },
                "username": {
                    "type": "string",
                    "description": "Username for protected downloads (optional)",
                },
                "password": {
                    "type": "string",
                    "description": "Password for protected downloads (optional)",
                },
            },
            "required": ["uri"],
        },
        handler="_handle_ds_create_task",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="ds_pause_tasks",
        description="Pause one or more download tasks",
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
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs to pause",
                },
            },
            "required": ["task_ids"],
        },
        handler="_handle_ds_pause_tasks",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="ds_resume_tasks",
        description="Resume one or more paused download tasks",
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
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs to resume",
                },
            },
            "required": ["task_ids"],
        },
        handler="_handle_ds_resume_tasks",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="ds_delete_tasks",
        description="Delete one or more download tasks",
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
                "task_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task IDs to delete",
                },
                "force_complete": {
                    "type": "boolean",
                    "description": "Force delete completed tasks (default: false)",
                },
            },
            "required": ["task_ids"],
        },
        handler="_handle_ds_delete_tasks",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="ds_get_statistics",
        description="Get Download Station download/upload statistics",
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
        handler="_handle_ds_get_statistics",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="ds_list_downloaded_files",
        description="List files in the Download Station destination folder",
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
                "destination": {
                    "type": "string",
                    "description": "Destination folder to list (optional, defaults "
                    "to download station's default)",
                },
            },
            "required": [],
        },
        handler="_handle_ds_list_downloaded_files",
        annotations=READ_ONLY,
    ),
)
