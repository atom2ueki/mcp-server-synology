"""DSM/package update and security-posture module tests. No NAS required."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from mcp_server import SynologyMCPServer, ToolFailureError
from update.synology_security import SynologySecurity
from update.synology_update import SynologyUpdate

BASE = "https://nas.example.com"

# Shapes captured from an RS1221+ on DSM 7.3.2-86009 Update 5, 2026-10-29.
SYSTEM_INFO = {"firmware_ver": "DSM 7.3.2-86009 Update 5", "firmware_date": "2026/10/02", "model": "RS1221+"}
CHECK_AVAILABLE = {
    "promotion": {"available": False, "rss_result": "success"},
    "update": {
        "available": True,
        "reboot": "now",
        "restart": "none",
        "rss_result": "success",
        "type": "system",
        "version": "DSM 7.4.1-90080 Update 1",
        "version_details": {"buildnumber": 90080, "isSecurityVersion": False, "major": 7, "minor": 4, "micro": 1, "nano": 1, "os_name": "DSM"},
    },
}
CHECK_NONE = {"promotion": {"available": False}, "update": {"available": False}}


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


class FakeDSM:
    """Answers entry.cgi by (api, method) and records every call made."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, params=None, data=None, **kwargs):
        sent = params if params is not None else data
        key = (sent["api"], sent["method"])
        self.calls.append((key, sent, "post" if data is not None else "get"))
        reply = self.routes.get(key)
        if reply is None:
            return _response({"success": False, "error": {"code": 103}})
        return _response(reply)

    def called(self, api, method):
        return [c for c in self.calls if c[0] == (api, method)]


def ok(data):
    return {"success": True, "data": data}


def dsm(routes):
    fake = FakeDSM(routes)
    return fake, patch("utils.synology_api.requests.get", side_effect=fake), patch(
        "utils.synology_api.requests.post", side_effect=fake
    )


def base_routes(check=CHECK_AVAILABLE, state=None):
    return {
        ("SYNO.Core.System", "info"): ok(SYSTEM_INFO),
        ("SYNO.Core.Upgrade.Server", "check"): ok(check),
        ("SYNO.Core.Upgrade", "basic_status"): ok(state or {"allow_upgrade": True, "status": "none"}),
        ("SYNO.Core.Upgrade.Server.Download", "progress"): ok({"percent": 0, "status": "none"}),
        ("SYNO.Core.Upgrade.Setting", "get"): ok({"auto_download": True, "upgrade_type": "hotfix"}),
    }


def upd():
    return SynologyUpdate(BASE, "sid", syno_token="tok")


# ----------------------------------------------------------------------
# Read
# ----------------------------------------------------------------------


def test_dsm_status_reports_available_update():
    fake, g, p = dsm(base_routes())
    with g, p:
        result = upd().dsm_update_status()
    assert result["success"] is True
    assert result["installed_version"] == "DSM 7.3.2-86009 Update 5"
    assert result["update_available"] is True
    assert result["update"]["version"] == "DSM 7.4.1-90080 Update 1"
    assert result["update"]["reboot"] == "now"
    assert result["update"]["is_security_version"] is False


def test_dsm_check_sends_admin_centers_parameters_json_encoded():
    fake, g, p = dsm(base_routes())
    with g, p:
        upd().dsm_update_status()
    (_, sent, _), = fake.called("SYNO.Core.Upgrade.Server", "check")
    assert sent["version"] == "3"
    assert sent["user_reading"] == "true"
    assert sent["need_auto_smallupdate"] == "true"
    assert sent["need_promotion"] == "true"


def test_installed_version_is_read_as_admin_centers_firmware_subset():
    # Regression: SYNO.Core.System/info v1 with no type answers the whole system
    # overview, which outran the 15 s read timeout on every call against an
    # RS1221+ on 2026-10-29, so update_status and dsm_update_download failed at
    # this step every time. Admin Center's Update panel asks for the firmware
    # subset at the API's maxVersion, and so must this.
    fake, g, p = dsm(base_routes())
    with g, p:
        upd().dsm_update_status()
    (_, sent, verb), = fake.called("SYNO.Core.System", "info")
    assert sent["version"] == "3"
    assert sent["type"] == '"firmware"'
    assert verb == "get"


