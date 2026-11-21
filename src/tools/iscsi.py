"""SAN Manager: iSCSI LUN and target provisioning.

Declarative tool catalog: description, JSON schema, handler and MCP safety
annotations live on one object, so a tool cannot ship without a classification.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec

TOOLS = (
    ToolSpec(
        name="synology_lun_create",
        description="Create an iSCSI LUN on a volume. Returns its uuid and lun_id. On a btrfs volume DSM provisions thin by default.",
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
                "name": {"type": "string", "description": "LUN name, e.g. 'macmini-dev'"},
                "location": {
                    "type": "string",
                    "description": "Volume path to create it on, e.g. '/volume2'",
                },
                "size": {
                    "type": "integer",
                    "description": "Size in BYTES (e.g. 536870912000 for 500 GB)",
                },
                "type": {
                    "type": "string",
                    "description": "LUN type. 'thin' (default, DSM 'BLUN') or "
                    "'advanced'/'file'; a raw DSM type name such as BLUN, "
                    "ADV, THIN or FILE is passed through unchanged. Note "
                    "lowercase 'thin' means BLUN, while uppercase 'THIN' "
                    "is the distinct legacy DSM type. Thick types were "
                    "refused on the btrfs volume this was tested against; "
                    "DSM decides, and its error is reported as given.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional free-text description stored on the LUN",
                },
            },
            "required": ["name", "location", "size"],
        },
        handler="_handle_lun_create",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_lun_delete",
        description="DESTRUCTIVE. Permanently delete an iSCSI LUN and everything stored on it. Requires confirm=true.",
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
                "uuid": {"type": "string", "description": "LUN UUID from synology_lun_list"},
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Guards against deleting a LUN by "
                    "accident; there is no undo.",
                },
            },
            "required": ["uuid", "confirm"],
        },
        handler="_handle_lun_delete",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_target_list",
        description="List iSCSI targets with IQN, auth type, enabled state and the LUNs mapped to each",
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
        handler_kwargs={"method_name": "target_list"},
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_target_get",
        description="Get a single iSCSI target by its numeric target_id",
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
                "target_id": {
                    "type": "integer",
                    "description": "Numeric target_id from synology_target_list",
                },
            },
            "required": ["target_id"],
        },
        handler="_handle_target_get",
        annotations=READ_ONLY,
    ),
    ToolSpec(
        name="synology_target_create",
        description="Create an iSCSI target. Returns its target_id. Leave chap_user/chap_password unset for a target with no authentication.",
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
                "name": {"type": "string", "description": "Target name, e.g. 'macmini-dev'"},
                "iqn": {
                    "type": "string",
                    "description": "Full IQN. Defaults to iqn.2000-01.com.synology:<name>.",
                },
                "chap_user": {
                    "type": "string",
                    "description": "CHAP username. Must be given together with "
                    "chap_password; supplying only one is refused "
                    "rather than silently creating an unauthenticated "
                    "target.",
                },
                "chap_password": {
                    "type": "string",
                    "description": "CHAP password (12-16 chars per DSM). Must be "
                    "given together with chap_user.",
                },
                "max_sessions": {
                    "type": "integer",
                    "description": "Max concurrent sessions; 0 (default) means "
                    "DSM's default, no explicit cap.",
                },
            },
            "required": ["name"],
        },
        handler="_handle_target_create",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_target_delete",
        description="DESTRUCTIVE. Delete an iSCSI target. Mapped LUNs survive but become unreachable over iSCSI. Requires confirm=true.",
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
                "target_id": {
                    "type": "integer",
                    "description": "Numeric target_id from synology_target_list",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Guards against disconnecting a live "
                    "initiator by accident.",
                },
            },
            "required": ["target_id", "confirm"],
        },
        handler="_handle_target_delete",
        annotations=DESTRUCTIVE,
    ),
    ToolSpec(
        name="synology_target_map_lun",
        description="Map one or more LUNs to an iSCSI target, making them visible to initiators that connect to it",
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
                "target_id": {
                    "type": "integer",
                    "description": "Numeric target_id from synology_target_list",
                },
                "lun_uuids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "LUN UUIDs from synology_lun_list",
                },
            },
            "required": ["target_id", "lun_uuids"],
        },
        handler="_handle_target_map_lun",
        annotations=ADDITIVE,
    ),
    ToolSpec(
        name="synology_target_unmap_lun",
        description="DESTRUCTIVE. Unmap one or more LUNs from an iSCSI target, disconnecting them from any initiator currently using them. The LUNs themselves are not deleted. Requires confirm=true.",
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
                "target_id": {
                    "type": "integer",
                    "description": "Numeric target_id from synology_target_list",
                },
                "lun_uuids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "LUN UUIDs from synology_lun_list",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "Must be true. Unmapping takes storage away from a "
                    "live initiator.",
                },
            },
            "required": ["target_id", "lun_uuids", "confirm"],
        },
        handler="_handle_target_unmap_lun",
        annotations=DESTRUCTIVE,
    ),
)
