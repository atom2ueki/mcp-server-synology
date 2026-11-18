"""The Xiaozhi bridge must deliver a ToolFailureError's structured payload."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server import ToolFailureError


@pytest.mark.asyncio
async def test_tool_failure_payload_reaches_the_bridge_client():
    from multiclient_bridge import MCPBridge

    payload = {
        "success": False,
        "errors": {"advisor": {"code": 105}},
        "terminal": {"ssh": {"enabled": True}},
    }
    bridge = MCPBridge("wss://endpoint.test/mcp/", "tok")
    bridge.mcp_server = MagicMock()
    bridge.mcp_server._dispatch_tool = AsyncMock(side_effect=ToolFailureError(payload))

    message = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "synology_security_posture", "arguments": {}},
        }
    )
    reply = await bridge._handle_message(message, "XIAOZHI")
    reply = json.loads(reply) if isinstance(reply, str) else reply

    assert reply["id"] == 7
    assert reply["result"]["isError"] is True
    assert json.loads(reply["result"]["content"][0]["text"]) == payload
