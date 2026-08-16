# src/mcp_server.py - MCP Server for Synology NAS operations

import asyncio
import json
import logging
from functools import partial
from typing import Callable, Dict, Optional

import urllib3

logger = logging.getLogger(__name__)

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, PaginatedRequestParams

from auth import SynologyAuth
from config import config
from container import SynologyContainer
from downloadstation import SynologyDownloadStation
from filestation import SynologyFileStation
from health import SynologyHealth
from nfs import SynologyNFS
from usermanagement import SynologyUserManager

# Suppress InsecureRequestWarning when verify_ssl is disabled (internal NAS devices).
# urllib3's warning filter is process-global, so this has to consider any NAS that
# turns verification off individually, not just the global setting.
_unverified_nas = [
    name for name, cfg in config.nas_configs.items() if not cfg.get("verify_ssl", config.verify_ssl)
]
if not config.verify_ssl or _unverified_nas:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    if not config.verify_ssl:
        logger.warning(
            "SSL verification is disabled. Set VERIFY_SSL=true if your NAS has a valid SSL certificate."
        )
    else:
        logger.warning(
            "SSL verification is disabled for: "
            f"{', '.join(_unverified_nas)}. Certificate warnings are suppressed process-wide."
        )


class SynologyMCPServer:
    """MCP Server for Synology NAS operations."""

    def __init__(self):
        self.auth_instances: Dict[str, SynologyAuth] = {}
        self.sessions: Dict[str, str] = {}  # base_url -> session_id
        self.syno_tokens: Dict[str, str] = {}  # base_url -> SynoToken (CSRF, DSM 7.3.2+)
        self.filestation_instances: Dict[str, SynologyFileStation] = {}
        self.downloadstation_instances: Dict[str, SynologyDownloadStation] = {}
        self.health_instances: Dict[str, SynologyHealth] = {}
        self.container_instances: Dict[str, SynologyContainer] = {}
        self.nfs_instances: Dict[str, SynologyNFS] = {}
        self.usermgr_instances: Dict[str, SynologyUserManager] = {}
        self.nas_name_map: Dict[str, str] = {}  # nas_name -> base_url
        self._tool_registry: Dict[str, tuple[types.Tool, Callable]] = {}
        self.server = self._create_server()
        self._register_all_tools()

    def _create_server(self) -> Server:
        """Create the MCP server with mcp 2.0 constructor-style handlers.

        mcp 2.0 removed the decorator API (`@server.list_tools()` /
        `@server.call_tool()`); handlers are now passed as constructor
        callbacks. The async wrappers below adapt the callback signatures
        (`ctx, params`) to the existing handler methods.
        """

        async def on_list_tools(
            ctx: ServerRequestContext, params: Optional[PaginatedRequestParams]
        ) -> ListToolsResult:
            return ListToolsResult(tools=self._get_tool_definitions())

        async def on_call_tool(
            ctx: ServerRequestContext, params: CallToolRequestParams
        ) -> CallToolResult:
            try:
                content = await self._dispatch_tool(params.name, params.arguments or {})
                return CallToolResult(content=content)
            except Exception as e:
                return CallToolResult(
                    content=[types.TextContent(type="text", text=f"Error executing {params.name}: {e!s}")],
                    is_error=True,
                )

        return Server(
            config.server_name,
            version=config.server_version,
            on_list_tools=on_list_tools,
            on_call_tool=on_call_tool,
        )

    def _register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
    ):
        """Register a tool definition and its handler in the registry."""
        tool = types.Tool(
            name=name,
            description=description,
            inputSchema=input_schema,
        )
        self._tool_registry[name] = (tool, handler)

    def _register_all_tools(self):
        """Register all tool definitions and their handlers.

        Each tool is a pair of (types.Tool, handler_fn). The registry
        co-locates the definition and implementation so that adding a new
        tool means adding one entry here — no separate if/elif chain needed.
        """
        # Base target properties reused across many tools
        T = {"type": "object", "properties": {}, "required": []}
        TN = {
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from secrets.json (e.g. \'nas1\', \'nas2\')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
            },
            "required": [],
        }
        TN_P = lambda props: {  # noqa: E731
            "type": "object",
            "properties": {
                "nas_name": {
                    "type": "string",
                    "description": "NAS identifier from secrets.json (e.g. \'nas1\', \'nas2\')",
                },
                "base_url": {
                    "type": "string",
                    "description": "Synology NAS base URL (alternative to nas_name)",
                },
                **props,
            },
            "required": [],
        }
        TN_PR = lambda props, required: {  # noqa: E731
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
                **props,
            },
            "required": required,
        }

        # Status & NAS listing
        self._register_tool("synology_status", "Check authentication status for Synology NAS instances", T, self._handle_status)
        self._register_tool("synology_list_nas", "List all configured NAS units from secrets.json. Returns NAS names, URLs, and connection status.", T, self._handle_list_nas)

        # File Station
        self._register_tool("list_shares", "List all available shares on the Synology NAS", TN, self._handle_list_shares)
        self._register_tool("list_directory", "List contents of a directory on the Synology NAS. Returns detailed information about files and folders including name, type, size, and timestamps.", TN_PR({"path": {"type": "string", "description": "Directory path to list (must start with /)"}}, ["path"]), self._handle_list_directory)
        self._register_tool("get_file_info", "Get detailed information about a specific file or directory", TN_PR({"path": {"type": "string", "description": "File or directory path (must start with /)"}}, ["path"]), self._handle_get_file_info)
        self._register_tool("search_files", "Recursively search a directory for files and folders whose name contains the given text (case-insensitive substring match). Wildcards are not special - searching for \'report\' and \'*report*\' return the same matches.", TN_PR({"path": {"type": "string", "description": "Directory path to search in (must start with /)"}, "pattern": {"type": "string", "description": "Text to look for in the name, e.g. \'invoice\' or \'.pdf\' (case-insensitive substring)"}}, ["path", "pattern"]), self._handle_search_files)
        self._register_tool("get_file_content", "Get the content of a file", TN_PR({"path": {"type": "string", "description": "File path (must start with /)"}}, ["path"]), self._handle_get_file_content)
        self._register_tool("rename_file", "Rename a file or directory on the Synology NAS", TN_PR({"path": {"type": "string", "description": "Full path to the file/directory to rename (must start with /)"}, "new_name": {"type": "string", "description": "New name for the file/directory (just the name, not full path)"}}, ["path", "new_name"]), self._handle_rename_file)
        self._register_tool("move_file", "Move a file or directory to a new location on the Synology NAS", TN_PR({"source_path": {"type": "string", "description": "Full path to the file/directory to move (must start with /)"}, "destination_path": {"type": "string", "description": "Where to move it (must start with /): an existing directory to move into, or a full path whose last segment is the new name"}, "overwrite": {"type": "boolean", "description": "Whether to overwrite existing files at destination (default: false)"}}, ["source_path", "destination_path"]), self._handle_move_file)
        self._register_tool("create_file", "Create a new file with specified content on the Synology NAS", TN_PR({"path": {"type": "string", "description": "Full path where the file should be created (must start with /)"}, "content": {"type": "string", "description": "Content to write to the file (default: empty string)"}, "overwrite": {"type": "boolean", "description": "Whether to overwrite existing file (default: false)"}}, ["path"]), self._handle_create_file)
        self._register_tool("create_directory", "Create a new directory on the Synology NAS", TN_PR({"folder_path": {"type": "string", "description": "Parent directory path where the new folder should be created (must start with /)"}, "name": {"type": "string", "description": "Name of the new directory to create"}, "force_parent": {"type": "boolean", "description": "Whether to create parent directories if they don\'t exist (default: false)"}}, ["folder_path", "name"]), self._handle_create_directory)
        self._register_tool("delete", "Delete a file or directory on the Synology NAS (auto-detects type)", TN_PR({"path": {"type": "string", "description": "Full path to the file/directory to delete (must start with /)"}}, ["path"]), self._handle_delete)

        # Download Station
        self._register_tool("ds_get_info", "Get Download Station information and settings", TN, self._handle_ds_get_info)
        self._register_tool("ds_list_tasks", "List all download tasks in Download Station", TN_P({"offset": {"type": "integer", "description": "Starting offset for pagination (default: 0)"}, "limit": {"type": "integer", "description": "Maximum number of tasks to return (default: -1 for all)"}}), self._handle_ds_list_tasks)
        self._register_tool("ds_create_task", "Create a new download task from URL or magnet link", TN_PR({"uri": {"type": "string", "description": "Download URL or magnet link"}, "destination": {"type": "string", "description": "Destination folder path (optional)"}, "username": {"type": "string", "description": "Username for protected downloads (optional)"}, "password": {"type": "string", "description": "Password for protected downloads (optional)"}}, ["uri"]), self._handle_ds_create_task)
        self._register_tool("ds_pause_tasks", "Pause one or more download tasks", TN_PR({"task_ids": {"type": "array", "items": {"type": "string"}, "description": "List of task IDs to pause"}}, ["task_ids"]), self._handle_ds_pause_tasks)
        self._register_tool("ds_resume_tasks", "Resume one or more paused download tasks", TN_PR({"task_ids": {"type": "array", "items": {"type": "string"}, "description": "List of task IDs to resume"}}, ["task_ids"]), self._handle_ds_resume_tasks)
        self._register_tool("ds_delete_tasks", "Delete one or more download tasks", TN_PR({"task_ids": {"type": "array", "items": {"type": "string"}, "description": "List of task IDs to delete"}, "force_complete": {"type": "boolean", "description": "Force delete completed tasks (default: false)"}}, ["task_ids"]), self._handle_ds_delete_tasks)
        self._register_tool("ds_get_statistics", "Get Download Station download/upload statistics", TN, self._handle_ds_get_statistics)
        self._register_tool("ds_list_downloaded_files", "List files in the Download Station destination folder", TN_P({"destination": {"type": "string", "description": "Destination folder to list (optional, defaults to download station\'s default)"}}), self._handle_ds_list_downloaded_files)

        # Health Monitoring
        self._register_tool("synology_system_info", "Get Synology NAS system information: model, serial, DSM version, uptime, temperature", TN, partial(self._handle_health_call, method_name="system_info"))
        self._register_tool("synology_utilization", "Get real-time CPU, memory, swap, and disk I/O utilization", TN, partial(self._handle_health_call, method_name="utilization"))
        self._register_tool("synology_disk_health", "List all physical disks with SMART health status, model, temperature, and capacity", TN, partial(self._handle_health_call, method_name="disk_list"))
        self._register_tool("synology_disk_smart", "Get detailed S.M.A.R.T. attributes for a specific physical disk", TN_PR({"disk_id": {"type": "string", "description": "Disk identifier from synology_disk_health output — either the disk id (e.g. \'sata1\', \'sda\', \'nvme0n1\') or its device path (e.g. \'/dev/sata1\')"}}, ["disk_id"]), self._handle_disk_smart)
        self._register_tool("synology_volume_status", "List all volumes/filesystems with status, total size, used space, and RAID info", TN, partial(self._handle_health_call, method_name="volume_list"))
        self._register_tool("synology_storage_pool", "List RAID/storage pools with RAID level, status, and member disks", TN, partial(self._handle_health_call, method_name="storage_pool_list"))
        self._register_tool("synology_lun_list", "List all iSCSI LUNs with name, UUID, size, used space, status, mapped targets, and backing volume", TN, partial(self._handle_health_call, method_name="lun_list"))
        self._register_tool("synology_lun_get", "Get details for a single iSCSI LUN by name or UUID", TN_PR({"name": {"type": "string", "description": "LUN name or UUID from synology_lun_list output"}}, ["name"]), self._handle_lun_get)
        self._register_tool("synology_network", "Get network interface status and transfer rates", TN, partial(self._handle_health_call, method_name="network_info"))
        self._register_tool("synology_ups", "Get UPS (uninterruptible power supply) status, battery level, and power info", TN, partial(self._handle_health_call, method_name="ups_info"))
        self._register_tool("synology_services", "List installed packages/services and their running status", TN, partial(self._handle_health_call, method_name="package_list"))
        self._register_tool("synology_system_log", "Get recent system log entries for diagnosing issues", TN_P({"offset": {"type": "integer", "description": "Starting offset (default: 0)"}, "limit": {"type": "integer", "description": "Max entries to return (default: 50)"}}), self._handle_system_log)
        self._register_tool("synology_health_summary", "Get a combined health overview: system info, CPU/memory utilization, disk health, volume status, storage pools, network, and UPS — all in one call", TN, partial(self._handle_health_call, method_name="health_summary"))

        # Container Manager
        CI = {
            "type": "object",
            "properties": {
                "nas_name": {"type": "string", "description": "NAS identifier from secrets.json (e.g. \'nas1\', \'nas2\')"},
                "base_url": {"type": "string", "description": "Synology NAS base URL (alternative to nas_name)"},
            },
            "required": [],
        }
        self._register_tool("synology_container_list", "List Container Manager containers", CI | {"properties": {**CI["properties"], "offset": {"type": "integer", "description": "Pagination offset"}, "limit": {"type": "integer", "description": "Maximum containers to return"}, "container_type": {"type": "string", "description": "Container filter (default: all)"}}}, partial(self._handle_container_call, method_name="list"))
        self._register_tool("synology_container_get", "Get detailed information about a specific container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="get"))
        self._register_tool("synology_container_start", "Start a container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="start"))
        self._register_tool("synology_container_stop", "Stop a running container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="stop"))
        self._register_tool("synology_container_restart", "Restart a container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="restart"))
        self._register_tool("synology_container_delete", "Delete a container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="delete"))
        self._register_tool("synology_container_logs", "Get logs from a container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}, "since": {"type": "string", "description": "Return logs since this timestamp (optional)"}, "offset": {"type": "integer", "description": "Pagination offset (default: 0)", "minimum": 0}, "limit": {"type": "integer", "description": "Max lines to return (default: 1000)", "minimum": 1}}, "required": ["name"]}, partial(self._handle_container_call, method_name="logs"))
        self._register_tool("synology_container_resource", "Get resource usage for a container", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Container name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="resource"))
        self._register_tool("synology_container_project_list", "List Docker Compose projects", CI, partial(self._handle_container_call, method_name="project_list"))
        self._register_tool("synology_container_project_get", "Get details of a Docker Compose project", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_get"))
        self._register_tool("synology_container_project_create", "Create a new Docker Compose project", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}, "content": {"type": "string", "description": "Docker Compose YAML content"}, "share_path": {"type": "string", "description": "Share path where the compose file is stored"}, "enable_service_portal": {"type": "boolean", "description": "Enable Synology service portal (default: false)"}, "service_portal_name": {"type": "string", "description": "Optional service portal name"}, "service_portal_port": {"type": "integer", "description": "Optional service portal port"}, "service_portal_protocol": {"type": "string", "description": "Service portal protocol (default: http)"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_create"))
        self._register_tool("synology_container_project_update", "Update a Docker Compose project", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}, "content": {"type": "string", "description": "New Docker Compose YAML content"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_update"))
        self._register_tool("synology_container_project_start", "Start Docker Compose project services", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_start"))
        self._register_tool("synology_container_project_stop", "Stop Docker Compose project services", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_stop"))
        self._register_tool("synology_container_project_restart", "Restart Docker Compose project services", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_restart"))
        self._register_tool("synology_container_project_build", "Build Docker Compose project images", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_build"))
        self._register_tool("synology_container_project_clean", "Clean Docker Compose project resources", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_clean"))
        self._register_tool("synology_container_project_delete", "Delete a Docker Compose project", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Project name (e.g. \'watchtower\')"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="project_delete"))
        self._register_tool("synology_container_image_list", "List Docker images", CI, partial(self._handle_container_call, method_name="image_list"))
        self._register_tool("synology_container_image_get", "Get details of a specific Docker image", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Image repository name (e.g. \'nginx\')"}, "tag": {"type": "string", "description": "Image tag (default: latest)"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="image_get"))
        self._register_tool("synology_container_image_delete", "Delete a Docker image", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Image repository name (e.g. \'nginx\')"}, "tag": {"type": "string", "description": "Image tag (default: latest)"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="image_delete"))
        self._register_tool("synology_container_image_prune_preview", "Preview unused Docker images without deleting anything", CI, partial(self._handle_container_call, method_name="image_prune_preview"))
        self._register_tool("synology_container_image_prune", "Remove Docker images unused by any container; preserves containers, networks, and build cache", CI, partial(self._handle_container_call, method_name="image_prune"))
        self._register_tool("synology_container_image_pull", "Pull a Docker image from a registry", CI | {"properties": {**CI["properties"], "repository": {"type": "string", "description": "Image repository name (e.g. \'nginx\')"}, "tag": {"type": "string", "description": "Image tag (default: latest)"}}}, partial(self._handle_container_call, method_name="image_pull"))
        self._register_tool("synology_container_registry_list", "List configured Docker registries", CI, partial(self._handle_container_call, method_name="registry_list"))
        self._register_tool("synology_container_registry_search", "Search for images in a Docker registry", CI | {"properties": {**CI["properties"], "query": {"type": "string", "description": "Search query for image name"}, "offset": {"type": "integer", "description": "Pagination offset (default: 0)"}, "limit": {"type": "integer", "description": "Max results to return (default: 50)"}}, "required": ["query"]}, partial(self._handle_container_call, method_name="registry_search"))
        self._register_tool("synology_container_registry_tags", "List tags for a repository in a Docker registry", CI | {"properties": {**CI["properties"], "repository": {"type": "string", "description": "Image repository name (e.g. \'nginx\')"}, "tag": {"type": "string", "description": "Image tag (default: latest)"}}}, partial(self._handle_container_call, method_name="registry_tags"))
        self._register_tool("synology_container_registry_download", "Download a Docker image from a registry", CI | {"properties": {**CI["properties"], "repository": {"type": "string", "description": "Image repository name (e.g. \'nginx\')"}, "tag": {"type": "string", "description": "Image tag (default: latest)"}}}, partial(self._handle_container_call, method_name="registry_download"))
        self._register_tool("synology_container_network_list", "List Docker networks", CI, partial(self._handle_container_call, method_name="network_list"))
        self._register_tool("synology_container_network_get", "Get details of a Docker network", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Network name"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="network_get"))
        self._register_tool("synology_container_network_create", "Create a Docker network", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Network name"}, "driver": {"type": "string", "description": "Network driver (default: bridge)"}, "subnet": {"type": "string", "description": "Subnet CIDR (optional)"}, "gateway": {"type": "string", "description": "Gateway IP (optional)"}, "ip_range": {"type": "string", "description": "IP range (optional)"}, "enable_ipv6": {"type": "boolean", "description": "Enable IPv6 (default: false)"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="network_create"))
        self._register_tool("synology_container_network_delete", "Delete a Docker network", CI | {"properties": {**CI["properties"], "name": {"type": "string", "description": "Network name"}}, "required": ["name"]}, partial(self._handle_container_call, method_name="network_delete"))

        # NFS Management
        self._register_tool("synology_nfs_status", "Get NFS service status and configuration (enabled/disabled, NFSv4 settings)", TN, partial(self._handle_nfs_call, method_name="nfs_status"))
        self._register_tool("synology_nfs_enable", "Enable or disable the NFS file service on the Synology NAS", TN_P({"enable": {"type": "boolean", "description": "True to enable NFS, false to disable (default: true)"}, "nfs_v4": {"type": "boolean", "description": "Enable NFSv4 support (default: false)"}}), self._handle_nfs_enable)
        self._register_tool("synology_nfs_list_shares", "List all shared folders with their NFS access permissions", TN, partial(self._handle_nfs_call, method_name="list_shares"))
        self._register_tool("synology_nfs_set_permission", "Set NFS client access permissions on a shared folder (IP/subnet, read/write, squash options)", TN_PR({"share_name": {"type": "string", "description": "Name of the shared folder (e.g. \'media\', \'backups\')"}, "client_ip": {"type": "string", "description": "Client IP or subnet (e.g. \'192.168.1.0/24\', \'10.0.0.5\')"}, "privilege": {"type": "string", "enum": ["readonly", "readwrite"], "description": "Access level (default: readwrite)"}, "squash": {"type": "string", "enum": ["root_squash", "no_root_squash", "all_squash"], "description": "Squash option for root user mapping (default: root_squash)"}, "security": {"type": "string", "enum": ["sys", "krb5", "krb5i", "krb5p"], "description": "Security mode (default: sys/AUTH_SYS)"}}, ["share_name", "client_ip"]), self._handle_nfs_set_permission)
        self._register_tool("synology_create_share", "Create a new shared folder on a Synology NAS volume", TN_PR({"share_name": {"type": "string", "description": "Name of the shared folder to create (e.g. \'rag-corpus\')"}, "vol_path": {"type": "string", "description": "Volume path where the share will be created (e.g. \'/volume1\', \'/volume2\')"}, "description": {"type": "string", "description": "Optional description for the shared folder"}, "enable_recycle_bin": {"type": "boolean", "description": "Enable recycle bin for deleted files (default: true)"}, "recycle_bin_admin_only": {"type": "boolean", "description": "Restrict recycle bin access to administrators only (default: true)"}}, ["share_name", "vol_path"]), self._handle_create_share)

        # User Management
        self._register_tool("synology_list_users", "List all local users on the Synology NAS", TN, partial(self._handle_usermgr_call, method_name="list_users"))
        self._register_tool("synology_get_user", "Get detailed information about a specific user", TN_PR({"name": {"type": "string", "description": "Username to look up"}}, ["name"]), self._handle_usermgr_get_user)
        self._register_tool("synology_create_user", "Create a new local user on the Synology NAS", TN_PR({"name": {"type": "string", "description": "Username for the new account"}, "password": {"type": "string", "description": "Password for the new account"}, "description": {"type": "string", "description": "User description (optional)"}, "email": {"type": "string", "description": "User email address (optional)"}, "cannot_chg_passwd": {"type": "boolean", "description": "Prevent user from changing password (default: false)"}, "passwd_never_expire": {"type": "boolean", "description": "Password never expires (default: true)"}}, ["name", "password"]), self._handle_usermgr_create_user)
        self._register_tool("synology_set_user", "Modify an existing user (rename, change password, enable/disable)", TN_PR({"name": {"type": "string", "description": "Target username to modify"}, "new_name": {"type": "string", "description": "Rename the user (optional)"}, "password": {"type": "string", "description": "New password (optional)"}, "description": {"type": "string", "description": "New description (optional)"}, "email": {"type": "string", "description": "New email (optional)"}, "expired": {"type": "string", "enum": ["normal", "now"], "description": "\'normal\' = active, \'now\' = disabled"}}, ["name"]), self._handle_usermgr_set_user)
        self._register_tool("synology_delete_user", "Delete a local user from the Synology NAS", TN_PR({"name": {"type": "string", "description": "Username to delete"}}, ["name"]), self._handle_usermgr_delete_user)
        self._register_tool("synology_list_groups", "List all local groups on the Synology NAS", TN, partial(self._handle_usermgr_call, method_name="list_groups"))
        self._register_tool("synology_list_group_members", "List members of a specific group", TN_PR({"group": {"type": "string", "description": "Group name to list members of"}}, ["group"]), self._handle_usermgr_list_group_members)
        self._register_tool("synology_add_user_to_group", "Add a user to one or more groups", TN_PR({"username": {"type": "string", "description": "Username to add to groups"}, "groups": {"type": "array", "items": {"type": "string"}, "description": "List of group names to join"}}, ["username", "groups"]), self._handle_usermgr_add_to_group)
        self._register_tool("synology_remove_user_from_group", "Remove a user from one or more groups", TN_PR({"username": {"type": "string", "description": "Username to remove from groups"}, "groups": {"type": "array", "items": {"type": "string"}, "description": "List of group names to leave"}}, ["username", "groups"]), self._handle_usermgr_remove_from_group)
        self._register_tool("synology_get_user_permissions", "Get shared folder permissions for a user", TN_PR({"name": {"type": "string", "description": "Username to check permissions for"}}, ["name"]), self._handle_usermgr_get_permissions)
        self._register_tool("synology_set_user_permissions", "Set shared folder permissions for a user (read/write/deny per folder)", TN_PR({"name": {"type": "string", "description": "Username to set permissions for"}, "permissions": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string", "description": "Shared folder name"}, "is_writable": {"type": "boolean", "description": "Grant write access"}, "is_deny": {"type": "boolean", "description": "Deny access entirely"}}, "required": ["name"]}, "description": "List of folder permission objects"}}, ["name", "permissions"]), self._handle_usermgr_set_permissions)

        # Conditional: login/logout only when auto-login is not available
        if not config.auto_login or not config.has_synology_credentials():
            self._register_tool(
                "synology_login",
                "Authenticate with Synology NAS and establish session.\n\n2FA/OTP accounts: pass `otp_code` on the first login only; DSM will issue a `device_id` in the response, which you can persist into settings.json to skip OTP on future logins. If you already have a `device_id`, pass it instead of `otp_code` — DSM treats trusted devices as already authenticated.",
                {
                    "type": "object",
                    "properties": {
                        "base_url": {"type": "string", "description": "Synology NAS base URL (e.g., https://192.168.1.100:5001)"},
                        "username": {"type": "string", "description": "Username for authentication"},
                        "password": {"type": "string", "description": "Password for authentication"},
                        "otp_code": {"type": "string", "description": "One-time 6-digit code from the user\'s authenticator. Required only on the first 2FA login for a new device. Ignored when `device_id` is also given."},
                        "device_id": {"type": "string", "description": "Long-lived trusted-device token previously issued by DSM (returned as `did` in a successful 2FA login). When supplied, DSM skips the OTP step. Preferred over `otp_code` for repeated logins."},
                    },
                    "required": ["base_url", "username", "password"],
                },
                self._handle_login,
            )
            self._register_tool(
                "synology_logout",
                "Logout from Synology NAS session",
                {
                    "type": "object",
                    "properties": {
                        "base_url": {"type": "string", "description": "Synology NAS base URL"},
                    },
                    "required": ["base_url"],
                },
                self._handle_logout,
            )

    def _get_tool_definitions(self):
        """Get tool definitions from the registry."""
        return [tool for tool, _ in self._tool_registry.values()]


    def _login_nas(self, nas_name: Optional[str]) -> str:
        """Log in to a configured NAS and return its base_url.

        Single login path shared by startup auto-login and the on-demand login
        in _get_base_url, so the two cannot drift apart. Raises with the DSM
        error code on failure; callers decide whether that is fatal.
        """
        nas_cfg = config.get_synology_config(nas_name)
        base_url = nas_cfg["base_url"].rstrip("/")
        label = nas_name or "default"

        if base_url not in self.auth_instances:
            self.auth_instances[base_url] = SynologyAuth(
                base_url, verify_ssl=config.verify_ssl_for(base_url)
            )

        auth = self.auth_instances[base_url]
        auth.on_relogin = self._resync_session_after_relogin
        # Optional 2FA material from settings.json (or legacy .env for
        # otp_code). device_id wins over otp_code; both None means the DSM
        # account has 2FA off.
        result = auth.login(
            nas_cfg["username"],
            nas_cfg["password"],
            otp_code=nas_cfg.get("otp_code"),
            device_id=nas_cfg.get("device_id"),
        )

        if not result.get("success"):
            code = result.get("error", {}).get("code", "?")
            raise Exception(
                f"Login to NAS '{label}' failed (DSM error code {code}). "
                "Common codes: 400 bad account/password, 401 account disabled, "
                "402 permission denied (the account may lack DSM application "
                "access), 403 2FA required, 404 2FA code failed."
            )

        session_id = result["data"]["sid"]
        self.sessions[base_url] = session_id
        syno_token = result["data"].get("synotoken")
        if syno_token:
            self.syno_tokens[base_url] = syno_token
        else:
            self.syno_tokens.pop(base_url, None)

        # Store the name->url mapping for tool resolution.
        self.nas_name_map[label] = base_url
        if nas_name is None:
            self.nas_name_map[base_url] = base_url

        # Persist the DSM device token rather than asking the user to copy it
        # by hand. DSM may return a *refreshed* `did` on a login that already
        # presented one, which retires the old value; keeping it only in
        # memory means the token in settings.json is dead as soon as this
        # process exits, and every later start falls back to "OTP required"
        # (403). Only present when DSM issued one — i.e. the first-time OTP
        # login (the steady-state `device_id` path doesn't echo it back).
        did = result["data"].get("did")
        if did and nas_name:
            if config.save_device_id(nas_name, did):
                logger.info(f"{label}: stored refreshed device_id")
        elif did:
            # Legacy .env single-NAS mode has no settings.json entry to write
            # into, so fall back to telling the user. Logged in full because
            # (a) the value is destined for settings.json anyway and (b) it's
            # useless without the password, so truncation provides no
            # meaningful protection.
            logger.warning(
                f"{label}: 2FA bootstrap - copy this device_id into "
                f"settings.json to skip OTP on future starts: {did}"
            )
        logger.info(f"{label}: session {session_id[:8]}...")

        for inst_dict in self._service_instance_dicts():
            inst_dict.pop(base_url, None)

        return base_url

    def _get_filestation(self, base_url: str) -> SynologyFileStation:
        """Get or create FileStation instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.filestation_instances:
            session_id = self.sessions[base_url]
            self.filestation_instances[base_url] = SynologyFileStation(
                base_url,
                session_id,
                verify_ssl=config.verify_ssl_for(base_url),
                syno_token=self.syno_tokens.get(base_url),
            )

        return self.filestation_instances[base_url]

    def _get_downloadstation(self, base_url: str) -> SynologyDownloadStation:
        """Get or create DownloadStation instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.downloadstation_instances:
            session_id = self.sessions[base_url]
            self.downloadstation_instances[base_url] = SynologyDownloadStation(
                base_url,
                session_id,
                verify_ssl=config.verify_ssl_for(base_url),
                syno_token=self.syno_tokens.get(base_url),
            )

        return self.downloadstation_instances[base_url]

    def _get_health(self, base_url: str) -> SynologyHealth:
        """Get or create Health instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.health_instances:
            session_id = self.sessions[base_url]
            self.health_instances[base_url] = SynologyHealth(
                base_url,
                session_id,
                verify_ssl=config.verify_ssl_for(base_url),
                syno_token=self.syno_tokens.get(base_url),
            )

        return self.health_instances[base_url]

    def _get_container(self, base_url: str) -> SynologyContainer:
        """Get or create Container Manager instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.container_instances:
            session_id = self.sessions[base_url]
            self.container_instances[base_url] = SynologyContainer(
                base_url,
                session_id,
                verify_ssl=config.verify_ssl_for(base_url),
                syno_token=self.syno_tokens.get(base_url),
                ssh_username=config.ssh_credentials_for(base_url)[0],
                ssh_password=config.ssh_credentials_for(base_url)[1],
                ssh_known_hosts=config.ssh_credentials_for(base_url)[2],
            )

        return self.container_instances[base_url]

    def _get_nfs(self, base_url: str) -> SynologyNFS:
        """Get or create NFS instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.nfs_instances:
            session_id = self.sessions[base_url]
            self.nfs_instances[base_url] = SynologyNFS(
                base_url,
                session_id,
                verify_ssl=config.verify_ssl_for(base_url),
                syno_token=self.syno_tokens.get(base_url),
            )

        return self.nfs_instances[base_url]

    def _get_usermgr(self, base_url: str) -> SynologyUserManager:
        """Get or create UserManager instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.usermgr_instances:
            session_id = self.sessions[base_url]
            self.usermgr_instances[base_url] = SynologyUserManager(
                base_url,
                session_id,
                verify_ssl=config.verify_ssl_for(base_url),
                syno_token=self.syno_tokens.get(base_url),
            )

        return self.usermgr_instances[base_url]

    async def _auto_login_if_configured(self):
        """Automatically login to all configured NAS units."""
        logger.debug(f"Config: {config}")

        if not config.auto_login:
            logger.info("Auto-login disabled")
            return
        if not config.has_synology_credentials():
            logger.warning("No Synology credentials configured")
            return

        nas_names = config.get_nas_names()
        if not nas_names:
            # Legacy single-NAS from .env
            nas_names = [None]

        success_count = 0
        for nas_name in nas_names:
            label = nas_name or "default"
            try:
                logger.info(f"Auto-login: {label}...")
                base_url = self._login_nas(nas_name)
                logger.info(f"{label}: session established ({base_url})")
                success_count += 1
            except Exception as e:
                logger.warning(f"{nas_name or 'default'}: {e}")
                if config.debug:
                    logger.debug("Traceback:", exc_info=True)

        if success_count == 0:
            raise Exception("Auto-login failed for all configured NAS units — stopping server.")
        logger.info(f"Connected to {success_count}/{len(nas_names)} NAS unit(s)")

    async def _dispatch_tool(
        self, name: str, arguments: dict
    ) -> list[types.TextContent]:
        """Dispatch a tool call, raising on failure (caller handles isError)."""
        logger.debug(f"Executing tool: {name}")
        try:
            _, handler = self._tool_registry[name]
        except KeyError:
            raise ValueError(f"Unknown tool: {name}")
        return await handler(arguments)

    def _service_instance_dicts(self):
        """Canonical set of per-domain instance caches keyed by base_url.

        Returned as one tuple so session login/relogin/logout/cleanup all evict
        the same set; adding a new service means updating this one place.
        """
        return (
            self.filestation_instances,
            self.downloadstation_instances,
            self.health_instances,
            self.container_instances,
            self.nfs_instances,
            self.usermgr_instances,
        )

    def _get_base_url(self, arguments: dict, *, allow_login: bool = True) -> str:
        """Get base URL from arguments or config.

        Accepts either:
          - base_url: a full URL like http://10.0.0.51:5000
          - nas_name: a key from secrets.json like 'nas1', 'nas2'
        Falls back to the first connected NAS if neither is provided.
        """
        # Try nas_name first
        nas_name = arguments.get("nas_name")
        if nas_name:
            base_url = self.nas_name_map.get(nas_name)
            # The mapping outlives the session: synology_logout drops the
            # session but leaves the name mapped. Returning the URL anyway
            # would bypass the on-demand login below and fail downstream with
            # "No active session", so lazy login would work exactly once per
            # process. When no login is permitted (logout) the URL is still the
            # right answer, and the caller reports the missing session itself.
            if base_url and (base_url in self.sessions or not allow_login):
                return base_url
            # nas_name_map is populated only by startup auto-login, so with
            # AUTO_LOGIN=false every nas_name call fails here and the only way
            # in is synology_login -- which takes the password as a tool
            # argument, so an MCP client writes it into its transcript. The
            # credentials are already configured, so log in from them on demand
            # and keep them inside the server.
            if allow_login:
                configured_name = nas_name if nas_name in config.nas_configs else None
                target_url = None
                if configured_name is not None:
                    target_url = config.get_synology_config(configured_name).get("base_url")
                elif not config.nas_configs and config.has_synology_credentials():
                    # Legacy single-NAS setups have no settings.json entries;
                    # _handle_list_nas surfaces them as "default" or the bare URL.
                    legacy_url = config.get_synology_config(None).get("base_url")
                    if nas_name in ("default", legacy_url):
                        target_url = legacy_url

                if target_url:
                    # Normalize so the configured value (which may carry a
                    # trailing slash) matches the stripped key under which
                    # _login_nas / _handle_login store sessions.
                    target_url = target_url.rstrip("/")
                    # A session may already exist for this URL without the name
                    # being mapped -- synology_login establishes one but does
                    # not touch nas_name_map. Logging in again would overwrite
                    # the tracked SID and strand that first session, leaving it
                    # open on the NAS and unreachable by logout.
                    if target_url in self.sessions:
                        self.nas_name_map[nas_name] = target_url
                        return target_url
                    return self._login_nas(configured_name)
            raise Exception(
                f"NAS '{nas_name}' not found. Available: {list(self.nas_name_map.keys())}"
            )

        # Try explicit base_url
        base_url = arguments.get("base_url")
        if base_url:
            return base_url.rstrip("/")

        # Fall back to first connected session
        if self.sessions:
            return next(iter(self.sessions))

        raise Exception("No nas_name or base_url provided and no active sessions.")

    def _validate_url(self, url: str) -> bool:
        """Validate URL format and scheme.

        Args:
            url: URL to validate

        Returns:
            True if URL is valid, False otherwise
        """
        from urllib.parse import urlparse

        try:
            result = urlparse(url)
            return bool(result.scheme in ("http", "https") and result.netloc)
        except Exception:
            return False

    def _resync_session_after_relogin(
        self, base_url: str, session_id: Optional[str], syno_token: Optional[str]
    ) -> None:
        """Resync cached session state after a transparent relogin (DSM 119 recovery).

        SynologyAuth invokes this once it re-authenticates an expired session.
        Without it, self.sessions / self.syno_tokens keep the dead SID — so logout
        would target the expired session (leaking the new one) and lazily-created
        subsystems would start with a stale SID. Mirrors the post-login bookkeeping.
        """
        if not session_id:
            return
        self.sessions[base_url] = session_id
        if syno_token:
            self.syno_tokens[base_url] = syno_token
        else:
            self.syno_tokens.pop(base_url, None)
        # Drop cached service instances so they rebuild with the refreshed session.
        for inst_dict in self._service_instance_dicts():
            inst_dict.pop(base_url, None)

    async def _handle_login(self, arguments: dict) -> list[types.TextContent]:
        """Handle Synology login."""
        base_url = arguments["base_url"].rstrip("/")
        username = arguments["username"]
        password = arguments["password"]
        # Both 2FA fields are optional. When both are supplied, `device_id`
        # wins (DSM won't ask for OTP on a trusted device). When neither is
        # supplied, behavior matches pre-2FA support.
        otp_code = arguments.get("otp_code")
        device_id = arguments.get("device_id")

        # Validate base_url format
        if not self._validate_url(base_url):
            return [
                types.TextContent(
                    type="text",
                    text=f"Invalid base_url format: {base_url}\n"
                    "URL must start with http:// or https:// and include a hostname",
                )
            ]

        # Create or get auth instance
        if base_url not in self.auth_instances:
            self.auth_instances[base_url] = SynologyAuth(base_url, verify_ssl=config.verify_ssl_for(base_url))

        auth = self.auth_instances[base_url]
        auth.on_relogin = self._resync_session_after_relogin

        # Perform login
        result = auth.login(username, password, otp_code=otp_code, device_id=device_id)

        # Store session if successful
        if result.get("success"):
            session_id = result["data"]["sid"]
            self.sessions[base_url] = session_id
            syno_token = result["data"].get("synotoken")
            if syno_token:
                self.syno_tokens[base_url] = syno_token
            else:
                self.syno_tokens.pop(base_url, None)

            # Drop cached service instances so they pick up the new session/token
            for inst_dict in self._service_instance_dicts():
                inst_dict.pop(base_url, None)

            return [
                types.TextContent(
                    type="text",
                    text=f"Successfully authenticated with {base_url}\n"
                    f"Session ID: {session_id}\n"
                    f"Response: {json.dumps(result, indent=2)}",
                )
            ]
        else:
            return [
                types.TextContent(
                    type="text", text=f"Authentication failed: {json.dumps(result, indent=2)}"
                )
            ]

    async def _handle_logout(self, arguments: dict) -> list[types.TextContent]:
        """Handle Synology logout."""
        # Never log in just to log out: that would create a DSM session purely
        # to tear it down, and can trip OTP failures, lockout counters and
        # login audit events on a NAS the user never meant to touch.
        base_url = self._get_base_url(arguments, allow_login=False)

        if base_url not in self.sessions:
            return [types.TextContent(type="text", text=f"No active session found for {base_url}")]

        session_id = self.sessions[base_url]
        auth = self.auth_instances[base_url]

        # Use the improved logout method
        result = auth.logout(session_id)

        # Handle the result and provide detailed feedback
        if result.get("success"):
            # Remove session and all cached service instances on successful logout
            del self.sessions[base_url]
            self.syno_tokens.pop(base_url, None)
            for inst_dict in self._service_instance_dicts():
                inst_dict.pop(base_url, None)

            return [
                types.TextContent(
                    type="text",
                    text=f"✅ Successfully logged out from {base_url}\n"
                    f"Session {session_id[:10]}... has been terminated",
                )
            ]
        else:
            error_info = result.get("error", {})
            error_code = error_info.get("code", "unknown")
            error_msg = error_info.get("message", "Unknown error")

            # Handle expected session expiration gracefully
            if str(error_code) in {"105", "106", "no_session"}:
                # Still clean up local session data
                del self.sessions[base_url]
                self.syno_tokens.pop(base_url, None)
                for inst_dict in self._service_instance_dicts():
                    inst_dict.pop(base_url, None)

                return [
                    types.TextContent(
                        type="text",
                        text=f"⚠️ Session for {base_url} was already expired or invalid\n"
                        f"Local session data has been cleaned up\n"
                        f"Details: {error_code} - {error_msg}",
                    )
                ]
            else:
                return [
                    types.TextContent(
                        type="text",
                        text=f"❌ Logout failed for {base_url}\n"
                        f"Error: {error_code} - {error_msg}\n"
                        f"Full response: {json.dumps(result, indent=2)}",
                    )
                ]

    async def _handle_status(self, arguments: dict) -> list[types.TextContent]:
        """Handle status check."""
        status_info = []

        # Show configuration status
        nas_names = config.get_nas_names()
        if nas_names:
            status_info.append(f"✓ Configured NAS units: {', '.join(nas_names)}")
        elif config.has_synology_credentials():
            status_info.append(f"✓ Configuration: {config.synology_url}")
        else:
            status_info.append("⚠ No Synology credentials configured")
        status_info.append(f"✓ Auto-login: {'enabled' if config.auto_login else 'disabled'}")

        # Show active sessions with NAS names
        if self.sessions:
            # Build reverse map: base_url -> nas_name
            url_to_name = {v: k for k, v in self.nas_name_map.items()}
            status_info.append(f"\nActive sessions ({len(self.sessions)}):")
            for base_url, session_id in self.sessions.items():
                name = url_to_name.get(base_url, "?")
                status_info.append(f"• {name} ({base_url}): session {session_id[:10]}...")

            # Show service instances
            if self.filestation_instances:
                status_info.append(f"\nFileStation instances: {len(self.filestation_instances)}")
            if self.downloadstation_instances:
                status_info.append(
                    f"DownloadStation instances: {len(self.downloadstation_instances)}"
                )
        else:
            status_info.append("\nNo active Synology sessions")

        return [types.TextContent(type="text", text="\n".join(status_info))]

    async def _handle_list_nas(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing configured NAS units from secrets.json."""
        nas_list = []

        # Get NAS names from config
        nas_names = config.get_nas_names()

        if not nas_names:
            # Fall back to .env if no secrets.json
            if config.synology_url:
                nas_list.append(
                    {
                        "nas_name": "default",
                        "base_url": config.synology_url,
                        "username": config.synology_username,
                        "note": "From .env (single NAS)",
                    }
                )
                nas_list.append(
                    {
                        "message": "No multi-NAS configured. Add credentials to ~/.config/synology-mcp/secrets.json for multi-NAS support."
                    }
                )
            else:
                nas_list.append(
                    {
                        "message": "No NAS configured. Set up credentials in .env or ~/.config/synology-mcp/secrets.json"
                    }
                )
        else:
            # List each NAS from secrets.json
            for nas_name in nas_names:
                nas_cfg = config.get_synology_config(nas_name)
                url = nas_cfg.get("base_url", "unknown")
                username = nas_cfg.get("username", "unknown")
                note = nas_cfg.get("note", "")

                # Check if connected
                connected = url in self.sessions

                nas_info = {
                    "nas_name": nas_name,
                    "base_url": url,
                    "username": username,
                    "connected": connected,
                }
                if note:
                    nas_info["note"] = note
                nas_list.append(nas_info)

        return [types.TextContent(type="text", text=json.dumps(nas_list, indent=2))]

    async def _handle_list_shares(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing shares."""
        base_url = self._get_base_url(arguments)
        filestation = self._get_filestation(base_url)

        shares = filestation.list_shares()

        return [types.TextContent(type="text", text=json.dumps(shares, indent=2))]

    async def _handle_list_directory(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing directory contents."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]

        filestation = self._get_filestation(base_url)
        files = filestation.list_directory(path)

        return [types.TextContent(type="text", text=json.dumps(files, indent=2))]

    async def _handle_get_file_info(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting file information."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]

        filestation = self._get_filestation(base_url)
        info = filestation.get_file_info(path)

        return [types.TextContent(type="text", text=json.dumps(info, indent=2))]

    async def _handle_search_files(self, arguments: dict) -> list[types.TextContent]:
        """Handle searching files."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        pattern = arguments["pattern"]

        filestation = self._get_filestation(base_url)
        results = filestation.search_files(path, pattern)

        return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

    async def _handle_get_file_content(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting file content."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]

        filestation = self._get_filestation(base_url)
        content = filestation.get_file_content(path)

        return [types.TextContent(type="text", text=content)]

    async def _handle_rename_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle renaming a file or directory."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        new_name = arguments["new_name"]

        filestation = self._get_filestation(base_url)
        result = filestation.rename_file(path, new_name)

        return [
            types.TextContent(type="text", text=f"Rename result: {json.dumps(result, indent=2)}")
        ]

    async def _handle_move_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle moving a file or directory."""
        base_url = self._get_base_url(arguments)
        source_path = arguments["source_path"]
        destination_path = arguments["destination_path"]
        overwrite = arguments.get("overwrite", False)  # Default to False if not provided

        filestation = self._get_filestation(base_url)
        result = filestation.move_file(source_path, destination_path, overwrite)

        return [types.TextContent(type="text", text=f"Move result: {json.dumps(result, indent=2)}")]

    async def _handle_create_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new file with specified content on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        content = arguments.get("content", "")
        overwrite = arguments.get("overwrite", False)

        filestation = self._get_filestation(base_url)
        result = filestation.create_file(path, content, overwrite)

        return [
            types.TextContent(
                type="text", text=f"Create file result: {json.dumps(result, indent=2)}"
            )
        ]

    async def _handle_create_directory(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new directory on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        folder_path = arguments["folder_path"]
        name = arguments["name"]
        force_parent = arguments.get("force_parent", False)

        filestation = self._get_filestation(base_url)
        result = filestation.create_directory(folder_path, name, force_parent)

        return [
            types.TextContent(
                type="text", text=f"Create directory result: {json.dumps(result, indent=2)}"
            )
        ]

    async def _handle_delete(self, arguments: dict) -> list[types.TextContent]:
        """Handle deleting a file or directory on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]

        filestation = self._get_filestation(base_url)
        result = filestation.delete(path)

        return [
            types.TextContent(type="text", text=f"Delete result: {json.dumps(result, indent=2)}")
        ]

    async def _handle_ds_get_info(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting Download Station information and settings."""
        base_url = self._get_base_url(arguments)
        downloadstation = self._get_downloadstation(base_url)

        info = downloadstation.get_info()

        return [types.TextContent(type="text", text=json.dumps(info, indent=2))]

    async def _handle_ds_list_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing all download tasks in Download Station."""
        base_url = self._get_base_url(arguments)
        downloadstation = self._get_downloadstation(base_url)

        tasks = downloadstation.list_tasks()

        return [types.TextContent(type="text", text=json.dumps(tasks, indent=2))]

    async def _handle_ds_create_task(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new download task from URL or magnet link."""
        base_url = self._get_base_url(arguments)
        uri = arguments["uri"]
        destination = arguments.get("destination")
        username = arguments.get("username")
        password = arguments.get("password")

        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.create_task(uri, destination, username, password)

        return [
            types.TextContent(
                type="text", text=f"Create task result: {json.dumps(result, indent=2)}"
            )
        ]

    async def _handle_ds_pause_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle pausing one or more download tasks."""
        base_url = self._get_base_url(arguments)
        task_ids = arguments["task_ids"]

        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.pause_tasks(task_ids)

        return [
            types.TextContent(
                type="text", text=f"Pause tasks result: {json.dumps(result, indent=2)}"
            )
        ]

    async def _handle_ds_resume_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle resuming one or more paused download tasks."""
        base_url = self._get_base_url(arguments)
        task_ids = arguments["task_ids"]

        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.resume_tasks(task_ids)

        return [
            types.TextContent(
                type="text", text=f"Resume tasks result: {json.dumps(result, indent=2)}"
            )
        ]

    async def _handle_ds_delete_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle deleting one or more download tasks."""
        base_url = self._get_base_url(arguments)
        task_ids = arguments["task_ids"]
        force_complete = arguments.get("force_complete", False)

        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.delete_tasks(task_ids, force_complete)

        return [
            types.TextContent(
                type="text", text=f"Delete tasks result: {json.dumps(result, indent=2)}"
            )
        ]

    async def _handle_ds_get_statistics(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting Download Station download/upload statistics."""
        base_url = self._get_base_url(arguments)
        downloadstation = self._get_downloadstation(base_url)

        statistics = downloadstation.get_statistics()

        return [types.TextContent(type="text", text=json.dumps(statistics, indent=2))]

    async def _handle_ds_list_downloaded_files(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing files in the download destination."""
        base_url = self._get_base_url(arguments)
        destination = arguments.get("destination")
        downloadstation = self._get_downloadstation(base_url)

        files = downloadstation.list_downloaded_files(destination)

        return [types.TextContent(type="text", text=json.dumps(files, indent=2))]

    # ------------------------------------------------------------------
    # Health monitoring handlers
    # ------------------------------------------------------------------

    async def _handle_health_call(
        self, arguments: dict, method_name: str
    ) -> list[types.TextContent]:
        """Generic handler for health monitoring calls."""
        base_url = self._get_base_url(arguments)
        health = self._get_health(base_url)
        result = getattr(health, method_name)()
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_disk_smart(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting SMART info for a specific disk."""
        base_url = self._get_base_url(arguments)
        disk_id = arguments["disk_id"]
        health = self._get_health(base_url)
        result = health.disk_smart_info(disk_id)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_lun_get(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting details for a single iSCSI LUN."""
        base_url = self._get_base_url(arguments)
        name = arguments["name"]
        health = self._get_health(base_url)
        result = health.lun_get(name)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_system_log(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting system log entries."""
        base_url = self._get_base_url(arguments)
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 50)
        health = self._get_health(base_url)
        result = health.system_log(offset=offset, limit=limit)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ------------------------------------------------------------------
    # NFS management handlers
    # ------------------------------------------------------------------

    async def _handle_nfs_call(self, arguments: dict, method_name: str) -> list[types.TextContent]:
        """Generic handler for NFS calls."""
        base_url = self._get_base_url(arguments)
        nfs = self._get_nfs(base_url)
        result = getattr(nfs, method_name)()
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_nfs_enable(self, arguments: dict) -> list[types.TextContent]:
        """Handle enabling/disabling NFS service."""
        base_url = self._get_base_url(arguments)
        enable = arguments.get("enable", True)
        nfs_v4 = arguments.get("nfs_v4", False)
        nfs = self._get_nfs(base_url)
        result = nfs.nfs_enable(enable=enable, nfs_v4=nfs_v4)
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_nfs_set_permission(self, arguments: dict) -> list[types.TextContent]:
        """Handle setting NFS permissions on a share."""
        base_url = self._get_base_url(arguments)
        nfs = self._get_nfs(base_url)
        result = nfs.set_nfs_permission(
            share_name=arguments["share_name"],
            client_ip=arguments["client_ip"],
            privilege=arguments.get("privilege", "readwrite"),
            squash=arguments.get("squash", "root_squash"),
            security=arguments.get("security", "sys"),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_create_share(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new shared folder."""
        base_url = self._get_base_url(arguments)
        nfs = self._get_nfs(base_url)
        result = nfs.create_share(
            name=arguments["share_name"],
            vol_path=arguments["vol_path"],
            desc=arguments.get("description", ""),
            enable_recycle_bin=arguments.get("enable_recycle_bin", True),
            recycle_bin_admin_only=arguments.get("recycle_bin_admin_only", True),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ------------------------------------------------------------------
    # Container Manager handlers
    # ------------------------------------------------------------------

    async def _handle_container_call(
        self, arguments: dict, method_name: str
    ) -> list[types.TextContent]:
        """Handle Container Manager container operations."""
        base_url = self._get_base_url(arguments)
        container = self._get_container(base_url)

        if method_name == "list":
            result = container.list_containers(
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", -1),
                container_type=arguments.get("container_type", "all"),
            )
        elif method_name == "project_list":
            result = container.list_projects()
        elif method_name == "project_create":
            result = container.create_project(
                name=arguments["name"],
                share_path=arguments["share_path"],
                content=arguments["content"],
                enable_service_portal=arguments.get("enable_service_portal", False),
                service_portal_name=arguments.get("service_portal_name"),
                service_portal_port=arguments.get("service_portal_port"),
                service_portal_protocol=arguments.get("service_portal_protocol", "http"),
            )
        elif method_name == "project_update":
            result = container.update_project(
                name=arguments["name"],
                content=arguments["content"],
                enable_service_portal=arguments.get("enable_service_portal"),
                service_portal_name=arguments.get("service_portal_name"),
                service_portal_port=arguments.get("service_portal_port"),
                service_portal_protocol=arguments.get("service_portal_protocol"),
            )
        elif method_name == "project_delete":
            result = container.delete_project(arguments["name"])
        elif method_name == "image_list":
            result = container.list_images(
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", -1),
                show_dsm=arguments.get("show_dsm", False),
            )
        elif method_name == "image_prune":
            result = container.prune_images()
        elif method_name == "image_prune_preview":
            result = container.preview_image_prune()
        elif method_name in {"image_get", "image_delete"}:
            image_method = {
                "image_get": container.get_image,
                "image_delete": container.delete_image,
            }[method_name]
            result = image_method(arguments["name"], tag=arguments.get("tag", "latest"))
        elif method_name in {"image_pull", "registry_download"}:
            result = container.pull_image(
                arguments["repository"],
                tag=arguments.get("tag", "latest"),
            )
        elif method_name == "registry_list":
            result = container.list_registries()
        elif method_name == "registry_search":
            result = container.search_registry(
                arguments["query"],
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", 50),
            )
        elif method_name == "registry_tags":
            result = container.list_registry_tags(
                arguments["repository"],
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", 50),
            )
        elif method_name == "network_list":
            result = container.list_networks()
        elif method_name == "network_get":
            result = container.get_network(arguments["name"])
        elif method_name == "network_create":
            result = container.create_network(
                arguments["name"],
                driver=arguments.get("driver", "bridge"),
                subnet=arguments.get("subnet"),
                gateway=arguments.get("gateway"),
                ip_range=arguments.get("ip_range"),
                enable_ipv6=arguments.get("enable_ipv6", False),
            )
        elif method_name == "network_delete":
            result = container.delete_network(arguments["name"])
        elif method_name == "delete":
            result = container.delete_container(
                arguments["name"],
                force=arguments.get("force", False),
                preserve_profile=arguments.get("preserve_profile", True),
            )
        elif method_name == "logs":
            result = container.get_container_logs(
                arguments["name"],
                since=arguments.get("since"),
                offset=arguments.get("offset", 0),
                limit=arguments.get("limit", 1000),
            )
        elif method_name in {
            "project_get",
            "project_start",
            "project_stop",
            "project_restart",
            "project_build",
            "project_clean",
        }:
            project_method = {
                "project_get": container.get_project,
                "project_start": container.start_project,
                "project_stop": container.stop_project,
                "project_restart": container.restart_project,
                "project_build": container.build_project,
                "project_clean": container.clean_project,
            }[method_name]
            result = project_method(arguments["name"])
        elif method_name in {"get", "start", "stop", "restart", "resource"}:
            container_method = {
                "get": container.get_container,
                "start": container.start_container,
                "stop": container.stop_container,
                "restart": container.restart_container,
                "resource": container.get_container_resource,
            }[method_name]
            result = container_method(arguments["name"])
        else:
            raise ValueError(f"Unknown container method: {method_name}")

        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    # ------------------------------------------------------------------
    # User management handlers
    # ------------------------------------------------------------------

    async def _handle_usermgr_call(
        self, arguments: dict, method_name: str
    ) -> list[types.TextContent]:
        """Generic handler for simple user management calls."""
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = getattr(usermgr, method_name)()
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_get_user(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.get_user(arguments["name"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_create_user(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.create_user(
            name=arguments["name"],
            password=arguments["password"],
            description=arguments.get("description", ""),
            email=arguments.get("email", ""),
            cannot_chg_passwd=arguments.get("cannot_chg_passwd", False),
            passwd_never_expire=arguments.get("passwd_never_expire", True),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_set_user(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.set_user(
            name=arguments["name"],
            new_name=arguments.get("new_name"),
            password=arguments.get("password"),
            description=arguments.get("description"),
            email=arguments.get("email"),
            expired=arguments.get("expired"),
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_delete_user(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.delete_user(arguments["name"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_list_group_members(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.list_group_members(arguments["group"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_add_to_group(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.add_user_to_group(arguments["username"], arguments["groups"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_remove_from_group(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.remove_user_from_group(arguments["username"], arguments["groups"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_get_permissions(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.get_user_permissions(arguments["name"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _handle_usermgr_set_permissions(self, arguments: dict) -> list[types.TextContent]:
        base_url = self._get_base_url(arguments)
        usermgr = self._get_usermgr(base_url)
        result = usermgr.set_user_permissions(arguments["name"], arguments["permissions"])
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async def _run_stdio(self):
        """Serve the MCP protocol over stdio until the client disconnects."""
        logger.info("Starting MCP server on stdio...")
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name=config.server_name,
                    server_version=config.server_version,
                    capabilities=self.server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    async def run_http(self):
        """Run the MCP server over Streamable HTTP (MCP 2.0 native).

        Serves the same Synology tools over HTTP so remote MCP clients can
        connect via a URL (Claude Desktop custom connectors, Claude.ai web,
        Cursor, etc.) without requiring stdio/SSH access.
        """
        # Validate configuration first
        config_errors = config.validate_config()
        if config_errors and config.auto_login:
            error_msg = f"Configuration errors: {', '.join(config_errors)}"
            logger.error(error_msg)
            raise Exception(f"Invalid configuration - stopping server. {error_msg}")
        elif config.debug:
            logger.debug(f"Configuration loaded: {config}")

        # Attempt auto-login if configured (this will raise exception on failure and stop server)
        logger.info("Attempting auto-login...")
        await self._auto_login_if_configured()

        try:
            import uvicorn
            from mcp.server.transport_security import TransportSecuritySettings

            # When the server binds to a non-loopback address (e.g. 0.0.0.0 in
            # Docker), the SDK's auto DNS rebinding protection does not engage.
            # Pass explicit TransportSecuritySettings with the expected
            # Host/Origin values from the reverse proxy perspective. The
            # docker-compose port mapping (127.0.0.1:8765) restricts which
            # host can reach the port, so the allowed values are the ones
            # the reverse proxy sends.
            host = config.http_host
            port = config.http_port
            transport_security = None

            # Build allowlist from env if set, otherwise default to loopback
            # host:port. Each side falls back independently so partial env var
            # config behaves symmetrically.
            loopback_default_hosts = [f"127.0.0.1:{port}", f"localhost:{port}"]
            loopback_default_origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
            allowed_hosts = config.http_allowed_hosts or loopback_default_hosts
            allowed_origins = config.http_allowed_origins or loopback_default_origins
            if allowed_hosts or allowed_origins:
                transport_security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=allowed_hosts,
                    allowed_origins=allowed_origins,
                )

            app = self.server.streamable_http_app(
                streamable_http_path=config.http_path,
                host=host,
                transport_security=transport_security,
            )
            logger.info(
                f"Starting Streamable HTTP MCP server on http://{config.http_host}:{config.http_port}{config.http_path}"
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=config.http_host,
                    port=config.http_port,
                    log_level="info" if config.debug else "warning",
                )
            )
            await server.serve()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal, cleaning up sessions...")
        except Exception as e:
            logger.error(f"Server runtime error: {e}")
            if config.debug:
                logger.debug("Traceback:", exc_info=True)
            raise
        finally:
            # Always attempt session cleanup on shutdown
            if self.sessions:
                logger.info("Cleaning up active sessions...")
                cleanup_results = await self.cleanup_sessions()

                if cleanup_results:
                    logger.info("Session cleanup summary:")
                    for result in cleanup_results:
                        logger.info(f"  {result}")

                logger.info("Session cleanup completed")
            else:
                logger.info("No active sessions to clean up")

    async def run(self):
        """Run the MCP server over the configured transport."""
        if config.http_enabled:
            return await self.run_http()
        return await self.run_stdio()

    async def run_stdio(self):
        """Run the MCP server over stdio (default)."""
        # Validate configuration first
        config_errors = config.validate_config()
        if config_errors and config.auto_login:
            error_msg = f"Configuration errors: {', '.join(config_errors)}"
            logger.error(error_msg)
            raise Exception(f"Invalid configuration - stopping server. {error_msg}")
        elif config.debug:
            logger.debug(f"Configuration loaded: {config}")

        # Attempt auto-login if configured (this will raise exception on failure and stop server)
        logger.info("Attempting auto-login...")
        await self._auto_login_if_configured()

        # Only start server if auto-login succeeded (or wasn't required)
        try:
            await self._run_stdio()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal, cleaning up sessions...")
        except Exception as e:
            logger.error(f"Server runtime error: {e}")
            if config.debug:
                logger.debug("Traceback:", exc_info=True)
            raise
        finally:
            # Always attempt session cleanup on shutdown
            if self.sessions:
                logger.info("Cleaning up active sessions...")
                cleanup_results = await self.cleanup_sessions()

                if cleanup_results:
                    logger.info("Session cleanup summary:")
                    for result in cleanup_results:
                        logger.info(f"  {result}")

                logger.info("Session cleanup completed")
            else:
                logger.info("No active sessions to clean up")

    async def cleanup_sessions(self):
        """Clean up all active sessions during shutdown."""
        cleanup_results = []

        for base_url, session_id in list(self.sessions.items()):
            try:
                auth = self.auth_instances.get(base_url)
                if auth:
                    logger.info(f"Cleaning up session for {base_url}...")
                    result = auth.logout(session_id)

                    if result.get("success"):
                        logger.info(f"Session {session_id[:10]}... logged out successfully")
                        cleanup_results.append(f"{base_url}: Logged out successfully")
                    else:
                        error_info = result.get("error", {})
                        error_code = error_info.get("code", "unknown")

                        if str(error_code) in {"105", "106", "no_session"}:
                            logger.info(f"Session {session_id[:10]}... was already expired")
                            cleanup_results.append(f"{base_url}: Session already expired")
                        else:
                            logger.error(f"Failed to logout {session_id[:10]}...: {error_code}")
                            cleanup_results.append(f"{base_url}: Logout failed - {error_code}")

                # Always clear local data
                del self.sessions[base_url]
                self.syno_tokens.pop(base_url, None)
                for inst_dict in self._service_instance_dicts():
                    inst_dict.pop(base_url, None)

            except Exception as e:
                logger.error(f"Exception during cleanup for {base_url}: {e}")
                cleanup_results.append(f"{base_url}: Exception - {str(e)}")

        return cleanup_results


async def main():
    """Main entry point."""
    server = SynologyMCPServer()
    await server.run()


def cli():
    """Synchronous CLI entry point for 'synology-mcp' console script."""
    from config import config

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("synology-mcp")

    if config.xiaozhi_enabled:
        logger.info("Starting Synology MCP Server with Xiaozhi Bridge")
        from multiclient_bridge import main as bridge_main

        return asyncio.run(bridge_main())
    else:
        logger.info("Starting Synology MCP Server")
        return asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
