# src/audit.py - Append-only audit trail for MCP tool calls
"""One JSON line per tool call, written before the next call starts.

Why this exists: DSM's Log Center is the authoritative record of what happened
on the NAS, but reading it needs an admin account, and the MCP account is
deliberately not admin (`SYNO.Core.SyslogClient.Log` answers error 105). An
operator asking "what did the agent do at 20:17?" then has to reconstruct the
timeline from session transcripts -- hours of work that one grep should answer.

The record is deliberately boring and self-contained:

    {"ts": "...", "event": "tool_call", "run": "<uuid>", "pid": 7,
     "tool": "delete", "risk": "destructive", "target": "/media/library/x",
     "args": {...redacted...}, "outcome": "ok", "duration_ms": 41}

Failure policy follows the OWASP agent guidance (fail closed for high-impact
actions, best effort otherwise):

* **Before** a DESTRUCTIVE tool runs, `ensure_writable()` proves the trail can
  be opened and a required `tool_intent` record is written and flushed.
  If either fails, the call is refused before execution.
* **After** any call, `record()` is best effort. The action already ran; losing
  the line must not turn a success into an error the caller can misinterpret.
  The gap is reported on stderr.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)

# Argument names whose *values* must never reach the trail. Matched as a full
# key name (case-insensitive), not a substring: `session_timeout` is not a
# secret, `session` is.
_SECRET_KEY = re.compile(
    r"^(passwd|password|passphrase|secret|token|otp|otp_code|api[_-]?key|"
    r"authorization|chap_password|sid|session|syno_token|confirm_token|device_id|did)$",
    re.IGNORECASE,
)

# One argument value is truncated to this many characters. `create_file` takes
# a whole payload; the trail records that it was written, not a copy of it.
MAX_VALUE_CHARS = 512

# Keys that identify "what was touched", in priority order.
_TARGET_KEYS = (
    "path",
    "folder_path",
    "file_path",
    "remote_path",
    "source_path",
    "destination_path",
    "target_path",
    "target",
)

# How long a successful writability probe is trusted, so a burst of destructive
# calls does not open the file once per call.
_PROBE_TTL_SECONDS = 30.0


class AuditUnavailableError(RuntimeError):
    """The audit trail cannot be written; destructive calls must be refused."""


def redact(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe copy of `value` with secrets masked and long strings cut.

    Recurses through dicts and lists. Masking is by key name, so a password
    passed as an unnamed positional value is not covered -- no MCP tool takes
    arguments positionally, and the alternative (value sniffing) produces false
    positives on ordinary strings like paths.
    """
    if isinstance(value, dict):
        return {str(k): ("***" if _SECRET_KEY.match(str(k)) else redact(v, key=str(k)))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, key=key) for item in value]
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + f"...({len(value)} chars)"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)[:MAX_VALUE_CHARS]


def target_of(arguments: dict[str, Any]) -> Optional[str]:
    """The path-like argument a call acted on, if it named one."""
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class AuditLog:
    """Append-only JSONL writer with size-based rotation."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        enabled: bool = True,
        max_bytes: int = 10 * 1024 * 1024,
        keep: int = 5,
        run_id: str = "",
    ) -> None:
        self.path = Path(path).expanduser()
        self.enabled = enabled
        self.max_bytes = max(0, int(max_bytes))
        self.keep = max(1, int(keep))
        self.run_id = run_id
        self._lock = threading.Lock()
        self._probe_ok_until = 0.0

    # -- writing ---------------------------------------------------------

    def record(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        outcome: str,
        duration_ms: float,
        risk: str,
        error: str = "",
        event: str = "tool_call",
        extra: Optional[dict[str, Any]] = None,
        required: bool = False,
    ) -> None:
        """Append a record; required intent records fail before an action runs."""
        if not self.enabled:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            "run": self.run_id,
            "pid": os.getpid(),
            "tool": tool,
            "risk": risk,
            "target": target_of(arguments),
            "args": redact(arguments),
            "outcome": outcome,
            "duration_ms": round(duration_ms, 1),
        }
        if error:
            entry["error"] = error[:MAX_VALUE_CHARS]
        for key, value in redact(extra or {}).items():
            entry.setdefault(key, value)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with FileLock(str(self.path) + ".lock", timeout=5, mode=0o600):
                    self._rotate_if_needed(len(line.encode()))
                    self._append(line)
        except (OSError, Timeout) as exc:
            if required:
                raise AuditUnavailableError(f"cannot persist audit intent: {self.path}: {exc}") from exc
            # The action already happened; report the lost audit line without
            # changing what the caller sees.
            logger.error("audit: cannot append to %s: %s", self.path, exc)

    def _append(self, line: str) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            remaining = memoryview(line.encode("utf-8"))
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("short audit write")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _rotate_if_needed(self, incoming: int) -> None:
        if self.max_bytes <= 0:
            return
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size + incoming <= self.max_bytes:
            return
        # audit.jsonl -> audit.jsonl.1 -> ... -> audit.jsonl.<keep>
        oldest = self.path.with_name(f"{self.path.name}.{self.keep}")
        oldest.unlink(missing_ok=True)
        for index in range(self.keep - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.replace(self.path.with_name(f"{self.path.name}.1"))

    # -- pre-flight ------------------------------------------------------

    def ensure_writable(self) -> None:
        """Prove a line can be appended; raise AuditUnavailableError if not.

        Called before a DESTRUCTIVE tool runs. The check creates the parent
        directory and opens the file in append mode, which catches the realistic
        failures: read-only mount, full disk, bad permissions.
        """
        if not self.enabled:
            return
        now = time.monotonic()
        if now < self._probe_ok_until:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
        except OSError as exc:
            self._probe_ok_until = 0.0
            raise AuditUnavailableError(f"audit trail is not writable: {self.path}: {exc}") from exc
        self._probe_ok_until = now + _PROBE_TTL_SECONDS