def test_download_reads_the_installed_version_the_same_way():
    fake, g, p = dsm(base_routes())
    with g, p:
        upd().dsm_update_download()
    calls = fake.called("SYNO.Core.System", "info")
    assert calls
    assert all(sent["type"] == '"firmware"' and sent["version"] == "3" for _, sent, _ in calls)


def test_dsm_status_without_update():
    fake, g, p = dsm(base_routes(check=CHECK_NONE))
    with g, p:
        result = upd().dsm_update_status()
    assert result["update_available"] is False
    assert result["update"] is None


def test_dsm_status_names_the_failing_step():
    routes = base_routes()
    routes[("SYNO.Core.Upgrade.Server", "check")] = {"success": False, "error": {"code": 105}}
    fake, g, p = dsm(routes)
    with g, p:
        result = upd().dsm_update_status()
    assert result["success"] is False
    assert result["step"] == "SYNO.Core.Upgrade.Server/check"
    assert result["error"]["code"] == 105
    assert result["error"]["api"] == "SYNO.Core.Upgrade.Server"


def test_package_status_flags_only_packages_with_an_operation():
    listing = ok(
        {
            "packages": [
                {"id": "SMBService", "name": "SMB Service", "version": "4.15.13-4251", "additional": {"available_operation": {}, "status": "running", "autoupdate": True}},
                {"id": "WebStation", "name": "Web Station", "version": "4.3.1-0530", "additional": {"available_operation": {"upgrade": {"version": "4.3.2-0600"}}, "status": "running", "autoupdate": True}},
            ]
        }
    )
    routes = {
        ("SYNO.Core.Package", "list"): listing,
        ("SYNO.Core.Package.Setting", "get"): ok({"enable_autoupdate": True, "autoupdateall": True, "autoupdateimportant": False}),
    }
    fake, g, p = dsm(routes)
    with g, p:
        result = upd().package_update_status()
    assert result["pending_count"] == 1
    assert result["pending"][0]["id"] == "WebStation"
    assert result["installed_count"] == 2
    assert result["auto_update"] == {"enabled": True, "all_packages": True, "important_only": False}
    (_, sent, _), = fake.called("SYNO.Core.Package", "list")
    assert json.loads(sent["additional"])[0] == "available_operation"


# ----------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------


def test_download_starts_with_admin_centers_target():
    routes = base_routes()
    routes[("SYNO.Core.Upgrade.Server.Download", "start")] = ok({})
    fake, g, p = dsm(routes)
    with g, p:
        result = upd().dsm_update_download()
    assert result["success"] is True and result["started"] is True
    (_, sent, verb), = fake.called("SYNO.Core.Upgrade.Server.Download", "start")
    assert verb == "post"
    assert sent["version"] == "2"
    assert sent["target"] == '"update"'
    assert sent["need_auto_smallupdate"] == "true"


def test_download_reports_a_failed_progress_read_after_starting():
    routes = base_routes()
    routes[("SYNO.Core.Upgrade.Server.Download", "start")] = ok({})
    fake, g, p = dsm(routes)
    answer = fake.__call__

    def progress_fails_once_started(url, params=None, data=None, **kwargs):
        sent = params if params is not None else data
        if sent["method"] == "progress" and fake.called("SYNO.Core.Upgrade.Server.Download", "start"):
            fake.calls.append(((sent["api"], sent["method"]), sent, "get"))
            return _response({"success": False, "error": {"code": 400}})
        return answer(url, params=params, data=data, **kwargs)

    g = patch("utils.synology_api.requests.get", side_effect=progress_fails_once_started)
    p = patch("utils.synology_api.requests.post", side_effect=progress_fails_once_started)
    with g, p:
        result = upd().dsm_update_download()
    assert result["success"] is False
    assert result["started"] is True
    assert result["step"] == "SYNO.Core.Upgrade.Server.Download/progress"
    assert result["error"]["code"] == 400


def test_download_refuses_when_no_update():
    fake, g, p = dsm(base_routes(check=CHECK_NONE))
    with g, p:
        result = upd().dsm_update_download()
    assert result["success"] is False
    assert result["error"]["code"] == "no_update_available"
    assert not fake.called("SYNO.Core.Upgrade.Server.Download", "start")


def test_download_is_a_no_op_when_already_downloaded():
    fake, g, p = dsm(base_routes(state={"status": "ready_upgrade", "target": "update", "allow_upgrade": True}))
    with g, p:
        result = upd().dsm_update_download()
    assert result["already_downloaded"] is True
    assert not fake.called("SYNO.Core.Upgrade.Server.Download", "start")


