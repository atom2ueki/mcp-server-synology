"""Login Portal reverse proxy entries and access-control profiles.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_reverse_proxy_list",
        description="List Login Portal reverse proxy entries (Control Panel > Login Portal > Advanced > Reverse Proxy) with source, destination, HSTS, WebSocket, headers and access control profile",
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
        handler="_handle_reverse_proxy_call",
        handler_kwargs={"method_name": "list_entries"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_reverse_proxy_get",
        description="Get one reverse proxy entry by UUID or by its description",
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
                "uuid_or_description": {
                    "type": "string",
                    "description": "Entry UUID or description from synology_reverse_proxy_list",
                },
            },
            "required": ["uuid_or_description"],
        },
        handler="_handle_reverse_proxy_get",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_access_control_profile_list",
        description="List Login Portal access control profiles that a reverse proxy entry can reference",
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
        handler="_handle_reverse_proxy_call",
        handler_kwargs={"method_name": "list_access_control_profiles"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_reverse_proxy_create",
        description="Create a reverse proxy entry. Takes effect immediately. Refused if the description is already in use.",
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
                "description": {
                    "type": "string",
                    "description": "Entry description shown in DSM. DSM has no "
                    "name field, so this is the entry's "
                    "human-readable handle and must be unique.",
                },
                "source_protocol": {
                    "type": "string",
                    "enum": ["HTTP", "HTTPS"],
                    "description": "Protocol of the public listener (default: HTTPS)",
                },
                "source_hostname": {
                    "type": "string",
                    "description": "Host name the listener answers to, e.g. "
                    "'app.example.com'. No scheme or path.",
                },
                "source_port": {
                    "type": "integer",
                    "description": "Port of the public listener (default: 443)",
                },
                "destination_protocol": {
                    "type": "string",
                    "enum": ["HTTP", "HTTPS"],
                    "description": "Protocol used to reach the destination (default: HTTP)",
                },
                "destination_hostname": {
                    "type": "string",
                    "description": "Host name or IP requests are proxied "
                    "to, e.g. 'localhost' or "
                    "'192.168.1.10'. No scheme or path.",
                },
                "destination_port": {
                    "type": "integer",
                    "description": "Port requests are proxied to",
                },
                "hsts": {
                    "type": "boolean",
                    "description": "Send Strict-Transport-Security. Requires an HTTPS source.",
                },
                "websocket": {
                    "type": "boolean",
                    "description": "Add DSM's WebSocket header pair (Upgrade/Connection)",
                },
                "custom_headers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                        "required": ["name"],
                    },
                    "description": "Extra request headers for the destination; "
                    "values may use nginx variables such as "
                    "$scheme. Use `websocket` for the "
                    "Upgrade/Connection pair.",
                },
                "access_control_profile": {
                    "type": "string",
                    "description": "UUID of an access control profile "
                    "from "
                    "synology_access_control_profile_list. "
                    "On update, an empty string removes "
                    "the profile.",
                },
                "proxy_connect_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait when connecting to "
                    "the destination (default: 60)",
                },
                "proxy_read_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait for a response from the "
                    "destination (default: 60)",
                },
                "proxy_send_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait while sending to the destination (default: 60)",
                },
            },
            "required": [
                "description",
                "source_hostname",
                "destination_hostname",
                "destination_port",
            ],
        },
        handler="_handle_reverse_proxy_create",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_reverse_proxy_update",
        description="Change fields of an existing reverse proxy entry; fields not given keep their value. Takes effect immediately, so a wrong destination makes the site unreachable.",
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
                "uuid": {
                    "type": "string",
                    "description": "Entry UUID from synology_reverse_proxy_list",
                },
                "description": {
                    "type": "string",
                    "description": "Entry description shown in DSM. DSM has no "
                    "name field, so this is the entry's "
                    "human-readable handle and must be unique.",
                },
                "source_protocol": {
                    "type": "string",
                    "enum": ["HTTP", "HTTPS"],
                    "description": "Protocol of the public listener (default: HTTPS)",
                },
                "source_hostname": {
                    "type": "string",
                    "description": "Host name the listener answers to, e.g. "
                    "'app.example.com'. No scheme or path.",
                },
                "source_port": {
                    "type": "integer",
                    "description": "Port of the public listener (default: 443)",
                },
                "destination_protocol": {
                    "type": "string",
                    "enum": ["HTTP", "HTTPS"],
                    "description": "Protocol used to reach the destination (default: HTTP)",
                },
                "destination_hostname": {
                    "type": "string",
                    "description": "Host name or IP requests are proxied "
                    "to, e.g. 'localhost' or "
                    "'192.168.1.10'. No scheme or path.",
                },
                "destination_port": {
                    "type": "integer",
                    "description": "Port requests are proxied to",
                },
                "hsts": {
                    "type": "boolean",
                    "description": "Send Strict-Transport-Security. Requires an HTTPS source.",
                },
                "websocket": {
                    "type": "boolean",
                    "description": "Add DSM's WebSocket header pair (Upgrade/Connection)",
                },
                "custom_headers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                        "required": ["name"],
                    },
                    "description": "Extra request headers for the destination; "
                    "values may use nginx variables such as "
                    "$scheme. Use `websocket` for the "
                    "Upgrade/Connection pair.",
                },
                "access_control_profile": {
                    "type": "string",
                    "description": "UUID of an access control profile "
                    "from "
                    "synology_access_control_profile_list. "
                    "On update, an empty string removes "
                    "the profile.",
                },
                "proxy_connect_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait when connecting to "
                    "the destination (default: 60)",
                },
                "proxy_read_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait for a response from the "
                    "destination (default: 60)",
                },
                "proxy_send_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait while sending to the destination (default: 60)",
                },
            },
            "required": ["uuid"],
        },
        handler="_handle_reverse_proxy_update",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_reverse_proxy_delete",
        description="DESTRUCTIVE. Delete a reverse proxy entry; the site it served stops answering. Requires confirm=true.",
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
                "uuid": {
                    "type": "string",
                    "description": "Entry UUID from synology_reverse_proxy_list",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Guards against taking a live site "
                    "offline by accident.",
                },
            },
            "required": ["uuid", "confirm"],
        },
        handler="_handle_reverse_proxy_delete",
        annotations=DESTRUCTIVE,
    ),
)
