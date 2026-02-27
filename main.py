#!/usr/bin/env python3
# main.py - Entry point for Synology MCP Server

"""
Synology MCP Server

A Model Context Protocol (MCP) server that provides tools for interacting with Synology NAS devices.
This server enables secure authentication and session management with Synology NAS systems.

Usage:
    python main.py

Configuration:
    All settings are loaded from ~/.config/synology-mcp/settings.json

    For Xiaozhi support, set in settings.json:
    {
      "xiaozhi": {
        "enabled": true,
        "token": "your_token",
        "endpoint": "wss://api.xiaozhi.me/mcp/"
      }
    }
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Legacy .env support (deprecated - use settings.json instead)
load_dotenv()

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def check_requirements():
    """Check if all requirements are met."""
    from config import config

    errors = []

    if config.xiaozhi_enabled:
        # Check for token
        if not config.xiaozhi_token:
            errors.append("Xiaozhi token is required when xiaozhi.enabled=true in settings.json")

        # Check for websockets package
        import importlib.util

        if importlib.util.find_spec("websockets") is None:
            errors.append(
                "websockets package is not installed. Run: pip install websockets>=11.0.3"
            )

    return errors


def setup_logging(level: str = "INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


if __name__ == "__main__":
    from config import config

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger("synology-mcp")

    enable_xiaozhi = config.xiaozhi_enabled

    if enable_xiaozhi:
        logger.info("Starting Synology MCP Server with Xiaozhi Bridge")
        logger.info("Supports BOTH Xiaozhi and Claude/Cursor simultaneously")
    else:
        logger.info("Starting Synology MCP Server")
        logger.info("Claude/Cursor only mode")

    # Check requirements
    errors = check_requirements()
    if errors:
        logger.error("Requirements check failed:")
        for error in errors:
            logger.error(f"  - {error}")
        sys.exit(1)

    logger.info("Requirements check passed")

    try:
        if enable_xiaozhi:
            # Show Xiaozhi configuration
            endpoint = config.xiaozhi_endpoint
            token = config.xiaozhi_token
            token_preview = f"{token[:8]}..." if token and len(token) > 8 else "***"

            logger.info(f"Xiaozhi Endpoint: {endpoint}")
            logger.info(f"Xiaozhi Token: {token_preview}")
            logger.info("Client Support: Xiaozhi (WebSocket), Claude/Cursor (stdio)")

            logger.info("Starting multi-client bridge... Press Ctrl+C to stop")

            # Import and run multiclient bridge
            from multiclient_bridge import main as bridge_main

            asyncio.run(bridge_main())
        else:
            logger.info("Client Support: Claude/Cursor (stdio)")
            logger.info("Starting MCP server... Press Ctrl+C to stop")

            # Import and run standard MCP server
            from mcp_server import main as server_main

            asyncio.run(server_main())

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Server error: {e}")
        logger.debug("Full traceback:", exc_info=True)
        sys.exit(1)
