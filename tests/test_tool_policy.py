"""Offline tests for the risk view over tool_safety's annotation table.

The table itself lives in `tool_safety.py` (main's #134 module) and refuses an
unclassified tool at registration time, so coverage is structural. These tests
pin the *view*: how hints map to the server's risk tier, the two dynamic
escalations the hints cannot express, and the fail-closed default.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tool_policy import Risk, risk_for, unclassified  # noqa: E402
from tool_safety import TOOL_ANNOTATIONS  # noqa: E402

SERVER_SOURCE = Path(__file__).parent.parent / "src" / "mcp_server.py"


def registered_tools() -> set[str]:
    """Every tool name passed to `_register_tool(...)` in the server module."""
    source = SERVER_SOURCE.read_text(encoding="utf-8")
    return set(re.findall(r"_register_tool\(\s*\"([^\"]+)\"", source))


class TestCoverage:
    def test_registry_has_tools(self):
        assert len(registered_tools()) > 90

    def test_every_registered_tool_is_classified(self):
        # tool_safety raises at registration for an unclassified tool; this
        # catches the same drift at test time, before the server even boots.
        missing = unclassified(registered_tools())
        assert missing == set(), f"unclassified tools: {sorted(missing)}"

    def test_no_classification_is_orphaned(self):
        assert set(TOOL_ANNOTATIONS) - registered_tools() == set()


class TestRiskView:
    def test_read_only_tools_map_to_read(self):
        assert risk_for("list_directory") is Risk.READ
        assert risk_for("synology_update_status") is Risk.READ

    def test_additive_tools_map_to_additive(self):
        assert risk_for("create_directory") is Risk.ADDITIVE

    def test_destructive_tools_map_to_destructive(self):
        assert risk_for("delete") is Risk.DESTRUCTIVE
        assert risk_for("synology_dsm_update_install") is Risk.DESTRUCTIVE

    def test_unknown_tool_is_destructive(self):
        # The spec's pessimistic default; the confirmation gate keys off it.
        assert risk_for("rm_minus_rf_everything") is Risk.DESTRUCTIVE


class TestDynamicEscalation:
    def test_overwrite_true_keeps_file_tools_destructive(self):
        # tool_safety classifies the overwrite-capable file tools as
        # destructive outright; the dynamic check keeps them destructive even
        # if that classification ever softens to additive.
        for name in ("create_file", "copy_file", "move_file"):
            assert risk_for(name, {"overwrite": True}) is Risk.DESTRUCTIVE
            assert risk_for(name, {"overwrite": False}) is Risk.DESTRUCTIVE

    def test_password_argument_escalates_set_user(self):
        # tool_safety already classifies set_user as destructive; the dynamic
        # escalation keeps it destructive even if that classification ever
        # softens, and never makes it softer than the table says.
        assert risk_for("synology_set_user", {"password": "x"}) is Risk.DESTRUCTIVE
        assert risk_for("create_directory", {"password": "x"}) is Risk.ADDITIVE
