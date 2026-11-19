"""Offline tests for the destructive-call confirmation flow.

Pins the MCP 2026-07-28 behaviour: the first call returns `input_required` and
does not execute; the retry executes only when the state verifies and the user
approved. Also pins the fail-closed behaviour for unsupported clients.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp import types  # noqa: E402

from audit import AuditLog  # noqa: E402
from config import config  # noqa: E402
from confirm import ConfirmationTokens  # noqa: E402
from mcp_server import SynologyMCPServer, ToolFailureError  # noqa: E402

SECRET = b"0" * 32


class FakeSession:
    def __init__(self, form: bool):
        self.client_capabilities = types.ClientCapabilities(
            elicitation=types.ElicitationCapability(
                form=types.FormElicitationCapability() if form else None
            )
        )


class FakeContext:
    def __init__(self, version: str = "2026-07-28", form: bool = True):
        self.protocol_version = version
        self.session = FakeSession(form)
        self.request_id = 1


def make_server(tmp_path):
    srv = SynologyMCPServer.__new__(SynologyMCPServer)
    srv._tool_registry = {}
    srv.run_id = "test"
    srv.sessions = {"http://nas.example:5000": "test-session"}
    srv.nas_name_map = {}
    srv.audit = AuditLog(tmp_path / "audit.jsonl", run_id="test")
    srv.confirm = ConfirmationTokens(SECRET, ttl_seconds=300)
    calls = []

    async def handler(arguments):
        calls.append(arguments)
        return [types.TextContent(type="text", text="executed")]

    srv._register_tool(
        "delete",
        "Delete a file",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        handler,
    )

    async def read_handler(arguments):
        return [types.TextContent(type="text", text="ok")]

    srv._register_tool("synology_status", "Status", {"type": "object", "properties": {}}, read_handler)
    return srv, calls


def params(**kwargs) -> types.CallToolRequestParams:
    return types.CallToolRequestParams(name="delete", **kwargs)


def audit_lines(tmp_path):
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


class TestRoundTrip:
    def test_first_call_asks_and_does_not_execute(self, tmp_path):
        srv, calls = make_server(tmp_path)
        result = asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, FakeContext(), params(arguments={"path": "/videos/x"})))
        assert isinstance(result, types.InputRequiredResult)
        assert result.result_type == "input_required"
        assert "confirm" in result.input_requests
        assert result.request_state
        assert calls == []
        entry = audit_lines(tmp_path)[-1]
        assert entry["outcome"] == "confirmation_requested"
        assert entry["confirmation"] == "round-trip"

    def test_accept_retry_executes(self, tmp_path):
        srv, calls = make_server(tmp_path)
        ctx = FakeContext()
        first = asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, params(arguments={"path": "/videos/x"})))
        retry = params(
            arguments={"path": "/videos/x"},
            request_state=first.request_state,
            input_responses={"confirm": {"action": "accept", "content": {"approve": True}}},
        )
        result = asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, retry))
        assert [c.text for c in result] == ["executed"]
        assert calls == [{"path": "/videos/x", "base_url": "http://nas.example:5000"}]
        assert audit_lines(tmp_path)[-1]["confirmation"] == "round-trip"

    def test_decline_refuses(self, tmp_path):
        srv, calls = make_server(tmp_path)
        ctx = FakeContext()
        first = asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, params(arguments={"path": "/videos/x"})))
        retry = params(
            arguments={"path": "/videos/x"},
            request_state=first.request_state,
            input_responses={"confirm": {"action": "accept", "content": {"approve": False}}},
        )
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, retry))
        assert caught.value.payload["error"]["code"] == "confirmation_declined"
        assert calls == []
        refusal = audit_lines(tmp_path)[-1]
        assert refusal["outcome"] == "confirmation_declined"
        assert refusal["confirmation"] == "confirmation_declined"
        assert "not approved" in refusal["error"]

    def test_state_does_not_transfer_to_other_arguments(self, tmp_path):
        srv, calls = make_server(tmp_path)
        ctx = FakeContext()
        first = asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, params(arguments={"path": "/videos/x"})))
        retry = params(
            arguments={"path": "/videos/other"},
            request_state=first.request_state,
            input_responses={"confirm": {"action": "accept", "content": {"approve": True}}},
        )
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/other"}, ctx, retry))
        assert caught.value.payload["error"]["code"] == "confirmation_invalid"
        assert calls == []

    def test_state_cannot_be_replayed(self, tmp_path):
        srv, calls = make_server(tmp_path)
        ctx = FakeContext()
        first = asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, params(arguments={"path": "/videos/x"})))
        retry = params(
            arguments={"path": "/videos/x"},
            request_state=first.request_state,
            input_responses={"confirm": {"action": "accept", "content": {"approve": True}}},
        )
        asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, retry))
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(srv._dispatch_tool("delete", {"path": "/videos/x"}, ctx, retry))
        assert caught.value.payload["error"]["code"] == "confirmation_invalid"
        assert len(calls) == 1


class TestFallback:
    def test_old_protocol_gets_confirmation_required(self, tmp_path):
        srv, calls = make_server(tmp_path)
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(
                srv._dispatch_tool(
                    "delete", {"path": "/videos/x"}, FakeContext(version="2025-11-25"),
                    params(arguments={"path": "/videos/x"}),
                )
            )
        assert caught.value.payload["error"]["code"] == "confirmation_required"
        assert calls == []
        assert audit_lines(tmp_path)[-1]["outcome"] == "confirmation_required"

    def test_client_without_form_capability_gets_confirmation_required(self, tmp_path):
        srv, calls = make_server(tmp_path)
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(
                srv._dispatch_tool(
                    "delete", {"path": "/videos/x"}, FakeContext(form=False),
                    params(arguments={"path": "/videos/x"}),
                )
            )
        assert caught.value.payload["error"]["code"] == "confirmation_required"
        assert calls == []

    def test_agent_supplied_token_cannot_bypass_form(self, tmp_path):
        srv, calls = make_server(tmp_path)
        token = srv.confirm.issue("delete", {"path": "/videos/x"})
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(srv._dispatch_tool(
                "delete", {"path": "/videos/x", "confirm_token": token}, FakeContext(),
                params(arguments={"path": "/videos/x"}),
            ))
        assert caught.value.payload["error"]["code"] == "confirmation_required"
        assert calls == []



class TestModes:
    def test_off_executes_without_confirmation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "confirm_mode", "off")
        srv, calls = make_server(tmp_path)
        result = asyncio_run(
            srv._dispatch_tool(
                "delete", {"path": "/videos/x"}, FakeContext(version="2025-11-25"),
                params(arguments={"path": "/videos/x"}),
            )
        )
        assert [c.text for c in result] == ["executed"]
        assert audit_lines(tmp_path)[-1]["confirmation"] == "none"

    def test_token_mode_never_asks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "confirm_mode", "token")
        srv, calls = make_server(tmp_path)
        with pytest.raises(ToolFailureError) as caught:
            asyncio_run(
                srv._dispatch_tool(
                    "delete", {"path": "/videos/x"}, FakeContext(),
                    params(arguments={"path": "/videos/x"}),
                )
            )
        assert caught.value.payload["error"]["code"] == "confirmation_required"
        assert calls == []


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.mark.parametrize("name", ["create_file", "copy_file", "move_file"])
def test_overwrite_requires_form_before_handler_runs(tmp_path, name):
    srv, calls = make_server(tmp_path)
    async def write(arguments):
        calls.append(arguments)
        return [types.TextContent(type="text", text="written")]
    srv._register_tool(name, "Write", {"type": "object", "properties": {}}, write)
    args = {"path": "/videos/existing", "overwrite": True}
    first = asyncio_run(srv._dispatch_tool(name, args, FakeContext()))
    assert isinstance(first, types.InputRequiredResult)
    assert calls == []
    retry = types.CallToolRequestParams(
        name=name, arguments=args, request_state=first.request_state,
        input_responses={"confirm": {"action": "accept", "content": {"approve": True}}},
    )
    asyncio_run(srv._dispatch_tool(name, args, FakeContext(), retry))
    assert calls == [{**args, "base_url": "http://nas.example:5000"}]
    assert any(e["event"] == "tool_intent" for e in audit_lines(tmp_path))


def test_audit_write_failure_stops_the_approved_handler(tmp_path, monkeypatch):
    srv, calls = make_server(tmp_path)
    args = {"path": "/videos/x"}
    first = asyncio_run(srv._dispatch_tool("delete", args, FakeContext()))
    retry = params(
        arguments=args, request_state=first.request_state,
        input_responses={"confirm": {"action": "accept", "content": {"approve": True}}},
    )
    def full_disk(*_):
        raise OSError("disk full")
    monkeypatch.setattr(srv.audit, "_append", full_disk)
    with pytest.raises(ToolFailureError) as caught:
        asyncio_run(srv._dispatch_tool("delete", args, FakeContext(), retry))
    assert caught.value.payload["error"]["code"] == "audit_unavailable"
    assert calls == []


def test_confirmation_form_names_destination_and_overwrite(tmp_path):
    srv, _ = make_server(tmp_path)
    prompt = srv._confirmation_plan("move_file", {
        "source_path": "/videos/a", "destination_path": "/videos/b", "overwrite": True,
    })
    assert "/videos/a" in prompt and "/videos/b" in prompt
    assert '"overwrite": true' in prompt


@pytest.mark.parametrize("approve", [True, False])
def test_real_sdk_round_trip(tmp_path, approve):
    from mcp import Client
    srv, calls = make_server(tmp_path)
    seen = []
    async def collect(context, request):
        seen.append(request.message)
        return types.ElicitResult(action="accept", content={"approve": approve})
    async def exercise():
        async with Client(srv._create_server(), mode="2026-07-28", elicitation_callback=collect) as client:
            result = await client.call_tool("delete", {"path": "/videos/x"})
            assert client.protocol_version == "2026-07-28"
            assert result.is_error is (not approve)
    asyncio_run(exercise())
    assert len(seen) == 1
    assert len(calls) == int(approve)


@pytest.mark.parametrize("name,args", [
    ("synology_container_project_update", {"name": "existing", "content": "services: {}"}),
    ("synology_set_user", {"name": "alice", "password": "replacement"}),
])
def test_configuration_and_password_overwrites_require_confirmation(tmp_path, name, args):
    srv, calls = make_server(tmp_path)
    async def handler(arguments):
        calls.append(arguments)
        return [types.TextContent(type="text", text="written")]
    srv._register_tool(name, "Write", {"type": "object", "properties": {}}, handler)
    result = asyncio_run(srv._dispatch_tool(name, args, FakeContext()))
    assert isinstance(result, types.InputRequiredResult)
    assert calls == []


def test_approval_cannot_follow_default_to_another_nas(tmp_path):
    srv, calls = make_server(tmp_path)
    srv.sessions = {"http://nas-a:5000": "a", "http://nas-b:5000": "b"}
    args = {"path": "/videos/x"}
    first = asyncio_run(srv._dispatch_tool("delete", args, FakeContext()))
    assert "http://nas-a:5000" in first.input_requests["confirm"].params.message
    del srv.sessions["http://nas-a:5000"]
    retry = params(arguments=args, request_state=first.request_state,
                   input_responses={"confirm": {"action": "accept", "content": {"approve": True}}})
    with pytest.raises(ToolFailureError) as caught:
        asyncio_run(srv._dispatch_tool("delete", args, FakeContext(), retry))
    assert caught.value.payload["error"]["code"] == "confirmation_invalid"
    assert calls == []


def test_approved_alias_is_removed_from_execution_arguments(tmp_path):
    srv, calls = make_server(tmp_path)
    srv.nas_name_map = {"primary": "http://nas.example:5000"}
    args = {"path": "/videos/x", "nas_name": "primary"}
    first = asyncio_run(srv._dispatch_tool("delete", args, FakeContext()))
    retry = params(arguments=args, request_state=first.request_state,
                   input_responses={"confirm": {"action": "accept", "content": {"approve": True}}})
    asyncio_run(srv._dispatch_tool("delete", args, FakeContext(), retry))
    assert calls == [{"path": "/videos/x", "base_url": "http://nas.example:5000"}]


def test_structured_dsm_failure_is_audited_as_error(tmp_path):
    srv, _ = make_server(tmp_path)
    failure = {"success": False, "error": {"code": 105}}
    async def fail(arguments):
        return [types.TextContent(type="text", text=json.dumps(failure))]
    srv._register_tool("synology_status", "Status", {"type": "object"}, fail)
    asyncio_run(srv._dispatch_tool("synology_status", {}))
    last = audit_lines(tmp_path)[-1]
    assert last["outcome"] == "error"
    assert "105" in last["error"]
