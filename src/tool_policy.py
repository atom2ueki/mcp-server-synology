# src/tool_policy.py - Server-side risk tier for one registered tool
"""Map a tool's MCP annotations to the server's coarser risk tier.

The annotations now live in the tool's own `ToolSpec` (see `tools/`), so there
is no second table to keep in sync. This module turns those hints into the tier
the audit trail records and the confirmation gate keys off, plus the two
dynamic escalations the hints cannot express:

* `overwrite: true` on a file tool turns a copy/move/create into a destructive
  call (it replaces existing data);
* a `password` argument on `synology_set_user` does the same.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import mcp.types as types


class Risk(str, Enum):
    """What a tool can do to the NAS, from least to most dangerous."""

    READ = "read"          # no state change
    ADDITIVE = "additive"  # creates something new; nothing existing is altered
    DESTRUCTIVE = "destructive"  # removes, overwrites or replaces data; may be unrecoverable


_OVERWRITE_TOOLS = frozenset({"create_file", "copy_file", "move_file"})


def risk_for(
    annotations: types.ToolAnnotations,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> Risk:
    """Risk of one call, from its tool's declared hints."""
    if tool_name in _OVERWRITE_TOOLS and (arguments or {}).get("overwrite"):
        return Risk.DESTRUCTIVE
    if tool_name == "synology_set_user" and "password" in (arguments or {}):
        return Risk.DESTRUCTIVE
    # Without confirm=true the install tool only runs DSM's own pre-install
    # checks and changes nothing; gating that read-only dry run would block
    # legacy clients from ever seeing why an install is refused.
    if tool_name == "synology_dsm_update_install" and (arguments or {}).get("confirm") is not True:
        return Risk.READ
    if annotations.read_only_hint:
        return Risk.READ
    if not annotations.destructive_hint:
        return Risk.ADDITIVE
    return Risk.DESTRUCTIVE
