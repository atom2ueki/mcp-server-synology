"""A DSM failure must reach the client as an error, not as a success.

DSM reports most failures as HTTP 200 with `{"success": false, ...}`. The
health, NFS, Container Manager and user-management modules pass that dict
back, and their handlers used to return it as ordinary content. The body
still said `success: false`, but the MCP `isError` flag was false, so a client
or agent that keys on the flag read a refused delete or a failed permission
change as done. The iSCSI and reverse-proxy handlers already route through
`_emit`, which raises ToolFailureError so the flag is set; these tests hold
the other four modules to the same rule.

No live NAS required: each module is replaced by a stub whose every method
returns a DSM failure.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from mcp import Client

from config import config

FAILURE = {"success": False, "error": {"code": 105, "message": "permission denied"}}

HEALTH_TOOLS = [
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
]

NFS_TOOLS = [
    "synology_nfs_status",
    "synology_nfs_enable",
    "synology_nfs_list_shares",
    "synology_nfs_set_permission",
    "synology_create_share",
]

CONTAINER_TOOLS = [
    "synology_container_list",
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
    "synology_container_image_pull",
    "synology_container_registry_list",
    "synology_container_registry_search",
    "synology_container_registry_tags",
    "synology_container_registry_download",
    "synology_container_network_list",
    "synology_container_network_get",
    "synology_container_network_create",
    "synology_container_network_delete",
]

USER_TOOLS = [
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
]

GETTERS = {
    "_get_health": HEALTH_TOOLS,
    "_get_nfs": NFS_TOOLS,
    "_get_container": CONTAINER_TOOLS,
    "_get_usermgr": USER_TOOLS,
}

CASES = [(getter, name) for getter, names in GETTERS.items() for name in names]

_DUMMY = {"string": "x", "integer": 1, "number": 1, "boolean": True, "array": ["x"], "object": {}}


def _arguments(tool):
    """The smallest argument set that satisfies the tool's required fields."""
    schema = tool.input_schema or {}
    properties = schema.get("properties", {})
    arguments = {}
    for name in schema.get("required", []):
        prop = properties.get(name, {})
        if "enum" in prop:
            arguments[name] = prop["enum"][0]
        elif prop.get("type") == "array" and prop.get("items", {}).get("type") == "object":
            arguments[name] = [{"name": "x"}]
        else:
            arguments[name] = _DUMMY[prop.get("type", "string")]
    return arguments


def _server(monkeypatch):
    monkeypatch.setattr(config, "auto_login", True)
    from mcp_server import SynologyMCPServer

    return SynologyMCPServer()


def _call(server, getter, name):
    tool, _ = server._tool_registry[name]
    module = _AlwaysFails()
    with (
        patch.object(server, getter, return_value=module),
        patch.object(server, "_get_base_url", return_value="https://nas:5001"),
        patch.object(server, "_resolve_base_url", return_value="https://nas:5001"),
    ):

        async def run():
            async with Client(server.server) as client:
                return await client.call_tool(name, _arguments(tool))

        return asyncio.run(run())


class _AlwaysFails:
    """A stand-in module: every method returns a DSM failure."""

    def __getattr__(self, attr):
        def method(*args, **kwargs):
            return dict(FAILURE)

        return method


def test_every_case_is_a_registered_tool(monkeypatch):
    registered = set(_server(monkeypatch)._tool_registry)
    assert {name for _, name in CASES} - registered == set()


@pytest.mark.parametrize("getter,name", CASES, ids=[name for _, name in CASES])
def test_dsm_failure_sets_is_error(monkeypatch, getter, name):
    result = _call(_server(monkeypatch), getter, name)
    assert result.is_error is True, result.content[0].text
    assert '"success": false' in result.content[0].text


def test_success_still_returns_normally(monkeypatch):
    server = _server(monkeypatch)
    module = MagicMock()
    module.list_users.return_value = {"success": True, "data": {"users": []}}
    with (
        patch.object(server, "_get_usermgr", return_value=module),
        patch.object(server, "_get_base_url", return_value="https://nas:5001"),
    ):

        async def run():
            async with Client(server.server) as client:
                return await client.call_tool("synology_list_users", {})

        result = asyncio.run(run())
    assert result.is_error is False
    assert '"success": true' in result.content[0].text
