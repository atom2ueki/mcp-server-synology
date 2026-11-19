# src/tool_policy.py - Server-side risk tier derived from the annotation table
"""One risk view over `tool_safety`'s annotation table.

`tool_safety.py` is the single classification table: it owns the MCP
annotations (`readOnlyHint` / `destructiveHint`) for every tool and refuses to
register a tool nobody classified. This module derives the server's own,
coarser risk tier from those same hints — the tier the audit trail records and
the confirmation gate keys off — plus the two dynamic escalations the hints
cannot express:

* `overwrite: true` on a file tool turns a copy/move/create into a destructive
  call (it replaces existing data);
* a `password` argument on `synology_set_user` does the same.

Two names for one classification was the review finding this layout fixes:
there is exactly one table, and this file is a view of it, not a second copy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

from tool_safety import TOOL_ANNOTATIONS, annotations_for  # noqa: F401 — re-export


class Risk(str, Enum):
    """What a tool can do to the NAS, from least to most dangerous."""

    READ = "read"          # no state change
    ADDITIVE = "additive"  # creates something new; nothing existing is altered
    DESTRUCTIVE = "destructive"  # removes, overwrites or replaces data; may be unrecoverable


_OVERWRITE_TOOLS = frozenset({"create_file", "copy_file", "move_file"})


def risk_for(tool_name: str, arguments: dict[str, Any] | None = None) -> Risk:
    """Risk of one call. Unknown names are DESTRUCTIVE, never "probably fine"."""
    if tool_name in _OVERWRITE_TOOLS and (arguments or {}).get("overwrite"):
        return Risk.DESTRUCTIVE
    if tool_name == "synology_set_user" and "password" in (arguments or {}):
        return Risk.DESTRUCTIVE
    hints = TOOL_ANNOTATIONS.get(tool_name)
    if hints is None:
        return Risk.DESTRUCTIVE
    if hints.read_only_hint:
        return Risk.READ
    if not hints.destructive_hint:
        return Risk.ADDITIVE
    return Risk.DESTRUCTIVE


def unclassified(tool_names: Iterable[str]) -> set[str]:
    """Tool names with no entry in the annotation table (they default to DESTRUCTIVE)."""
    return {name for name in tool_names if name not in TOOL_ANNOTATIONS}
