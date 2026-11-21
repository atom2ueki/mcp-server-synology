"""Opt-in tool profiles: which tools exist at all.

Filtering happens at registration, so a withheld tool is absent from
tools/list and cannot be called. Every `ToolSpec` carries its annotations, so
there is no unclassified path that a profile could hide.
"""

import sys
from pathlib import Path

import mcp.types as types
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import config  # noqa: E402
from mcp_server import SynologyMCPServer  # noqa: E402
from tools import ADDITIVE, ALL_TOOLS, LOGIN_TOOL, LOGOUT_TOOL, ToolSpec  # noqa: E402

_ANNOTATIONS = {
    spec.name: spec.annotations
    for spec in (*ALL_TOOLS, LOGIN_TOOL, LOGOUT_TOOL)
}


def server_for(profile, monkeypatch):
    monkeypatch.setattr(config, "tool_profile", profile)
    return SynologyMCPServer()


class TestProfiles:
    def test_full_exposes_everything(self, monkeypatch):
        srv = server_for("full", monkeypatch)
        names = set(srv._tool_registry)
        assert "delete" in names
        assert "list_directory" in names
        assert srv._withheld_tools == 0

    def test_read_only_hides_everything_that_writes(self, monkeypatch):
        # Force the no-credentials branch so login/logout are registered, as
        # they are on CI and on any deployment without stored credentials.
        monkeypatch.setattr(config, "has_synology_credentials", lambda: False)
        srv = server_for("read_only", monkeypatch)
        names = set(srv._tool_registry)
        session_tools = {"synology_login", "synology_logout"}
        assert session_tools <= names
        for name in names - session_tools:
            assert _ANNOTATIONS[name].read_only_hint is True, name
        assert "delete" not in names
        assert "create_directory" not in names
        assert "list_directory" in names
        assert srv._withheld_tools > 0

    def test_no_destructive_keeps_additive_hides_destructive(self, monkeypatch):
        srv = server_for("no_destructive", monkeypatch)
        names = set(srv._tool_registry)
        for name in names:
            assert _ANNOTATIONS[name].destructive_hint is not True, name
        assert "delete" not in names
        assert "create_directory" in names
        assert "list_directory" in names

    def test_profiles_are_nested(self, monkeypatch):
        full = set(server_for("full", monkeypatch)._tool_registry)
        no_dest = set(server_for("no_destructive", monkeypatch)._tool_registry)
        read_only = set(server_for("read_only", monkeypatch)._tool_registry)
        assert read_only <= no_dest <= full

    def test_session_tools_survive_read_only(self, monkeypatch):
        # With auto-login off, login is the only way a client can read anything;
        # a read-only profile must not hide it.
        monkeypatch.setattr(config, "auto_login", False)
        srv = server_for("read_only", monkeypatch)
        assert "synology_login" in srv._tool_registry
        assert "synology_logout" in srv._tool_registry
        assert "delete" not in srv._tool_registry

    def test_unknown_profile_falls_back_to_full(self, monkeypatch):
        srv = server_for("YOLO", monkeypatch)
        assert srv.tool_profile == "full"
        assert "delete" in srv._tool_registry

    def test_annotations_are_required_at_construction(self):
        # The old name table failed at registration; now a spec cannot exist
        # without a classification at all.
        with pytest.raises(TypeError):
            ToolSpec("brand_new_tool", "d", {"type": "object"}, lambda a: a)

    def test_incomplete_hints_are_withheld_by_no_destructive(self, monkeypatch):
        # destructive_hint=None means the protocol default (destructive).
        srv = server_for("no_destructive", monkeypatch)
        before = srv._withheld_tools
        srv._register_tool(
            ToolSpec(
                "incomplete_write", "d", {"type": "object"},
                lambda a: a, types.ToolAnnotations(read_only_hint=False),
            )
        )
        assert "incomplete_write" not in srv._tool_registry
        assert srv._withheld_tools == before + 1

    def test_a_new_spec_is_still_filtered_by_profile(self, monkeypatch):
        srv = server_for("read_only", monkeypatch)
        before = srv._withheld_tools
        srv._register_tool(
            ToolSpec("brand_new_tool", "d", {"type": "object"}, lambda a: a, ADDITIVE)
        )
        assert "brand_new_tool" not in srv._tool_registry
        assert srv._withheld_tools == before + 1
