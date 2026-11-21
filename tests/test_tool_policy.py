"""Offline tests for the risk view over each tool's declared annotations.

Annotations live on the tool's `ToolSpec`, so coverage is structural. These
tests pin the *view*: how hints map to the server's risk tier, the two dynamic
escalations the hints cannot express, and the per-tool declarations that the
old name table used to hold.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import mcp.types as types  # noqa: E402

from tool_policy import Risk, risk_for  # noqa: E402
from tools import ALL_TOOLS  # noqa: E402

SPECS = {spec.name: spec for spec in ALL_TOOLS}


class TestCoverage:
    def test_catalog_has_tools(self):
        assert len(SPECS) > 90

    def test_catalog_size_is_deliberate(self):
        # The catalog is the product surface; a spec dropped while editing a
        # module should fail here, not silently shrink tools/list.
        assert len(SPECS) == 107

    def test_every_spec_carries_annotations(self):
        for spec in ALL_TOOLS:
            assert isinstance(spec.annotations, types.ToolAnnotations), spec.name


class TestRiskView:
    def test_read_only_tools_map_to_read(self):
        for name in ("list_directory", "synology_update_status"):
            assert risk_for(SPECS[name].annotations, name) is Risk.READ

    def test_additive_tools_map_to_additive(self):
        assert risk_for(SPECS["create_directory"].annotations, "create_directory") is Risk.ADDITIVE

    def test_destructive_tools_map_to_destructive(self):
        for name in ("delete", "synology_terminal_set"):
            assert risk_for(SPECS[name].annotations, name) is Risk.DESTRUCTIVE

    def test_dsm_install_precheck_is_read_only(self):
        # The handler's no-confirm branch runs DSM's pre-install checks only.
        hints = SPECS["synology_dsm_update_install"].annotations
        assert risk_for(hints, "synology_dsm_update_install", {}) is Risk.READ
        assert risk_for(hints, "synology_dsm_update_install", {"confirm": False}) is Risk.READ
        assert risk_for(hints, "synology_dsm_update_install", {"confirm": True}) is Risk.DESTRUCTIVE


class TestDynamicEscalation:
    def test_overwrite_true_keeps_file_tools_destructive(self):
        # The specs classify the overwrite-capable file tools as destructive
        # outright; the dynamic check keeps them destructive even if that
        # classification ever softens to additive.
        for name in ("create_file", "copy_file", "move_file"):
            hints = SPECS[name].annotations
            assert risk_for(hints, name, {"overwrite": True}) is Risk.DESTRUCTIVE
            assert risk_for(hints, name, {"overwrite": False}) is Risk.DESTRUCTIVE

    def test_password_argument_escalates_set_user(self):
        assert risk_for(
            SPECS["synology_set_user"].annotations, "synology_set_user", {"password": "x"}
        ) is Risk.DESTRUCTIVE
        assert risk_for(
            SPECS["create_directory"].annotations, "create_directory", {"password": "x"}
        ) is Risk.ADDITIVE
