# src/tool_policy.py - Risk classification and MCP tool annotations
"""One risk table for every MCP tool, and the annotations derived from it.

Two problems this solves:

1. **Clients cannot prompt on what the server does not declare.** The MCP spec
   says an un-annotated tool is assumed destructive, non-idempotent and
   open-world — the pessimistic default. That default is only useful if the
   client honours it; most do not. Declaring the truth lets a client with
   approval UI ask about the calls that matter and skip the rest.

2. **The server itself needs to know which calls are destructive** before it
   executes them: the audit trail fails closed for those (see `audit.py`), and
   the confirmation gate keys off the same table.

Annotations are hints, not enforcement — a client may ignore them, and this
server must not rely on them for safety. `risk_for()` is the server-side
authority; `annotations_for()` is the outward projection.

Unknown tools are classified DESTRUCTIVE, per the spec's "assume the worst"
stance: a tool added without a table entry still gets the strictest handling.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

import mcp.types as types


class Risk(str, Enum):
    """What a tool can do to the NAS, from least to most dangerous."""

    READ = "read"          # no state change
    CONTROL = "control"    # changes only the MCP's own NAS session, not NAS state
    ADDITIVE = "additive"  # creates something new; nothing existing is altered
    MUTATING = "mutating"  # changes existing state, recoverable by setting it back
    DESTRUCTIVE = "destructive"  # removes or overwrites data; may be unrecoverable


# The single source of truth. Adding a tool without adding it here leaves it
# DESTRUCTIVE by default; `tests/test_tool_policy.py` fails the suite if the
# registry and this table drift apart.
_READ: frozenset[str] = frozenset({
    "synology_status",
    "synology_list_nas",
    "list_shares",
    "list_directory",
    "get_file_info",
    "search_files",
    "get_file_content",
    "ds_get_info",
    "ds_list_tasks",
    "ds_get_statistics",
    "ds_list_downloaded_files",
    "synology_system_info",
    "synology_utilization",
    "synology_disk_health",
    "synology_disk_smart",
    "synology_volume_status",
    "synology_storage_pool",
    "synology_network",
    "synology_ups",
    "synology_services",
    "synology_system_log",
    "synology_lun_list",
    "synology_lun_get",
    "synology_target_list",
    "synology_target_get",
    "synology_health_summary",
    "synology_container_list",
    "synology_container_health_summary",
    "synology_container_disk_usage",
    "synology_container_get",
    "synology_container_logs",
    "synology_container_resource",
    "synology_container_project_list",
    "synology_container_project_get",
    "synology_container_image_list",
    "synology_container_image_get",
    "synology_container_image_prune_preview",
    "synology_container_registry_list",
    "synology_container_registry_search",
    "synology_container_registry_tags",
    "synology_container_network_list",
    "synology_container_network_get",
    "synology_nfs_status",
    "synology_nfs_list_shares",
    "synology_reverse_proxy_list",
    "synology_reverse_proxy_get",
    "synology_access_control_profile_list",
    "synology_list_users",
    "synology_get_user",
    "synology_list_groups",
    "synology_list_group_members",
    "synology_get_user_permissions",
})

_CONTROL: frozenset[str] = frozenset({
    "synology_login",
    "synology_logout",
})

_ADDITIVE: frozenset[str] = frozenset({
    "copy_file",
    "create_file",
    "create_directory",
    "ds_create_task",
    "synology_lun_create",
    "synology_target_create",
    "synology_target_map_lun",
    "synology_container_project_create",
    "synology_container_network_create",
    "synology_container_image_pull",
    "synology_container_registry_download",
    "synology_create_share",
    "synology_reverse_proxy_create",
    "synology_create_user",
    "synology_add_user_to_group",
})

_MUTATING: frozenset[str] = frozenset({
    "rename_file",
    "move_file",
    "ds_pause_tasks",
    "ds_resume_tasks",
    "synology_container_start",
    "synology_container_stop",
    "synology_container_restart",
    "synology_container_project_start",
    "synology_container_project_stop",
    "synology_container_project_restart",
    "synology_container_project_build",
    "synology_nfs_enable",
    "synology_nfs_set_permission",
    "synology_reverse_proxy_update",
    "synology_set_user",
    "synology_set_user_permissions",
})

_DESTRUCTIVE: frozenset[str] = frozenset({
    "delete",
    "ds_delete_tasks",
    "synology_lun_delete",
    "synology_target_delete",
    "synology_target_unmap_lun",
    "synology_container_delete",
    "synology_container_project_delete",
    "synology_container_project_clean",
    "synology_container_project_update",
    "synology_container_image_delete",
    "synology_container_image_prune",
    "synology_container_network_delete",
    "synology_reverse_proxy_delete",
    "synology_delete_user",
    "synology_remove_user_from_group",
})

# Tools whose *output* crosses a trust boundary: the container registry tools
# query a remote registry and image_pull/registry_download fetch bytes from it.
# MCP's `openWorldHint` is about "does this reach outside its local domain",
# which is exactly what the rest of this server does not do.
_OPEN_WORLD: frozenset[str] = frozenset({
    "synology_container_registry_search",
    "synology_container_registry_tags",
    "synology_container_registry_download",
    "synology_container_image_pull",
})

_RISK_BY_TOOL: dict[str, Risk] = {
    **{name: Risk.READ for name in _READ},
    **{name: Risk.CONTROL for name in _CONTROL},
    **{name: Risk.ADDITIVE for name in _ADDITIVE},
    **{name: Risk.MUTATING for name in _MUTATING},
    **{name: Risk.DESTRUCTIVE for name in _DESTRUCTIVE},
}

# Projection of Risk onto the four MCP hints.
#
#   READ         read-only, idempotent, not destructive
#   CONTROL      session login/logout: writes, but only to the MCP's own state
#   ADDITIVE     writes, but creates rather than destroys; repeating it is not
#                safe (a second create fails or duplicates), so idempotent=False
#   MUTATING     writes to existing state; a repeated set/start/stop converges,
#                so idempotent=True, but it can still overwrite, hence destructive
#   DESTRUCTIVE  removes or overwrites; destructive, not idempotent
#
# `destructiveHint` is deliberately conservative for MUTATING: a rename or a
# permission change can lose data if the caller passes the wrong target.
_HINTS: dict[Risk, dict[str, bool]] = {
    Risk.READ: {"read_only": True, "destructive": False, "idempotent": True},
    Risk.CONTROL: {"read_only": False, "destructive": False, "idempotent": True},
    Risk.ADDITIVE: {"read_only": False, "destructive": False, "idempotent": False},
    Risk.MUTATING: {"read_only": False, "destructive": True, "idempotent": True},
    Risk.DESTRUCTIVE: {"read_only": False, "destructive": True, "idempotent": False},
}


_OVERWRITE_TOOLS = frozenset({"create_file", "copy_file", "move_file"})


def risk_for(tool_name: str, arguments: dict[str, Any] | None = None) -> Risk:
    """Risk of one tool. Unknown names are DESTRUCTIVE, never "probably fine"."""
    if tool_name in _OVERWRITE_TOOLS and (arguments or {}).get("overwrite"):
        return Risk.DESTRUCTIVE
    if tool_name == "synology_set_user" and "password" in (arguments or {}):
        return Risk.DESTRUCTIVE
    return _RISK_BY_TOOL.get(tool_name, Risk.DESTRUCTIVE)


def annotations_for(tool_name: str) -> types.ToolAnnotations:
    """MCP annotations for one tool, derived from its risk class."""
    risk = risk_for(tool_name)
    hints = _HINTS[risk]
    # The SDK model uses snake_case fields and serialises them with the
    # camelCase aliases the protocol specifies (`destructiveHint`, ...).
    return types.ToolAnnotations(
        read_only_hint=hints["read_only"],
        destructive_hint=hints["destructive"] or tool_name in _OVERWRITE_TOOLS,
        idempotent_hint=hints["idempotent"],
        open_world_hint=tool_name in _OPEN_WORLD,
    )


def unclassified(tool_names: Iterable[str]) -> set[str]:
    """Tool names with no explicit entry in the table (they default to DESTRUCTIVE)."""
    return {name for name in tool_names if name not in _RISK_BY_TOOL}
