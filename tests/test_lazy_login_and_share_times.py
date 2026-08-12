"""On-demand login and share timestamps.

Both cover defects found 2026-08-01 while trying to read NAS backup state
through this server:

  * With AUTO_LOGIN=false, nas_name_map was populated only by startup
    auto-login, so every nas_name call failed with "not found. Available: []".
    The only way in was synology_login, which takes the password as a visible
    tool argument — unusable from an agent whose transcript is logged.
  * list_shares dropped the timestamps DSM returns. A share's atime is the only
    externally visible proof that a scheduled job touched it, and Active Backup
    for Google Workspace has no task-state API at all, so dropping it forced
    callers out to hand-rolled DSM scripts.

No NAS required: auth and HTTP are mocked.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mcp_server import SynologyMCPServer  # noqa: E402
from filestation.synology_filestation import SynologyFileStation  # noqa: E402


def _ok_login(sid="sid-aaa"):
    return {"success": True, "data": {"sid": sid, "synotoken": "tok"}}


@pytest.fixture
def server():
    with patch("mcp_server.Server"):
        srv = SynologyMCPServer()
    # Startup auto-login never ran, which is the state AUTO_LOGIN=false leaves.
    assert srv.nas_name_map == {}
    return srv


class TestOnDemandLogin:
    def test_nas_name_logs_in_when_map_is_empty(self, server):
        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        auth.login.return_value = _ok_login()
        with patch("mcp_server.config.nas_configs", {"admin": cfg}), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            base = server._get_base_url({"nas_name": "admin"})
        assert base == "https://nas:5001"
        assert server.sessions[base] == "sid-aaa"
        assert auth.login.call_count == 1
        assert auth.login.call_args.args == ("u", "p")
        # 2FA material is forwarded, so a configured device_id/otp_code is not
        # silently dropped on this path the way it would be if the on-demand
        # login rolled its own call.
        assert "otp_code" in auth.login.call_args.kwargs
        assert "device_id" in auth.login.call_args.kwargs
        assert auth.on_relogin == server._resync_session_after_relogin

    def test_second_call_reuses_the_session(self, server):
        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        auth.login.return_value = _ok_login()
        with patch("mcp_server.config.nas_configs", {"admin": cfg}), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            server._get_base_url({"nas_name": "admin"})
            server._get_base_url({"nas_name": "admin"})
        assert auth.login.call_count == 1, "a cached session must not re-authenticate"

    def test_legacy_env_default_can_log_in_on_demand(self, server):
        """No settings.json entries: credentials come from the environment.

        _handle_list_nas surfaces that NAS as "default", so that name has to
        reach _login_nas(None) instead of being rejected as unconfigured.
        """
        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        auth.login.return_value = _ok_login("sid-legacy")
        with patch("mcp_server.config.nas_configs", {}), \
             patch("mcp_server.config.has_synology_credentials", return_value=True), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            base = server._get_base_url({"nas_name": "default"})
            assert base == "https://nas:5001"
            assert server.sessions[base] == "sid-legacy"
            # The bare URL is the other name _handle_list_nas can surface.
            server.sessions.clear()
            server.nas_name_map.clear()
            auth.login.return_value = _ok_login("sid-legacy-2")
            assert server._get_base_url({"nas_name": "https://nas:5001"}) == "https://nas:5001"

    def test_logout_never_authenticates(self, server):
        """Logging in purely to tear the session down can trip OTP failures,
        lockout counters and login audit events on an untouched NAS."""
        import asyncio

        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        with patch("mcp_server.config.nas_configs", {"admin": cfg}), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            # With an empty nas_name_map and allow_login=False, _get_base_url
            # raises for the unmapped name rather than touching the NAS.
            # Resolving it would trigger an on-demand login purely to tear the
            # session down, which we must not do.
            with pytest.raises(Exception, match="NAS 'admin' not found"):
                asyncio.run(server._handle_logout({"nas_name": "admin"}))
        auth.login.assert_not_called()

    def test_logging_in_again_after_a_logout(self, server):
        """nas_name_map outlives the session it was created for.

        Returning the mapped URL regardless would bypass the on-demand login and
        fail downstream with "No active session", making lazy login work exactly
        once per process.
        """
        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        auth.login.side_effect = [_ok_login("sid-1"), _ok_login("sid-2")]
        with patch("mcp_server.config.nas_configs", {"admin": cfg}), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            base = server._get_base_url({"nas_name": "admin"})
            # Simulate synology_logout: session gone, mapping left behind.
            del server.sessions[base]
            assert "admin" in server.nas_name_map
            server._get_base_url({"nas_name": "admin"})
        assert server.sessions[base] == "sid-2"
        assert auth.login.call_count == 2

    def test_reuses_a_session_established_by_synology_login(self, server):
        """synology_login fills self.sessions but not nas_name_map.

        Logging in again would overwrite the tracked SID and strand the first
        session: still open on the NAS, unreachable by logout.
        """
        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        server.sessions["https://nas:5001"] = "sid-manual"
        with patch("mcp_server.config.nas_configs", {"admin": cfg}), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            base = server._get_base_url({"nas_name": "admin"})
        assert base == "https://nas:5001"
        assert server.sessions[base] == "sid-manual"
        auth.login.assert_not_called()
        assert server.nas_name_map["admin"] == "https://nas:5001"

    def test_unknown_name_still_raises_rather_than_guessing(self, server):
        """An unconfigured name must not silently resolve to some other NAS."""
        with patch("mcp_server.config.nas_configs", {"admin": {}}), \
             patch("mcp_server.config.has_synology_credentials", return_value=False):
            with pytest.raises(Exception) as exc:
                server._get_base_url({"nas_name": "typo"})
        assert "typo" in str(exc.value)

    def test_login_failure_surfaces_the_dsm_code(self, server):
        """402 is what a DSM account without application access returns."""
        cfg = {"base_url": "https://nas:5001", "username": "u", "password": "p"}
        auth = MagicMock()
        auth.login.return_value = {"success": False, "error": {"code": 402}}
        with patch("mcp_server.config.nas_configs", {"default": cfg}), \
             patch("mcp_server.config.get_synology_config", return_value=cfg), \
             patch("mcp_server.SynologyAuth", return_value=auth):
            with pytest.raises(Exception) as exc:
                server._get_base_url({"nas_name": "default"})
        assert "402" in str(exc.value)
        # The message identifies the NAS by label, not by URL.
        assert "https://nas:5001" not in str(exc.value)
        assert "default" in str(exc.value)


class TestShareTimestamps:
    def _fs(self, payload):
        fs = SynologyFileStation("https://nas:5001", "sid", verify_ssl=False)
        fs._make_request = MagicMock(return_value=payload)
        return fs

    def test_iso_is_utc_and_offset_bearing(self):
        """A naive local string is read in whatever timezone the consumer assumes."""
        fs = self._fs({"shares": [{
            "name": "x", "path": "/x",
            "additional": {"time": {"atime": 0}},
        }]})
        assert fs.list_shares()[0]["atime_iso"] == "1970-01-01T00:00:00Z"

    def test_timestamps_are_returned_with_iso_forms(self):
        fs = self._fs({"shares": [{
            "name": "ActiveBackupForGSuite",
            "path": "/ActiveBackupForGSuite",
            "iswritable": True,
            "additional": {"time": {"atime": 1785654685, "mtime": 1778006460,
                                    "ctime": 1778006460, "crtime": 1778005903}},
        }]})
        share = fs.list_shares()[0]
        assert share["atime"] == 1785654685
        assert share["atime_iso"].startswith("2026-")
        for key in ("mtime", "ctime", "crtime"):
            assert key in share and f"{key}_iso" in share

    def test_time_additional_is_a_json_array(self):
        """DSM 7.3.2 silently ignores the comma-string form."""
        fs = self._fs({"shares": []})
        fs.list_shares()
        assert fs._make_request.call_args.kwargs["additional"] == '["time"]'

    def test_legacy_fields_survive(self):
        fs = self._fs({"shares": [{"name": "docker", "path": "/docker",
                                   "desc": "d", "iswritable": False}]})
        share = fs.list_shares()[0]
        assert share == {"name": "docker", "path": "/docker",
                         "description": "d", "is_writable": False}

    def test_missing_time_block_is_not_fatal(self):
        """Older DSM, or a share the account cannot stat, returns no additional."""
        fs = self._fs({"shares": [{"name": "x", "path": "/x"}]})
        share = fs.list_shares()[0]
        assert share["name"] == "x"
        assert "atime" not in share

    def test_nonsense_epoch_does_not_break_the_listing(self):
        fs = self._fs({"shares": [{"name": "x", "path": "/x",
                                   "additional": {"time": {"atime": 10 ** 20}}}]})
        share = fs.list_shares()[0]
        assert share["atime"] == 10 ** 20
        assert "atime_iso" not in share

    def test_non_numeric_epoch_does_not_break_the_listing(self):
        fs = self._fs({"shares": [{"name": "x", "path": "/x",
                                   "additional": {"time": {"atime": "not-an-epoch"}}}]})
        share = fs.list_shares()[0]
        assert share["atime"] == "not-an-epoch"
        assert "atime_iso" not in share
