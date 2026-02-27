# src/mcp_server.py - MCP Server for Synology NAS operations

import asyncio
import json
import logging
from typing import Dict

import urllib3

logger = logging.getLogger(__name__)

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions

from auth import SynologyAuth
from config import config
from downloadstation import SynologyDownloadStation
from filestation import SynologyFileStation
from health import SynologyHealth
from nfs import SynologyNFS
from usermanagement import SynologyUserManager

# Suppress InsecureRequestWarning when verify_ssl is disabled (internal NAS devices)
if not config.verify_ssl:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logger.warning(
        "SSL verification is disabled. "
        "Set VERIFY_SSL=true if your NAS has a valid SSL certificate."
    )


class SynologyMCPServer:
    """MCP Server for Synology NAS operations."""

    def __init__(self):
        self.server = Server(config.server_name)
        self.auth_instances: Dict[str, SynologyAuth] = {}
        self.sessions: Dict[str, str] = {}  # base_url -> session_id
        self.filestation_instances: Dict[str, SynologyFileStation] = {}
        self.downloadstation_instances: Dict[str, SynologyDownloadStation] = {}
        self.health_instances: Dict[str, SynologyHealth] = {}
        self.nfs_instances: Dict[str, SynologyNFS] = {}
        self.usermgr_instances: Dict[str, SynologyUserManager] = {}
        self.nas_name_map: Dict[str, str] = {}  # nas_name -> base_url
        self._setup_handlers()

    def _get_filestation(self, base_url: str) -> SynologyFileStation:
        """Get or create FileStation instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.filestation_instances:
            session_id = self.sessions[base_url]
            self.filestation_instances[base_url] = SynologyFileStation(
                base_url, session_id, verify_ssl=config.verify_ssl
            )

        return self.filestation_instances[base_url]

    def _get_downloadstation(self, base_url: str) -> SynologyDownloadStation:
        """Get or create DownloadStation instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.downloadstation_instances:
            session_id = self.sessions[base_url]
            self.downloadstation_instances[base_url] = SynologyDownloadStation(
                base_url, session_id, verify_ssl=config.verify_ssl
            )

        return self.downloadstation_instances[base_url]

    def _get_health(self, base_url: str) -> SynologyHealth:
        """Get or create Health instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.health_instances:
            session_id = self.sessions[base_url]
            self.health_instances[base_url] = SynologyHealth(
                base_url, session_id, verify_ssl=config.verify_ssl
            )

        return self.health_instances[base_url]

    def _get_nfs(self, base_url: str) -> SynologyNFS:
        """Get or create NFS instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.nfs_instances:
            session_id = self.sessions[base_url]
            self.nfs_instances[base_url] = SynologyNFS(
                base_url, session_id, verify_ssl=config.verify_ssl
            )

        return self.nfs_instances[base_url]

    def _get_usermgr(self, base_url: str) -> SynologyUserManager:
        """Get or create UserManager instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")

        if base_url not in self.usermgr_instances:
            session_id = self.sessions[base_url]
            self.usermgr_instances[base_url] = SynologyUserManager(
                base_url, session_id, verify_ssl=config.verify_ssl
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
            try:
                nas_cfg = config.get_synology_config(nas_name)
                base_url = nas_cfg["base_url"]
                label = nas_name or base_url

                logger.info(f"Auto-login: {label} ({base_url})...")

                if base_url not in self.auth_instances:
                    self.auth_instances[base_url] = SynologyAuth(
                        base_url, verify_ssl=config.verify_ssl
                    )

                auth = self.auth_instances[base_url]
                result = auth.login(nas_cfg["username"], nas_cfg["password"])

                if result.get("success"):
                    session_id = result["data"]["sid"]
                    self.sessions[base_url] = session_id
                    # Store the name->url mapping for tool resolution
                    self.nas_name_map[label] = base_url
                    logger.info(f"{label}: session {session_id[:8]}...")

                    for inst_dict in (
                        self.filestation_instances,
                        self.downloadstation_instances,
                        self.health_instances,
                        self.nfs_instances,
                        self.usermgr_instances,
                    ):
                        inst_dict.pop(base_url, None)
                    success_count += 1
                else:
                    error_code = result.get("error", {}).get("code", "?")
                    logger.warning(f"{label}: login failed (code {error_code})")

            except Exception as e:
                logger.warning(f"{nas_name or 'default'}: {e}")
                if config.debug:

                    logger.debug("Traceback:", exc_info=True)

        if success_count == 0:
            raise Exception("Auto-login failed for all configured NAS units — stopping server.")
        logger.info(f"Connected to {success_count}/{len(nas_names)} NAS unit(s)")

    def _setup_handlers(self):
        """Setup MCP server handlers."""

        @self.server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """List available Synology tools."""
            tools = self._get_tool_definitions()

            # Add login/logout tools only if not using auto-login or no credentials configured
            if not config.auto_login or not config.has_synology_credentials():
                tools.extend(
                    [
                        types.Tool(
                            name="synology_login",
                            description="Authenticate with Synology NAS and establish session",
                            inputSchema={
                                "type": "object",
                                "properties": {
                                    "base_url": {
                                        "type": "string",
                                        "description": "Synology NAS base URL (e.g., https://192.168.1.100:5001)",
                                    },
                                    "username": {
                                        "type": "string",
                                        "description": "Username for authentication",
                                    },
                                    "password": {
                                        "type": "string",
                                        "description": "Password for authentication",
                                    },
                                },
                                "required": ["base_url", "username", "password"],
                            },
                        ),
                        types.Tool(
                            name="synology_logout",
                            description="Logout from Synology NAS session",
                            inputSchema={
                                "type": "object",
                                "properties": {
                                    "base_url": {
                                        "type": "string",
                                        "description": "Synology NAS base URL",
                                    }
                                },
                                "required": ["base_url"],
                            },
                        ),
                    ]
                )

            return tools

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            """Handle tool calls."""
            try:
                logger.debug(f"Executing tool: {name}")
                if name == "synology_login":
                    return await self._handle_login(arguments)
                elif name == "synology_logout":
                    return await self._handle_logout(arguments)
                elif name == "synology_status":
                    return await self._handle_status(arguments)
                elif name == "synology_list_nas":
                    return await self._handle_list_nas(arguments)
                elif name == "list_shares":
                    return await self._handle_list_shares(arguments)
                elif name == "list_directory":
                    return await self._handle_list_directory(arguments)
                elif name == "get_file_info":
                    return await self._handle_get_file_info(arguments)
                elif name == "search_files":
                    return await self._handle_search_files(arguments)
                elif name == "get_file_content":
                    return await self._handle_get_file_content(arguments)
                elif name == "rename_file":
                    return await self._handle_rename_file(arguments)
                elif name == "move_file":
                    return await self._handle_move_file(arguments)
                elif name == "create_file":
                    return await self._handle_create_file(arguments)
                elif name == "create_directory":
                    return await self._handle_create_directory(arguments)
                elif name == "delete":
                    return await self._handle_delete(arguments)
                # Download Station handlers
                elif name == "ds_get_info":
                    return await self._handle_ds_get_info(arguments)
                elif name == "ds_list_tasks":
                    return await self._handle_ds_list_tasks(arguments)
                elif name == "ds_create_task":
                    return await self._handle_ds_create_task(arguments)
                elif name == "ds_pause_tasks":
                    return await self._handle_ds_pause_tasks(arguments)
                elif name == "ds_resume_tasks":
                    return await self._handle_ds_resume_tasks(arguments)
                elif name == "ds_delete_tasks":
                    return await self._handle_ds_delete_tasks(arguments)
                elif name == "ds_get_statistics":
                    return await self._handle_ds_get_statistics(arguments)
                elif name == "ds_list_downloaded_files":
                    return await self._handle_ds_list_downloaded_files(arguments)
                # Health monitoring handlers
                elif name == "synology_system_info":
                    return await self._handle_health_call(arguments, "system_info")
                elif name == "synology_utilization":
                    return await self._handle_health_call(arguments, "utilization")
                elif name == "synology_disk_health":
                    return await self._handle_health_call(arguments, "disk_list")
                elif name == "synology_disk_smart":
                    return await self._handle_disk_smart(arguments)
                elif name == "synology_volume_status":
                    return await self._handle_health_call(arguments, "volume_list")
                elif name == "synology_storage_pool":
                    return await self._handle_health_call(arguments, "storage_pool_list")
                elif name == "synology_network":
                    return await self._handle_health_call(arguments, "network_info")
                elif name == "synology_ups":
                    return await self._handle_health_call(arguments, "ups_info")
                elif name == "synology_services":
                    return await self._handle_health_call(arguments, "package_list")
                elif name == "synology_system_log":
                    return await self._handle_system_log(arguments)
                elif name == "synology_health_summary":
                    return await self._handle_health_call(arguments, "health_summary")
                # NFS management handlers
                elif name == "synology_nfs_status":
                    return await self._handle_nfs_call(arguments, "nfs_status")
                elif name == "synology_nfs_enable":
                    return await self._handle_nfs_enable(arguments)
                elif name == "synology_nfs_list_shares":
                    return await self._handle_nfs_call(arguments, "list_shares")
                elif name == "synology_nfs_set_permission":
                    return await self._handle_nfs_set_permission(arguments)
                elif name == "synology_create_share":
                    return await self._handle_create_share(arguments)
                # User management handlers
                elif name == "synology_list_users":
                    return await self._handle_usermgr_call(arguments, "list_users")
                elif name == "synology_get_user":
                    return await self._handle_usermgr_get_user(arguments)
                elif name == "synology_create_user":
                    return await self._handle_usermgr_create_user(arguments)
                elif name == "synology_set_user":
                    return await self._handle_usermgr_set_user(arguments)
                elif name == "synology_delete_user":
                    return await self._handle_usermgr_delete_user(arguments)
                elif name == "synology_list_groups":
                    return await self._handle_usermgr_call(arguments, "list_groups")
                elif name == "synology_list_group_members":
                    return await self._handle_usermgr_list_group_members(arguments)
                elif name == "synology_add_user_to_group":
                    return await self._handle_usermgr_add_to_group(arguments)
                elif name == "synology_remove_user_from_group":
                    return await self._handle_usermgr_remove_from_group(arguments)
                elif name == "synology_get_user_permissions":
                    return await self._handle_usermgr_get_permissions(arguments)
                elif name == "synology_set_user_permissions":
                    return await self._handle_usermgr_set_permissions(arguments)
                else:
                    raise ValueError(f"Unknown tool: {name}")
            except Exception as e:
                return [types.TextContent(type="text", text=f"Error executing {name}: {str(e)}")]

    def _get_base_url(self, arguments: dict) -> str:
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
            if base_url:
                return base_url
            raise Exception(
                f"NAS '{nas_name}' not found. Available: {list(self.nas_name_map.keys())}"
            )

        # Try explicit base_url
        base_url = arguments.get("base_url")
        if base_url:
            return base_url

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

    async def _handle_login(self, arguments: dict) -> list[types.TextContent]:
        """Handle Synology login."""
        base_url = arguments["base_url"]
        username = arguments["username"]
        password = arguments["password"]

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
            self.auth_instances[base_url] = SynologyAuth(base_url, verify_ssl=config.verify_ssl)

        auth = self.auth_instances[base_url]

        # Perform login
        result = auth.login(username, password)

        # Store session if successful
        if result.get("success"):
            session_id = result["data"]["sid"]
            self.sessions[base_url] = session_id

            # Clear any existing FileStation/DownloadStation instances to force recreation with new session
            if base_url in self.filestation_instances:
                del self.filestation_instances[base_url]
            if base_url in self.downloadstation_instances:
                del self.downloadstation_instances[base_url]

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
        base_url = self._get_base_url(arguments)

        if base_url not in self.sessions:
            return [types.TextContent(type="text", text=f"No active session found for {base_url}")]

        session_id = self.sessions[base_url]
        auth = self.auth_instances[base_url]

        # Use the improved logout method
        result = auth.logout(session_id)

        # Handle the result and provide detailed feedback
        if result.get("success"):
            # Remove session and FileStation/DownloadStation instances on successful logout
            del self.sessions[base_url]
            if base_url in self.filestation_instances:
                del self.filestation_instances[base_url]
            if base_url in self.downloadstation_instances:
                del self.downloadstation_instances[base_url]

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
            if error_code in ["105", "106", "no_session"]:
                # Still clean up local session data
                del self.sessions[base_url]
                if base_url in self.filestation_instances:
                    del self.filestation_instances[base_url]
                if base_url in self.downloadstation_instances:
                    del self.downloadstation_instances[base_url]

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

    def _get_tool_definitions(self):
        """Get tool definitions shared between MCP handler and bridge."""
        return [
            types.Tool(
                name="synology_status",
                description="Check authentication status for Synology NAS instances",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="synology_list_nas",
                description="List all configured NAS units from secrets.json. Returns NAS names, URLs, and connection status.",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            types.Tool(
                name="list_shares",
                description="List all available shares on the Synology NAS",
                inputSchema={
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
            ),
            types.Tool(
                name="list_directory",
                description="List contents of a directory on the Synology NAS. Returns detailed information about files and folders including name, type, size, and timestamps.",
                inputSchema={
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
            ),
            types.Tool(
                name="get_file_info",
                description="Get detailed information about a specific file or directory",
                inputSchema={
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
            ),
            types.Tool(
                name="search_files",
                description="Search for files and directories matching a pattern",
                inputSchema={
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
                            "description": "Search pattern (supports wildcards like *.txt)",
                        },
                    },
                    "required": ["path", "pattern"],
                },
            ),
            types.Tool(
                name="get_file_content",
                description="Get the content of a file",
                inputSchema={
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
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="rename_file",
                description="Rename a file or directory on the Synology NAS",
                inputSchema={
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
            ),
            types.Tool(
                name="move_file",
                description="Move a file or directory to a new location on the Synology NAS",
                inputSchema={
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
                            "description": "Destination path - can be a directory or full path with new name (must start with /)",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Whether to overwrite existing files at destination (default: false)",
                        },
                    },
                    "required": ["source_path", "destination_path"],
                },
            ),
            types.Tool(
                name="create_file",
                description="Create a new file with specified content on the Synology NAS",
                inputSchema={
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
                            "description": "Content to write to the file (default: empty string)",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Whether to overwrite existing file (default: false)",
                        },
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="create_directory",
                description="Create a new directory on the Synology NAS",
                inputSchema={
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
                            "description": "Parent directory path where the new folder should be created (must start with /)",
                        },
                        "name": {
                            "type": "string",
                            "description": "Name of the new directory to create",
                        },
                        "force_parent": {
                            "type": "boolean",
                            "description": "Whether to create parent directories if they don't exist (default: false)",
                        },
                    },
                    "required": ["folder_path", "name"],
                },
            ),
            types.Tool(
                name="delete",
                description="Delete a file or directory on the Synology NAS (auto-detects type)",
                inputSchema={
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
            ),
            # Download Station Tools
            types.Tool(
                name="ds_get_info",
                description="Get Download Station information and settings",
                inputSchema={
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
            ),
            types.Tool(
                name="ds_list_tasks",
                description="List all download tasks in Download Station",
                inputSchema={
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
            ),
            types.Tool(
                name="ds_create_task",
                description="Create a new download task from URL or magnet link",
                inputSchema={
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
            ),
            types.Tool(
                name="ds_pause_tasks",
                description="Pause one or more download tasks",
                inputSchema={
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
                        "task_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of task IDs to pause",
                        },
                    },
                    "required": ["task_ids"],
                },
            ),
            types.Tool(
                name="ds_resume_tasks",
                description="Resume one or more paused download tasks",
                inputSchema={
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
                        "task_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of task IDs to resume",
                        },
                    },
                    "required": ["task_ids"],
                },
            ),
            types.Tool(
                name="ds_delete_tasks",
                description="Delete one or more download tasks",
                inputSchema={
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
            ),
            types.Tool(
                name="ds_get_statistics",
                description="Get Download Station download/upload statistics",
                inputSchema={
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
            ),
            types.Tool(
                name="ds_list_downloaded_files",
                description="List files in the Download Station destination folder",
                inputSchema={
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
                        "destination": {
                            "type": "string",
                            "description": "Destination folder to list (optional, defaults to download station's default)",
                        },
                    },
                    "required": [],
                },
            ),
            # ============================================================
            # Health Monitoring Tools
            # ============================================================
            types.Tool(
                name="synology_system_info",
                description="Get Synology NAS system information: model, serial, DSM version, uptime, temperature",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_utilization",
                description="Get real-time CPU, memory, swap, and disk I/O utilization",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_disk_health",
                description="List all physical disks with SMART health status, model, temperature, and capacity",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_disk_smart",
                description="Get detailed S.M.A.R.T. attributes for a specific physical disk",
                inputSchema={
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
                        "disk_id": {
                            "type": "string",
                            "description": "Disk identifier (e.g. 'sda', 'sdb') from synology_disk_health output",
                        },
                    },
                    "required": ["disk_id"],
                },
            ),
            types.Tool(
                name="synology_volume_status",
                description="List all volumes/filesystems with status, total size, used space, and RAID info",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_storage_pool",
                description="List RAID/storage pools with RAID level, status, and member disks",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_network",
                description="Get network interface status and transfer rates",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_ups",
                description="Get UPS (uninterruptible power supply) status, battery level, and power info",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_services",
                description="List installed packages/services and their running status",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_system_log",
                description="Get recent system log entries for diagnosing issues",
                inputSchema={
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
                        "offset": {
                            "type": "integer",
                            "description": "Starting offset (default: 0)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max entries to return (default: 50)",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="synology_health_summary",
                description="Get a combined health overview: system info, CPU/memory utilization, disk health, volume status, storage pools, network, and UPS — all in one call",
                inputSchema={
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
            ),
            # ============================================================
            # NFS Management Tools
            # ============================================================
            types.Tool(
                name="synology_nfs_status",
                description="Get NFS service status and configuration (enabled/disabled, NFSv4 settings)",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_nfs_enable",
                description="Enable or disable the NFS file service on the Synology NAS",
                inputSchema={
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
                        "enable": {
                            "type": "boolean",
                            "description": "True to enable NFS, false to disable (default: true)",
                        },
                        "nfs_v4": {
                            "type": "boolean",
                            "description": "Enable NFSv4 support (default: false)",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="synology_nfs_list_shares",
                description="List all shared folders with their NFS access permissions",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_nfs_set_permission",
                description="Set NFS client access permissions on a shared folder (IP/subnet, read/write, squash options)",
                inputSchema={
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
                            "description": "Name of the shared folder (e.g. 'media', 'backups')",
                        },
                        "client_ip": {
                            "type": "string",
                            "description": "Client IP or subnet (e.g. '192.168.1.0/24', '10.0.0.5')",
                        },
                        "privilege": {
                            "type": "string",
                            "enum": ["readonly", "readwrite"],
                            "description": "Access level (default: readwrite)",
                        },
                        "squash": {
                            "type": "string",
                            "enum": ["root_squash", "no_root_squash", "all_squash"],
                            "description": "Squash option for root user mapping (default: root_squash)",
                        },
                        "security": {
                            "type": "string",
                            "enum": ["sys", "krb5", "krb5i", "krb5p"],
                            "description": "Security mode (default: sys/AUTH_SYS)",
                        },
                    },
                    "required": ["share_name", "client_ip"],
                },
            ),
            types.Tool(
                name="synology_create_share",
                description="Create a new shared folder on a Synology NAS volume",
                inputSchema={
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
                            "description": "Volume path where the share will be created (e.g. '/volume1', '/volume2')",
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
                            "description": "Restrict recycle bin access to administrators only (default: true)",
                        },
                    },
                    "required": ["share_name", "vol_path"],
                },
            ),
            # ============================================================
            # User Management Tools
            # ============================================================
            types.Tool(
                name="synology_list_users",
                description="List all local users on the Synology NAS",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_get_user",
                description="Get detailed information about a specific user",
                inputSchema={
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
                        "name": {"type": "string", "description": "Username to look up"},
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="synology_create_user",
                description="Create a new local user on the Synology NAS",
                inputSchema={
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
                        "name": {"type": "string", "description": "Username for the new account"},
                        "password": {
                            "type": "string",
                            "description": "Password for the new account",
                        },
                        "description": {
                            "type": "string",
                            "description": "User description (optional)",
                        },
                        "email": {"type": "string", "description": "User email address (optional)"},
                        "cannot_chg_passwd": {
                            "type": "boolean",
                            "description": "Prevent user from changing password (default: false)",
                        },
                        "passwd_never_expire": {
                            "type": "boolean",
                            "description": "Password never expires (default: true)",
                        },
                    },
                    "required": ["name", "password"],
                },
            ),
            types.Tool(
                name="synology_set_user",
                description="Modify an existing user (rename, change password, enable/disable)",
                inputSchema={
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
                        "name": {"type": "string", "description": "Target username to modify"},
                        "new_name": {"type": "string", "description": "Rename the user (optional)"},
                        "password": {"type": "string", "description": "New password (optional)"},
                        "description": {
                            "type": "string",
                            "description": "New description (optional)",
                        },
                        "email": {"type": "string", "description": "New email (optional)"},
                        "expired": {
                            "type": "string",
                            "enum": ["normal", "now"],
                            "description": "'normal' = active, 'now' = disabled",
                        },
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="synology_delete_user",
                description="Delete a local user from the Synology NAS",
                inputSchema={
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
                        "name": {"type": "string", "description": "Username to delete"},
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="synology_list_groups",
                description="List all local groups on the Synology NAS",
                inputSchema={
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
            ),
            types.Tool(
                name="synology_list_group_members",
                description="List members of a specific group",
                inputSchema={
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
                        "group": {"type": "string", "description": "Group name to list members of"},
                    },
                    "required": ["group"],
                },
            ),
            types.Tool(
                name="synology_add_user_to_group",
                description="Add a user to one or more groups",
                inputSchema={
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
                        "username": {"type": "string", "description": "Username to add to groups"},
                        "groups": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of group names to join",
                        },
                    },
                    "required": ["username", "groups"],
                },
            ),
            types.Tool(
                name="synology_remove_user_from_group",
                description="Remove a user from one or more groups",
                inputSchema={
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
                        "username": {
                            "type": "string",
                            "description": "Username to remove from groups",
                        },
                        "groups": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of group names to leave",
                        },
                    },
                    "required": ["username", "groups"],
                },
            ),
            types.Tool(
                name="synology_get_user_permissions",
                description="Get shared folder permissions for a user",
                inputSchema={
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
                        "name": {
                            "type": "string",
                            "description": "Username to check permissions for",
                        },
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="synology_set_user_permissions",
                description="Set shared folder permissions for a user (read/write/deny per folder)",
                inputSchema={
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
                        "name": {
                            "type": "string",
                            "description": "Username to set permissions for",
                        },
                        "permissions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Shared folder name"},
                                    "is_writable": {
                                        "type": "boolean",
                                        "description": "Grant write access",
                                    },
                                    "is_deny": {
                                        "type": "boolean",
                                        "description": "Deny access entirely",
                                    },
                                },
                                "required": ["name"],
                            },
                            "description": "List of folder permission objects",
                        },
                    },
                    "required": ["name", "permissions"],
                },
            ),
        ]

    async def get_tools_list(self):
        """Get the list of available tools (for bridge use)."""
        return self._get_tool_definitions()

    async def call_tool_direct(self, name: str, arguments: dict):
        """Call a tool directly (for bridge use).
        Delegates to the same handler used by handle_call_tool."""
        try:
            # Build a dispatch table from the tool name to its handler
            dispatch = {
                "synology_login": lambda a: self._handle_login(a),
                "synology_logout": lambda a: self._handle_logout(a),
                "synology_status": lambda a: self._handle_status(a),
                "list_shares": lambda a: self._handle_list_shares(a),
                "list_directory": lambda a: self._handle_list_directory(a),
                "get_file_info": lambda a: self._handle_get_file_info(a),
                "search_files": lambda a: self._handle_search_files(a),
                "get_file_content": lambda a: self._handle_get_file_content(a),
                "rename_file": lambda a: self._handle_rename_file(a),
                "move_file": lambda a: self._handle_move_file(a),
                "create_file": lambda a: self._handle_create_file(a),
                "create_directory": lambda a: self._handle_create_directory(a),
                "delete": lambda a: self._handle_delete(a),
                "ds_get_info": lambda a: self._handle_ds_get_info(a),
                "ds_list_tasks": lambda a: self._handle_ds_list_tasks(a),
                "ds_create_task": lambda a: self._handle_ds_create_task(a),
                "ds_pause_tasks": lambda a: self._handle_ds_pause_tasks(a),
                "ds_resume_tasks": lambda a: self._handle_ds_resume_tasks(a),
                "ds_delete_tasks": lambda a: self._handle_ds_delete_tasks(a),
                "ds_get_statistics": lambda a: self._handle_ds_get_statistics(a),
                "ds_list_downloaded_files": lambda a: self._handle_ds_list_downloaded_files(a),
                # Health monitoring
                "synology_system_info": lambda a: self._handle_health_call(a, "system_info"),
                "synology_utilization": lambda a: self._handle_health_call(a, "utilization"),
                "synology_disk_health": lambda a: self._handle_health_call(a, "disk_list"),
                "synology_disk_smart": lambda a: self._handle_disk_smart(a),
                "synology_volume_status": lambda a: self._handle_health_call(a, "volume_list"),
                "synology_storage_pool": lambda a: self._handle_health_call(a, "storage_pool_list"),
                "synology_network": lambda a: self._handle_health_call(a, "network_info"),
                "synology_ups": lambda a: self._handle_health_call(a, "ups_info"),
                "synology_services": lambda a: self._handle_health_call(a, "package_list"),
                "synology_system_log": lambda a: self._handle_system_log(a),
                "synology_health_summary": lambda a: self._handle_health_call(a, "health_summary"),
                # NFS management
                "synology_nfs_status": lambda a: self._handle_nfs_call(a, "nfs_status"),
                "synology_nfs_enable": lambda a: self._handle_nfs_enable(a),
                "synology_nfs_list_shares": lambda a: self._handle_nfs_call(a, "list_shares"),
                "synology_nfs_set_permission": lambda a: self._handle_nfs_set_permission(a),
                # User management
                "synology_list_users": lambda a: self._handle_usermgr_call(a, "list_users"),
                "synology_get_user": lambda a: self._handle_usermgr_get_user(a),
                "synology_create_user": lambda a: self._handle_usermgr_create_user(a),
                "synology_set_user": lambda a: self._handle_usermgr_set_user(a),
                "synology_delete_user": lambda a: self._handle_usermgr_delete_user(a),
                "synology_list_groups": lambda a: self._handle_usermgr_call(a, "list_groups"),
                "synology_list_group_members": lambda a: self._handle_usermgr_list_group_members(a),
                "synology_add_user_to_group": lambda a: self._handle_usermgr_add_to_group(a),
                "synology_remove_user_from_group": lambda a: self._handle_usermgr_remove_from_group(
                    a
                ),
                "synology_get_user_permissions": lambda a: self._handle_usermgr_get_permissions(a),
                "synology_set_user_permissions": lambda a: self._handle_usermgr_set_permissions(a),
            }

            handler = dispatch.get(name)
            if handler is None:
                raise ValueError(f"Unknown tool: {name}")
            return await handler(arguments)
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error executing {name}: {str(e)}")]

    async def run(self):
        """Run the MCP server."""
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

                        if error_code in ["105", "106", "no_session"]:
                            logger.info(f"Session {session_id[:10]}... was already expired")
                            cleanup_results.append(f"{base_url}: Session already expired")
                        else:
                            logger.error(f"Failed to logout {session_id[:10]}...: {error_code}")
                            cleanup_results.append(f"{base_url}: Logout failed - {error_code}")

                # Always clear local data
                del self.sessions[base_url]
                for inst_dict in (
                    self.filestation_instances,
                    self.downloadstation_instances,
                    self.health_instances,
                    self.nfs_instances,
                    self.usermgr_instances,
                ):
                    inst_dict.pop(base_url, None)

            except Exception as e:
                logger.error(f"Exception during cleanup for {base_url}: {e}")
                cleanup_results.append(f"{base_url}: Exception - {str(e)}")

        return cleanup_results


async def main():
    """Main entry point."""
    server = SynologyMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
