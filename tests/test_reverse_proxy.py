"""Login Portal reverse proxy module tests."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from appportal.synology_reverse_proxy import (
    REVERSE_PROXY_API,
    SynologyReverseProxy,
    normalize_entry,
)

UUID = "1b0d0c30-9e1f-4a2b-8f7e-2c9d1a5b7e40"


def _raw_entry(**overrides):
    entry = {
        "UUID": UUID,
        "_key": UUID,
        "description": "Nextcloud",
        "proxy_connect_timeout": 60,
        "proxy_read_timeout": 60,
        "proxy_send_timeout": 60,
        "proxy_http_version": 1,
        "proxy_intercept_errors": False,
        "frontend": {
            "acl": None,
            "fqdn": "cloud.example.com",
            "port": 443,
            "protocol": 1,
            "https": {"hsts": True},
        },
        "backend": {"fqdn": "localhost", "port": 8080, "protocol": 0},
        "customize_headers": [
            {"name": "Upgrade", "value": "$http_upgrade"},
            {"name": "Connection", "value": "$connection_upgrade"},
        ],
    }
    entry.update(overrides)
    return entry


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(SynologyReverseProxy, "_lock_directory", tmp_path)
    return SynologyReverseProxy(
        "https://nas.example.com:5001", "sid_xyz", syno_token="tok_abc"
    )


def _listing(*entries):
    return _response({"success": True, "data": {"entries": list(entries)}})


# ----------------------------------------------------------------------
# Normalising
# ----------------------------------------------------------------------


def test_normalize_entry_decodes_protocols_and_hides_internal_fields():
    normalized = normalize_entry(_raw_entry())

    assert normalized["uuid"] == UUID
    assert normalized["source"] == {"protocol": "HTTPS", "hostname": "cloud.example.com", "port": 443}
    assert normalized["destination"] == {"protocol": "HTTP", "hostname": "localhost", "port": 8080}
    assert normalized["hsts"] is True
    assert normalized["websocket"] is True
    # The WebSocket pair is reported as the flag, not repeated as headers.
    assert normalized["custom_headers"] == []
    assert normalized["access_control_profile"] is None
    assert "_key" not in json.dumps(normalized)
    assert "http2" not in normalized


def test_normalize_entry_keeps_other_headers_and_reports_http2_only_when_present():
    raw = _raw_entry(customize_headers=[{"name": "X-Real-IP", "value": "$remote_addr"}])
    raw["frontend"]["https"]["http2"] = True

    normalized = normalize_entry(raw)

    assert normalized["websocket"] is False
    assert normalized["custom_headers"] == [{"name": "X-Real-IP", "value": "$remote_addr"}]
    assert normalized["http2"] is True


# ----------------------------------------------------------------------
# Reads
# ----------------------------------------------------------------------


def test_list_entries_wire_format(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())) as get:
        result = proxy.list_entries()

    assert result["success"] is True
    assert result["data"]["total"] == 1
    assert result["data"]["entries"][0]["description"] == "Nextcloud"
    params = get.call_args.kwargs["params"]
    assert params["api"] == REVERSE_PROXY_API
    assert params["method"] == "list"
    assert params["version"] == "1"


@pytest.mark.parametrize("data", [None, {}, {"entries": "x"}, {"entries": ["x"]}])
def test_list_entries_rejects_malformed_data(data, tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    payload = {"success": True, "data": data}
    with patch("utils.synology_api.requests.get", return_value=_response(payload)):
        result = proxy.list_entries()

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_response"


def test_get_entry_by_uuid_and_by_description(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())):
        assert proxy.get_entry(UUID)["data"]["uuid"] == UUID
        assert proxy.get_entry("Nextcloud")["data"]["uuid"] == UUID
        missing = proxy.get_entry("nope")

    assert missing["success"] is False
    assert missing["error"]["code"] == "not_found"


def test_get_entry_refuses_ambiguous_description(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    twins = _listing(_raw_entry(), _raw_entry(UUID="other", _key="other"))
    with patch("utils.synology_api.requests.get", return_value=twins):
        result = proxy.get_entry("Nextcloud")

    assert result["success"] is False
    assert "address one by its UUID" in result["error"]["message"]


# ----------------------------------------------------------------------
# Create
# ----------------------------------------------------------------------


def test_create_entry_wire_format(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    created = _raw_entry(description="Prometheus")
    with (
        patch(
            "utils.synology_api.requests.get",
            side_effect=[_listing(), _listing(created)],
        ),
        patch(
            "utils.synology_api.requests.post", return_value=_response({"success": True})
        ) as post,
    ):
        result = proxy.create_entry(
            description="Prometheus",
            source_hostname="metrics.example.com",
            destination_hostname="127.0.0.1",
            destination_port=9090,
            hsts=True,
            websocket=True,
            custom_headers=[{"name": "X-Real-IP", "value": "$remote_addr"}],
        )

    assert result["success"] is True
    assert result["data"]["description"] == "Prometheus"
    sent = post.call_args.kwargs["data"]
    assert sent["api"] == REVERSE_PROXY_API
    assert sent["method"] == "create"
    assert post.call_args.kwargs["headers"]["X-SYNO-TOKEN"] == "tok_abc"
    # DSM wants the whole entry as one JSON form value; flattened fields get 4151.
    entry = json.loads(sent["entry"])
    assert entry["frontend"] == {
        "acl": None,
        "fqdn": "metrics.example.com",
        "port": 443,
        "protocol": 1,
        "https": {"hsts": True},
    }
    assert entry["backend"] == {"fqdn": "127.0.0.1", "port": 9090, "protocol": 0}
    assert entry["customize_headers"] == [
        {"name": "X-Real-IP", "value": "$remote_addr"},
        {"name": "Upgrade", "value": "$http_upgrade"},
        {"name": "Connection", "value": "$connection_upgrade"},
    ]
    assert entry["proxy_http_version"] == 1
    assert "UUID" not in entry


def test_create_entry_refuses_duplicate_description(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())),
        patch("utils.synology_api.requests.post") as post,
    ):
        result = proxy.create_entry(
            description="Nextcloud",
            source_hostname="cloud2.example.com",
            destination_hostname="localhost",
            destination_port=8081,
        )

    assert result["success"] is False
    assert "already exists" in result["error"]["message"]
    post.assert_not_called()


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"source_protocol": "ftp"}, "source_protocol"),
        ({"destination_port": True}, "boolean"),
        ({"destination_port": 70000}, "between 1 and 65535"),
        ({"destination_port": 80.5}, "destination_port"),
        ({"source_hostname": "https://x.example.com"}, "without scheme"),
        ({"source_hostname": ""}, "source_hostname"),
        ({"description": "  "}, "description"),
        ({"source_protocol": "HTTP", "source_port": 80, "hsts": True}, "hsts requires"),
        ({"custom_headers": [{"name": "Bad Header", "value": "x"}]}, "letters, digits"),
        ({"proxy_read_timeout": 0}, "at least 1"),
    ],
)
def test_create_entry_validates_before_sending(overrides, fragment, tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    args = {
        "description": "Prometheus",
        "source_hostname": "metrics.example.com",
        "destination_hostname": "127.0.0.1",
        "destination_port": 9090,
    }
    args.update(overrides)
    with (
        patch("utils.synology_api.requests.get") as get,
        patch("utils.synology_api.requests.post") as post,
    ):
        result = proxy.create_entry(**args)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_argument"
    assert fragment in result["error"]["message"]
    get.assert_not_called()
    post.assert_not_called()


def test_create_entry_describes_dsm_duplicate_code(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing()),
        patch(
            "utils.synology_api.requests.post",
            return_value=_response({"success": False, "error": {"code": 4154}}),
        ),
    ):
        result = proxy.create_entry(
            description="Prometheus",
            source_hostname="metrics.example.com",
            destination_hostname="127.0.0.1",
            destination_port=9090,
        )

    assert result["success"] is False
    assert result["error"]["code"] == 4154
    assert "already exists" in result["error"]["message"]
    assert result["error"]["method"] == "create"


# ----------------------------------------------------------------------
# Update
# ----------------------------------------------------------------------


def test_update_entry_layers_changes_over_stored_entry(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    stored = _raw_entry(future_field={"kept": True})
    after = _raw_entry()
    after["backend"]["port"] = 8443
    with (
        patch(
            "utils.synology_api.requests.get", side_effect=[_listing(stored), _listing(after)]
        ),
        patch(
            "utils.synology_api.requests.post", return_value=_response({"success": True})
        ) as post,
    ):
        result = proxy.update_entry(UUID, destination_port=8443, websocket=False)

    assert result["success"] is True
    assert result["data"]["destination"]["port"] == 8443
    sent = post.call_args.kwargs["data"]
    assert sent["method"] == "update"
    entry = json.loads(sent["entry"])
    # Identity and fields this module does not model survive the round trip.
    assert entry["UUID"] == UUID
    assert entry["_key"] == UUID
    assert entry["future_field"] == {"kept": True}
    assert entry["backend"] == {"fqdn": "localhost", "port": 8443, "protocol": 0}
    assert entry["frontend"]["https"] == {"hsts": True}
    assert entry["customize_headers"] == []


def test_update_entry_custom_headers_keep_websocket_pair(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch(
            "utils.synology_api.requests.get",
            side_effect=[_listing(_raw_entry()), _listing(_raw_entry())],
        ),
        patch(
            "utils.synology_api.requests.post", return_value=_response({"success": True})
        ) as post,
    ):
        proxy.update_entry(UUID, custom_headers=[{"name": "X-Forwarded-Proto", "value": "$scheme"}])

    entry = json.loads(post.call_args.kwargs["data"]["entry"])
    assert entry["customize_headers"] == [
        {"name": "X-Forwarded-Proto", "value": "$scheme"},
        {"name": "Upgrade", "value": "$http_upgrade"},
        {"name": "Connection", "value": "$connection_upgrade"},
    ]


def test_update_entry_empty_profile_clears_acl(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    stored = _raw_entry()
    stored["frontend"]["acl"] = "profile-uuid"
    with (
        patch(
            "utils.synology_api.requests.get", side_effect=[_listing(stored), _listing(stored)]
        ),
        patch(
            "utils.synology_api.requests.post", return_value=_response({"success": True})
        ) as post,
    ):
        proxy.update_entry(UUID, access_control_profile="")

    entry = json.loads(post.call_args.kwargs["data"]["entry"])
    assert entry["frontend"]["acl"] is None


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({}, "Nothing to change"),
        ({"colour": "red"}, "Unknown field"),
    ],
)
def test_update_entry_refuses_empty_or_unknown_changes(changes, fragment, tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with patch("utils.synology_api.requests.get") as get:
        result = proxy.update_entry(UUID, **changes)

    assert result["success"] is False
    assert fragment in result["error"]["message"]
    get.assert_not_called()


def test_update_entry_rejects_hsts_on_http_source(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())),
        patch("utils.synology_api.requests.post") as post,
    ):
        result = proxy.update_entry(UUID, source_protocol="HTTP", source_port=80)

    assert result["success"] is False
    assert "hsts requires" in result["error"]["message"]
    post.assert_not_called()


def test_update_entry_unknown_uuid(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing()),
        patch("utils.synology_api.requests.post") as post,
    ):
        result = proxy.update_entry("missing", destination_port=1234)

    assert result["success"] is False
    assert result["error"]["code"] == "not_found"
    assert result["error"]["method"] == "update"
    post.assert_not_called()


# ----------------------------------------------------------------------
# Delete
# ----------------------------------------------------------------------


def test_delete_entry_wire_format(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())),
        patch(
            "utils.synology_api.requests.post", return_value=_response({"success": True})
        ) as post,
    ):
        result = proxy.delete_entry(UUID)

    assert result["success"] is True
    assert result["data"]["deleted"]["description"] == "Nextcloud"
    sent = post.call_args.kwargs["data"]
    assert sent["method"] == "delete"
    assert json.loads(sent["uuids"]) == [UUID]


def test_delete_entry_unknown_uuid_is_reported_not_sent(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())),
        patch("utils.synology_api.requests.post") as post,
    ):
        result = proxy.delete_entry("missing")

    assert result["success"] is False
    assert result["error"]["code"] == "not_found"
    post.assert_not_called()


# ----------------------------------------------------------------------
# Access control profiles
# ----------------------------------------------------------------------


def test_list_access_control_profiles(tmp_path, monkeypatch):
    proxy = _proxy(tmp_path, monkeypatch)
    payload = {
        "success": True,
        "data": {"entries": [{"UUID": "p1", "name": "LAN only", "rules": [{"address": "192.0.2.0/24", "allow": True}]}]},
    }
    with patch("utils.synology_api.requests.get", return_value=_response(payload)) as get:
        result = proxy.list_access_control_profiles()

    assert result["data"]["profiles"][0]["uuid"] == "p1"
    assert result["data"]["profiles"][0]["name"] == "LAN only"
    assert get.call_args.kwargs["params"]["api"] == "SYNO.Core.AppPortal.AccessControl"


# ----------------------------------------------------------------------
# MCP handlers
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_handler_refuses_without_confirm():
    from mcp_server import SynologyMCPServer, ToolFailureError

    server = SynologyMCPServer()
    with patch.object(server, "_get_reverse_proxy") as get_proxy:
        with pytest.raises(ToolFailureError) as refused:
            await server._handle_reverse_proxy_delete({"uuid": UUID, "confirm": "true"})

    assert refused.value.payload["error"]["code"] == "confirmation_required"
    get_proxy.assert_not_called()


@pytest.mark.asyncio
async def test_update_handler_passes_only_known_fields_on_worker_thread():
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    proxy = MagicMock()
    to_thread = AsyncMock(return_value={"success": True})
    with (
        patch.object(server, "_get_base_url", return_value="https://nas.example.com:5001"),
        patch.object(server, "_get_reverse_proxy", return_value=proxy),
        patch("mcp_server.asyncio.to_thread", to_thread),
    ):
        await server._handle_reverse_proxy_update(
            {"uuid": UUID, "destination_port": 8443, "nas_name": "nas1"}
        )

    to_thread.assert_awaited_once_with(proxy.update_entry, UUID, destination_port=8443)


def test_reverse_proxy_tools_registered():
    from mcp_server import SynologyMCPServer

    tools = {tool.name: tool for tool in SynologyMCPServer()._get_tool_definitions()}

    assert tools["synology_reverse_proxy_delete"].input_schema["required"] == ["uuid", "confirm"]
    assert tools["synology_reverse_proxy_update"].input_schema["required"] == ["uuid"]
    assert set(tools["synology_reverse_proxy_create"].input_schema["required"]) == {
        "description",
        "source_hostname",
        "destination_hostname",
        "destination_port",
    }
    for name in ("synology_reverse_proxy_list", "synology_reverse_proxy_get", "synology_access_control_profile_list"):
        assert name in tools


def test_write_timeout_is_long_and_reported_as_outcome_unknown(tmp_path, monkeypatch):
    import requests

    from appportal.synology_reverse_proxy import WRITE_TIMEOUT_SECONDS

    proxy = _proxy(tmp_path, monkeypatch)
    with (
        patch("utils.synology_api.requests.get", return_value=_listing(_raw_entry())),
        patch(
            "utils.synology_api.requests.post",
            side_effect=requests.exceptions.ReadTimeout("Read timed out."),
        ) as post,
    ):
        result = proxy.delete_entry(UUID)

    # DSM rewrites and reloads nginx before answering a write; 15 s is not enough.
    assert post.call_args.kwargs["timeout"] == WRITE_TIMEOUT_SECONDS
    assert WRITE_TIMEOUT_SECONDS > 15
    assert result["success"] is False
    assert result["error"]["outcome_unknown"] is True
    assert "before retrying" in result["error"]["message"]
    post.assert_called_once()
