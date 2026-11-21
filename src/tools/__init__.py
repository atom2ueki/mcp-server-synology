"""Tool catalog: one declaration per tool, aggregated in registration order.

A tool's description, JSON schema, handler and MCP safety annotations live in
the same `ToolSpec`, so there is no second table to keep in sync. Adding a tool
is adding one spec; forgetting its classification is impossible.
"""

from tools._common import ADDITIVE, DESTRUCTIVE, READ_ONLY, ToolSpec
from tools.container import TOOLS as _CONTAINER
from tools.download_station import TOOLS as _DOWNLOAD_STATION
from tools.file_station import TOOLS as _FILE_STATION
from tools.health import TOOLS as _HEALTH
from tools.iscsi import TOOLS as _ISCSI
from tools.nfs import TOOLS as _NFS
from tools.reverse_proxy import TOOLS as _REVERSE_PROXY
from tools.session import LOGIN_TOOL, LOGOUT_TOOL
from tools.session import TOOLS as _SESSION
from tools.shares import TOOLS as _SHARES
from tools.update_security import TOOLS as _UPDATE_SECURITY
from tools.users import TOOLS as _USERS

_BY_NAME = {
    spec.name: spec
    for spec in (
        *_SESSION,
        *_FILE_STATION,
        *_DOWNLOAD_STATION,
        *_HEALTH,
        *_UPDATE_SECURITY,
        *_ISCSI,
        *_CONTAINER,
        *_NFS,
        *_SHARES,
        *_REVERSE_PROXY,
        *_USERS,
    )
}

# Registration order is part of the server's observable surface (tools/list);
# keep it stable across the module split.
_ORDER = (
    "synology_status",
    "synology_list_nas",
    "list_shares",
    "list_directory",
    "get_file_info",
    "search_files",
    "get_file_content",
    "rename_file",
    "move_file",
    "copy_file",
    "create_file",
    "create_directory",
    "delete",
    "ds_get_info",
    "ds_list_tasks",
    "ds_create_task",
    "ds_pause_tasks",
    "ds_resume_tasks",
    "ds_delete_tasks",
    "ds_get_statistics",
    "ds_list_downloaded_files",
    "synology_system_info",
    "synology_utilization",
    "synology_disk_health",
    "synology_disk_smart",
    "synology_volume_status",
    "synology_storage_pool",
    "synology_lun_list",
    "synology_lun_get",
    "synology_network",
    "synology_ups",
    "synology_services",
    "synology_system_log",
    "synology_update_status",
    "synology_security_posture",
    "synology_terminal_set",
    "synology_autoblock_set",
    "synology_autoblock_allow_add",
    "synology_autoblock_allow_remove",
    "synology_https_redirect_set",
    "synology_dsm_update_download",
    "synology_dsm_update_install",
    "synology_lun_create",
    "synology_lun_delete",
    "synology_target_list",
    "synology_target_get",
    "synology_target_create",
    "synology_target_delete",
    "synology_target_map_lun",
    "synology_target_unmap_lun",
    "synology_health_summary",
    "synology_container_list",
    "synology_container_health_summary",
    "synology_container_disk_usage",
    "synology_container_get",
    "synology_container_start",
    "synology_container_stop",
    "synology_container_restart",
    "synology_container_delete",
    "synology_container_logs",
    "synology_container_resource",
    "synology_container_project_list",
    "synology_container_project_get",
    "synology_container_project_create",
    "synology_container_project_update",
    "synology_container_project_start",
    "synology_container_project_stop",
    "synology_container_project_restart",
    "synology_container_project_build",
    "synology_container_project_clean",
    "synology_container_project_delete",
    "synology_container_image_list",
    "synology_container_image_get",
    "synology_container_image_delete",
    "synology_container_image_prune_preview",
    "synology_container_image_prune",
    "synology_container_image_pull",
    "synology_container_registry_list",
    "synology_container_registry_search",
    "synology_container_registry_tags",
    "synology_container_registry_download",
    "synology_container_network_list",
    "synology_container_network_get",
    "synology_container_network_create",
    "synology_container_network_delete",
    "synology_nfs_status",
    "synology_nfs_enable",
    "synology_nfs_list_shares",
    "synology_nfs_set_permission",
    "synology_create_share",
    "synology_reverse_proxy_list",
    "synology_reverse_proxy_get",
    "synology_access_control_profile_list",
    "synology_reverse_proxy_create",
    "synology_reverse_proxy_update",
    "synology_reverse_proxy_delete",
    "synology_list_users",
    "synology_get_user",
    "synology_create_user",
    "synology_set_user",
    "synology_delete_user",
    "synology_list_groups",
    "synology_list_group_members",
    "synology_add_user_to_group",
    "synology_remove_user_from_group",
    "synology_get_user_permissions",
    "synology_set_user_permissions",
)

ALL_TOOLS = tuple(_BY_NAME[name] for name in _ORDER)

assert len(_BY_NAME) == len(_ORDER) == 107, "duplicate or missing tool spec"
assert set(_BY_NAME) == set(_ORDER)

__all__ = [
    "ADDITIVE",
    "ALL_TOOLS",
    "DESTRUCTIVE",
    "LOGIN_TOOL",
    "LOGOUT_TOOL",
    "READ_ONLY",
    "ToolSpec",
]
