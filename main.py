#!/usr/bin/env python3
# main.py - Entry point for Synology MCP Server

"""
Synology MCP Server

A Model Context Protocol (MCP) server that provides tools for interacting with Synology NAS devices.
This server enables secure authentication and session management with Synology NAS systems.

Usage:
    python main.py

The server will start and listen for MCP client connections via stdio.
"""

import asyncio
import sys
import traceback
from src.mcp_server import main

if __name__ == "__main__":
    try:
        print("Starting Synology MCP Server...", file=sys.stderr)
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped by user", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"Server error: {e}", file=sys.stderr)
        print("Full traceback:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
