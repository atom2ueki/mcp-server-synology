"""Opt-in tool profiles: which tools exist at all.

Filtering happens at registration, so a withheld tool is absent from
tools/list and cannot be called. Classification still runs for every tool, so
an unclassified tool fails startup instead of hiding behind a profile.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import config  # noqa: E402
from mcp_server import SynologyMCPServer  # noqa: E402
from tool_safety import TOOL_ANNOTATIONS  # noqa: E402


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
        srv = server_for("read_only", monkeypatch)
        names = set(srv._tool_registry)
        for name in names:
            assert TOOL_ANNOTATIONS[name].read_only_hint is True, name
        assert "delete" not in names
        assert "create_directory" not in names
        assert "list_directory" in names
        assert srv._withheld_tools > 0

    def test_no_destructive_keeps_additive_hides_destructive(self, monkeypatch):
        srv = server_for("no_destructive", monkeypatch)
        names = set(srv._tool_registry)
        for name in names:
            assert TOOL_ANNOTATIONS[name].destructive_hint is not True, name
        assert "delete" not in names
        assert "create_directory" in names
        assert "list_directory" in names

    def test_profiles_are_nested(self, monkeypatch):
        full = set(server_for("full", monkeypatch)._tool_registry)
        no_dest = set(server_for("no_destructive", monkeypatch)._tool_registry)
        read_only = set(server_for("read_only", monkeypatch)._tool_registry)
        assert read_only <= no_dest <= full

    def test_unknown_profile_falls_back_to_full(self, monkeypatch):
        srv = server_for("YOLO", monkeypatch)
        assert srv.tool_profile == "full"
        assert "delete" in srv._tool_registry

    def test_classification_still_runs_for_withheld_tools(self, monkeypatch):
        # An unclassified tool raises regardless of profile; otherwise a
        # read-only deployment would never notice a missing classification.
        srv = server_for("read_only", monkeypatch)
        with pytest.raises(ValueError, match="no safety classification"):
            srv._register_tool("brand_new_tool", "d", {"type": "object"}, lambda a: a)