# ----------------------------------------------------------------------
# Precheck / install
# ----------------------------------------------------------------------

READY = {"status": "ready_upgrade", "target": "update", "allow_upgrade": True, "patch_info": {"upgradetype": "dsm", "reboot": "now"}}


def install_routes(feasibility=None, precheck=None, state=READY):
    routes = base_routes(state=state)
    routes[("SYNO.Core.Upgrade", "status")] = ok(feasibility or {"check_type": "none"})
    routes[("SYNO.Core.Upgrade.PreCheck", "start")] = precheck if precheck is not None else ok({"precheck": False})
    routes[("SYNO.Core.Upgrade", "start")] = ok({})
    return routes


def test_install_starts_after_clean_prechecks():
    fake, g, p = dsm(install_routes())
    with g, p:
        result = upd().dsm_update_install()
    assert result["success"] is True
    (_, sent, verb), = fake.called("SYNO.Core.Upgrade", "start")
    assert verb == "post"
    assert sent["force"] == "true"
    assert sent["type"] == '"server"'
    (_, pre, _), = fake.called("SYNO.Core.Upgrade.PreCheck", "start")
    assert pre["version"] == "2" and pre["type"] == '"server"'


def test_install_refuses_when_not_downloaded():
    fake, g, p = dsm(install_routes(state={"status": "none", "allow_upgrade": True}))
    with g, p:
        result = upd().dsm_update_install()
    assert result["success"] is False
    assert result["error"]["code"] == "precheck_refused"
    assert not fake.called("SYNO.Core.Upgrade", "start")


def test_install_refuses_a_promotion():
    state = {**READY, "target": "promotion"}
    fake, g, p = dsm(install_routes(state=state))
    with g, p:
        result = upd().dsm_update_install(accept_warnings=True, accept_eula=True)
    assert result["success"] is False
    assert not fake.called("SYNO.Core.Upgrade", "start")


def test_hard_feasibility_check_blocks_even_with_accepts():
    feas = {"check_type": "hard", "upgrade": {"check_msg": ["volume_crashed"]}}
    fake, g, p = dsm(install_routes(feasibility=feas))
    with g, p:
        result = upd().dsm_update_install(accept_warnings=True, accept_eula=True)
    assert result["success"] is False
    assert result["precheck"]["blockers"][0]["messages"] == ["volume_crashed"]
    assert not fake.called("SYNO.Core.Upgrade", "start")


def test_soft_feasibility_check_needs_accept_warnings():
    feas = {"check_type": "soft", "poweroff": {"check_msg": ["pkg_running"]}}
    fake, g, p = dsm(install_routes(feasibility=feas))
    with g, p:
        refused = upd().dsm_update_install()
    assert refused["success"] is False
    assert not fake.called("SYNO.Core.Upgrade", "start")
    fake, g, p = dsm(install_routes(feasibility=feas))
    with g, p:
        accepted = upd().dsm_update_install(accept_warnings=True)
    assert accepted["success"] is True
    assert fake.called("SYNO.Core.Upgrade", "start")


def test_precheck_cannot_continue_blocks():
    pre = ok({"precheck": True, "precheck_info": {"can_continue": False, "message": "Insufficient space"}})
    fake, g, p = dsm(install_routes(precheck=pre))
    with g, p:
        result = upd().dsm_update_install(accept_warnings=True, accept_eula=True)
    assert result["success"] is False
    assert not fake.called("SYNO.Core.Upgrade", "start")


def test_failed_precheck_call_blocks():
    fake, g, p = dsm(install_routes(precheck={"success": False, "error": {"code": 105}}))
    with g, p:
        result = upd().dsm_update_install(accept_warnings=True, accept_eula=True)
    assert result["success"] is False
    assert not fake.called("SYNO.Core.Upgrade", "start")


def test_eula_needs_accept_eula():
    pre = ok({"precheck": False, "eulacheck": True, "eulacheck_content": "<p>terms</p>"})
    fake, g, p = dsm(install_routes(precheck=pre))
    with g, p:
        refused = upd().dsm_update_install(accept_warnings=True)
    assert refused["success"] is False
    assert refused["precheck"]["eula"] == "<p>terms</p>"
    fake, g, p = dsm(install_routes(precheck=pre))
    with g, p:
        assert upd().dsm_update_install(accept_eula=True)["success"] is True


