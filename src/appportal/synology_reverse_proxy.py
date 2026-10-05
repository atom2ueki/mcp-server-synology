# src/appportal/synology_reverse_proxy.py - Synology Login Portal reverse proxy management
#
# Wraps SYNO.Core.AppPortal.ReverseProxy (Control Panel > Login Portal > Advanced >
# Reverse Proxy) and SYNO.Core.AppPortal.AccessControl (the access control
# profiles an entry can reference), both v1 on entry.cgi.
#
# Neither API is in Synology's published developer guide. The wire contract
# follows batonogov/terraform-provider-synology-dsm, which reconstructed it from
# several independent captures. It was checked against a live DS218+ running
# DSM 7.4.1-90080 on 2026-10-05: list, create, update and delete of a throwaway
# entry behave as described below, and the six existing entries on that NAS were
# byte-identical afterwards. Code 4151 and 4154 are as that provider reports
# them; neither was reproduced here.

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock, Timeout

from utils.synology_api import SynologyAPIClient

REVERSE_PROXY_API = "SYNO.Core.AppPortal.ReverseProxy"
ACCESS_CONTROL_API = "SYNO.Core.AppPortal.AccessControl"

# DSM encodes an endpoint's protocol as an integer.
_PROTOCOL_CODES = {"HTTP": 0, "HTTPS": 1}
_PROTOCOL_NAMES = {code: name for name, code in _PROTOCOL_CODES.items()}

# The header pair DSM's own "WebSocket" preset inserts into customize_headers.
WEBSOCKET_HEADERS = (
    {"name": "Upgrade", "value": "$http_upgrade"},
    {"name": "Connection", "value": "$connection_upgrade"},
)
_WEBSOCKET_NAMES = frozenset(h["name"].lower() for h in WEBSOCKET_HEADERS)

# Seconds to wait for a create, update or delete. Each one makes DSM rewrite and
# reload its nginx configuration before it answers, and on a DS218+ that took
# well over the client's 15-second default: three creates in a row timed out
# there. A timeout does not cancel the write -- one of those three landed anyway
# -- so a timed-out write is reported as "outcome unknown", never retried.
WRITE_TIMEOUT_SECONDS = 120

# DSM's defaults for a new entry, as the UI fills them in.
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_PROXY_HTTP_VERSION = 1

# Codes specific to this API. Unmapped codes still get api/method/version
# attached by SynologyAPIClient, which is what makes them searchable.
REVERSE_PROXY_ERRORS: Dict[int, str] = {
    4151: "DSM rejected the entry format",
    4154: "A reverse proxy entry with this source already exists",
}

# Fields DSM keeps for its own bookkeeping. They are carried through an update
# untouched but never shown to the caller: nothing here needs them, and a
# caller that copied one into another call could only do harm.
_INTERNAL_FIELDS = ("_key",)


def _invalid(method: str, message: str) -> Dict[str, Any]:
    """Refuse a call on its arguments, before anything is sent."""
    return {
        "success": False,
        "error": {
            "code": "invalid_argument",
            "message": message,
            "api": REVERSE_PROXY_API,
            "method": method,
        },
    }


def _not_found(method: str, uuid: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": {
            "code": "not_found",
            "message": f"No reverse proxy entry with UUID {uuid!r}",
            "api": REVERSE_PROXY_API,
            "method": method,
        },
    }


def _describe(result: Dict[str, Any]) -> Dict[str, Any]:
    """Add this API's own error descriptions where DSM sent only a number."""
    error = result.get("error")
    if not result.get("success") and isinstance(error, dict) and "message" not in error:
        description = REVERSE_PROXY_ERRORS.get(error.get("code"))
        if description:
            error["message"] = description
    return result


def _write_outcome(result: Dict[str, Any]) -> Dict[str, Any]:
    """Describe a write's failure, flagging one whose outcome is unknown.

    A network error on a write means DSM may still have applied it, so the
    caller is told to look before trying again rather than left to retry into a
    duplicate.
    """
    result = _describe(result)
    error = result.get("error")
    if not result.get("success") and isinstance(error, dict) and error.get("code") == "network_error":
        error["outcome_unknown"] = True
        error["message"] = (
            f"{error.get('message', '')} -- DSM may still have applied this change. "
            "Check synology_reverse_proxy_list before retrying."
        )
    return result


