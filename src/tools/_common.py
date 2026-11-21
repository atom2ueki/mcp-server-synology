"""Shared schema shapes and the one ToolSpec declaration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import mcp.types as types

from tool_safety import ADDITIVE, DESTRUCTIVE, READ_ONLY

T = {"type": "object", "properties": {}, "required": []}
TN = {
    "type": "object",
    "properties": {
        "nas_name": {
            "type": "string",
            "description": "NAS identifier from secrets.json (e.g. 'nas1', 'nas2')",
        },
        "base_url": {
            "type": "string",
            "description": "Synology NAS base URL (alternative to nas_name)",
        },
    },
    "required": [],
}


def nas_props(props: dict[str, Any]) -> dict[str, Any]:
    return {**TN, "properties": {**TN["properties"], **props}}


def nas_required(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {**TN, "properties": {**TN["properties"], **props}, "required": required}


@dataclass(frozen=True)
class ToolSpec:
    """One tool: everything a caller and the server need, in one place.

    `handler` is a method name on SynologyMCPServer (with optional
    `handler_kwargs` for the shared dispatchers) or a callable, which tests use
    to register a stub without building a bound method.
    """

    name: str
    description: str
    schema: dict[str, Any]
    handler: str | Callable
    annotations: types.ToolAnnotations
    handler_kwargs: dict[str, str] = field(default_factory=dict)


__all__ = [
    "ADDITIVE",
    "DESTRUCTIVE",
    "READ_ONLY",
    "T",
    "TN",
    "ToolSpec",
    "nas_props",
    "nas_required",
]
