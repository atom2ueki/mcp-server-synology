# src/update/synology_security.py - security posture of a DSM NAS, and the
# few hardening settings it can change
#
# Each read below was run against an RS1221+ on DSM 7.3.2-86009 Update 5 on
# 2026-10-29. The Security Advisor calls are the ones its UI makes
# (SYNO.Core.SecurityScan.Status system_get / rule_get); none of these APIs has
# a published guide.
#
# The writes send exactly what DSM 7.4.1-90080's own UI sends, read from the
# JavaScript the NAS serves:
#
#   Terminal   SYNO.Core.Terminal get/set v3 (Control Panel > Terminal & SNMP,
#              admin_center.js TerminalTab: a FormPanel posting enable_telnet,
#              enable_ssh, ssh_port at the API's maxVersion)
#   AutoBlock  SYNO.Core.Security.AutoBlock get/set v1 (Security > Protection:
#              enable, attempts, within_mins, expire_day; expire_day 0 = never)
#   Allow list SYNO.Core.Security.AutoBlock.Rules list/create/delete v1
#              (AddWhiteListDialog: {type: "allow", block_days: 0,
#              overwrite: true, ip: [...]}; a subnet is "<ip>/<mask or prefix>",
#              a range "<begin>~<end>"; delete is {type, ip: [...]})
#   Redirect   SYNO.Core.Web.DSM get/set v2 (Login Portal > DSM,
#              LoginStyleVueBundle.js: the whole get result sent back with the
#              checkbox changed and http_port/https_port as integers)
#
# Every write reads the setting first, sends the UI's full parameter set with
# only the requested fields changed, and reads it back.

import json
from typing import Any, Callable, Dict, List, Optional

from utils.synology_api import SynologyAPIClient

# Security Advisor rule statuses that are not findings.
_NOT_A_FINDING = frozenset({"pass", "skip"})


