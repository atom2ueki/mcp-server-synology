"""Container Manager module tests."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _container():
    from container.synology_container import SynologyContainer

    return SynologyContainer(
        "https://nas.example.com:5001",
        "sid_xyz",
        verify_ssl=False,
        syno_token="tok_abc",
    )


def _successful_response(data=None):
    fake_response = MagicMock()
    fake_response.json.return_value = {"data": data or {}, "success": True}
    fake_response.raise_for_status = MagicMock()
    return fake_response


@pytest.mark.parametrize(
    ("method_name", "api_method"),
    [
        ("get_container", "get"),
        ("start_container", "start"),
        ("stop_container", "stop"),
        ("restart_container", "restart"),
        ("get_container_resource", "get"),
    ],
)
def test_container_name_methods_quote_name_for_dsm(method_name, api_method):
    """Name-based Container Manager methods JSON-quote the container name."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        return_value=_successful_response(),
    ) as post:
        result = getattr(container, method_name)("watchtower")

    assert result == {"data": {}, "success": True}

    sent_data = post.call_args.kwargs["data"]
    sent_headers = post.call_args.kwargs["headers"]

    assert sent_headers is not None
    assert sent_headers.get("X-SYNO-TOKEN") == "tok_abc"

    expected_api = (
        "SYNO.Docker.Container.Resource"
        if method_name == "get_container_resource"
        else "SYNO.Docker.Container"
    )

    assert sent_data["api"] == expected_api
    assert sent_data["method"] == api_method
    assert sent_data["version"] == "1"
    assert sent_data["_sid"] == "sid_xyz"
    assert sent_data["name"] == '"watchtower"'


def test_container_list_wire_format_matches_dsm():
    """List sends Container Manager pagination and type filters."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        return_value=_successful_response({"containers": []}),
    ) as post:
        result = container.list_containers(offset=5, limit=10, container_type="running")

    assert result == {"data": {"containers": []}, "success": True}

    sent_data = post.call_args.kwargs["data"]
    assert sent_data["api"] == "SYNO.Docker.Container"
    assert sent_data["method"] == "list"
    assert sent_data["version"] == "1"
    assert sent_data["_sid"] == "sid_xyz"
    assert sent_data["offset"] == "5"
    assert sent_data["limit"] == "10"
    assert sent_data["type"] == "running"


def test_container_resource_filters_requested_container():
    """Resource lookup returns only the requested container's resource entry."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        return_value=_successful_response(
            {
                "resources": [
                    {"name": "watchtower", "cpu": 1.0},
                    {"name": "postgres", "cpu": 2.0},
                ]
            }
        ),
    ):
        result = container.get_container_resource("watchtower")

    assert result == {
        "data": {"resources": [{"name": "watchtower", "cpu": 1.0}]},
        "success": True,
    }


def test_container_delete_wire_format_matches_dsm():
    """Delete sends quoted name and force/preserve_profile flags."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        return_value=_successful_response(),
    ) as post:
        container.delete_container("old-container", force=True, preserve_profile=False)

    sent_data = post.call_args.kwargs["data"]
    assert sent_data["api"] == "SYNO.Docker.Container"
    assert sent_data["method"] == "delete"
    assert sent_data["name"] == '"old-container"'
    assert sent_data["force"] == "true"
    assert sent_data["preserve_profile"] == "false"


@pytest.mark.parametrize(
    ("method_name", "api_method"),
    [
        ("get_project", "get"),
        ("start_project", "start"),
        ("stop_project", "stop"),
        ("restart_project", "restart"),
        ("build_project", "build"),
        ("clean_project", "clean"),
        ("delete_project", "delete"),
    ],
)
def test_project_name_methods_find_project_id_before_calling_dsm(method_name, api_method):
    """Project methods are name-based for MCP callers and ID-based for DSM."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        side_effect=[
            _successful_response(
                {
                    "abc-123": {
                        "id": "abc-123",
                        "name": "watchtower",
                        "path": "/volume1/docker/appdata/watchtower",
                    }
                }
            ),
            _successful_response(),
        ],
    ) as post:
        result = getattr(container, method_name)("watchtower")

    assert result == {"data": {}, "success": True}

    list_data = post.call_args_list[0].kwargs["data"]
    assert list_data["api"] == "SYNO.Docker.Project"
    assert list_data["method"] == "list"

    action_data = post.call_args_list[1].kwargs["data"]
    assert action_data["api"] == "SYNO.Docker.Project"
    assert action_data["method"] == api_method
    assert action_data["id"] == "abc-123"


