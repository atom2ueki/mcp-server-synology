"""DSM updates and security posture/controls.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_update_status",
        description="Read-only. Installed DSM version, the available DSM update (version, security release, whether it reboots), its download/install state, and installed packages with updates pending plus the package auto-update policy",
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
        handler="_handle_update_call",
        handler_kwargs={"method_name": "update_status"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_security_posture",
        description="Read-only. QuickConnect, auto-block and its allow list, Telnet/SSH, DSM's HTTP-to-HTTPS redirect, firewall, 2FA enforcement, admin/guest account state, administrators group members (DSM user accounts only; non-user members such as service accounts are omitted), and Security Advisor's last result and open findings",
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
        handler="_handle_security_posture",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_terminal_set",
        description="Turn Telnet and/or SSH on or off (Control Panel > Terminal & SNMP). Omitted settings and the SSH port are kept. Requires confirm=true. Returns before/after.",
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
                "enable_telnet": {
                    "type": "boolean",
                    "description": "Telnet on (true) or off (false). Omit to keep.",
                },
                "enable_ssh": {
                    "type": "boolean",
                    "description": "SSH on (true) or off (false). Omit to keep.",
                },
                "confirm": {"type": "boolean", "description": "Must be true."},
            },
            "required": ["confirm"],
        },
        handler="_handle_terminal_set",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_autoblock_set",
        description="Configure auto-block (Security > Protection). Omitted settings are kept. Only an actual enable transition is refused while the allow list is empty. Requires confirm=true. Returns before/after.",
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
                "enable": {"type": "boolean", "description": "Auto-block on or off. Omit to keep."},
                "attempts": {
                    "type": "integer",
                    "description": "Failed login attempts that trigger a block.",
                },
                "within_minutes": {
                    "type": "integer",
                    "description": "Window, in minutes, the attempts are counted over.",
                },
                "expire_days": {
                    "type": "integer",
                    "description": "Days until a block lifts; 0 means never.",
                },
                "confirm": {"type": "boolean", "description": "Must be true."},
            },
            "required": ["confirm"],
        },
        handler="_handle_autoblock_set",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_autoblock_allow_add",
        description="Add entries to the auto-block allow list, which are never blocked: an IP, a subnet as ip/mask or ip/prefix (192.168.0.0/22), or a range a~b. Requires confirm=true. Returns before/after.",
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
                "entries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IPs, subnets or ranges to allow.",
                },
                "confirm": {"type": "boolean", "description": "Must be true."},
            },
            "required": ["entries", "confirm"],
        },
        handler="_handle_allow_list",
        handler_kwargs={"method_name": "add_allow_list"},
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_autoblock_allow_remove",
        description="Remove entries from the auto-block allow list, named exactly as synology_security_posture lists them. Requires confirm=true. Returns before/after.",
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
                "entries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Entries to remove.",
                },
                "confirm": {"type": "boolean", "description": "Must be true."},
            },
            "required": ["entries", "confirm"],
        },
        handler="_handle_allow_list",
        handler_kwargs={"method_name": "remove_allow_list"},
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_https_redirect_set",
        description="Turn DSM's automatic HTTP-to-HTTPS redirect on or off (Login Portal > DSM). DSM restarts its web server. Requires confirm=true. Returns before/after.",
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
                "enable": {"type": "boolean", "description": "Redirect on (true) or off (false)."},
                "confirm": {"type": "boolean", "description": "Must be true."},
            },
            "required": ["enable", "confirm"],
        },
        handler="_handle_https_redirect_set",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_dsm_update_download",
        description="Download the available DSM update. Installs nothing. Requires confirm=true.",
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
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Downloads the DSM update onto the NAS.",
                },
            },
            "required": ["confirm"],
        },
        handler="_handle_dsm_update_download",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_dsm_update_install",
        description="Install a downloaded DSM update, after DSM's own pre-install checks. Without confirm=true it only runs the checks and returns blockers, warnings and any EULA. The NAS may reboot.",
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
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true to install. Omit to run the "
                    "pre-install checks only.",
                },
                "accept_warnings": {
                    "type": "boolean",
                    "description": "Proceed past DSM's soft warnings (its "
                    "confirm dialog). Default false.",
                },
                "accept_eula": {
                    "type": "boolean",
                    "description": "Accept the update's terms when DSM requires it. Default false.",
                },
            },
            "required": [],
        },
        handler="_handle_dsm_update_install",
        annotations=DESTRUCTIVE,
    ),
)
