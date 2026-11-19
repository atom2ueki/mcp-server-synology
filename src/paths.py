# src/paths.py - Delete-path admission policy
"""Reject ambiguous paths and share/volume roots before either delete path.

This is admission policy for `delete`, not trash behaviour: it runs whether the
file is about to be moved into a trash folder or unlinked outright, so it lives
on its own rather than inside `trash.py` (which is otherwise only about the
move-into-trash half).
"""

from __future__ import annotations

import re

_VOLUME = re.compile(r"volume\d+")

# The same prefixes FileStation.delete refuses. Soft delete moves the target
# instead of unlinking it, so it must not become a way around this list.
_PROTECTED = ("/volume1", "/homes", "/var", "/etc", "/usr", "/bin", "/sbin")


def validate_delete_path(path: str) -> str:
    """Reject ambiguous paths and share/volume roots; return the canonical path."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ValueError("delete path must be absolute")
    parts = path.strip("/").split("/")
    if any(part in {".", "..", ""} for part in parts):
        raise ValueError("delete path must be canonical; root deletion is refused")
    if len(parts) < 2 or (_VOLUME.fullmatch(parts[0]) and len(parts) < 3):
        raise ValueError("refusing to delete a filesystem, volume, or share root")
    canonical = "/" + "/".join(parts)
    if any(canonical == p or canonical.startswith(p + "/") for p in _PROTECTED):
        raise ValueError(f"refusing to delete a protected system path: {canonical}")
    return canonical