def test_project_create_wire_format_matches_dsm():
    """Project creation follows DSM's create-empty-then-update flow."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        side_effect=[
            _successful_response({"folders": [{"path": "/docker/media", "name": "media"}]}),
            _successful_response(),
            _successful_response({"abc-123": {"id": "abc-123", "name": "media"}}),
            _successful_response(),
        ],
    ) as post:
        result = container.create_project(
            name="media",
            share_path="/docker/media",
            content="services:\n  app:\n    image: caddy:alpine\n",
            enable_service_portal=True,
            service_portal_name="media",
            service_portal_port=8080,
            service_portal_protocol="https",
        )

    assert result == {"data": {}, "success": True}

    mkdir_data = post.call_args_list[0].kwargs["data"]
    assert mkdir_data["api"] == "SYNO.FileStation.CreateFolder"
    assert mkdir_data["method"] == "create"
    assert mkdir_data["folder_path"] == "/docker"
    assert mkdir_data["name"] == "media"
    assert mkdir_data["force_parent"] == "true"

    sent_data = post.call_args_list[1].kwargs["data"]
    assert sent_data["api"] == "SYNO.Docker.Project"
    assert sent_data["method"] == "create"
    assert sent_data["version"] == "1"
    assert sent_data["name"] == '"media"'
    assert sent_data["share_path"] == '"/docker/media"'
    assert sent_data["content"] == '""'
    assert sent_data["enable_service_portal"] == "true"
    assert sent_data["service_portal_name"] == '"media"'
    assert sent_data["service_portal_port"] == "8080"
    assert sent_data["service_portal_protocol"] == '"https"'

    list_data = post.call_args_list[2].kwargs["data"]
    assert list_data["method"] == "list"

    update_data = post.call_args_list[3].kwargs["data"]
    assert update_data["method"] == "update"
    assert update_data["id"] == "abc-123"
    assert update_data["content"] == "services:\n  app:\n    image: caddy:alpine\n"


def test_project_update_finds_project_id_before_updating():
    """Project updates find DSM's internal ID before posting compose content."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        side_effect=[
            _successful_response({"abc-123": {"id": "abc-123", "name": "media"}}),
            _successful_response(),
        ],
    ) as post:
        result = container.update_project(
            name="media",
            content="services:\n  app:\n    image: caddy:latest\n",
            enable_service_portal=False,
        )

    assert result == {"data": {}, "success": True}

    update_data = post.call_args_list[1].kwargs["data"]
    assert update_data["api"] == "SYNO.Docker.Project"
    assert update_data["method"] == "update"
    assert update_data["id"] == "abc-123"
    assert update_data["content"] == "services:\n  app:\n    image: caddy:latest\n"
    assert update_data["enable_service_portal"] == "false"


def test_image_methods_wire_format_matches_dsm():
    """Image tools expose local image list/get/delete and pull."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        side_effect=[
            _successful_response({"images": []}),
            _successful_response(),
            _successful_response(
                {
                    "images": [
                        {
                            "id": "sha256:caddy",
                            "repository": "caddy",
                            "tags": ["alpine"],
                        }
                    ]
                }
            ),
            _successful_response(),
            _successful_response({"task_id": "pull-1"}),
        ],
    ) as post:
        assert container.list_images(offset=2, limit=5) == {
            "data": {"images": []},
            "success": True,
        }
        container.get_image("caddy", tag="alpine")
        container.delete_image("caddy", tag="alpine")
        container.pull_image("caddy", tag="alpine")

    list_data = post.call_args_list[0].kwargs["data"]
    assert list_data["api"] == "SYNO.Docker.Image"
    assert list_data["method"] == "list"
    assert list_data["limit"] == "5"
    assert list_data["offset"] == "2"

    get_data = post.call_args_list[1].kwargs["data"]
    assert get_data["method"] == "get"
    assert get_data["image"] == "caddy:alpine"
    assert "name" not in get_data
    assert "tag" not in get_data

    delete_lookup_data = post.call_args_list[2].kwargs["data"]
    assert delete_lookup_data["method"] == "list"
    assert delete_lookup_data["limit"] == "-1"

    delete_data = post.call_args_list[3].kwargs["data"]
    assert delete_data["method"] == "delete"
    assert json.loads(delete_data["images"]) == [
        {"id": "sha256:caddy", "repository": "caddy", "tags": ["alpine"]}
    ]
    assert "name" not in delete_data
    assert "tag" not in delete_data

    pull_data = post.call_args_list[4].kwargs["data"]
    assert pull_data["method"] == "pull_start"
    assert pull_data["repository"] == "caddy"
    assert pull_data["tag"] == "alpine"


def test_registry_methods_wire_format_matches_dsm():
    """Registry tools expose registry list, search, tags, and download."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        side_effect=[
            _successful_response({"registries": []}),
            _successful_response({"data": []}),
            _successful_response({"tags": ["latest"]}),
            _successful_response({"task_id": "pull-1"}),
        ],
    ) as post:
        container.list_registries()
        container.search_registry("postgres", offset=10, limit=25)
        container.list_registry_tags("postgres", offset=5, limit=10)
        container.pull_image("postgres", tag="16")

    assert post.call_args_list[0].kwargs["data"]["api"] == "SYNO.Docker.Registry"
    assert post.call_args_list[0].kwargs["data"]["method"] == "get"

    search_data = post.call_args_list[1].kwargs["data"]
    assert search_data["api"] == "SYNO.Docker.Registry"
    assert search_data["method"] == "search"
    assert search_data["q"] == "postgres"
    assert search_data["offset"] == "10"
    assert search_data["limit"] == "25"
    assert search_data["page_size"] == "25"

    tags_data = post.call_args_list[2].kwargs["data"]
    assert tags_data["method"] == "tags"
    assert tags_data["version"] == "2"
    assert tags_data["repository"] == "postgres"
    assert "name" not in tags_data
    assert tags_data["offset"] == "5"
    assert tags_data["limit"] == "10"

    download_data = post.call_args_list[3].kwargs["data"]
    assert download_data["api"] == "SYNO.Docker.Image"
    assert download_data["method"] == "pull_start"
    assert download_data["repository"] == "postgres"
    assert download_data["tag"] == "16"


