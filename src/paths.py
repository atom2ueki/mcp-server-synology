# src/paths.py - Write-path admission policy
"""Admission policy for paths a write is about to touch.

Two independent guards live here:

* structural — reject ambiguous paths and share/volume roots, so a client that
  passes only ``/media`` cannot turn a delete into "the whole share";
* allow-list — optional ``MCP_FILESTATION_WRITE_PATHS`` confines writes to chosen
  subtrees. It is empty by default, which keeps today's behaviour.

The policy runs before any network call, and for `delete` it runs before the
trash decision: soft delete moves the target instead of unlinking it, so it must
not become a way around either guard.
"""

from __future__ import annotations

import posixpath
import re
from typing import Iterable, Sequence

_VOLUME = re.compile(r"volume\d+")

# The same prefixes FileStation.delete refuses. Soft delete moves the target
# instead of unlinking it, so it must not become a way around this list.
_PROTECTED = ("/volume1", "/homes", "/var", "/etc", "/usr", "/bin", "/sbin")
# Writes on volume-prefixed paths (/volume1/share/...) are ordinary; only the
# volume mount itself is a root, which `_refuse_root` already rejects. Delete
# keeps the stricter list for compatibility.
_PROTECTED_WRITE = ("/homes", "/var", "/etc", "/usr", "/bin", "/sbin")


class PathOutsideRootsError(ValueError):
    """A structurally valid path that the configured roots do not admit."""


def _canonical(path: str, what: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ValueError(f"{what} must be absolute")
    parts = path.strip("/").split("/")
    if any(part in {".", "..", ""} for part in parts):
        raise ValueError(f"{what} must be canonical")
    return "/" + "/".join(parts)


def _refuse_root(parts: Sequence[str], what: str) -> None:
    if len(parts) < 2 or (_VOLUME.fullmatch(parts[0]) and len(parts) < 3):
        raise ValueError(f"refusing to {what} a filesystem, volume, or share root: /{'/'.join(parts)}")


def _refuse_protected(canonical: str, protected: Sequence[str] = _PROTECTED) -> None:
    if any(canonical == p or canonical.startswith(p + "/") for p in protected):
        raise ValueError(f"refusing to write a protected system path: {canonical}")


def validate_delete_path(path: str) -> str:
    """Reject ambiguous paths and share/volume roots; return the canonical path."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise ValueError("delete path must be absolute")
    parts = path.strip("/").split("/")
    if any(part in {".", "..", ""} for part in parts):
        raise ValueError("delete path must be canonical; root deletion is refused")
    _refuse_root(parts, "delete")
    canonical = "/" + "/".join(parts)
    if any(canonical == p or canonical.startswith(p + "/") for p in _PROTECTED):
        raise ValueError(f"refusing to delete a protected system path: {canonical}")
    return canonical


def normalize_roots(roots: Iterable[str] | None) -> tuple[str, ...]:
    """Validate and canonicalize the File Station write allow-list.

    A relative, empty, or ``..``-containing root is a configuration error, not
    something to silently drop: dropping it would widen access. ``/`` is
    rejected too — leave the setting empty to allow every share.
    """
    normalized: list[str] = []
    for raw in roots or ():
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("File Station roots must be non-empty strings")
        raw = raw.strip()
        if not raw.startswith("/") or "\x00" in raw:
            raise ValueError(f"File Station root must be absolute: {raw}")
        if any(part == ".." for part in raw.split("/")):
            raise ValueError(f"File Station root must not contain '..': {raw}")
        # Collapse repeated and trailing slashes the same way DSM reports paths.
        canonical = posixpath.normpath(raw)
        if canonical == "/":
            raise ValueError("File Station roots must not contain '/'; leave the setting empty instead")
        if canonical not in normalized:
            normalized.append(canonical)
    return tuple(normalized)


def ensure_within_roots(canonical: str, roots: Iterable[str] | None) -> str:
    """Return ``canonical`` if it is one of, or below, a configured root."""
    allowed = normalize_roots(roots)
    if not allowed:
        return canonical
    if any(canonical == root or canonical.startswith(root + "/") for root in allowed):
        return canonical
    raise PathOutsideRootsError(
        f"{canonical} is outside the configured File Station write paths ({', '.join(allowed)})"
    )


def validate_write_path(
    path: str,
    roots: Iterable[str] | None = None,
    *,
    allow_share_root: bool = False,
) -> str:
    """Admit a write target: canonical, not a share/volume root, inside ``roots``.

    ``allow_share_root`` exists for move destinations: DSM treats an existing
    directory destination as "move into this folder", so the effective target is
    ``<destination>/<source name>``. The folder itself may be a share root; the
    entry that gets written is still one level below it.
    """
    canonical = _canonical(path, "path")
    parts = canonical.strip("/").split("/")
    if not allow_share_root:
        _refuse_root(parts, "path")
    elif _VOLUME.fullmatch(parts[0]) and len(parts) < 2:
        # A share root may be a move destination, a volume mount may not.
        raise ValueError(
            f"refusing to write a filesystem, volume, or share root: {canonical}"
        )
    _refuse_protected(canonical, _PROTECTED_WRITE)
    return ensure_within_roots(canonical, roots)


def create_directory_target(folder_path: str, name: str) -> str:
    """The path `create_directory` will create, using the implementation's clean rule."""
    clean_name = name.strip().replace("/", "").replace("\\", "")
    if clean_name in {"", ".", ".."}:
        raise ValueError("directory name must be a plain name")
    return posixpath.normpath(f"{folder_path.rstrip('/')}/{clean_name}")


def copy_target(destination_folder: str, source_path: str) -> str:
    """The file `copy_file` will write: source name preserved inside the folder."""
    name = posixpath.basename(source_path.rstrip("/"))
    if name in {"", ".", ".."}:
        raise ValueError("source_path must name a file")
    return posixpath.normpath(f"{destination_folder.rstrip('/')}/{name}")


def rename_target(path: str, new_name: str) -> str:
    """The sibling path `rename_file` will produce."""
    clean_name = new_name.strip().replace("/", "").replace("\\", "")
    if clean_name in {"", ".", ".."}:
        raise ValueError("new_name must be a plain name")
    return posixpath.normpath(f"{posixpath.dirname(path.rstrip('/'))}/{clean_name}")
