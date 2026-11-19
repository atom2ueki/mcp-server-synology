# src/confirm.py - Confirmation for destructive tool calls
"""Binding a destructive call to an explicit approval, on MCP 2026-07-28.

Signed state for the standard confirmation round-trip:

* **MRTR (the standard path).** The server answers the first call with
  `InputRequiredResult` carrying an `elicitation/create` request and an opaque
  `requestState`. The client asks the user, then retries the same tool call with
  `inputResponses` and the state echoed back. The state is an HMAC over the tool
  name, the canonical arguments and an expiry, so an approval cannot be replayed
  against different arguments, a different tool, or after it lapses.
Clients without form elicitation are refused. Signing state prevents tampering;
the client's confirmation UI remains responsible for collecting human consent.

The spec requires `requestState` to be treated as attacker-controlled and to be
integrity-protected whenever it influences authorization. That is exactly what
this module does; the low-level `Server` does not seal it for us (only the
high-level `MCPServer` does).

Tokens are single-use within the process: consuming one records its signature
for the remainder of its lifetime. A restart only invalidates tokens in flight,
which is the safe direction.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

VERSION = "v2"


class ConfirmationError(RuntimeError):
    """A confirmation token is missing, malformed, expired, or already used."""


def canonical_args(arguments: dict[str, Any] | None) -> str:
    """Stable JSON for argument binding: order-independent, no volatile fields."""
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _signature(secret: bytes, tool: str, arguments: dict[str, Any] | None, expires_at: int, nonce: str) -> str:
    message = f"{VERSION}\n{tool}\n{expires_at}\n{nonce}\n{canonical_args(arguments)}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def load_secret(explicit: str, path: Path) -> bytes:
    """Secret for HMACs: an explicit value, else a persisted random one.

    This is configuration key material. ConfirmationTokens derives a fresh
    process key so state cannot be replayed after a restart.
    """
    if explicit:
        return explicit.encode()
    try:
        data = path.read_text().strip()
        if data:
            return bytes.fromhex(data)
    except (OSError, ValueError):
        pass
    secret = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secret.hex().encode())
        finally:
            os.close(fd)
    except FileExistsError:
        # Another process won the race; use its secret.
        try:
            return bytes.fromhex(path.read_text().strip())
        except (OSError, ValueError):
            return secret
    except OSError:
        pass
    return secret


class ConfirmationTokens:
    """Issue, verify and consume argument-bound confirmation tokens."""

    def __init__(self, secret: bytes, ttl_seconds: int = 300) -> None:
        # Replay tracking is process-local, so state must also be process-local.
        # A persisted configuration secret alone would permit replay on restart.
        self._secret = hmac.digest(secret, secrets.token_bytes(32), "sha256")
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._used: dict[str, int] = {}
        self._lock = threading.RLock()

    def issue(self, tool: str, arguments: dict[str, Any] | None) -> str:
        with self._lock:
            self._prune_locked()
        expires_at = int(time.time()) + self.ttl_seconds
        nonce = secrets.token_hex(16)
        signature = _signature(self._secret, tool, arguments, expires_at, nonce)
        return f"{VERSION}.{expires_at}.{nonce}.{signature}"

    def verify(self, token: str, tool: str, arguments: dict[str, Any] | None) -> int:
        """Return the expiry of a valid, unused token; raise ConfirmationError."""
        try:
            version, expires_raw, nonce, signature = token.split(".")
        except (AttributeError, ValueError) as exc:
            raise ConfirmationError("malformed confirmation token") from exc
        if version != VERSION:
            raise ConfirmationError("unsupported confirmation token version")
        try:
            expires_at = int(expires_raw)
        except ValueError as exc:
            raise ConfirmationError("malformed confirmation expiry") from exc
        if expires_at <= int(time.time()):
            raise ConfirmationError("confirmation token has expired")
        expected = _signature(self._secret, tool, arguments, expires_at, nonce)
        if not hmac.compare_digest(expected, signature):
            raise ConfirmationError(
                "confirmation token does not match this tool call "
                "(arguments changed, or the token belongs to another action)"
            )
        with self._lock:
            self._prune_locked()
            if signature in self._used:
                raise ConfirmationError("confirmation token has already been used")
        return expires_at

    def consume(self, token: str, tool: str, arguments: dict[str, Any] | None) -> int:
        """Verify and burn a token in one step."""
        with self._lock:
            expires_at = self.verify(token, tool, arguments)
            signature = token.rsplit(".", 1)[1]
            self._used[signature] = expires_at
        return expires_at

    def _prune_locked(self) -> None:
        now = int(time.time())
        for signature, expires_at in list(self._used.items()):
            if expires_at < now:
                del self._used[signature]

    @staticmethod
    def encode_state(token: str) -> str:
        """The wire form of `requestState` (opaque to the client)."""
        return base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")

    @staticmethod
    def decode_state(state: str) -> str:
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", state):
            raise ConfirmationError("malformed request state")
        padded = state + "=" * (-len(state) % 4)
        try:
            return base64.urlsafe_b64decode(padded.encode()).decode()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConfirmationError("malformed request state") from exc