def test_network_methods_wire_format_matches_dsm():
    """Network tools expose list/get/create/delete by name."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        side_effect=[
            _successful_response(
                {"network": [{"id": "net-1", "name": "app_net", "driver": "bridge"}]}
            ),
            _successful_response(
                {"network": [{"id": "net-1", "name": "app_net", "driver": "bridge"}]}
            ),
            _successful_response(),
            _successful_response(
                {"network": [{"id": "net-1", "name": "app_net", "driver": "bridge"}]}
            ),
            _successful_response(),
        ],
    ) as post:
        container.list_networks()
        assert container.get_network("app_net") == {
            "data": {"network": {"id": "net-1", "name": "app_net", "driver": "bridge"}},
            "success": True,
        }
        container.create_network(
            "app_net",
            subnet="172.28.0.0/16",
            gateway="172.28.0.1",
            ip_range="172.28.5.0/24",
            enable_ipv6=True,
        )
        container.delete_network("app_net")

    assert post.call_args_list[0].kwargs["data"]["api"] == "SYNO.Docker.Network"
    assert post.call_args_list[0].kwargs["data"]["method"] == "list"

    create_data = post.call_args_list[2].kwargs["data"]
    assert create_data["method"] == "create"
    assert create_data["name"] == "app_net"
    assert create_data["driver"] == "bridge"
    assert create_data["subnet"] == "172.28.0.0/16"
    assert create_data["gateway"] == "172.28.0.1"
    assert create_data["iprange"] == "172.28.5.0/24"
    assert create_data["enable_ipv6"] == "true"

    delete_lookup_data = post.call_args_list[3].kwargs["data"]
    assert delete_lookup_data["method"] == "list"

    delete_data = post.call_args_list[4].kwargs["data"]
    assert delete_data["method"] == "remove"
    assert json.loads(delete_data["networks"]) == [
        {"id": "net-1", "name": "app_net", "driver": "bridge"}
    ]


def test_container_logs_wire_format_matches_dsm():
    """Logs use the same filter payload as DSM's Container Manager UI."""
    container = _container()

    with patch(
        "utils.synology_api.requests.post",
        return_value=_successful_response({"logs": []}),
    ) as post:
        container.get_container_logs("watchtower", since="2026-06-13T00:00:00", timestamps=True)

    sent_data = post.call_args.kwargs["data"]
    assert sent_data["api"] == "SYNO.Docker.Container.Log"
    assert sent_data["method"] == "get"
    assert sent_data["version"] == "1"
    assert sent_data["name"] == '"watchtower"'
    assert sent_data["from"] == '"2026-06-13T00:00:00"'
    assert sent_data["to"] == '""'
    assert sent_data["level"] == '""'
    assert sent_data["keyword"] == '""'
    assert sent_data["sort_dir"] == '"DESC"'
    assert sent_data["offset"] == "0"
    assert sent_data["limit"] == "1000"
    assert "timestamps" not in sent_data


def test_container_tools_are_registered():
    """MCP exposes the container operations."""
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    names = {tool.name for tool in server._get_tool_definitions()}

    assert {
        "synology_container_list",
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
    }.issubset(names)

    assert "synology_container_api_call" not in names


@pytest.mark.asyncio
async def test_legacy_single_nas_auto_login_registers_default_name():
    """Legacy single-NAS config should match synology_list_nas' default name."""
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    auth = MagicMock()
    auth.login.return_value = {"success": True, "data": {"sid": "sid_xyz", "synotoken": "tok_abc"}}

    with (
        patch("mcp_server.SynologyAuth", return_value=auth),
        patch("mcp_server.config") as fake_config,
    ):
        fake_config.auto_login = True
        fake_config.has_synology_credentials.return_value = True
        fake_config.get_nas_names.return_value = []
        fake_config.get_synology_config.return_value = {
            "base_url": "http://nas.example.com:5000/",
            "username": "user",
            "password": "pass",
        }
        fake_config.verify_ssl = False
        fake_config.debug = False

        await server._auto_login_if_configured()

    assert server.nas_name_map["default"] == "http://nas.example.com:5000/"
