"""Offline tests for the tool risk table and the annotations derived from it.

The table is the server's own authority on what is dangerous -- the audit
pre-flight refuses destructive calls when it cannot record them -- so drift is a
safety bug, not a docs bug. The coverage test reads the registrations straight
from the server source, which catches a new tool even though the registry is
built at runtime.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tool_policy import (  # noqa: E402
    _RISK_BY_TOOL,
    Risk,
    annotations_for,
    risk_for,
    unclassified,
)

SERVER_SOURCE = Path(__file__).parent.parent / "src" / "mcp_server.py"


def registered_tools() -> set[str]:
    """Every tool name passed to `_register_tool(...)` in the server module."""
    source = SERVER_SOURCE.read_text(encoding="utf-8")
    return set(re.findall(r"_register_tool\(\s*\"([^\"]+)\"", source))


class TestCoverage:
    def test_registry_has_tools(self):
        assert len(registered_tools()) > 90

    def test_every_registered_tool_is_classified(self):
        missing = unclassified(registered_tools())
        assert missing == set(), (
            "tools fall back to DESTRUCTIVE; add them to a risk set in "
            f"src/tool_policy.py: {sorted(missing)}"
        )

    def test_every_classified_tool_is_registered(self):
        # A renamed tool leaves a stale entry behind; that is how a
        # destructive name silently stops matching the real call.
        assert set(_RISK_BY_TOOL) - registered_tools() == set()


class TestRiskLookup:
    def test_destructive_tools_resolve_destructive(self):
        assert risk_for("delete") is Risk.DESTRUCTIVE
        assert risk_for("synology_container_image_prune") is Risk.DESTRUCTIVE
        assert risk_for("synology_delete_user") is Risk.DESTRUCTIVE

    def test_read_tools_resolve_read(self):
        assert risk_for("list_directory") is Risk.READ
        assert risk_for("synology_container_list") is Risk.READ
        assert risk_for("synology_health_summary") is Risk.READ

    def test_additive_is_not_destructive(self):
        assert risk_for("create_directory") is Risk.ADDITIVE
        assert risk_for("synology_target_create") is Risk.ADDITIVE

    def test_unknown_tool_is_destructive(self):
        # The spec's pessimistic default, and the audit pre-flight keys off it.
        assert risk_for("rm_minus_rf_everything") is Risk.DESTRUCTIVE


class TestAnnotations:
    def test_read_only_projection(self):
        ann = annotations_for("list_directory")
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is True
        assert ann.open_world_hint is False

    def test_destructive_projection(self):
        ann = annotations_for("delete")
        assert ann.read_only_hint is False
        assert ann.destructive_hint is True
        assert ann.idempotent_hint is False

    def test_mutating_projection_is_destructive_but_idempotent(self):
        # A repeated `set_user_permissions` converges; it can still overwrite.
        ann = annotations_for("synology_set_user_permissions")
        assert ann.destructive_hint is True
        assert ann.idempotent_hint is True

    def test_open_world_only_for_registry_tools(self):
        assert annotations_for("synology_container_registry_search").open_world_hint is True
        assert annotations_for("synology_container_image_pull").open_world_hint is True
        assert annotations_for("synology_container_list").open_world_hint is False

    def test_annotations_are_populated_for_a_registered_tool(self):
        # End-to-end through the real registry: the annotations actually reach
        # types.Tool, not just the helper.
        from mcp_server import SynologyMCPServer

        server = SynologyMCPServer.__new__(SynologyMCPServer)
        server._tool_registry = {}
        server._register_tool("delete", "delete a file", {"type": "object"}, lambda a: a)
        tool, _ = server._tool_registry["delete"]
        assert tool.annotations is not None
        assert tool.annotations.destructive_hint is True

    def test_session_tools_are_not_destructive(self):
        # login/logout change only the MCP's own session, never NAS data.
        for name in ("synology_login", "synology_logout"):
            ann = annotations_for(name)
            assert ann.destructive_hint is False
            assert ann.read_only_hint is False

    def test_unclassified_tool_still_gets_annotations(self):
        ann = annotations_for("brand_new_tool")
        assert ann.destructive_hint is True
        assert ann.read_only_hint is False
