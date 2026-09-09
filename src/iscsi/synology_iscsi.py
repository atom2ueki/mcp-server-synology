# src/iscsi/synology_iscsi.py - Synology SAN Manager (iSCSI LUN + target) management
#
# Wraps SYNO.Core.ISCSI.LUN and SYNO.Core.ISCSI.Target, both v1 on entry.cgi.
#
# Every call shape here was established against a live RS1221+ running DSM
# 7.3.2-86009 Update 4 on 2026-09-08, not carried over from another DSM
# generation: the OpenStack Cinder driver and jparklab/synology-csi were the
# starting point, and both differ from what this DSM actually accepts.

import json
from typing import Any, Dict, List, Optional

from utils.synology_api import LUN_WRITE_TIMEOUT, SynologyAPIClient

LUN_API = "SYNO.Core.ISCSI.LUN"
TARGET_API = "SYNO.Core.ISCSI.Target"

# LUN types accepted by DSM 7.3.2 on a btrfs volume, verified by creating one of
# each and reading the type back:
#
#   BLUN -> 263 (type_str "BLUN")   the SAN Manager default on btrfs, thin
#   THIN -> 7                       legacy thin
#   ADV  -> 15                      legacy "advanced" (thin + snapshot support)
#   FILE -> 3                       regular-file LUN
#
# BLUN_THICK, BLUN_SINK and ADV_THICK are recognised names but were refused
# with 18990503 on the btrfs volume this was tested against; THICK, VMWARE and
# RAW were refused with 18990500 as unrecognised. That is one volume on one
# DSM build, not a platform rule, so nothing is validated client-side beyond
# the friendly aliases below: another DSM or an ext4 volume may accept a
# different set, and a hard-coded allowlist would then be wrong in a way the
# caller could not override. DSM decides; its refusal is reported as given.
LUN_TYPE_ALIASES = {
    "thin": "BLUN",
    "btrfs": "BLUN",
    "advanced": "ADV",
    "adv": "ADV",
    "file": "FILE",
    "legacy_thin": "THIN",
}
DEFAULT_LUN_TYPE = "BLUN"

# DSM type names seen to be recognised by SYNO.Core.ISCSI.LUN/create, whether or
# not this particular volume accepts them. Held separately from the aliases
# because the two collide: "THIN" is a real DSM type (7) and "thin" is the
# friendly name for BLUN (263). Lowercasing before the alias lookup silently
# turned an explicit request for type 7 into type 263.
DSM_LUN_TYPES = frozenset(
    {
        "BLUN",
        "BLUN_THICK",
        "BLUN_SINK",
        "BLUN_THICK_SINK",
        "BLOCK",
        "FILE",
        "THIN",
        "ADV",
        "ADV_THICK",
        "SINK",
        "CINDER",
        "CINDER_BLUN",
        "CINDER_BLUN_THICK",
    }
)


def _resolve_lun_type(lun_type: str) -> str:
    """Map a caller's LUN type onto a DSM type name.

    An exact DSM name is passed through untouched, and that check comes FIRST:
    otherwise `THIN` (DSM type 7) would be lowercased into the `thin` alias and
    silently become `BLUN` (type 263). Anything else is looked up
    case-insensitively in the friendly aliases, and an unknown value is
    forwarded as given so a DSM or volume with a type this list has never seen
    is still reachable.
    """
    given = lun_type.strip()
    if given in DSM_LUN_TYPES:
        return given
    return LUN_TYPE_ALIASES.get(given.lower(), given)

# auth_type as SAN Manager stores it, confirmed by creating a target with each
# and reading `auth_type` back from Target/get.
AUTH_NONE = 0
AUTH_CHAP = 1

DEFAULT_IQN_PREFIX = "iqn.2000-01.com.synology"


def _invalid(method: str, message: str, api: str = LUN_API) -> Dict[str, Any]:
    """Refuse a call on its arguments, before anything is sent."""
    return {
        "success": False,
        "error": {"code": "invalid_argument", "message": message, "api": api, "method": method},
    }


def _coerce_int(value: Any, field: str) -> int:
    """Return `value` as an int, refusing anything that would silently change it.

    `int()` is too permissive for caller input here. `int(True)` is 1, so a JSON
    `true` would become a one-byte LUN; `int(0.9)` is 0, so a fractional size
    would be truncated without a word. Both hand back a LUN that is not the one
    that was asked for, which is worse than an error.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number, not a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field} must be a whole number, got {value}")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text.lstrip("-").isdigit():
            raise ValueError(f"{field} must be a whole number, got {value!r}")
        return int(text)
    raise ValueError(f"{field} must be a number, got {type(value).__name__}")


def _uuid_list(value: Any, field: str) -> List[str]:
    """Validate a list of UUID strings.

    Truthiness is not enough. A dict iterates over its KEYS, so
    `{"<real-uuid>": false}` would map that LUN while discarding the value that
    was meant to prevent it; a bare string iterates character by character.
    Both reach a write having been read as something the caller did not mean.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of UUID strings, got {type(value).__name__}")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain non-empty UUID strings, got {item!r}")
        out.append(item.strip())
    return out


