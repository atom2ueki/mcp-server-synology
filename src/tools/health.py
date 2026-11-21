"""System health: hardware, storage, network, logs, services.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_system_info",
        description="Get Synology NAS system information: model, serial, DSM version, uptime, temperature",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "system_info"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_utilization",
        description="Get real-time CPU, memory, swap, and disk I/O utilization",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "utilization"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_disk_health",
        description="List all physical disks with SMART health status, model, temperature, and capacity",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "disk_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_disk_smart",
        description="Get detailed S.M.A.R.T. attributes for a specific physical disk",
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
                "disk_id": {
                    "type": "string",
                    "description": "Disk identifier from synology_disk_health output — "
                    "either the disk id (e.g. 'sata1', 'sda', "
                    "'nvme0n1') or its device path (e.g. "
                    "'/dev/sata1')",
                },
            },
            "required": ["disk_id"],
        },
        handler="_handle_disk_smart",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_volume_status",
        description="List all volumes/filesystems with status, total size, used space, and RAID info",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "volume_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_storage_pool",
        description="List RAID/storage pools with RAID level, status, and member disks",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "storage_pool_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_lun_list",
        description="List all iSCSI LUNs with name, UUID, size, type, status and backing volume. To see which target a LUN is attached to, use synology_target_list.",
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
        handler="_handle_iscsi_call",
        handler_kwargs={"method_name": "lun_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_lun_get",
        description="Get details for a single iSCSI LUN by name or UUID",
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
                "name": {
                    "type": "string",
                    "description": "LUN name or UUID from synology_lun_list output",
                },
            },
            "required": ["name"],
        },
        handler="_handle_lun_get",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_network",
        description="Get network interface status and transfer rates",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "network_info"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_ups",
        description="Get UPS (uninterruptible power supply) status, battery level, and power info",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "ups_info"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_services",
        description="List installed packages/services and their running status",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "package_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_system_log",
        description="Get recent system log entries for diagnosing issues",
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
                "offset": {"type": "integer", "description": "Starting offset (default: 0)"},
                "limit": {"type": "integer", "description": "Max entries to return (default: 50)"},
            },
            "required": [],
        },
        handler="_handle_system_log",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_health_summary",
        description="Get a combined health overview: system info, CPU/memory utilization, disk health, volume status, storage pools, network, and UPS — all in one call",
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
        handler="_handle_health_call",
        handler_kwargs={"method_name": "health_summary"},
        annotations=READ_ONLY,
    ),
)