def _data(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def _failed(step: str, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": False, "step": step, "error": result.get("error")}


# The codes SynologyAPIClient gives a request that got no DSM result:
# network_error (a timeout, a dropped connection, or an HTTP 4xx/5xx) and
# unknown_error (a reply that could not be read). For a write, neither says
# whether DSM applied it.
_UNANSWERED = frozenset({"network_error", "unknown_error"})


def _write_failed(step: str, result: Dict[str, Any], reread: str) -> Dict[str, Any]:
    """A failed write, marked outcome "unknown" when DSM returned no result for it."""
    out = _failed(step, result)
    error = result.get("error")
    if isinstance(error, dict) and error.get("code") in _UNANSWERED:
        out["outcome"] = "unknown"
        out["note"] = f"DSM returned no result for this write (no reply, an HTTP error or an unreadable reply), so it may have been applied. {reread}"
    return out


def _refused(code: str, message: str) -> Dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}


_MISMATCH = {"code": "read_back_mismatch", "message": "DSM accepted the change but the read-back differs"}


# SYNO.Core.Web.DSM get fields that describe the NAS rather than configure it.
# The Login Portal sends them back unchanged; so do we.
_WEB_DSM_PORT_FIELDS = ("http_port", "https_port")

# Auto-block allow-list page size, the same one Admin Center's grid uses.
_RULES_PAGE = 250


class SynologySecurity:
    """Security posture, plus telnet/SSH, auto-block, its allow list and the HTTPS redirect."""

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
    ):
        self._api = SynologyAPIClient(
            base_url, session_id, verify_ssl, syno_token=syno_token
        )

    def _get(self, api: str, method: str, version: int = 1, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        extra = {k: json.dumps(v) for k, v in (params or {}).items()}
        return self._api.get(api, method, version, extra)

    def _post(self, api: str, method: str, version: int, params: Dict[str, Any]) -> Dict[str, Any]:
        extra = {k: json.dumps(v) for k, v in params.items()}
        return self._api.post(api, method, version, extra)

    # ------------------------------------------------------------------
    # Readers shared by posture() and the writes' before/after
    # ------------------------------------------------------------------

    def _terminal(self) -> Dict[str, Any]:
        return self._get("SYNO.Core.Terminal", "get", 3)

    def _web_dsm(self) -> Dict[str, Any]:
        return self._get("SYNO.Core.Web.DSM", "get", 2)

    def _auto_block(self) -> Dict[str, Any]:
        return self._get("SYNO.Core.Security.AutoBlock", "get")

    def _allow_list(self) -> Dict[str, Any]:
        """Every allow-list entry, paging as Admin Center's grid does."""
        entries: List[str] = []
        offset = 0
        while True:
            result = self._get(
                "SYNO.Core.Security.AutoBlock.Rules",
                "list",
                1,
                {"offset": offset, "limit": _RULES_PAGE, "type": "allow"},
            )
            if not result.get("success"):
                return result
            data = _data(result)
            page = [row.get("ip") for row in data.get("ip_info") or []]
            entries.extend(page)
            offset += len(page)
            if not page or offset >= (data.get("total") or 0):
                return {"success": True, "data": {"entries": entries}}

    @staticmethod
    def _shape_terminal(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "telnet_enabled": d.get("enable_telnet"),
            "ssh_enabled": d.get("enable_ssh"),
            "ssh_port": d.get("ssh_port"),
        }

    @staticmethod
    def _shape_web_dsm(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "https_redirect": d.get("enable_https_redirect"),
            "hsts": d.get("enable_hsts"),
            "http_port": d.get("http_port"),
            "https_port": d.get("https_port"),
            "custom_domain_enabled": d.get("enable_custom_domain"),
            "fqdn": d.get("fqdn"),
        }

    @staticmethod
    def _shape_auto_block(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "enabled": d.get("enable"),
            "attempts": d.get("attempts"),
            "within_minutes": d.get("within_mins"),
            "expire_days": d.get("expire_day"),
        }

    def posture(self) -> Dict[str, Any]:
        """One read of every setting below; a failed section is reported, not hidden."""
        errors: List[Dict[str, Any]] = []

        def section(name: str, result: Dict[str, Any], shape: Callable[[Dict[str, Any]], Any]) -> Any:
            if not result.get("success"):
                errors.append({"section": name, "error": result.get("error")})
                return None
            return shape(_data(result))

        quickconnect = section(
            "quickconnect",
            self._get("SYNO.Core.QuickConnect", "get"),
            lambda d: {"enabled": d.get("enabled"), "server_alias": d.get("server_alias")},
        )
        auto_block = section("auto_block", self._auto_block(), self._shape_auto_block)
        allow_list = section("auto_block_allow_list", self._allow_list(), lambda d: d.get("entries"))
        if isinstance(auto_block, dict):
            auto_block["allow_list"] = allow_list
        terminal = section("terminal", self._terminal(), self._shape_terminal)
        web = section("dsm_web", self._web_dsm(), self._shape_web_dsm)
        firewall = section(
            "firewall",
            self._get("SYNO.Core.Security.Firewall", "get"),
            lambda d: {"enabled": d.get("enable_firewall"), "profile": d.get("profile_name")},
        )
        two_factor = section(
            "two_factor",
            self._get("SYNO.Core.OTP.EnforcePolicy", "get"),
            lambda d: {"enforced_for": d.get("otp_enforce_option")},
        )

        def accounts(d: Dict[str, Any]) -> Dict[str, Any]:
            # `expired` is "now" for a disabled account and "normal" for an
            # enabled one; anything else is passed through as DSM gave it.
            by_name = {u.get("name"): u.get("expired") for u in d.get("users") or []}
            return {
                "admin_disabled": by_name.get("admin") == "now" if "admin" in by_name else None,
                "guest_disabled": by_name.get("guest") == "now" if "guest" in by_name else None,
                "enabled_users": sorted(n for n, e in by_name.items() if e != "now"),
            }

        users = section(
            "accounts",
            self._get("SYNO.Core.User", "list", 1, {"additional": ["expired"], "offset": 0, "limit": -1}),
            accounts,
        )
        administrators = section(
            "administrators",
            self._get("SYNO.Core.Group.Member", "list", 1, {"group": "administrators"}),
            lambda d: sorted(u.get("name") for u in d.get("users") or []),
        )

        def advisor_summary(d: Dict[str, Any]) -> Dict[str, Any]:
            items = d.get("items") or {}
            return {
                "overall": d.get("sysStatus"),
                "last_scan_time": d.get("lastScanTime"),
                "categories": {
                    name: {"severity": item.get("failSeverity"), "fail": item.get("fail")}
                    for name, item in items.items()
                },
            }

        advisor = section(
            "security_advisor",
            self._get("SYNO.Core.SecurityScan.Status", "system_get"),
            advisor_summary,
        )

        def findings(d: Dict[str, Any]) -> List[Dict[str, Any]]:
            return sorted(
                (
                    {
                        "id": rule.get("id"),
                        "category": rule.get("category"),
                        "severity": rule.get("severity"),
                        "status": rule.get("status"),
                        "str_id": rule.get("strId"),
                    }
                    for rule in (d.get("items") or {}).values()
                    if rule.get("status") not in _NOT_A_FINDING
                ),
                key=lambda r: (r["category"] or "", r["id"] or ""),
            )

        advisor_findings = section(
            "security_advisor_findings",
            self._get("SYNO.Core.SecurityScan.Status", "rule_get", 1, {"items": "ALL"}),
            findings,
        )
        schedule = section(
            "security_advisor_schedule",
            self._get("SYNO.Core.SecurityScan.Conf", "get"),
            lambda d: {
                "enabled": d.get("enableSchedule"),
                "weekday": d.get("weekday"),
                "hour": d.get("hour"),
                "minute": d.get("minute"),
            },
        )
        if isinstance(advisor, dict):
            advisor["findings"] = advisor_findings
            advisor["schedule"] = schedule

        return {
            "success": not errors,
            "quickconnect": quickconnect,
            "auto_block": auto_block,
            "terminal": terminal,
            "dsm_web": web,
            "firewall": firewall,
            "two_factor": two_factor,
            "accounts": users,
            "administrators": administrators,
            "security_advisor": advisor,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Writes: each reads first, changes only what was asked, reads back
    # ------------------------------------------------------------------

    def set_terminal(
        self, enable_telnet: Optional[bool] = None, enable_ssh: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Turn Telnet and/or SSH on or off; the SSH port is sent back as read."""
        before = self._terminal()
        if not before.get("success"):
            return _failed("SYNO.Core.Terminal/get", before)
        current = _data(before)
        params = {
            "enable_telnet": current.get("enable_telnet") if enable_telnet is None else enable_telnet,
            "enable_ssh": current.get("enable_ssh") if enable_ssh is None else enable_ssh,
            "ssh_port": current.get("ssh_port"),
        }
        written = self._post("SYNO.Core.Terminal", "set", 3, params)
        if not written.get("success"):
            return {**_write_failed("SYNO.Core.Terminal/set", written, "Re-read it with synology_security_posture before retrying."), "before": self._shape_terminal(current)}
        after = self._terminal()
        if not after.get("success"):
            return {**_failed("SYNO.Core.Terminal/get (read-back)", after), "before": self._shape_terminal(current)}
        shaped = self._shape_terminal(_data(after))
        applied = shaped["telnet_enabled"] == params["enable_telnet"] and shaped["ssh_enabled"] == params["enable_ssh"]
        return {
            "success": applied,
            "before": self._shape_terminal(current),
            "after": shaped,
            **({} if applied else {"error": _MISMATCH}),
        }

    def set_auto_block(
        self,
        enable: Optional[bool] = None,
        attempts: Optional[int] = None,
        within_minutes: Optional[int] = None,
        expire_days: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Change auto-block. Enabling it is refused while the allow list is empty."""
        before = self._auto_block()
        if not before.get("success"):
            return _failed("SYNO.Core.Security.AutoBlock/get", before)
        current = _data(before)
        params = {
            "enable": current.get("enable") if enable is None else enable,
            "attempts": current.get("attempts") if attempts is None else attempts,
            "within_mins": current.get("within_mins") if within_minutes is None else within_minutes,
            "expire_day": current.get("expire_day") if expire_days is None else expire_days,
        }
        for name in ("attempts", "within_mins"):
            if not isinstance(params[name], int) or isinstance(params[name], bool) or params[name] < 1:
                return _refused("invalid_argument", f"{name} must be a positive integer (Admin Center's minimum is 1)")
        if not isinstance(params["expire_day"], int) or isinstance(params["expire_day"], bool) or params["expire_day"] < 0:
            return _refused("invalid_argument", "expire_days must be 0 (never) or a positive integer")
        if params["enable"] is True:
            allow = self._allow_list()
            if not allow.get("success"):
                return _failed("SYNO.Core.Security.AutoBlock.Rules/list", allow)
            if not _data(allow).get("entries"):
                return {
                    **_refused(
                        "empty_allow_list",
                        "Refusing to enable auto-block with an empty allow list: add this "
                        "server's address (and the LAN) with synology_autoblock_allow_add first, "
                        "so the account this server signs in as cannot be blocked.",
                    ),
                    "before": self._shape_auto_block(current),
                }
        written = self._post("SYNO.Core.Security.AutoBlock", "set", 1, params)
        if not written.get("success"):
            return {**_write_failed("SYNO.Core.Security.AutoBlock/set", written, "Re-read it with synology_security_posture before retrying."), "before": self._shape_auto_block(current)}
        after = self._auto_block()
        if not after.get("success"):
            return {**_failed("SYNO.Core.Security.AutoBlock/get (read-back)", after), "before": self._shape_auto_block(current)}
        got = _data(after)
        applied = all(got.get(k) == v for k, v in params.items())
        return {
            "success": applied,
            "before": self._shape_auto_block(current),
            "after": self._shape_auto_block(got),
            **({} if applied else {"error": _MISMATCH}),
        }

    def _change_allow_list(self, method: str, entries: List[str]) -> Dict[str, Any]:
        before = self._allow_list()
        if not before.get("success"):
            return _failed("SYNO.Core.Security.AutoBlock.Rules/list", before)
        params: Dict[str, Any] = {"type": "allow", "ip": list(entries)}
        if method == "create":
            params = {"type": "allow", "block_days": 0, "overwrite": True, "ip": list(entries)}
        written = self._post("SYNO.Core.Security.AutoBlock.Rules", method, 1, params)
        before_entries = _data(before).get("entries")
        if not written.get("success"):
            return {**_write_failed(f"SYNO.Core.Security.AutoBlock.Rules/{method}", written, "Re-read it with synology_security_posture before retrying."), "before": before_entries}
        after = self._allow_list()
        if not after.get("success"):
            return {**_failed("SYNO.Core.Security.AutoBlock.Rules/list (read-back)", after), "before": before_entries}
        after_entries = _data(after).get("entries") or []
        # DSM may store an entry in its own notation (a /22 as a dotted mask),
        # so membership is reported rather than asserted.
        return {
            "success": True,
            "before": before_entries,
            "after": after_entries,
            "requested": list(entries),
            "not_listed_verbatim": [e for e in entries if (e in after_entries) != (method == "create")],
        }

    def add_allow_list(self, entries: List[str]) -> Dict[str, Any]:
        """Add IPs, subnets ("ip/mask" or "ip/prefix") or ranges ("a~b") to the allow list."""
        return self._change_allow_list("create", entries)

    def remove_allow_list(self, entries: List[str]) -> Dict[str, Any]:
        """Remove allow-list entries, named exactly as the list shows them."""
        return self._change_allow_list("delete", entries)

    def set_https_redirect(self, enable: bool) -> Dict[str, Any]:
        """Login Portal > DSM > Automatically redirect HTTP connection to HTTPS."""
        before = self._web_dsm()
        if not before.get("success"):
            return _failed("SYNO.Core.Web.DSM/get", before)
        current = _data(before)
        params = dict(current)
        params["enable_https_redirect"] = enable
        for name in _WEB_DSM_PORT_FIELDS:
            if name in params:
                params[name] = int(params[name])
        written = self._post("SYNO.Core.Web.DSM", "set", 2, params)
        if not written.get("success"):
            return {**_write_failed("SYNO.Core.Web.DSM/set", written, "Re-read it with synology_security_posture before retrying."), "before": self._shape_web_dsm(current)}
        after = self._web_dsm()
        if not after.get("success"):
            # The Login Portal waits for DSM's web server to restart after
            # this set, so a read straight after it can fail. Say so rather
            # than call the change verified.
            return {
                "success": True,
                "verified": False,
                "before": self._shape_web_dsm(current),
                "after": None,
                "note": "DSM accepted the change but the read-back failed. Re-read with synology_security_posture.",
                "read_back_error": after.get("error"),
            }
        shaped = self._shape_web_dsm(_data(after))
        applied = shaped["https_redirect"] == enable
        return {
            "success": applied,
            "verified": True,
            "before": self._shape_web_dsm(current),
            "after": shaped,
            "redirect_url": _data(written).get("redirect_url"),
            **({} if applied else {"error": _MISMATCH}),
        }