def _empty_selection(method: str, field: str) -> Dict[str, Any]:
    """Refuse an operation whose target list is empty.

    `all([])` is True, so an empty list would otherwise make no request at all
    and report success - a check that passes precisely because it examined
    nothing.
    """
    return {
        "success": False,
        "error": {
            "code": "empty_selection",
            "message": f"{field} was empty, so nothing was changed. Name at least one.",
            "api": LUN_API,
            "method": method,
        },
    }


def _json_str(value: Any) -> str:
    """Render a value as a JSON string literal, i.e. with its quote characters.

    SYNO.Core.ISCSI.Target parses `target_id` as JSON, so it wants the three
    characters `"1"` and not the one character `1`. Sending the bare form fails
    with 18990710 on get, set and delete - the same code DSM returns for a
    target that does not exist, which is what makes it so hard to diagnose from
    the outside. SYNO.Core.ISCSI.LUN is tolerant of a bare `uuid`, so this is
    applied only where it was shown to be required.
    """
    return json.dumps(str(value))


class SynologyISCSI:
    """Manage iSCSI LUNs and targets (DSM SAN Manager) on a Synology NAS."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
        api_client: Optional[SynologyAPIClient] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
        self.syno_token = syno_token
        # `api_client` lets a caller that already holds a client for this NAS
        # share it rather than opening a second one. SynologyHealth uses it so
        # that its lun_* methods and this module are one implementation with one
        # session, instead of two that can drift apart.
        self._api = api_client or SynologyAPIClient(
            base_url, session_id, verify_ssl, syno_token=syno_token
        )

    def _get(self, api: str, method: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        return self._api.get(api, method, 1, params)

    def _post(
        self, api: str, method: str, params: Optional[Dict] = None, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        if timeout is None:
            return self._api.post(api, method, 1, params)
        return self._api.post(api, method, 1, params, timeout=timeout)

    # ------------------------------------------------------------------
    # LUNs
    # ------------------------------------------------------------------

    def lun_list(self, additional: Optional[List[str]] = None) -> Dict[str, Any]:
        """List iSCSI LUNs.

        `additional` names optional fields to include. DSM ignores a field it
        does not recognise rather than erroring, so asking for the wrong name
        silently returns less data - `status` and `is_mapped` are the two that
        were confirmed to work here. Note that `mapped_targets` is NOT one of
        them, despite reading like the obvious name; to see what a LUN is
        attached to, read `mapped_luns` from `target_list` instead.
        """
        # `is None`, not `or`: an explicit empty list means "no extra fields",
        # and silently replacing it with the default honours neither the request
        # nor an error.
        wanted = ["status", "is_mapped"] if additional is None else additional
        params = {"additional": json.dumps(wanted)}
        return self._get(LUN_API, "list", params)

    def lun_get(self, name_or_uuid: str) -> Dict[str, Any]:
        """Get a single LUN by name or UUID.

        Resolved client-side from the list so that a name works as well as a
        UUID; SYNO.Core.ISCSI.LUN/get takes a UUID only.
        """
        result = self.lun_list()
        if not result.get("success"):
            return result
        luns = result.get("data", {}).get("luns", []) or []
        for lun in luns:
            if name_or_uuid in (lun.get("name"), lun.get("uuid")):
                return {"success": True, "data": lun}
        return {
            "success": False,
            "error": {
                "code": "lun_not_found",
                "message": f"No iSCSI LUN found matching '{name_or_uuid}'",
                "api": LUN_API,
                "method": "get",
            },
        }

    def lun_create(
        self,
        name: str,
        location: str,
        size: int,
        lun_type: str = DEFAULT_LUN_TYPE,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a LUN. `size` is in bytes; `location` is a volume path (/volume2).

        Returns {"success": true, "data": {"lun_id": N, "uuid": "..."}}.
        """
        try:
            byte_size = _coerce_int(size, "size")
        except ValueError as exc:
            return _invalid("create", str(exc))
        resolved_type = _resolve_lun_type(lun_type)
        params = {
            "name": name,
            "type": resolved_type,
            "location": location,
            "size": str(byte_size),
        }
        if description is not None:
            params["description"] = description
        # Creating a LUN allocates storage. The default 15s timeout is not
        # enough on a busy volume, and a timeout here does NOT cancel the
        # create -- it just loses the uuid, leaving a LUN nobody can name.
        return self._post(LUN_API, "create", params, timeout=LUN_WRITE_TIMEOUT)

    def lun_delete(self, uuid: str) -> Dict[str, Any]:
        """Delete a LUN by UUID. Destroys the LUN and everything stored on it."""
        return self._post(LUN_API, "delete", {"uuid": uuid}, timeout=LUN_WRITE_TIMEOUT)

    def _map(self, method: str, uuid: str, target_ids: List[Any]) -> Dict[str, Any]:
        """Shared body of map_target / unmap_target."""
        if not target_ids:
            return _empty_selection(method, "target_ids")
        # A dict iterates over its keys and a string over its characters, so an
        # argument of the wrong shape reaches the write read as something the
        # caller never meant. Refuse the shape rather than coerce it.
        if isinstance(target_ids, (str, bytes)) or not isinstance(target_ids, (list, tuple)):
            return _invalid(
                method,
                f"target_ids must be a list, got {type(target_ids).__name__}",
            )
        try:
            ids = [str(_coerce_int(t, "target_ids")) for t in target_ids]
        except ValueError as exc:
            return _invalid(method, str(exc))
        return self._post(LUN_API, method, {"uuid": uuid, "target_ids": json.dumps(ids)})

    def lun_map_targets(self, uuid: str, target_ids: List[Any]) -> Dict[str, Any]:
        """Map a LUN to one or more targets."""
        return self._map("map_target", uuid, target_ids)

    def lun_unmap_targets(self, uuid: str, target_ids: List[Any]) -> Dict[str, Any]:
        """Unmap a LUN from one or more targets."""
        return self._map("unmap_target", uuid, target_ids)

    # ------------------------------------------------------------------
    # Targets
    # ------------------------------------------------------------------

    def target_list(self, additional: Optional[List[str]] = None) -> Dict[str, Any]:
        """List iSCSI targets, including the LUNs mapped to each.

        `mapped_lun` (singular) is the field name that populates `mapped_luns`
        in the response - `mapped_luns`, `luns` and `mapping` are all ignored.
        """
        wanted = ["mapped_lun"] if additional is None else additional
        params = {"additional": json.dumps(wanted)}
        return self._get(TARGET_API, "list", params)

    def target_get(self, target_id: Any) -> Dict[str, Any]:
        """Get a single target by numeric target_id."""
        return self._get(TARGET_API, "get", {"target_id": _json_str(target_id)})

    def target_create(
        self,
        name: str,
        iqn: Optional[str] = None,
        chap_user: Optional[str] = None,
        chap_password: Optional[str] = None,
        max_sessions: int = 0,
    ) -> Dict[str, Any]:
        """Create an iSCSI target.

        CHAP is off unless BOTH `chap_user` and `chap_password` are given; a
        target created with auth_type 0 accepts any initiator that can reach it.
        Half a credential is refused rather than quietly downgraded to no
        authentication, which would leave the target open while the caller
        believed it was protected.

        `max_sessions` 0 means DSM's default (no explicit cap).
        """
        # Omitted and supplied-but-empty are DIFFERENT. Both are falsey, so a
        # `bool()` test treats chap_user="" as "no CHAP wanted" and creates an
        # OPEN target -- while the caller, who passed a credential field, has
        # every reason to believe it asked for authentication. Only `is None`
        # means omitted; an empty string is a malformed credential and is
        # refused.
        supplied = [
            field
            for field, value in (("chap_user", chap_user), ("chap_password", chap_password))
            if value is not None
        ]
        if supplied:
            blank = [
                field
                for field, value in (("chap_user", chap_user), ("chap_password", chap_password))
                if value is not None and not str(value).strip()
            ]
            if blank:
                return {
                    "success": False,
                    "error": {
                        "code": "chap_incomplete",
                        "message": (
                            f"{', '.join(blank)} was supplied but empty, so no target was "
                            "created. An empty credential would have produced a target with "
                            "no authentication. Give a real value, or omit both fields."
                        ),
                        "api": TARGET_API,
                        "method": "create",
                    },
                }
            if len(supplied) != 2:
                return {
                    "success": False,
                    "error": {
                        "code": "chap_incomplete",
                        "message": (
                            "CHAP needs both chap_user and chap_password. Supply both to "
                            "enable CHAP, or neither for a target with no authentication."
                        ),
                        "api": TARGET_API,
                        "method": "create",
                    },
                }

        if iqn is not None and not iqn.strip():
            return _invalid(
                "create",
                "iqn was supplied but empty. Omit it to get the default "
                f"{DEFAULT_IQN_PREFIX}:<name>, or give a real IQN.",
                TARGET_API,
            )
        try:
            sessions = _coerce_int(max_sessions, "max_sessions")
        except ValueError as exc:
            return _invalid("create", str(exc), TARGET_API)

        use_chap = len(supplied) == 2
        params = {
            "name": name,
            "iqn": iqn if iqn is not None else f"{DEFAULT_IQN_PREFIX}:{name}",
            "auth_type": str(AUTH_CHAP if use_chap else AUTH_NONE),
            "user": chap_user or "",
            "password": chap_password or "",
            "max_sessions": str(sessions),
        }
        return self._post(TARGET_API, "create", params)

    def target_delete(self, target_id: Any) -> Dict[str, Any]:
        """Delete a target by numeric target_id. Any mapped LUNs survive."""
        return self._post(TARGET_API, "delete", {"target_id": _json_str(target_id)})