def test_precheck_skipped_for_a_non_dsm_patch():
    state = {**READY, "patch_info": {"upgradetype": "smallupdate"}}
    fake, g, p = dsm(install_routes(state=state))
    with g, p:
        result = upd().dsm_update_install()
    assert result["success"] is True
    assert not fake.called("SYNO.Core.Upgrade.PreCheck", "start")


# ----------------------------------------------------------------------
# Security posture
# ----------------------------------------------------------------------


def posture_routes():
    return {
        ("SYNO.Core.QuickConnect", "get"): ok({"enabled": False, "server_alias": "nas"}),
        ("SYNO.Core.Security.AutoBlock", "get"): ok({"attempts": 10, "enable": False, "expire_day": 0, "within_mins": 5}),
        ("SYNO.Core.Security.Firewall", "get"): ok({"enable_firewall": False, "profile_name": "default"}),
        ("SYNO.Core.OTP.EnforcePolicy", "get"): ok({"otp_enforce_option": "none"}),
        ("SYNO.Core.User", "list"): ok({"users": [{"name": "admin", "expired": "now"}, {"name": "guest", "expired": "now"}, {"name": "richard", "expired": "normal"}]}),
        ("SYNO.Core.Group.Member", "list"): ok({"users": [{"name": "richard"}, {"name": "admin"}]}),
        ("SYNO.Core.SecurityScan.Status", "system_get"): ok({"sysStatus": "risk", "lastScanTime": "1792843415", "items": {"network": {"failSeverity": "risk", "fail": {"risk": 1}}}}),
        ("SYNO.Core.SecurityScan.Status", "rule_get"): ok({"items": {
            "a": {"id": "a", "category": "network", "severity": "risk", "status": "fail", "strId": "rule_a"},
            "b": {"id": "b", "category": "network", "severity": "warning", "status": "pass", "strId": "rule_b"},
            "c": {"id": "c", "category": "systemCheck", "severity": "warning", "status": "skip", "strId": "rule_c"},
        }}),
        ("SYNO.Core.SecurityScan.Conf", "get"): ok({"enableSchedule": True, "weekday": "6", "hour": 5, "minute": 3}),
        ("SYNO.Core.Terminal", "get"): ok(dict(TERMINAL)),
        ("SYNO.Core.Web.DSM", "get"): ok(dict(WEB_DSM)),
        ("SYNO.Core.Security.AutoBlock.Rules", "list"): ok({"ip_info": [], "offset": 0, "total": 0}),
    }


# Shapes captured from an RS1221+ on DSM 7.4.1-90080 Update 1, 2026-10-29
# (Terminal trimmed of its cipher lists).
TERMINAL = {"enable_ssh": True, "enable_telnet": True, "forbid_console": False, "ssh_port": 22}
WEB_DSM = {
    "enable_avahi": True, "enable_custom_domain": True, "enable_hsts": True, "enable_https_redirect": False,
    "enable_max_connections": False, "enable_reuseport": False, "enable_server_header": True, "enable_spdy": True,
    "enable_ssdp": True, "fqdn": "nas.example.com", "http_port": 5000, "https_port": 5001, "main_app": "DSM",
    "max_connections": 4096, "max_connections_limit": {"lower": 4096, "upper": 262140}, "server_header": "nginx",
    "support_reuseport": True,
}


def test_posture_summarises_every_section():
    fake, g, p = dsm(posture_routes())
    with g, p:
        result = SynologySecurity(BASE, "sid").posture()
    assert result["success"] is True
    assert result["quickconnect"]["enabled"] is False
    assert result["auto_block"]["enabled"] is False
    assert result["firewall"]["enabled"] is False
    assert result["two_factor"]["enforced_for"] == "none"
    assert result["accounts"]["admin_disabled"] is True
    assert result["accounts"]["enabled_users"] == ["richard"]
    assert result["administrators"] == ["admin", "richard"]
    assert [f["id"] for f in result["security_advisor"]["findings"]] == ["a"]
    assert result["security_advisor"]["schedule"]["enabled"] is True


