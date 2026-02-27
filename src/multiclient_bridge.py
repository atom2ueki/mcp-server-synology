#!/usr/bin/env python3
# src/multiclient_bridge.py - Clean Multi-client MCP Bridge

"""
Multi-Client MCP Bridge

This bridge supports both:
1. WebSocket connections (for Xiaozhi)
2. Stdio connections (for Claude/Cursor)

Clean, simple architecture with proper error handling.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

if TYPE_CHECKING:
    from src.mcp_server import SynologyMCPServer
from urllib.parse import urlparse

import websockets

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class MCPBridge:
    """Clean multi-client MCP bridge."""

    def __init__(self, xiaozhi_endpoint: str, xiaozhi_token: str):
        self.xiaozhi_endpoint = xiaozhi_endpoint
        self.xiaozhi_token = xiaozhi_token
        self.mcp_server: "SynologyMCPServer | None" = None
        self.websocket_clients: Set[websockets.WebSocketServerProtocol] = set()
        self.running = False
        self.shutdown_event = asyncio.Event()

    async def _initialize_mcp_server(self) -> bool:
        """Initialize the MCP server instance."""
        try:
            from src.mcp_server import SynologyMCPServer

            self.mcp_server = SynologyMCPServer()
            logger.info("✅ MCP server initialized")

            # Perform auto-login if configured
            await self.mcp_server._auto_login_if_configured()

            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize MCP server: {e}")
            return False

    async def _process_mcp_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single MCP request and return response."""
        method = request_data.get("method")
        params = request_data.get("params", {})
        request_id = request_data.get("id")

        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {},
                            "logging": {},
                            "prompts": {},
                            "resources": {},
                        },
                        "serverInfo": {"name": "synology-mcp-server", "version": "1.0.0"},
                    },
                }

            elif method == "notifications/initialized":
                # No response for notifications
                return {}

            elif method == "tools/list":
                # Call the MCP server methods directly
                if not self.mcp_server:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": "MCP server not initialized"},
                    }
                tools_list = await self.mcp_server.get_tools_list()

                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": [tool.model_dump() for tool in tools_list]},
                }

            elif method == "tools/call":
                # Call tool
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                # Call the MCP server method directly
                if not self.mcp_server:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": "MCP server not initialized"},
                    }
                result = await self.mcp_server.call_tool_direct(tool_name, arguments)

                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [content.model_dump() for content in result]},
                }

            elif method == "ping":
                # Handle Xiaozhi ping - respond with simple pong
                return {"jsonrpc": "2.0", "id": request_id, "result": "pong"}

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }

        except Exception as e:
            logger.error(f"❌ Error processing {method}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
            }

    async def _handle_message(self, message: str, client_type: str) -> Optional[str]:
        """Handle incoming message from any client."""
        try:
            data = json.loads(message)
            method = data.get("method", "unknown")

            # Use robot emoji for Xiaozhi, regular emoji for others
            emoji_in = "🤖" if "XIAOZHI" in client_type else "📥"
            emoji_out = "🤖" if "XIAOZHI" in client_type else "📤"

            logger.info(f"{emoji_in} {client_type}: {method}")
            logger.debug(f"📋 {client_type} full message: {message}")

            # Process request
            response_data = await self._process_mcp_request(data)

            if response_data is None:
                # Notification - no response
                logger.info(f"📢 {client_type}: notification processed")
                return None

            response = json.dumps(response_data)
            logger.info(f"{emoji_out} {client_type}: response sent")
            logger.debug(f"📋 {client_type} full response: {response}")
            return response

        except json.JSONDecodeError:
            logger.error(f"❌ {client_type}: invalid JSON")
            return None
        except Exception as e:
            logger.error(f"❌ {client_type}: error handling message: {e}")
            return None

    async def _websocket_handler(self, websocket, path):
        """Handle individual WebSocket connection."""
        client_addr = websocket.remote_address
        logger.info(f"🔗 WebSocket connected: {client_addr}")

        self.websocket_clients.add(websocket)

        try:
            async for message in websocket:
                if self.shutdown_event.is_set():
                    break

                response = await self._handle_message(message, f"WS[{client_addr}]")
                if response:
                    await websocket.send(response)

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"🔌 WebSocket disconnected: {client_addr}")
        except Exception as e:
            logger.error(f"❌ WebSocket error [{client_addr}]: {e}")
        finally:
            self.websocket_clients.discard(websocket)

    async def _stdio_handler(self):
        """Handle stdio communication using proper MCP stdio server."""
        logger.info("📟 Starting stdio handler")

        try:
            import mcp.server.stdio
            from mcp.server.lowlevel import NotificationOptions
            from mcp.server.models import InitializationOptions

            # Use the same server instance that handles Xiaozhi requests
            if not self.mcp_server or not self.mcp_server.server:
                logger.error("❌ MCP server not available for stdio")
                return

            # Run the stdio server with proper MCP protocol handling
            async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
                logger.info("📟 MCP stdio server started")
                await self.mcp_server.server.run(
                    read_stream,
                    write_stream,
                    InitializationOptions(
                        server_name="synology-mcp-server",
                        server_version="1.0.0",
                        capabilities=self.mcp_server.server.get_capabilities(
                            notification_options=NotificationOptions(),
                            experimental_capabilities={},
                        ),
                    ),
                )

        except Exception as e:
            logger.error(f"❌ Stdio handler error: {e}")

        logger.info("📟 Stdio handler stopped")

    async def _xiaozhi_client(self):
        """Connect to Xiaozhi as a client with robust keep-alive."""
        reconnect_delay = 5.0  # Start with 5 seconds
        max_reconnect_delay = 300  # Max 5 minutes
        consecutive_failures = 0
        connection_stable_threshold = 30  # Consider connection stable after 30 seconds

        while not self.shutdown_event.is_set():
            connection_start_time = None
            websocket = None

            try:
                parsed_url = urlparse(self.xiaozhi_endpoint)
                ws_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?token={self.xiaozhi_token}"

                if consecutive_failures > 0:
                    logger.info(
                        f"🤖 Connecting to Xiaozhi (attempt #{consecutive_failures + 1})..."
                    )
                    logger.debug(
                        f"🔍 Connection URL: {parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?token=***"
                    )
                    logger.debug(
                        f"🔍 Token length: {len(self.xiaozhi_token) if self.xiaozhi_token else 0}"
                    )
                else:
                    logger.info("🤖 Connecting to Xiaozhi...")

                connection_start_time = asyncio.get_event_loop().time()

                # More aggressive keep-alive settings
                websocket = await websockets.connect(
                    ws_url,
                    ping_interval=20,  # Built-in ping every 20 seconds
                    ping_timeout=10,  # Wait max 10 seconds for pong
                    close_timeout=10,  # Close timeout
                )

                logger.info("🤖 Connected to Xiaozhi successfully")

                # Test connection with initial ping
                try:
                    ping_start = asyncio.get_event_loop().time()
                    pong_waiter = await websocket.ping()
                    await asyncio.wait_for(pong_waiter, timeout=5.0)
                    ping_duration = asyncio.get_event_loop().time() - ping_start
                    logger.info(f"🏓 Initial ping successful ({ping_duration:.3f}s)")
                except asyncio.TimeoutError:
                    logger.warning("🏓 Initial ping failed - connection may be unstable")
                except Exception as e:
                    logger.warning(f"🏓 Initial ping error: {e}")

                # Reset counters on successful connection
                consecutive_failures = 0
                reconnect_delay = 5.0

                # No custom heartbeat needed - Xiaozhi sends its own pings

                # Main message handling loop
                async for message in websocket:
                    if self.shutdown_event.is_set():
                        logger.info("🤖 Shutdown requested, closing Xiaozhi connection")
                        await websocket.close(code=1000, reason="Shutdown requested")
                        return

                    # Check if connection has been stable
                    if connection_start_time:
                        connection_duration = (
                            asyncio.get_event_loop().time() - connection_start_time
                        )
                        if connection_duration > connection_stable_threshold:
                            # Connection is considered stable, reset failure count
                            consecutive_failures = 0
                            # Don't reset connection_start_time - we need it for disconnect logging
                            logger.debug(f"✅ Connection stable for {connection_duration:.1f}s")

                    try:
                        response = await self._handle_message(message, "XIAOZHI")
                        if response:
                            await websocket.send(response)
                    except Exception as e:
                        logger.error(f"❌ Error handling message: {e}")
                        # Don't break on message handling errors, just log them

            except websockets.exceptions.ConnectionClosed as e:
                connection_duration = (
                    asyncio.get_event_loop().time() - connection_start_time
                    if connection_start_time
                    else 0
                )

                if e.code == 1000:  # Normal closure
                    logger.info("✅ Normal connection closure")
                    if self.shutdown_event.is_set():
                        return  # Only return if shutdown was requested
                elif e.code == 1001:  # Going away
                    logger.info("👋 Server going away")
                    consecutive_failures += 1
                elif e.code == 1006:  # Abnormal closure
                    if connection_duration > connection_stable_threshold:
                        logger.warning(
                            f"🔌 Connection lost after {connection_duration:.1f}s (was stable)"
                        )
                        consecutive_failures = min(
                            consecutive_failures + 1, 2
                        )  # Cap failures for stable connections
                    else:
                        logger.warning(f"🔌 Connection lost after {connection_duration:.1f}s")
                        consecutive_failures += 1
                else:
                    logger.warning(f"🔌 Connection closed: code={e.code}, reason={e.reason}")
                    consecutive_failures += 1
            except websockets.exceptions.InvalidHandshake as e:
                logger.error(f"🤖 Xiaozhi handshake failed: {e}")
                consecutive_failures += 1
            except websockets.exceptions.InvalidURI as e:
                logger.error(f"🤖 Invalid Xiaozhi URI: {e}")
                # Don't retry on invalid URI - this is fatal
                logger.error("🤖 Invalid URI is fatal, stopping Xiaozhi client")
                return
            except asyncio.TimeoutError:
                logger.error("🤖 Xiaozhi connection error: timed out during opening handshake")
                consecutive_failures += 1
            except OSError as e:
                logger.error(f"🤖 Xiaozhi network error: {e}")
                consecutive_failures += 1
            except Exception as e:
                logger.error(f"🤖 Xiaozhi connection error: {e}")
                consecutive_failures += 1
            finally:
                # Ensure websocket is properly closed
                if websocket and not websocket.closed:
                    try:
                        await websocket.close()
                    except Exception:
                        pass

            # Check if we should continue trying to reconnect
            if self.shutdown_event.is_set():
                break

            # Implement reconnection backoff strategy
            if consecutive_failures >= 10:
                logger.error("❌ Too many consecutive failures (10+), extending delay...")
                reconnect_delay = min(max_reconnect_delay, reconnect_delay * 2)
            elif consecutive_failures >= 5:
                logger.warning("⚠️ Multiple consecutive failures, increasing delay...")
                reconnect_delay = min(max_reconnect_delay, reconnect_delay * 1.5)
            elif consecutive_failures >= 3:
                reconnect_delay = min(max_reconnect_delay, reconnect_delay * 1.2)

            # Add jitter to prevent thundering herd
            jitter = reconnect_delay * 0.1 * (0.5 - asyncio.get_event_loop().time() % 1)
            actual_delay = reconnect_delay + jitter

            logger.info(
                f"🤖 Reconnecting to Xiaozhi in {actual_delay:.1f} seconds... (failures: {consecutive_failures})"
            )

            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=actual_delay)
                # If we get here, shutdown was requested
                break
            except asyncio.TimeoutError:
                # Normal timeout, continue with reconnection
                pass

    async def start(self):
        """Start the bridge."""
        logger.info("🚀 Starting MCP Bridge...")

        # Initialize MCP server
        if not await self._initialize_mcp_server():
            return False

        self.running = True

        # Start all handlers
        tasks = [
            asyncio.create_task(self._stdio_handler()),
            asyncio.create_task(self._xiaozhi_client()),
        ]

        try:
            # Wait for shutdown or any task to fail
            await asyncio.wait(
                tasks + [asyncio.create_task(self.shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except Exception as e:
            logger.error(f"❌ Bridge error: {e}")
        finally:
            # Cancel remaining tasks with timeout
            self.shutdown_event.set()

            for task in tasks:
                if not task.done():
                    task.cancel()

            # Wait for task cancellation with timeout
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), timeout=3.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("⚠️ Task cancellation timeout")
                except Exception:
                    pass  # Ignore cancellation errors

        await self.stop()
        return True

    async def stop(self):
        """Stop the bridge."""
        if not self.running:
            return

        logger.info("🛑 Stopping MCP Bridge...")
        self.running = False
        self.shutdown_event.set()

        # Close WebSocket connections with timeout
        close_tasks = []
        for ws in list(self.websocket_clients):
            try:
                close_tasks.append(asyncio.create_task(ws.close()))
            except Exception:
                pass

        if close_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*close_tasks, return_exceptions=True), timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning("⚠️ WebSocket close timeout")

        self.websocket_clients.clear()

        # Clean up MCP server
        if self.mcp_server:
            try:
                if hasattr(self.mcp_server, "cleanup_sessions"):
                    await asyncio.wait_for(self.mcp_server.cleanup_sessions(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("⚠️ MCP server cleanup timeout")
            except Exception as e:
                logger.error(f"❌ Error cleaning up MCP server: {e}")

        logger.info("✅ MCP Bridge stopped")


async def main():
    """Main entry point."""
    # Get config from environment
    xiaozhi_endpoint = os.getenv("XIAOZHI_MCP_ENDPOINT", "wss://api.xiaozhi.me/mcp/")
    xiaozhi_token = os.getenv("XIAOZHI_TOKEN")

    if not xiaozhi_token:
        logger.error("❌ XIAOZHI_TOKEN environment variable required")
        return 1

    # Validate endpoint
    try:
        parsed = urlparse(xiaozhi_endpoint)
        if not parsed.scheme or not parsed.netloc:
            logger.error(f"❌ Invalid XIAOZHI_MCP_ENDPOINT: {xiaozhi_endpoint}")
            return 1
        logger.info(f"📡 Xiaozhi Endpoint: {parsed.scheme}://{parsed.netloc}{parsed.path}")
    except Exception as e:
        logger.error(f"❌ Error parsing XIAOZHI_MCP_ENDPOINT: {e}")
        return 1

    # Create bridge
    bridge = MCPBridge(xiaozhi_endpoint, xiaozhi_token)

    # Setup asyncio-compatible signal handlers
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("📡 Received shutdown signal (Ctrl+C)")
        if not bridge.shutdown_event.is_set():
            bridge.shutdown_event.set()
            logger.info("🛑 Initiating graceful shutdown...")

    # Register signal handlers with asyncio
    try:
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
    except NotImplementedError:
        # Signal handlers not supported on this platform (Windows)
        logger.warning("⚠️ Signal handlers not supported on this platform")

    try:
        await bridge.start()
        return 0
    except KeyboardInterrupt:
        logger.info("⌨️ Keyboard interrupt")
        bridge.shutdown_event.set()
        await bridge.stop()
        return 0
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        bridge.shutdown_event.set()
        await bridge.stop()
        return 1
    finally:
        # Ensure cleanup
        if bridge.running:
            await bridge.stop()


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("🔴 Force exit")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)
