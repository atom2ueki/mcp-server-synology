"""File Station tools: reads, writes, deletes.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="list_shares",
        description="List all available shares on the Synology NAS",
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
            },
            "required": [],
        },
        handler="_handle_list_shares",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="list_directory",
        description="List contents of a directory on the Synology NAS. Returns detailed information about files and folders including name, type, size, and timestamps.",
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
                "path": {
                    "type": "string",
                    "description": "Directory path to list (must start with /)",
                },
            },
            "required": ["path"],
        },
        handler="_handle_list_directory",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="get_file_info",
        description="Get detailed information about a specific file or directory",
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
                "path": {
                    "type": "string",
                    "description": "File or directory path (must start with /)",
                },
            },
            "required": ["path"],
        },
        handler="_handle_get_file_info",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="search_files",
        description="Recursively search a directory for files and folders whose name contains the given text (case-insensitive substring match). Wildcards are not special - searching for 'report' and '*report*' return the same matches.",
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
                "path": {
                    "type": "string",
                    "description": "Directory path to search in (must start with /)",
                },
                "pattern": {
                    "type": "string",
                    "description": "Text to look for in the name, e.g. 'invoice' or "
                    "'.pdf' (case-insensitive substring)",
                },
            },
            "required": ["path", "pattern"],
        },
        handler="_handle_search_files",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="get_file_content",
        description="Read a file as strict UTF-8 text or lossless structured base64",
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
                "path": {"type": "string", "description": "File path (must start with /)"},
                "encoding": {
                    "type": "string",
                    "enum": ["text", "base64"],
                    "description": "Return strict UTF-8 text (default) or structured "
                    "lossless base64",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8388608,
                    "description": "Maximum raw bytes to read (default: 1048576; "
                    "hard maximum: 8388608)",
                },
            },
            "required": ["path"],
        },
        handler="_handle_get_file_content",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="rename_file",
        description="Rename a file or directory on the Synology NAS",
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
                "path": {
                    "type": "string",
                    "description": "Full path to the file/directory to rename (must start with /)",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for the file/directory (just the name, not full path)",
                },
            },
            "required": ["path", "new_name"],
        },
        handler="_handle_rename_file",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="move_file",
        description="Move a file or directory to a new location on the Synology NAS",
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
                "source_path": {
                    "type": "string",
                    "description": "Full path to the file/directory to move (must start with /)",
                },
                "destination_path": {
                    "type": "string",
                    "description": "Where to move it (must start with /): an "
                    "existing directory to move into, or a "
                    "full path whose last segment is the new "
                    "name",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Whether to overwrite existing files at "
                    "destination (default: false)",
                },
            },
            "required": ["source_path", "destination_path"],
        },
        handler="_handle_move_file",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="copy_file",
        description="Copy one regular file server-side into an existing NAS directory and verify target path and byte count; not a transactionally consistent backup of a live database",
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
                "source_path": {
                    "type": "string",
                    "description": "Full path to the regular file to copy (must start with /)",
                },
                "destination_folder": {
                    "type": "string",
                    "description": "Existing destination directory (must "
                    "start with /); the filename is "
                    "preserved",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Whether to overwrite an existing same-named file "
                    "(default: false)",
                },
            },
            "required": ["source_path", "destination_folder"],
        },
        handler="_handle_copy_file",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="create_file",
        description="Create a UTF-8 text or lossless base64-encoded file on the Synology NAS",
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
                "path": {
                    "type": "string",
                    "description": "Full path where the file should be created (must start with /)",
                },
                "content": {
                    "type": "string",
                    "description": "Text or strict base64 content (default: empty string)",
                },
                "encoding": {
                    "type": "string",
                    "enum": ["text", "base64"],
                    "description": "Interpret content as UTF-8 text (default) or "
                    "strict base64; decoded content is limited to "
                    "8388608 bytes",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Whether to overwrite existing file (default: false)",
                },
            },
            "required": ["path"],
        },
        handler="_handle_create_file",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="create_directory",
        description="Create a new directory on the Synology NAS",
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
                "folder_path": {
                    "type": "string",
                    "description": "Parent directory path where the new folder "
                    "should be created (must start with /)",
                },
                "name": {"type": "string", "description": "Name of the new directory to create"},
                "force_parent": {
                    "type": "boolean",
                    "description": "Whether to create parent directories if they "
                    "don't exist (default: false)",
                },
            },
            "required": ["folder_path", "name"],
        },
        handler="_handle_create_directory",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="delete",
        description="Delete a file or directory on the Synology NAS (auto-detects type)",
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
                "path": {
                    "type": "string",
                    "description": "Full path to the file/directory to delete (must start with /)",
                },
            },
            "required": ["path"],
        },
        handler="_handle_delete",
        annotations=DESTRUCTIVE,
    ),
)
