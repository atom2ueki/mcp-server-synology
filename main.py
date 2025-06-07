#!/usr/bin/env python3
# main.py - Entry point for Synology MCP Server

"""
Synology MCP Server

A Model Context Protocol (MCP) server that provides tools for interacting with Synology NAS devices.
This server enables secure authentication and session management with Synology NAS systems.

Usage:
    python main.py

Environment Variables:
    ENABLE_XIAOZHI: Enable Xiaozhi WebSocket bridge (true/false, default: false)
    XIAOZHI_TOKEN: Your Xiaozhi authentication token (required if ENABLE_XIAOZHI=true)
    XIAOZHI_MCP_ENDPOINT: Xiaozhi MCP endpoint (optional, defaults to wss://api.xiaozhi.me/mcp/)

When ENABLE_XIAOZHI=false: Only Claude/Cursor support via stdio
When ENABLE_XIAOZHI=true: Both Xiaozhi (WebSocket) and Claude/Cursor (stdio) support
"""

import asyncio
import os
import sys
import traceback
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))


def check_requirements():
    """Check if all requirements are met."""
    errors = []
    
    enable_xiaozhi = os.getenv('ENABLE_XIAOZHI', 'false').lower() == 'true'
    
    if enable_xiaozhi:
        # Check for token
        token = os.getenv('XIAOZHI_TOKEN')
        if not token:
            errors.append("XIAOZHI_TOKEN environment variable is required when ENABLE_XIAOZHI=true")
        
        # Check for websockets package
        try:
            import websockets
        except ImportError:
            errors.append("websockets package is not installed. Run: pip install websockets>=11.0.3")
    
    return errors


if __name__ == "__main__":
    enable_xiaozhi = os.getenv('ENABLE_XIAOZHI', 'false').lower() == 'true'
    
    if enable_xiaozhi:
        print("🚀 Synology MCP Server with Xiaozhi Bridge", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
        print("🌟 Supports BOTH Xiaozhi and Claude/Cursor simultaneously!", file=sys.stderr)
        print("=" * 50, file=sys.stderr)
    else:
        print("🚀 Synology MCP Server", file=sys.stderr)
        print("=" * 30, file=sys.stderr)
        print("📌 Claude/Cursor only mode (ENABLE_XIAOZHI=false)", file=sys.stderr)
        print("=" * 30, file=sys.stderr)
    
    # Check requirements
    errors = check_requirements()
    if errors:
        print("❌ Requirements check failed:", file=sys.stderr)
        for error in errors:
            print(f"   • {error}", file=sys.stderr)
        print("\nPlease fix the above issues and try again.", file=sys.stderr)
        sys.exit(1)
    
    print("✅ Requirements check passed", file=sys.stderr)
    
    try:
        if enable_xiaozhi:
            # Show Xiaozhi configuration
            endpoint = os.getenv('XIAOZHI_MCP_ENDPOINT', 'wss://api.xiaozhi.me/mcp/')
            token = os.getenv('XIAOZHI_TOKEN')
            token_preview = f"{token[:8]}..." if token and len(token) > 8 else "***"
            
            print(f"📡 Xiaozhi Endpoint: {endpoint}", file=sys.stderr)
            print(f"🔑 Token: {token_preview}", file=sys.stderr)
            print("", file=sys.stderr)
            print("🔗 Client Support:", file=sys.stderr)
            print("   • Xiaozhi: WebSocket connection", file=sys.stderr)
            print("   • Claude/Cursor: stdio connection", file=sys.stderr)
            print("", file=sys.stderr)
            
            print("Starting multi-client bridge... Press Ctrl+C to stop", file=sys.stderr)
            print("=" * 50, file=sys.stderr)
            
            # Import and run multiclient bridge
            from multiclient_bridge import main as bridge_main
            asyncio.run(bridge_main())
        else:
            print("", file=sys.stderr)
            print("🔗 Client Support:", file=sys.stderr)
            print("   • Claude/Cursor: stdio connection", file=sys.stderr)
            print("", file=sys.stderr)
            
            print("Starting MCP server... Press Ctrl+C to stop", file=sys.stderr)
            print("=" * 30, file=sys.stderr)
            
            # Import and run standard MCP server
            from mcp_server import main as server_main
            asyncio.run(server_main())
            
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Server error: {e}", file=sys.stderr)
        print("Full traceback:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
