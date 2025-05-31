# src/mcp_server.py - MCP Server for Synology NAS operations

import asyncio
import json
import sys
from typing import Any, Dict, Optional
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import mcp.types as types
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.lowlevel import NotificationOptions
import mcp.server.stdio

from .config import config
from .auth import SynologyAuth
from .filestation import SynologyFileStation
from .downloadstation import SynologyDownloadStation


class SynologyMCPServer:
    """MCP Server for Synology NAS operations."""
    
    def __init__(self):
        self.server = Server(config.server_name)
        self.auth_instances: Dict[str, SynologyAuth] = {}
        self.sessions: Dict[str, str] = {}  # base_url -> session_id
        self.filestation_instances: Dict[str, SynologyFileStation] = {}
        self.downloadstation_instances: Dict[str, SynologyDownloadStation] = {}
        self._setup_handlers()
    
    def _get_filestation(self, base_url: str) -> SynologyFileStation:
        """Get or create FileStation instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")
        
        if base_url not in self.filestation_instances:
            session_id = self.sessions[base_url]
            self.filestation_instances[base_url] = SynologyFileStation(base_url, session_id)
        
        return self.filestation_instances[base_url]
    
    def _get_downloadstation(self, base_url: str) -> SynologyDownloadStation:
        """Get or create DownloadStation instance for a base URL."""
        if base_url not in self.sessions:
            raise Exception(f"No active session for {base_url}. Please login first.")
        
        if base_url not in self.downloadstation_instances:
            session_id = self.sessions[base_url]
            self.downloadstation_instances[base_url] = SynologyDownloadStation(base_url, session_id)
        
        return self.downloadstation_instances[base_url]
    
    async def _auto_login_if_configured(self):
        """Automatically login if credentials are configured and auto_login is enabled."""
        # Debug output to see what config values we have
        print(f"🔍 DEBUG: config.auto_login = {config.auto_login}", file=sys.stderr)
        print(f"🔍 DEBUG: config.has_synology_credentials() = {config.has_synology_credentials()}", file=sys.stderr)
        print(f"🔍 DEBUG: config = {config}", file=sys.stderr)
        
        if config.auto_login and config.has_synology_credentials():
            try:
                synology_config = config.get_synology_config()
                base_url = synology_config['base_url']
                
                print(f"Auto-login enabled, attempting to login to {base_url}", file=sys.stderr)
                
                # Create auth instance
                if base_url not in self.auth_instances:
                    self.auth_instances[base_url] = SynologyAuth(base_url)
                
                auth = self.auth_instances[base_url]
                result = auth.login(synology_config['username'], synology_config['password'])
                
                if result.get("success"):
                    session_id = result["data"]["sid"]
                    self.sessions[base_url] = session_id
                    print(f"✅ Auto-login successful for {base_url} (Session: {session_id[:8]}...)", file=sys.stderr)
                    
                    # Clear any existing FileStation/DownloadStation instances to force recreation with new session
                    if base_url in self.filestation_instances:
                        del self.filestation_instances[base_url]
                    if base_url in self.downloadstation_instances:
                        del self.downloadstation_instances[base_url]
                else:
                    error_msg = f"Auto-login failed for {base_url}: {result}"
                    print(f"❌ {error_msg}", file=sys.stderr)
                    raise Exception(error_msg)
                    
            except Exception as e:
                error_msg = f"Auto-login error: {e}"
                print(f"❌ {error_msg}", file=sys.stderr)
                if config.debug:
                    import traceback
                    traceback.print_exc(file=sys.stderr)
                raise Exception(f"Auto-login failed - stopping server. {error_msg}")
        elif not config.auto_login:
            print("⚠️  Auto-login disabled (AUTO_LOGIN=false)", file=sys.stderr)
        elif not config.has_synology_credentials():
            print("⚠️  No Synology credentials configured", file=sys.stderr)
        else:
            print("⚠️  Auto-login conditions not met", file=sys.stderr)
    
    def _setup_handlers(self):
        """Setup MCP server handlers."""
        
        @self.server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """List available Synology tools."""
            tools = [
                types.Tool(
                    name="synology_status",
                    description="Check authentication status for Synology NAS instances",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                types.Tool(
                    name="list_shares",
                    description="List all available shares on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            }
                        },
                        "required": []
                    }
                ),
                types.Tool(
                    name="list_directory",
                    description="List contents of a directory on the Synology NAS. Returns detailed information about files and folders including name, type, size, and timestamps.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "path": {
                                "type": "string",
                                "description": "Directory path to list (must start with /)"
                            }
                        },
                        "required": ["path"]
                    }
                ),
                types.Tool(
                    name="get_file_info",
                    description="Get detailed information about a specific file or directory",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "path": {
                                "type": "string",
                                "description": "File or directory path (must start with /)"
                            }
                        },
                        "required": ["path"]
                    }
                ),
                types.Tool(
                    name="search_files",
                    description="Search for files and directories matching a pattern",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "path": {
                                "type": "string",
                                "description": "Directory path to search in (must start with /)"
                            },
                            "pattern": {
                                "type": "string",
                                "description": "Search pattern (supports wildcards like *.txt)"
                            }
                        },
                        "required": ["path", "pattern"]
                    }
                ),
                types.Tool(
                    name="rename_file",
                    description="Rename a file or directory on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "path": {
                                "type": "string",
                                "description": "Full path to the file/directory to rename (must start with /)"
                            },
                            "new_name": {
                                "type": "string",
                                "description": "New name for the file/directory (just the name, not full path)"
                            }
                        },
                        "required": ["path", "new_name"]
                    }
                ),
                types.Tool(
                    name="move_file",
                    description="Move a file or directory to a new location on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "source_path": {
                                "type": "string",
                                "description": "Full path to the file/directory to move (must start with /)"
                            },
                            "destination_path": {
                                "type": "string",
                                "description": "Destination path - can be a directory or full path with new name (must start with /)"
                            },
                            "overwrite": {
                                "type": "boolean",
                                "description": "Whether to overwrite existing files at destination (default: false)"
                            }
                        },
                        "required": ["source_path", "destination_path"]
                    }
                ),
                types.Tool(
                    name="create_file",
                    description="Create a new file with specified content on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "path": {
                                "type": "string",
                                "description": "Full path where the file should be created (must start with /)"
                            },
                            "content": {
                                "type": "string",
                                "description": "Content to write to the file (default: empty string)"
                            },
                            "overwrite": {
                                "type": "boolean",
                                "description": "Whether to overwrite existing file (default: false)"
                            }
                        },
                        "required": ["path"]
                    }
                ),
                types.Tool(
                    name="create_directory",
                    description="Create a new directory on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "folder_path": {
                                "type": "string",
                                "description": "Parent directory path where the new folder should be created (must start with /)"
                            },
                            "name": {
                                "type": "string",
                                "description": "Name of the new directory to create"
                            },
                            "force_parent": {
                                "type": "boolean",
                                "description": "Whether to create parent directories if they don't exist (default: false)"
                            }
                        },
                        "required": ["folder_path", "name"]
                    }
                ),
                types.Tool(
                    name="delete_file",
                    description="Delete a file or directory on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "path": {
                                "type": "string",
                                "description": "Full path to the file/directory to delete (must start with /)"
                            },
                            "recursive": {
                                "type": "boolean",
                                "description": "Whether to delete directories recursively (default: false)"
                            }
                        },
                        "required": ["path"]
                    }
                ),
                types.Tool(
                    name="remove_directory",
                    description="Remove a directory and optionally its contents on the Synology NAS",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "directory_path": {
                                "type": "string",
                                "description": "Full path to the directory to remove (must start with /)"
                            },
                            "recursive": {
                                "type": "boolean",
                                "description": "Whether to remove directory contents recursively (default: true)"
                            }
                        },
                        "required": ["directory_path"]
                    }
                ),
                # Download Station Tools
                types.Tool(
                    name="ds_get_info",
                    description="Get Download Station information and settings",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            }
                        },
                        "required": []
                    }
                ),
                types.Tool(
                    name="ds_list_tasks",
                    description="List all download tasks in Download Station",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Starting offset for pagination (default: 0)"
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of tasks to return (default: -1 for all)"
                            }
                        },
                        "required": []
                    }
                ),
                types.Tool(
                    name="ds_create_task",
                    description="Create a new download task from URL or magnet link",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "uri": {
                                "type": "string",
                                "description": "Download URL or magnet link"
                            },
                            "destination": {
                                "type": "string",
                                "description": "Destination folder path (optional)"
                            },
                            "username": {
                                "type": "string",
                                "description": "Username for protected downloads (optional)"
                            },
                            "password": {
                                "type": "string",
                                "description": "Password for protected downloads (optional)"
                            }
                        },
                        "required": ["uri"]
                    }
                ),
                types.Tool(
                    name="ds_pause_tasks",
                    description="Pause one or more download tasks",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "task_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of task IDs to pause"
                            }
                        },
                        "required": ["task_ids"]
                    }
                ),
                types.Tool(
                    name="ds_resume_tasks",
                    description="Resume one or more paused download tasks",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "task_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of task IDs to resume"
                            }
                        },
                        "required": ["task_ids"]
                    }
                ),
                types.Tool(
                    name="ds_delete_tasks",
                    description="Delete one or more download tasks",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            },
                            "task_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of task IDs to delete"
                            },
                            "force_complete": {
                                "type": "boolean",
                                "description": "Force delete completed tasks (default: false)"
                            }
                        },
                        "required": ["task_ids"]
                    }
                ),
                types.Tool(
                    name="ds_get_statistics",
                    description="Get Download Station download/upload statistics",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "base_url": {
                                "type": "string",
                                "description": "Synology NAS base URL (optional if configured in .env)"
                            }
                        },
                        "required": []
                    }
                )
            ]
            
            # Add login/logout tools only if not using auto-login or no credentials configured
            if not config.auto_login or not config.has_synology_credentials():
                tools.extend([
                    types.Tool(
                        name="synology_login",
                        description="Authenticate with Synology NAS and establish session",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "base_url": {
                                    "type": "string",
                                    "description": "Synology NAS base URL (e.g., https://192.168.1.100:5001)"
                                },
                                "username": {
                                    "type": "string",
                                    "description": "Username for authentication"
                                },
                                "password": {
                                    "type": "string",
                                    "description": "Password for authentication"
                                }
                            },
                            "required": ["base_url", "username", "password"]
                        }
                    ),
                    types.Tool(
                        name="synology_logout",
                        description="Logout from Synology NAS session",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "base_url": {
                                    "type": "string",
                                    "description": "Synology NAS base URL"
                                }
                            },
                            "required": ["base_url"]
                        }
                    )
                ])
            
            return tools
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            """Handle tool calls."""
            try:
                if name == "synology_login":
                    return await self._handle_login(arguments)
                elif name == "synology_logout":
                    return await self._handle_logout(arguments)
                elif name == "synology_status":
                    return await self._handle_status(arguments)
                elif name == "list_shares":
                    return await self._handle_list_shares(arguments)
                elif name == "list_directory":
                    return await self._handle_list_directory(arguments)
                elif name == "get_file_info":
                    return await self._handle_get_file_info(arguments)
                elif name == "search_files":
                    return await self._handle_search_files(arguments)
                elif name == "rename_file":
                    return await self._handle_rename_file(arguments)
                elif name == "move_file":
                    return await self._handle_move_file(arguments)
                elif name == "create_file":
                    return await self._handle_create_file(arguments)
                elif name == "create_directory":
                    return await self._handle_create_directory(arguments)
                elif name == "delete_file":
                    return await self._handle_delete_file(arguments)
                elif name == "remove_directory":
                    return await self._handle_remove_directory(arguments)
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
                else:
                    raise ValueError(f"Unknown tool: {name}")
            except Exception as e:
                return [types.TextContent(
                    type="text",
                    text=f"Error executing {name}: {str(e)}"
                )]
    
    def _get_base_url(self, arguments: dict) -> str:
        """Get base URL from arguments or config."""
        base_url = arguments.get("base_url")
        if not base_url:
            if config.synology_url:
                base_url = config.synology_url
            else:
                raise Exception("No base_url provided and SYNOLOGY_URL not configured in .env")
        return base_url
    
    async def _handle_login(self, arguments: dict) -> list[types.TextContent]:
        """Handle Synology login."""
        base_url = arguments["base_url"]
        username = arguments["username"]
        password = arguments["password"]
        
        # Create or get auth instance
        if base_url not in self.auth_instances:
            self.auth_instances[base_url] = SynologyAuth(base_url)
        
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
            
            return [types.TextContent(
                type="text",
                text=f"Successfully authenticated with {base_url}\n"
                     f"Session ID: {session_id}\n"
                     f"Response: {json.dumps(result, indent=2)}"
            )]
        else:
            return [types.TextContent(
                type="text",
                text=f"Authentication failed: {json.dumps(result, indent=2)}"
            )]
    
    async def _handle_logout(self, arguments: dict) -> list[types.TextContent]:
        """Handle Synology logout."""
        base_url = self._get_base_url(arguments)
        
        if base_url not in self.sessions:
            return [types.TextContent(
                type="text",
                text=f"No active session found for {base_url}"
            )]
        
        session_id = self.sessions[base_url]
        auth = self.auth_instances[base_url]
        
        # Use the improved logout method
        result = auth.logout(session_id)
        
        # Handle the result and provide detailed feedback
        if result.get('success'):
            # Remove session and FileStation/DownloadStation instances on successful logout
            del self.sessions[base_url]
            if base_url in self.filestation_instances:
                del self.filestation_instances[base_url]
            if base_url in self.downloadstation_instances:
                del self.downloadstation_instances[base_url]
            
            return [types.TextContent(
                type="text",
                text=f"✅ Successfully logged out from {base_url}\n"
                     f"Session {session_id[:10]}... has been terminated"
            )]
        else:
            error_info = result.get('error', {})
            error_code = error_info.get('code', 'unknown')
            error_msg = error_info.get('message', 'Unknown error')
            
            # Handle expected session expiration gracefully
            if error_code in ['105', '106', 'no_session']:
                # Still clean up local session data
                del self.sessions[base_url]
                if base_url in self.filestation_instances:
                    del self.filestation_instances[base_url]
                if base_url in self.downloadstation_instances:
                    del self.downloadstation_instances[base_url]
                
                return [types.TextContent(
                    type="text",
                    text=f"⚠️ Session for {base_url} was already expired or invalid\n"
                         f"Local session data has been cleaned up\n"
                         f"Details: {error_code} - {error_msg}"
                )]
            else:
                return [types.TextContent(
                    type="text",
                    text=f"❌ Logout failed for {base_url}\n"
                         f"Error: {error_code} - {error_msg}\n"
                         f"Full response: {json.dumps(result, indent=2)}"
                )]
    
    async def _handle_status(self, arguments: dict) -> list[types.TextContent]:
        """Handle status check."""
        status_info = []
        
        # Show configuration status
        if config.has_synology_credentials():
            status_info.append(f"✓ Configuration: {config.synology_url} (user: {config.synology_username})")
            status_info.append(f"✓ Auto-login: {'enabled' if config.auto_login else 'disabled'}")
        else:
            status_info.append("⚠ No Synology credentials configured in .env")
        
        # Show active sessions with detailed info
        if self.sessions:
            status_info.append(f"\nActive sessions ({len(self.sessions)}):")
            for base_url, session_id in self.sessions.items():
                auth = self.auth_instances.get(base_url)
                if auth and auth.is_logged_in():
                    session_info = auth.get_session_info()
                    session_type = session_info.get('session_type', 'Unknown')
                    status_info.append(f"• {base_url}: {session_type} session {session_id[:10]}...")
                else:
                    status_info.append(f"• {base_url}: Session {session_id[:10]}... (status unknown)")
                    
            # Show service instances
            if self.filestation_instances:
                status_info.append(f"\nFileStation instances: {len(self.filestation_instances)}")
            if self.downloadstation_instances:
                status_info.append(f"DownloadStation instances: {len(self.downloadstation_instances)}")
        else:
            status_info.append("\nNo active Synology sessions")
        
        return [types.TextContent(
            type="text",
            text="\n".join(status_info)
        )]
    
    async def _handle_list_shares(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing shares."""
        base_url = self._get_base_url(arguments)
        filestation = self._get_filestation(base_url)
        
        shares = filestation.list_shares()
        
        return [types.TextContent(
            type="text",
            text=json.dumps(shares, indent=2)
        )]
    
    async def _handle_list_directory(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing directory contents."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        
        filestation = self._get_filestation(base_url)
        files = filestation.list_directory(path)
        
        return [types.TextContent(
            type="text",
            text=json.dumps(files, indent=2)
        )]
    
    async def _handle_get_file_info(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting file information."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        
        filestation = self._get_filestation(base_url)
        info = filestation.get_file_info(path)
        
        return [types.TextContent(
            type="text",
            text=json.dumps(info, indent=2)
        )]
    
    async def _handle_search_files(self, arguments: dict) -> list[types.TextContent]:
        """Handle searching files."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        pattern = arguments["pattern"]
        
        filestation = self._get_filestation(base_url)
        results = filestation.search_files(path, pattern)
        
        return [types.TextContent(
            type="text",
            text=json.dumps(results, indent=2)
        )]
    
    async def _handle_rename_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle renaming a file or directory."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        new_name = arguments["new_name"]
        
        filestation = self._get_filestation(base_url)
        result = filestation.rename_file(path, new_name)
        
        return [types.TextContent(
            type="text",
            text=f"Rename result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_move_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle moving a file or directory."""
        base_url = self._get_base_url(arguments)
        source_path = arguments["source_path"]
        destination_path = arguments["destination_path"]
        overwrite = arguments.get("overwrite", False)  # Default to False if not provided
        
        filestation = self._get_filestation(base_url)
        result = filestation.move_file(source_path, destination_path, overwrite)
        
        return [types.TextContent(
            type="text",
            text=f"Move result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_create_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new file with specified content on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        content = arguments.get("content", "")
        overwrite = arguments.get("overwrite", False)
        
        filestation = self._get_filestation(base_url)
        result = filestation.create_file(path, content, overwrite)
        
        return [types.TextContent(
            type="text",
            text=f"Create file result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_create_directory(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new directory on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        folder_path = arguments["folder_path"]
        name = arguments["name"]
        force_parent = arguments.get("force_parent", False)
        
        filestation = self._get_filestation(base_url)
        result = filestation.create_directory(folder_path, name, force_parent)
        
        return [types.TextContent(
            type="text",
            text=f"Create directory result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_delete_file(self, arguments: dict) -> list[types.TextContent]:
        """Handle deleting a file or directory on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        path = arguments["path"]
        recursive = arguments.get("recursive", False)
        
        filestation = self._get_filestation(base_url)
        result = filestation.delete_file(path, recursive)
        
        return [types.TextContent(
            type="text",
            text=f"Delete file result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_remove_directory(self, arguments: dict) -> list[types.TextContent]:
        """Handle removing a directory and optionally its contents on the Synology NAS."""
        base_url = self._get_base_url(arguments)
        directory_path = arguments["directory_path"]
        recursive = arguments.get("recursive", True)
        
        filestation = self._get_filestation(base_url)
        result = filestation.remove_directory(directory_path, recursive)
        
        return [types.TextContent(
            type="text",
            text=f"Remove directory result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_ds_get_info(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting Download Station information and settings."""
        base_url = self._get_base_url(arguments)
        downloadstation = self._get_downloadstation(base_url)
        
        info = downloadstation.get_info()
        
        return [types.TextContent(
            type="text",
            text=json.dumps(info, indent=2)
        )]
    
    async def _handle_ds_list_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle listing all download tasks in Download Station."""
        base_url = self._get_base_url(arguments)
        downloadstation = self._get_downloadstation(base_url)
        
        tasks = downloadstation.list_tasks()
        
        return [types.TextContent(
            type="text",
            text=json.dumps(tasks, indent=2)
        )]
    
    async def _handle_ds_create_task(self, arguments: dict) -> list[types.TextContent]:
        """Handle creating a new download task from URL or magnet link."""
        base_url = self._get_base_url(arguments)
        uri = arguments["uri"]
        destination = arguments.get("destination")
        username = arguments.get("username")
        password = arguments.get("password")
        
        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.create_task(uri, destination, username, password)
        
        return [types.TextContent(
            type="text",
            text=f"Create task result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_ds_pause_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle pausing one or more download tasks."""
        base_url = self._get_base_url(arguments)
        task_ids = arguments["task_ids"]
        
        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.pause_tasks(task_ids)
        
        return [types.TextContent(
            type="text",
            text=f"Pause tasks result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_ds_resume_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle resuming one or more paused download tasks."""
        base_url = self._get_base_url(arguments)
        task_ids = arguments["task_ids"]
        
        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.resume_tasks(task_ids)
        
        return [types.TextContent(
            type="text",
            text=f"Resume tasks result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_ds_delete_tasks(self, arguments: dict) -> list[types.TextContent]:
        """Handle deleting one or more download tasks."""
        base_url = self._get_base_url(arguments)
        task_ids = arguments["task_ids"]
        force_complete = arguments.get("force_complete", False)
        
        downloadstation = self._get_downloadstation(base_url)
        result = downloadstation.delete_tasks(task_ids, force_complete)
        
        return [types.TextContent(
            type="text",
            text=f"Delete tasks result: {json.dumps(result, indent=2)}"
        )]
    
    async def _handle_ds_get_statistics(self, arguments: dict) -> list[types.TextContent]:
        """Handle getting Download Station download/upload statistics."""
        base_url = self._get_base_url(arguments)
        downloadstation = self._get_downloadstation(base_url)
        
        statistics = downloadstation.get_statistics()
        
        return [types.TextContent(
            type="text",
            text=json.dumps(statistics, indent=2)
        )]
    
    async def run(self):
        """Run the MCP server."""
        # Validate configuration first
        config_errors = config.validate_config()
        if config_errors and config.auto_login:
            error_msg = f"Configuration errors: {', '.join(config_errors)}"
            print(f"❌ {error_msg}", file=sys.stderr)
            raise Exception(f"Invalid configuration - stopping server. {error_msg}")
        elif config.debug:
            print(f"Configuration loaded: {config}", file=sys.stderr)
        
        # Attempt auto-login if configured (this will raise exception on failure and stop server)
        print("Attempting auto-login...", file=sys.stderr)
        await self._auto_login_if_configured()
        
        # Only start server if auto-login succeeded (or wasn't required)
        try:
            print("Starting MCP server on stdio...", file=sys.stderr)
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
            print("\n🔄 Received shutdown signal, cleaning up sessions...", file=sys.stderr)
        except Exception as e:
            print(f"❌ Server runtime error: {e}", file=sys.stderr)
            if config.debug:
                import traceback
                traceback.print_exc(file=sys.stderr)
            raise
        finally:
            # Always attempt session cleanup on shutdown
            if self.sessions:
                print("🧹 Cleaning up active sessions...", file=sys.stderr)
                cleanup_results = await self.cleanup_sessions()
                
                if cleanup_results:
                    print("📋 Session cleanup summary:", file=sys.stderr)
                    for result in cleanup_results:
                        print(f"  {result}", file=sys.stderr)
                
                print("✅ Session cleanup completed", file=sys.stderr)
            else:
                print("✅ No active sessions to clean up", file=sys.stderr)

    async def cleanup_sessions(self):
        """Clean up all active sessions during shutdown."""
        cleanup_results = []
        
        for base_url, session_id in list(self.sessions.items()):
            try:
                auth = self.auth_instances.get(base_url)
                if auth:
                    print(f"🔄 Cleaning up session for {base_url}...", file=sys.stderr)
                    result = auth.logout(session_id)
                    
                    if result.get('success'):
                        print(f"✅ Session {session_id[:10]}... logged out successfully", file=sys.stderr)
                        cleanup_results.append(f"✅ {base_url}: Logged out successfully")
                    else:
                        error_info = result.get('error', {})
                        error_code = error_info.get('code', 'unknown')
                        
                        if error_code in ['105', '106', 'no_session']:
                            print(f"⚠️ Session {session_id[:10]}... was already expired", file=sys.stderr)
                            cleanup_results.append(f"⚠️ {base_url}: Session already expired")
                        else:
                            print(f"❌ Failed to logout {session_id[:10]}...: {error_code}", file=sys.stderr)
                            cleanup_results.append(f"❌ {base_url}: Logout failed - {error_code}")
                
                # Always clear local data
                del self.sessions[base_url]
                if base_url in self.filestation_instances:
                    del self.filestation_instances[base_url]
                if base_url in self.downloadstation_instances:
                    del self.downloadstation_instances[base_url]
                    
            except Exception as e:
                print(f"❌ Exception during cleanup for {base_url}: {e}", file=sys.stderr)
                cleanup_results.append(f"❌ {base_url}: Exception - {str(e)}")
        
        return cleanup_results


async def main():
    """Main entry point."""
    server = SynologyMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main()) 