def test_posture_reports_terminal_redirect_and_allow_list():
    routes = posture_routes()
    routes[("SYNO.Core.Security.AutoBlock.Rules", "list")] = ok({"ip_info": [{"ip": "192.168.0.5"}], "offset": 0, "total": 1})
    fake, g, p = dsm(routes)
    with g, p:
        result = SynologySecurity(BASE, "sid").posture()
    assert result["terminal"] == {"telnet_enabled": True, "ssh_enabled": True, "ssh_port": 22}
    assert result["dsm_web"]["https_redirect"] is False
    assert result["dsm_web"]["https_port"] == 5001
    assert result["auto_block"]["allow_list"] == ["192.168.0.5"]
    sent = fake.called("SYNO.Core.Security.AutoBlock.Rules", "list")[0][1]
    assert sent["type"] == '"allow"'


class Stateful(FakeDSM):
    """A FakeDSM whose set/create/delete change what the next get returns."""

    def __init__(self, routes, terminal=None, web=None, auto_block=None, allow=None):
        super().__init__(routes)
        self.terminal = dict(terminal or TERMINAL)
        self.web = dict(web or WEB_DSM)
        self.auto_block = dict(auto_block or {"attempts": 10, "enable": False, "expire_day": 0, "within_mins": 5})
        self.allow = list(allow or [])

    def __call__(self, url, params=None, data=None, **kwargs):
        sent = params if params is not None else data
        key = (sent["api"], sent["method"])
        decoded = {k: json.loads(v) for k, v in sent.items() if k not in ("api", "method", "version", "_sid")}
        if key == ("SYNO.Core.Terminal", "set"):
            self.terminal.update(decoded)
        elif key == ("SYNO.Core.Web.DSM", "set"):
            self.web.update(decoded)
        elif key == ("SYNO.Core.Security.AutoBlock", "set"):
            self.auto_block.update(decoded)
        elif key == ("SYNO.Core.Security.AutoBlock.Rules", "create"):
            self.allow.extend(i for i in decoded["ip"] if i not in self.allow)
        elif key == ("SYNO.Core.Security.AutoBlock.Rules", "delete"):
            self.allow = [i for i in self.allow if i not in decoded["ip"]]
        self.routes[("SYNO.Core.Terminal", "get")] = ok(dict(self.terminal))
        self.routes[("SYNO.Core.Web.DSM", "get")] = ok(dict(self.web))
        self.routes[("SYNO.Core.Security.AutoBlock", "get")] = ok(dict(self.auto_block))
        self.routes[("SYNO.Core.Security.AutoBlock.Rules", "list")] = ok(
            {"ip_info": [{"ip": i} for i in self.allow], "offset": 0, "total": len(self.allow)}
        )
        for k in [("SYNO.Core.Terminal", "set"), ("SYNO.Core.Web.DSM", "set"), ("SYNO.Core.Security.AutoBlock", "set"),
                  ("SYNO.Core.Security.AutoBlock.Rules", "create"), ("SYNO.Core.Security.AutoBlock.Rules", "delete")]:
            self.routes.setdefault(k, ok({}))
        return super().__call__(url, params=params, data=data, **kwargs)


def stateful(**kw):
    fake = Stateful({}, **kw)
    return fake, patch("utils.synology_api.requests.get", side_effect=fake), patch(
        "utils.synology_api.requests.post", side_effect=fake
    )


def test_telnet_off_keeps_ssh_and_its_port():
    fake, g, p = stateful()
    with g, p:
        result = SynologySecurity(BASE, "sid").set_terminal(enable_telnet=False)
    assert result["success"] is True
    assert result["before"]["telnet_enabled"] is True
    assert result["after"] == {"telnet_enabled": False, "ssh_enabled": True, "ssh_port": 22}
    (_, sent, how), = fake.called("SYNO.Core.Terminal", "set")
    assert how == "post" and str(sent["version"]) == "3"
    assert {k: sent[k] for k in ("enable_telnet", "enable_ssh", "ssh_port")} == {
        "enable_telnet": "false", "enable_ssh": "true", "ssh_port": "22"}


def test_terminal_read_back_mismatch_is_a_failure():
    fake, g, p = stateful()
    fake.routes[("SYNO.Core.Terminal", "set")] = ok({})
    original = fake.__class__.__call__

    def ignore_set(self, url, params=None, data=None, **kwargs):
        sent = params if params is not None else data
        if (sent["api"], sent["method"]) == ("SYNO.Core.Terminal", "set"):
            return FakeDSM.__call__(self, url, params=params, data=data, **kwargs)
        return original(self, url, params=params, data=data, **kwargs)

    with g, p, patch.object(Stateful, "__call__", ignore_set):
        result = SynologySecurity(BASE, "sid").set_terminal(enable_telnet=False)
    assert result["success"] is False
    assert result["after"]["telnet_enabled"] is True


