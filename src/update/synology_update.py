# src/update/synology_update.py - DSM and package update status, DSM update install
#
# Every API, method, version and parameter here is the one DSM's own Admin
# Center (webman/modules/AdminCenter/admin_center.js) and Package Center
# (webman/modules/PkgManApp/PkgManApp.js) send, read from the JavaScript served
# by an RS1221+ on DSM 7.3.2-86009 Update 5 on 2026-10-29, and the read calls
# were run against that NAS. Synology publishes no Web API guide for any of
# them.
#
# The DSM update flow, as Admin Center drives it:
#
#   version   SYNO.Core.System/info v3 {type: "firmware"}
#             -> {firmware_ver, firmware_date, model}
#   check     SYNO.Core.Upgrade.Server/check v3
#             {user_reading, need_auto_smallupdate, need_promotion}
#             -> data.update {available, version, reboot, restart, type,
#                             version_details.isSecurityVersion}
#   state     SYNO.Core.Upgrade/basic_status v1
#             -> {status, target, allow_upgrade, patch_info}
#   download  SYNO.Core.Upgrade.Server.Download/start v2
#             {target: "update", need_auto_smallupdate: true}
#   progress  SYNO.Core.Upgrade.Server.Download/progress v2 {need_download_target}
#   precheck  SYNO.Core.Upgrade/status v1 {feasibility_check: true}
#             SYNO.Core.Upgrade.PreCheck/start v2 {lang, type: "server"}
#             (the second only when patch_info.upgradetype is "dsm")
#   install   SYNO.Core.Upgrade/start v1 {force: true, type: "server"}
#
# Install refuses on what Admin Center refuses on: a "hard" feasibility check, a
# PreCheck call that failed, or a PreCheck whose precheck_info.can_continue is
# not true AND carries a message (Admin Center's own condition; can_continue
# false with no message does not block there either). It refuses two things
# Admin Center allows: a major-version upgrade (target "promotion"), which this
# module does not start, and basic_status.allow_upgrade false. What Admin Center
# merely warns about
# (a "soft" feasibility check, a PreCheck message with can_continue true, a
# EULA) needs an explicit accept_* argument here instead of a dialog.

import json
from typing import Any, Dict, List, Optional

from utils.synology_api import SynologyAPIClient

# DSM_UPDATE_TARGET is the value Admin Center passes for an ordinary update; the
# other value it uses, "promotion", is a major-version upgrade, which this module
# does not start.
DSM_UPDATE_TARGET = "update"
INSTALL_TYPE = "server"
# The installed version comes from SYNO.Core.System/info exactly as Admin
# Center's Update & Restore panel asks for it: the API's maxVersion (3 on DSM
# 7.3) with type "firmware". Without a type, info answers the full system
# overview, which on the RS1221+ took longer than the 15 s read timeout on
# every call on 2026-10-29 -- so the status read, and the download that starts
# from it, failed every time while the firmware subset answers at once.
SYSTEM_INFO_VERSION = 3
SYSTEM_INFO_TYPE = "firmware"
# A download start or an install start returns once DSM has queued the work,
# but DSM is slower to answer these than a read.
UPDATE_WRITE_TIMEOUT = 60
# basic_status.status once the update has been downloaded and can be installed.
READY_TO_INSTALL = "ready_upgrade"
# basic_status.status values that mean an install is already in flight.
INSTALL_IN_PROGRESS = frozenset({"upgrading"})


def _json_params(params: Dict[str, Any]) -> Dict[str, str]:
    """Encode parameters the way DSM's JSON-format APIs expect them.

    Every API here is registered with requestFormat JSON, so a boolean is the
    four characters `true` and a string is quoted -- the same encoding the
    SYNO.API.Request helper in DSM's own JavaScript applies.
    """
    return {k: json.dumps(v) for k, v in params.items()}


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


