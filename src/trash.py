# src/trash.py - Capability-detected soft delete
"""Move deletions into a trash folder when the share can host one.

The trash is **not** DSM's Recycle Bin and does not depend on it. It is a plain
folder (`<share>/#trash/<date>/…`) that the MCP moves files into before
deleting, using the same FileStation move as any other rename. Within one share
that is a metadata operation: instant, and no extra space.

Nothing is assumed about the NAS. Other people's shares may be read-only, on a
full volume, or not writable by the MCP account at all. So the policy is:

* `auto`   — try the trash; if the share cannot host it, delete normally.
* `always` — require the trash; refuse the delete if it is unavailable.
* `never`  — delete normally.

`TrashUnavailableError` is raised only before anything has moved, so the caller can
safely fall back. `TrashVerificationError` is raised after a move was attempted
and cannot be accounted for: the caller must **not** then hard-delete, because
the data may already be in the trash.
"""

from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4


@dataclass(frozen=True)
class TrashPolicy:
    mode: str = "auto"
    dir_name: str = "#trash"

    @property
    def enabled(self) -> bool:
        return self.mode in ("auto", "always")

    @property
    def required(self) -> bool:
        return self.mode == "always"


class TrashUnavailableError(RuntimeError):
    """The share cannot host a trash folder. Nothing has been moved."""


class TrashVerificationError(RuntimeError):
    """A move was attempted and its outcome is unknown. Do not hard-delete."""


def _is_dir(filestation: Any, path: str) -> bool:
    try:
        return filestation.get_file_info(path).get("type") == "directory"
    except Exception:
        return False


def _ensure_dir(filestation: Any, parent: str, name: str) -> str:
    """Create `parent/name` if missing; return its path; raise TrashUnavailableError."""
    path = f"{parent.rstrip('/')}/{name}"
    if _is_dir(filestation, path):
        return path
    try:
        filestation.create_directory(parent, name, force_parent=True)
    except Exception as exc:
        # A concurrent create is harmless; anything else means no trash here.
        if not _is_dir(filestation, path):
            raise TrashUnavailableError(f"cannot create {path}: {exc}") from exc
    return path


def trash_root_for(path: str, policy: TrashPolicy) -> str:
    """Trash root for a DSM path.

    `dir_name` is normally a folder name under the share (`#trash`), which is
    the cleanest default. A leading `/` makes it an absolute template instead,
    with `{share}` substituted — the escape hatch for accounts that cannot
    write the share root but can write inside the library (`/media/library/.trash`).
    """
    normalized = "/" + path.strip("/")
    parts = normalized.split("/")
    share = parts[1] if len(parts) > 1 else ""
    if not share:
        raise TrashUnavailableError(f"not a share path: {path}")
    configured = policy.dir_name.strip()
    if configured.startswith("/"):
        root = configured.replace("{share}", share).rstrip("/")
    else:
        root = f"/{share}/{configured.strip('/')}"
    root = posixpath.normpath(root)
    if not root.startswith(f"/{share}/"):
        raise TrashUnavailableError("trash must be inside the source share")
    return root


def soft_delete(
    filestation: Any,
    path: str,
    policy: TrashPolicy,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Move `path` into the share's trash; return where it landed."""
    now = now or datetime.now()
    normalized = "/" + path.strip("/")
    if normalized == "/":
        raise TrashUnavailableError("refusing to trash the filesystem root")
    if not path.startswith("/"):
        raise TrashUnavailableError(f"path must be absolute: {path}")

    root = trash_root_for(normalized, policy)
    if normalized == root or normalized.startswith(root + "/"):
        raise TrashUnavailableError(f"{normalized} is already inside {root}")

    name = os.path.basename(normalized)
    if not name:
        raise TrashUnavailableError(f"cannot derive a name from {path}")

    parent = os.path.dirname(normalized) or "/"
    dated = _ensure_dir(filestation, root, now.strftime("%Y-%m-%d"))
    # Always isolate each deletion. A check-then-move of a shared basename
    # races: FileStation treats a directory created by another call as a
    # container, nesting the second source inside the first recovery entry.
    folder = _ensure_dir(filestation, dated, uuid4().hex)
    destination = f"{folder}/{name}"

    try:
        moved = filestation.move_file(normalized, destination)
    except Exception as exc:
        # `move_file` may rename before it moves; if the source is gone, the
        # outcome is unknown and a hard delete could destroy the only copy.
        if not _path_exists(filestation, normalized):
            raise TrashVerificationError(
                f"move of {normalized} failed after the source disappeared ({exc}); "
                "refusing to delete"
            ) from exc
        raise TrashVerificationError(
            f"cannot move {normalized} into {root}: {exc}; refusing to delete"
        ) from exc

    actual = moved.get("destination_path", moved.get("destination", destination)) if isinstance(moved, dict) else destination
    if actual != destination or not _path_exists(filestation, destination) or _path_exists(filestation, normalized):
        raise TrashVerificationError(
            f"move reported success but {destination} is missing; refusing to delete"
        )
    return {
        "soft_delete": True,
        "trash_path": destination,
        "trash_root": root,
        "original_path": normalized,
        "source_parent": parent,
    }


def _path_exists(filestation: Any, path: str) -> bool:
    try:
        filestation.get_file_info(path)
        return True
    except Exception:
        return False