def test_autoblock_enable_refused_with_empty_allow_list():
    fake, g, p = stateful()
    with g, p:
        result = SynologySecurity(BASE, "sid").set_auto_block(enable=True)
    assert result["success"] is False
    assert "allow list" in result["error"]["message"]
    assert not fake.called("SYNO.Core.Security.AutoBlock", "set")


def test_autoblock_enable_with_allow_list_sends_admin_centers_fields():
    fake, g, p = stateful(allow=["192.168.0.0/22"])
    with g, p:
        result = SynologySecurity(BASE, "sid").set_auto_block(enable=True, attempts=10, within_minutes=5, expire_days=0)
    assert result["success"] is True
    assert result["after"] == {"enabled": True, "attempts": 10, "within_minutes": 5, "expire_days": 0}
    (_, sent, how), = fake.called("SYNO.Core.Security.AutoBlock", "set")
    assert how == "post"
    assert {k: sent[k] for k in ("enable", "attempts", "within_mins", "expire_day")} == {
        "enable": "true", "attempts": "10", "within_mins": "5", "expire_day": "0"}


@pytest.mark.parametrize("bad", [{"attempts": 0}, {"within_minutes": -1}, {"expire_days": -1}, {"attempts": True}])
def test_autoblock_rejects_out_of_range_values(bad):
    fake, g, p = stateful(allow=["192.168.0.5"])
    with g, p:
        result = SynologySecurity(BASE, "sid").set_auto_block(**bad)
    assert result["success"] is False
    assert not fake.called("SYNO.Core.Security.AutoBlock", "set")


def test_allow_add_sends_the_dialogs_parameters_and_reads_back():
    fake, g, p = stateful()
    with g, p:
        result = SynologySecurity(BASE, "sid").add_allow_list(["192.168.0.0/22", "192.168.0.5"])
    assert result["success"] is True
    assert result["before"] == []
    assert result["after"] == ["192.168.0.0/22", "192.168.0.5"]
    assert result["not_listed_verbatim"] == []
    (_, sent, _), = fake.called("SYNO.Core.Security.AutoBlock.Rules", "create")
    assert json.loads(sent["ip"]) == ["192.168.0.0/22", "192.168.0.5"]
    assert sent["type"] == '"allow"' and sent["block_days"] == "0" and sent["overwrite"] == "true"


def test_allow_remove():
    fake, g, p = stateful(allow=["10.0.0.1", "192.168.0.5"])
    with g, p:
        result = SynologySecurity(BASE, "sid").remove_allow_list(["10.0.0.1"])
    assert result["after"] == ["192.168.0.5"]
    assert result["not_listed_verbatim"] == []


def test_https_redirect_sends_the_whole_setting_back_with_one_change():
    fake, g, p = stateful()
    with g, p:
        result = SynologySecurity(BASE, "sid").set_https_redirect(True)
    assert result["success"] is True and result["verified"] is True
    assert result["before"]["https_redirect"] is False
    assert result["after"]["https_redirect"] is True
    (_, sent, how), = fake.called("SYNO.Core.Web.DSM", "set")
    assert how == "post" and str(sent["version"]) == "2"
    decoded = {k: json.loads(v) for k, v in sent.items() if k not in ("api", "method", "version", "_sid")}
    assert decoded == {**WEB_DSM, "enable_https_redirect": True}


def test_https_redirect_unreadable_after_restart_is_reported_unverified():
    fake, g, p = stateful()
    real = Stateful.__call__
    calls = {"get": 0}

    def flaky(self, url, params=None, data=None, **kwargs):
        sent = params if params is not None else data
        if (sent["api"], sent["method"]) == ("SYNO.Core.Web.DSM", "get"):
            calls["get"] += 1
            if calls["get"] == 2:
                return _response({"success": False, "error": {"code": 119}})
        return real(self, url, params=params, data=data, **kwargs)

    with g, p, patch.object(Stateful, "__call__", flaky):
        result = SynologySecurity(BASE, "sid").set_https_redirect(True)
    assert result["success"] is True and result["verified"] is False