def _protocol_code(value: Any, field: str) -> int:
    if not isinstance(value, str) or value.strip().upper() not in _PROTOCOL_CODES:
        raise ValueError(f"{field} must be 'HTTP' or 'HTTPS', got {value!r}")
    return _PROTOCOL_CODES[value.strip().upper()]


def _port(value: Any, field: str) -> int:
    """Return a TCP port, refusing anything that would silently become another.

    `int(True)` is 1 and `int(443.9)` is 443, so plain int() would turn a
    malformed argument into a listener on a port nobody asked for.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number, not a boolean")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError(f"{field} must be a port between 1 and 65535, got {value!r}")
    return value


def _hostname(value: Any, field: str) -> str:
    """A bare host name or IP. DSM stores it as given, so a scheme or path here
    would produce an nginx server block that never matches."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty host name or IP address")
    host = value.strip()
    if "://" in host or "/" in host or any(c.isspace() for c in host):
        raise ValueError(f"{field} must be a bare host name or IP, without scheme or path: {value!r}")
    return host


def _timeout(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a whole number of seconds, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} must be a whole number of seconds, got {value!r}")
    if value < 1:
        raise ValueError(f"{field} must be at least 1 second, got {value!r}")
    return int(value)


def _headers(value: Any) -> List[Dict[str, str]]:
    """Validate custom headers given as a list of {name, value} objects."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("custom_headers must be a list of {name, value} objects")
    out = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError(f"custom_headers entries need a string 'name', got {item!r}")
        name = item["name"].strip()
        if not name or not all(c.isalnum() or c == "-" for c in name):
            raise ValueError(f"Header name may contain only letters, digits and '-': {name!r}")
        header_value = item.get("value", "")
        if not isinstance(header_value, str):
            raise ValueError(f"Header {name!r} needs a string value, got {header_value!r}")
        out.append({"name": name, "value": header_value})
    return out


def _with_websocket(headers: List[Dict[str, str]], enabled: bool) -> List[Dict[str, str]]:
    """Add or remove DSM's WebSocket header pair, leaving other headers alone."""
    kept = [h for h in headers if h.get("name", "").lower() not in _WEBSOCKET_NAMES]
    return kept + [dict(h) for h in WEBSOCKET_HEADERS] if enabled else kept


def normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Render a raw DSM entry in the shape this module's tools accept.

    Protocols become names instead of DSM's integer codes, the WebSocket header
    pair becomes a flag, and DSM's internal fields are left out.
    """
    frontend = entry.get("frontend") or {}
    backend = entry.get("backend") or {}
    https = frontend.get("https") or {}
    headers = [
        {"name": h.get("name", ""), "value": h.get("value", "")}
        for h in entry.get("customize_headers") or []
        if isinstance(h, dict)
    ]
    present = {h["name"].lower(): h["value"] for h in headers}
    websocket = all(present.get(h["name"].lower()) == h["value"] for h in WEBSOCKET_HEADERS)
    normalized = {
        "uuid": entry.get("UUID") or entry.get("uuid"),
        "description": entry.get("description", ""),
        "source": {
            "protocol": _PROTOCOL_NAMES.get(frontend.get("protocol"), frontend.get("protocol")),
            "hostname": frontend.get("fqdn", ""),
            "port": frontend.get("port"),
        },
        "destination": {
            "protocol": _PROTOCOL_NAMES.get(backend.get("protocol"), backend.get("protocol")),
            "hostname": backend.get("fqdn", ""),
            "port": backend.get("port"),
        },
        "hsts": bool(https.get("hsts", False)),
        "access_control_profile": frontend.get("acl") or None,
        "websocket": websocket,
        "custom_headers": [h for h in headers if not websocket or h["name"].lower() not in _WEBSOCKET_NAMES],
        "proxy_connect_timeout": entry.get("proxy_connect_timeout"),
        "proxy_read_timeout": entry.get("proxy_read_timeout"),
        "proxy_send_timeout": entry.get("proxy_send_timeout"),
        "proxy_http_version": entry.get("proxy_http_version"),
        "proxy_intercept_errors": bool(entry.get("proxy_intercept_errors", False)),
    }
    # "http2" is defined by Synology next to "hsts" but has not been seen in a
    # captured payload, so it is reported only when DSM actually returns it.
    if "http2" in https:
        normalized["http2"] = bool(https["http2"])
    return normalized


class SynologyReverseProxy:
    """Manage Login Portal reverse proxy entries on a Synology NAS."""

    _lock_directory = Path(tempfile.gettempdir()) / "synology-mcp-reverse-proxy-locks"
    _lock_timeout_seconds = 30

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
        self.syno_token = syno_token
        self._api = SynologyAPIClient(base_url, session_id, verify_ssl, syno_token=syno_token)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write_lock(self) -> FileLock:
        """One host-wide lock per NAS.

        Every write here is read-modify-write or check-then-write (a duplicate
        check before create, the stored entry before update), so two writers on
        the same NAS -- two MCP processes included -- must not interleave.
        """
        identity = self.base_url.encode("utf-8")
        lock_name = f"{hashlib.sha256(identity).hexdigest()}.lock"
        self._lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return FileLock(str(self._lock_directory / lock_name), timeout=self._lock_timeout_seconds)

    def _raw_entries(self) -> Dict[str, Any]:
        """List as DSM returns it. On success `data.entries` is a list of dicts."""
        result = _describe(self._api.get(REVERSE_PROXY_API, "list"))
        if not result.get("success"):
            return result
        data = result.get("data")
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            return {
                "success": False,
                "error": {
                    "code": "invalid_response",
                    "message": "DSM returned malformed reverse proxy data",
                    "api": REVERSE_PROXY_API,
                    "method": "list",
                },
            }
        return result

    def _find_raw(self, uuid: str) -> Dict[str, Any]:
        """{"success": True, "entry": <raw>} or a failure dict."""
        listing = self._raw_entries()
        if not listing.get("success"):
            return listing
        for entry in listing["data"]["entries"]:
            if entry.get("UUID") == uuid:
                return {"success": True, "entry": entry}
        return _not_found("get", uuid)

    def _locked(self, method: str, action):
        try:
            with self._write_lock():
                return action()
        except Timeout:
            return {
                "success": False,
                "error": {
                    "code": "reverse_proxy_update_busy",
                    "message": "Timed out waiting for another reverse proxy change on this NAS",
                    "api": REVERSE_PROXY_API,
                    "method": method,
                },
            }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_entries(self) -> Dict[str, Any]:
        """List all reverse proxy entries."""
        listing = self._raw_entries()
        if not listing.get("success"):
            return listing
        entries = [normalize_entry(e) for e in listing["data"]["entries"]]
        return {"success": True, "data": {"entries": entries, "total": len(entries)}}

    def get_entry(self, uuid_or_description: str) -> Dict[str, Any]:
        """Get one entry by UUID, or by its description (DSM has no name field).

        DSM offers no per-entry get, so this filters the list. A description
        matching several entries is refused rather than guessed at.
        """
        if not isinstance(uuid_or_description, str) or not uuid_or_description.strip():
            return _invalid("get", "uuid_or_description must be a non-empty string")
        key = uuid_or_description.strip()
        listing = self._raw_entries()
        if not listing.get("success"):
            return listing
        entries = listing["data"]["entries"]
        by_uuid = [e for e in entries if e.get("UUID") == key]
        matches = by_uuid or [e for e in entries if e.get("description") == key]
        if len(matches) > 1:
            return _invalid(
                "get",
                f"{len(matches)} entries are described {key!r}; address one by its UUID",
            )
        if not matches:
            return {
                "success": False,
                "error": {
                    "code": "not_found",
                    "message": f"No reverse proxy entry with UUID or description {key!r}",
                    "api": REVERSE_PROXY_API,
                    "method": "get",
                },
            }
        return {"success": True, "data": normalize_entry(matches[0])}

    def list_access_control_profiles(self) -> Dict[str, Any]:
        """List the access control profiles an entry can reference by UUID."""
        result = self._api.get(ACCESS_CONTROL_API, "list")
        if not result.get("success"):
            return result
        data = result.get("data")
        entries = data.get("entries") if isinstance(data, dict) else None
        profiles = [
            {"uuid": e.get("UUID") or e.get("uuid"), "name": e.get("name", ""), "rules": e.get("rules", [])}
            for e in entries or []
            if isinstance(e, dict)
        ]
        return {"success": True, "data": {"profiles": profiles, "total": len(profiles)}}

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_entry(
        self,
        description: str,
        source_hostname: str,
        destination_hostname: str,
        destination_port: Any,
        source_protocol: str = "HTTPS",
        source_port: Any = 443,
        destination_protocol: str = "HTTP",
        hsts: bool = False,
        websocket: bool = False,
        custom_headers: Optional[List[Dict[str, str]]] = None,
        access_control_profile: Optional[str] = None,
        proxy_connect_timeout: Any = DEFAULT_TIMEOUT_SECONDS,
        proxy_read_timeout: Any = DEFAULT_TIMEOUT_SECONDS,
        proxy_send_timeout: Any = DEFAULT_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Create an entry and return it as DSM stored it.

        DSM answers a create with a bare success and no UUID, so the new entry
        is found again by its description. That is why descriptions are kept
        unique here: a second entry with the same one could not be told apart
        from the first.
        """
        try:
            if not isinstance(description, str) or not description.strip():
                raise ValueError("description must be a non-empty string")
            description = description.strip()
            source_code = _protocol_code(source_protocol, "source_protocol")
            entry = {
                "description": description,
                "proxy_connect_timeout": _timeout(proxy_connect_timeout, "proxy_connect_timeout"),
                "proxy_read_timeout": _timeout(proxy_read_timeout, "proxy_read_timeout"),
                "proxy_send_timeout": _timeout(proxy_send_timeout, "proxy_send_timeout"),
                "proxy_http_version": DEFAULT_PROXY_HTTP_VERSION,
                "proxy_intercept_errors": False,
                "frontend": {
                    "acl": access_control_profile or None,
                    "fqdn": _hostname(source_hostname, "source_hostname"),
                    "port": _port(source_port, "source_port"),
                    "protocol": source_code,
                    "https": {"hsts": bool(hsts)},
                },
                "backend": {
                    "fqdn": _hostname(destination_hostname, "destination_hostname"),
                    "port": _port(destination_port, "destination_port"),
                    "protocol": _protocol_code(destination_protocol, "destination_protocol"),
                },
                "customize_headers": _with_websocket(_headers(custom_headers or []), bool(websocket)),
            }
            if hsts and source_code != _PROTOCOL_CODES["HTTPS"]:
                raise ValueError("hsts requires an HTTPS source")
        except ValueError as exc:
            return _invalid("create", str(exc))

        def action():
            listing = self._raw_entries()
            if not listing.get("success"):
                return listing
            if any(e.get("description") == description for e in listing["data"]["entries"]):
                return _invalid(
                    "create",
                    f"An entry described {description!r} already exists; descriptions must be unique",
                )
            created = _write_outcome(
                self._api.post(
                    REVERSE_PROXY_API, "create", 1, {"entry": json.dumps(entry)},
                    timeout=WRITE_TIMEOUT_SECONDS,
                )
            )
            if not created.get("success"):
                return created
            return self.get_entry(description)

        return self._locked("create", action)

    def update_entry(self, uuid: str, **changes: Any) -> Dict[str, Any]:
        """Change some fields of an entry, keeping every field not named.

        The stored entry is read first and the changes are layered over it, so
        DSM's internal fields (`_key`) and anything this module does not model
        survive the round trip. Accepted keys match create_entry's arguments.
        """
        if not isinstance(uuid, str) or not uuid.strip():
            return _invalid("update", "uuid must be a non-empty string")
        uuid = uuid.strip()
        allowed = {
            "description", "source_protocol", "source_hostname", "source_port",
            "destination_protocol", "destination_hostname", "destination_port",
            "hsts", "websocket", "custom_headers", "access_control_profile",
            "proxy_connect_timeout", "proxy_read_timeout", "proxy_send_timeout",
        }
        changes = {k: v for k, v in changes.items() if v is not None}
        unknown = set(changes) - allowed
        if unknown:
            return _invalid("update", f"Unknown field(s): {', '.join(sorted(unknown))}")
        if not changes:
            return _invalid("update", "Nothing to change; name at least one field")

        def action():
            found = self._find_raw(uuid)
            if not found.get("success"):
                found["error"]["method"] = "update"
                return found
            entry = json.loads(json.dumps(found["entry"]))  # deep copy
            frontend = entry.setdefault("frontend", {})
            backend = entry.setdefault("backend", {})
            https = frontend.setdefault("https", {})
            try:
                if "description" in changes:
                    if not isinstance(changes["description"], str) or not changes["description"].strip():
                        raise ValueError("description must be a non-empty string")
                    entry["description"] = changes["description"].strip()
                if "source_protocol" in changes:
                    frontend["protocol"] = _protocol_code(changes["source_protocol"], "source_protocol")
                if "source_hostname" in changes:
                    frontend["fqdn"] = _hostname(changes["source_hostname"], "source_hostname")
                if "source_port" in changes:
                    frontend["port"] = _port(changes["source_port"], "source_port")
                if "destination_protocol" in changes:
                    backend["protocol"] = _protocol_code(
                        changes["destination_protocol"], "destination_protocol"
                    )
                if "destination_hostname" in changes:
                    backend["fqdn"] = _hostname(changes["destination_hostname"], "destination_hostname")
                if "destination_port" in changes:
                    backend["port"] = _port(changes["destination_port"], "destination_port")
                if "hsts" in changes:
                    https["hsts"] = bool(changes["hsts"])
                if "access_control_profile" in changes:
                    # An empty string clears the profile; DSM stores that as null.
                    frontend["acl"] = changes["access_control_profile"] or None
                for field in ("proxy_connect_timeout", "proxy_read_timeout", "proxy_send_timeout"):
                    if field in changes:
                        entry[field] = _timeout(changes[field], field)
                headers = entry.get("customize_headers") or []
                if "custom_headers" in changes:
                    # Replaces the custom headers but keeps the WebSocket pair
                    # unless `websocket` says otherwise.
                    had_websocket = normalize_entry(entry)["websocket"]
                    headers = _with_websocket(_headers(changes["custom_headers"]), had_websocket)
                if "websocket" in changes:
                    headers = _with_websocket(headers, bool(changes["websocket"]))
                entry["customize_headers"] = headers
                if https.get("hsts") and frontend.get("protocol") != _PROTOCOL_CODES["HTTPS"]:
                    raise ValueError("hsts requires an HTTPS source")
            except ValueError as exc:
                return _invalid("update", str(exc))

            updated = _write_outcome(
                self._api.post(
                    REVERSE_PROXY_API, "update", 1, {"entry": json.dumps(entry)},
                    timeout=WRITE_TIMEOUT_SECONDS,
                )
            )
            if not updated.get("success"):
                return updated
            found = self._find_raw(uuid)
            if not found.get("success"):
                return found
            return {"success": True, "data": normalize_entry(found["entry"])}

        return self._locked("update", action)

    def delete_entry(self, uuid: str) -> Dict[str, Any]:
        """Delete one entry. DSM takes a JSON array of UUIDs even for one.

        The UUID is looked up first and an unknown one is reported without
        calling DSM, so a typo cannot come back looking like a completed
        removal.
        """
        if not isinstance(uuid, str) or not uuid.strip():
            return _invalid("delete", "uuid must be a non-empty string")
        uuid = uuid.strip()

        def action():
            found = self._find_raw(uuid)
            if not found.get("success"):
                found["error"]["method"] = "delete"
                return found
            deleted = _write_outcome(
                self._api.post(
                    REVERSE_PROXY_API, "delete", 1, {"uuids": json.dumps([uuid])},
                    timeout=WRITE_TIMEOUT_SECONDS,
                )
            )
            if not deleted.get("success"):
                return deleted
            return {
                "success": True,
                "data": {"deleted": normalize_entry(found["entry"])},
            }

        return self._locked("delete", action)