class SynologyUpdate:
    """DSM update and package update status, and DSM update download/install."""

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

    def _call(
        self,
        api: str,
        method: str,
        version: int,
        params: Optional[Dict[str, Any]] = None,
        *,
        post: bool = False,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        extra = _json_params(params or {})
        if post:
            return self._api.post(api, method, version, extra, timeout=timeout)
        return self._api.get(api, method, version, extra, timeout=timeout)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def dsm_update_status(self) -> Dict[str, Any]:
        """Installed DSM version, the available update, and its download state."""
        info = self._call(
            "SYNO.Core.System", "info", SYSTEM_INFO_VERSION, {"type": SYSTEM_INFO_TYPE}
        )
        if not info.get("success"):
            return _failed("SYNO.Core.System/info", info)
        check = self._call(
            "SYNO.Core.Upgrade.Server",
            "check",
            3,
            {"user_reading": True, "need_auto_smallupdate": True, "need_promotion": True},
        )
        if not check.get("success"):
            return _failed("SYNO.Core.Upgrade.Server/check", check)
        state = self._call("SYNO.Core.Upgrade", "basic_status", 1)
        if not state.get("success"):
            return _failed("SYNO.Core.Upgrade/basic_status", state)
        progress = self._call(
            "SYNO.Core.Upgrade.Server.Download",
            "progress",
            2,
            {"need_download_target": True},
        )
        if not progress.get("success"):
            return _failed("SYNO.Core.Upgrade.Server.Download/progress", progress)
        setting = self._call("SYNO.Core.Upgrade.Setting", "get", 1)
        if not setting.get("success"):
            return _failed("SYNO.Core.Upgrade.Setting/get", setting)

        update = _data(check).get("update") or {}
        details = update.get("version_details") or {}
        promotion = _data(check).get("promotion") or {}
        available = update.get("available") is True
        return {
            "success": True,
            "installed_version": _data(info).get("firmware_ver"),
            "installed_date": _data(info).get("firmware_date"),
            "model": _data(info).get("model"),
            "update_available": available,
            "update": (
                {
                    "version": update.get("version"),
                    "type": update.get("type"),
                    "is_security_version": details.get("isSecurityVersion"),
                    # "now" means the NAS reboots to install it; "none" means
                    # it does not. Reported verbatim rather than mapped to a
                    # boolean, because DSM has more values than those two.
                    "reboot": update.get("reboot"),
                    "restart": update.get("restart"),
                    "version_details": details,
                }
                if available
                else None
            ),
            "major_upgrade_available": promotion.get("available") is True,
            "major_upgrade_version": promotion.get("version"),
            "state": _data(state),
            "download": _data(progress),
            "settings": _data(setting),
        }

    def package_update_status(self) -> Dict[str, Any]:
        """Installed packages, which have an update pending, and the auto-update policy.

        A package has an update pending when DSM reports a non-empty
        `available_operation` for it -- the test Package Center's own
        `_isUpgradable` applies (`upgrade`, `repair` or `forcereplaced`).
        """
        listing = self._call(
            "SYNO.Core.Package",
            "list",
            2,
            {"additional": ["available_operation", "status", "autoupdate", "beta"]},
        )
        if not listing.get("success"):
            return _failed("SYNO.Core.Package/list", listing)
        setting = self._call("SYNO.Core.Package.Setting", "get", 1)
        if not setting.get("success"):
            return _failed("SYNO.Core.Package.Setting/get", setting)

        installed: List[Dict[str, Any]] = []
        pending: List[Dict[str, Any]] = []
        for pkg in _data(listing).get("packages") or []:
            extra = pkg.get("additional") or {}
            operations = extra.get("available_operation") or {}
            row = {
                "id": pkg.get("id"),
                "name": pkg.get("name"),
                "version": pkg.get("version"),
                "status": extra.get("status"),
                "autoupdate": extra.get("autoupdate"),
            }
            installed.append(row)
            if operations:
                pending.append({**row, "available_operation": operations})
        policy = _data(setting)
        return {
            "success": True,
            "pending_count": len(pending),
            "pending": pending,
            "installed_count": len(installed),
            "installed": installed,
            "auto_update": {
                "enabled": policy.get("enable_autoupdate"),
                "all_packages": policy.get("autoupdateall"),
                "important_only": policy.get("autoupdateimportant"),
            },
        }

    def update_status(self) -> Dict[str, Any]:
        """DSM and package update status in one call."""
        dsm = self.dsm_update_status()
        packages = self.package_update_status()
        return {
            "success": bool(dsm.get("success") and packages.get("success")),
            "dsm": dsm,
            "packages": packages,
        }

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def dsm_update_download(self) -> Dict[str, Any]:
        """Start downloading the available DSM update. Installs nothing."""
        status = self.dsm_update_status()
        if not status.get("success"):
            return status
        if not status["update_available"]:
            return {
                "success": False,
                "error": {
                    "code": "no_update_available",
                    "message": f"DSM reports no update for {status['installed_version']}; nothing was downloaded.",
                },
            }
        if status["state"].get("status") == READY_TO_INSTALL:
            return {
                "success": True,
                "already_downloaded": True,
                "version": status["update"]["version"],
                "state": status["state"],
            }
        started = self._call(
            "SYNO.Core.Upgrade.Server.Download",
            "start",
            2,
            {"target": DSM_UPDATE_TARGET, "need_auto_smallupdate": True},
            post=True,
            timeout=UPDATE_WRITE_TIMEOUT,
        )
        if not started.get("success"):
            return _write_failed(
                "SYNO.Core.Upgrade.Server.Download/start",
                started,
                "Check synology_update_status before retrying.",
            )
        progress = self._call(
            "SYNO.Core.Upgrade.Server.Download",
            "progress",
            2,
            {"need_download_target": True},
        )
        if not progress.get("success"):
            # The download is already running; say so, and that its progress
            # could not be read, rather than report success with no progress.
            return {
                **_failed("SYNO.Core.Upgrade.Server.Download/progress", progress),
                "started": True,
                "version": status["update"]["version"],
            }
        return {
            "success": True,
            "started": True,
            "version": status["update"]["version"],
            "download": _data(progress),
            "next": "Poll synology_update_status until state.status is "
            f"'{READY_TO_INSTALL}', then call synology_dsm_update_install.",
        }

    def dsm_update_precheck(self, lang: str = "enu") -> Dict[str, Any]:
        """Run DSM's own pre-install checks and classify them as Admin Center does.

        Returns `blockers` (Admin Center refuses to continue), `warnings`
        (Admin Center asks the user to confirm) and `eula` (Admin Center shows
        terms to accept). Changes nothing on the NAS.
        """
        state = self._call("SYNO.Core.Upgrade", "basic_status", 1)
        if not state.get("success"):
            return _failed("SYNO.Core.Upgrade/basic_status", state)
        st = _data(state)
        blockers: List[Any] = []
        warnings: List[Any] = []
        eula: Optional[str] = None

        if st.get("status") in INSTALL_IN_PROGRESS:
            blockers.append(f"DSM is already installing an update (status {st.get('status')!r}).")
        elif st.get("status") != READY_TO_INSTALL:
            blockers.append(
                f"No downloaded update is ready to install (status {st.get('status')!r}, "
                f"target {st.get('target')!r}); run synology_dsm_update_download first."
            )
        if st.get("target") == "promotion":
            blockers.append(
                "The downloaded update is a major-version upgrade (target 'promotion'); "
                "this tool installs ordinary updates only."
            )
        if st.get("allow_upgrade") is False:
            blockers.append("DSM reports allow_upgrade=false.")

        feasibility = self._call(
            "SYNO.Core.Upgrade", "status", 1, {"feasibility_check": True}
        )
        if not feasibility.get("success"):
            return _failed("SYNO.Core.Upgrade/status", feasibility)
        fz = _data(feasibility)
        messages = [
            msg
            for part in ("poweroff", "upgrade")
            for msg in ((fz.get(part) or {}).get("check_msg") or [])
        ]
        if fz.get("check_type") == "hard":
            blockers.append({"feasibility_check": "hard", "messages": messages})
        elif fz.get("check_type") == "soft":
            warnings.append({"feasibility_check": "soft", "messages": messages})

        patch_info = st.get("patch_info") or {}
        precheck_data: Optional[Dict[str, Any]] = None
        if patch_info.get("upgradetype") == "dsm":
            pre = self._call(
                "SYNO.Core.Upgrade.PreCheck",
                "start",
                2,
                {"lang": lang, "type": INSTALL_TYPE},
                timeout=UPDATE_WRITE_TIMEOUT,
            )
            if not pre.get("success"):
                blockers.append({"precheck_failed": pre.get("error")})
            else:
                precheck_data = _data(pre)
                info = precheck_data.get("precheck_info") or {}
                if precheck_data.get("precheck") and info:
                    entry = {"message": info.get("message"), "leading_note": info.get("leading_note")}
                    if info.get("can_continue") is not True and info.get("message"):
                        blockers.append({"precheck": entry})
                    elif info.get("message"):
                        warnings.append({"precheck": entry})
                if precheck_data.get("eulacheck") and precheck_data.get("eulacheck_content"):
                    eula = precheck_data.get("eulacheck_content")

        return {
            "success": True,
            "state": st,
            "patch_info": patch_info,
            "blockers": blockers,
            "warnings": warnings,
            "eula_required": eula is not None,
            "eula": eula,
            "feasibility": fz,
        }

    def dsm_update_install(
        self,
        *,
        accept_warnings: bool = False,
        accept_eula: bool = False,
        lang: str = "enu",
    ) -> Dict[str, Any]:
        """Install the downloaded DSM update. The NAS may reboot."""
        pre = self.dsm_update_precheck(lang)
        if not pre.get("success"):
            return pre
        refusal: Optional[str] = None
        if pre["blockers"]:
            refusal = "DSM's pre-install checks refuse this install."
        elif pre["warnings"] and not accept_warnings:
            refusal = "DSM raised warnings; re-run with accept_warnings=true once they are read."
        elif pre["eula_required"] and not accept_eula:
            refusal = "DSM requires the update's terms to be accepted; re-run with accept_eula=true once read."
        if refusal:
            return {
                "success": False,
                "error": {"code": "precheck_refused", "message": refusal + " Nothing was installed."},
                "precheck": pre,
            }
        started = self._call(
            "SYNO.Core.Upgrade",
            "start",
            1,
            {"force": True, "type": INSTALL_TYPE},
            post=True,
            timeout=UPDATE_WRITE_TIMEOUT,
        )
        if not started.get("success"):
            return {
                **_write_failed(
                    "SYNO.Core.Upgrade/start",
                    started,
                    "The install may have started and the NAS may reboot; do not retry "
                    "until synology_update_status answers and shows it did not.",
                ),
                "precheck": pre,
            }
        return {
            "success": True,
            "started": True,
            "patch_info": pre["patch_info"],
            "warnings_accepted": pre["warnings"],
            "note": "DSM is installing. If patch_info.reboot is 'now' the NAS reboots and "
            "this server's session ends; poll synology_update_status once it is back.",
        }