def test_posture_reports_a_failed_section_instead_of_hiding_it():
    routes = posture_routes()
    routes[("SYNO.Core.Security.Firewall", "get")] = {"success": False, "error": {"code": 105}}
    fake, g, p = dsm(routes)
    with g, p:
        result = SynologySecurity(BASE, "sid").posture()
    assert result["success"] is False
    assert result["firewall"] is None
    assert result["errors"][0]["section"] == "firewall"
    assert result["errors"][0]["error"]["code"] == 105
    assert result["quickconnect"]["enabled"] is False


# ----------------------------------------------------------------------
# MCP wiring
# ----------------------------------------------------------------------


@pytest.fixture
def server():
    srv = SynologyMCPServer()
    srv.sessions[BASE] = "sid"
    return srv


def test_tools_are_registered(server):
    names = {t.name for t in server._get_tool_definitions()}
    assert {
        "synology_update_status",
        "synology_security_posture",
        "synology_dsm_update_download",
        "synology_dsm_update_install",
    } <= names


@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_download_refuses_without_boolean_confirm(server, confirm):
    args = {"base_url": BASE}
    if confirm is not None:
        args["confirm"] = confirm
    fake, g, p = dsm(base_routes())
    with g, p, pytest.raises(ToolFailureError):
        asyncio.run(server._dispatch_tool("synology_dsm_update_download", args))
    assert not fake.calls


def test_install_without_confirm_only_prechecks(server):
    fake, g, p = dsm(install_routes())
    with g, p:
        content = asyncio.run(server._dispatch_tool("synology_dsm_update_install", {"base_url": BASE}))
    body = json.loads(content[0].text)
    assert body["installed"] is False
    assert not fake.called("SYNO.Core.Upgrade", "start")


def test_install_with_confirm_installs(server):
    fake, g, p = dsm(install_routes())
    with g, p:
        asyncio.run(server._dispatch_tool("synology_dsm_update_install", {"base_url": BASE, "confirm": True}))
    assert fake.called("SYNO.Core.Upgrade", "start")


WRITE_TOOLS = [
    ("synology_terminal_set", {"enable_telnet": False}),
    ("synology_autoblock_set", {"enable": False}),
    ("synology_autoblock_allow_add", {"entries": ["192.168.0.5"]}),
    ("synology_autoblock_allow_remove", {"entries": ["192.168.0.5"]}),
    ("synology_https_redirect_set", {"enable": True}),
]


def test_hardening_tools_are_registered(server):
    names = {t.name for t in server._get_tool_definitions()}
    assert {name for name, _ in WRITE_TOOLS} <= names


@pytest.mark.parametrize("name,args", WRITE_TOOLS)
@pytest.mark.parametrize("confirm", [None, False, "true"])
def test_hardening_tools_refuse_without_boolean_confirm(server, name, args, confirm):
    call = {"base_url": BASE, **args}
    if confirm is not None:
        call["confirm"] = confirm
    fake, g, p = stateful()
    with g, p, pytest.raises(ToolFailureError):
        asyncio.run(server._dispatch_tool(name, call))
    assert not fake.calls


@pytest.mark.parametrize("name,args", [
    ("synology_terminal_set", {}),
    ("synology_terminal_set", {"enable_telnet": "false"}),
    ("synology_autoblock_set", {}),
    ("synology_autoblock_allow_add", {"entries": []}),
    ("synology_autoblock_allow_add", {"entries": "192.168.0.5"}),
    ("synology_https_redirect_set", {"enable": "true"}),
])
def test_hardening_tools_refuse_bad_arguments(server, name, args):
    fake, g, p = stateful()
    with g, p, pytest.raises(ToolFailureError):
        asyncio.run(server._dispatch_tool(name, {"base_url": BASE, "confirm": True, **args}))
    assert not fake.calls


def test_terminal_set_through_the_server(server):
    fake, g, p = stateful()
    with g, p:
        content = asyncio.run(server._dispatch_tool("synology_terminal_set", {"base_url": BASE, "confirm": True, "enable_telnet": False}))
    assert json.loads(content[0].text)["after"]["telnet_enabled"] is False


def test_install_refuses_undeclared_arguments(server):
    fake, g, p = dsm(install_routes())
    with g, p, pytest.raises(ToolFailureError):
        asyncio.run(server._dispatch_tool("synology_dsm_update_install", {"base_url": BASE, "confirm": True, "force": True}))
    assert not fake.calls
