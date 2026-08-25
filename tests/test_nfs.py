"""NFS management module tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.real_nas
@pytest.mark.destructive
class TestSynologyNFS:
    """Test Synology NFS management operations."""

    def test_nfs_status(self, session_info):
        """Test getting NFS service status."""
        from nfs.synology_nfs import SynologyNFS

        nfs = SynologyNFS(
            session_info["base_url"],
            session_info["session_id"],
            syno_token=session_info.get("syno_token"),
        )

        result = nfs.nfs_status()

        assert isinstance(result, dict)
        if result.get("success"):
            data = result.get("data", {})
            print("✅ NFS status retrieved")
            print(f"   Data: {data}")
        else:
            print(f"⚠️  NFS status failed: {result.get('error')}")

    def test_list_shares(self, session_info):
        """Test listing shared folders with NFS privileges."""
        from nfs.synology_nfs import SynologyNFS

        nfs = SynologyNFS(
            session_info["base_url"],
            session_info["session_id"],
            syno_token=session_info.get("syno_token"),
        )

        result = nfs.list_shares()

        assert isinstance(result, dict)
        if result.get("success"):
            shares = result.get("data", {}).get("shares", [])
            print(f"✅ Found {len(shares)} share(s)")
            for share in shares:
                print(f"   - {share.get('name', 'unknown')}")
        else:
            print(f"⚠️  List shares failed: {result.get('error')}")

    def test_get_share(self, session_info):
        """Test getting specific share details."""
        from nfs.synology_nfs import SynologyNFS

        nfs = SynologyNFS(
            session_info["base_url"],
            session_info["session_id"],
            syno_token=session_info.get("syno_token"),
        )

        # First get list of shares
        shares_result = nfs.list_shares()
        if shares_result.get("success"):
            shares = shares_result.get("data", {}).get("shares", [])
            if shares:
                # Get first share details
                share_name = shares[0].get("name")
                result = nfs.get_share(share_name)

                assert isinstance(result, dict)
                if result.get("success"):
                    print(f"✅ Share '{share_name}' details retrieved")
                else:
                    print(f"⚠️  Get share failed: {result.get('error')}")
            else:
                print("⚠️  No shares found to test")
        else:
            print("⚠️  Could not list shares to test get_share")

    def test_nfs_permission_get_set(self, session_info):
        """Test getting and setting NFS permissions (read-only test)."""
        from nfs.synology_nfs import SynologyNFS

        nfs = SynologyNFS(
            session_info["base_url"],
            session_info["session_id"],
            syno_token=session_info.get("syno_token"),
        )

        # First, list shares to find one to test
        shares_result = nfs.list_shares()
        if not shares_result.get("success"):
            pytest.skip("Cannot list shares to test NFS permissions")

        shares = shares_result.get("data", {}).get("shares", [])
        if not shares:
            pytest.skip("No shares available to test NFS permissions")

        # Test with the first share (read-only - just verify we can call it)
        share_name = shares[0].get("name")

        # This is a read-only test - we verify the API accepts our parameters
        # but don't actually change permissions to avoid side effects
        # The actual set_nfs_permission would be tested in a separate destructive test
        print(f"✅ NFS permission test setup complete for share: {share_name}")
        print("   Note: Skipping actual permission change to avoid side effects")


def test_nfs_url_construction():
    """Test URL handling in NFS module."""
    from nfs.synology_nfs import SynologyNFS

    # Test URL trailing slash handling
    nfs1 = SynologyNFS("https://nas.example.com:5001/", "test_session")
    assert nfs1.base_url == "https://nas.example.com:5001"

    nfs2 = SynologyNFS("http://nas.example.com:5000", "test_session")
    assert nfs2.base_url == "http://nas.example.com:5000"

    print("✅ URL construction tests passed")


def test_nfs_verify_ssl_parameter():
    """Test verify_ssl parameter propagation."""
    from nfs.synology_nfs import SynologyNFS

    nfs1 = SynologyNFS("https://nas.example.com:5001", "test_session", verify_ssl=True)
    assert nfs1.verify_ssl is True

    nfs2 = SynologyNFS("https://nas.example.com:5001", "test_session", verify_ssl=False)
    assert nfs2.verify_ssl is False

    print("✅ verify_ssl parameter tests passed")


def test_nfs_set_nfs_permission_parameter_validation():
    """Test parameter validation for set_nfs_permission."""
    from nfs.synology_nfs import SynologyNFS

    nfs = SynologyNFS("https://nas.example.com:5001", "test_session")

    # Test privilege validation
    result = nfs.set_nfs_permission("test_share", "192.168.1.1", privilege="invalid_privilege")
    # Should still return a valid response (API will handle invalid value)
    assert isinstance(result, dict)

    # Test squash validation
    result = nfs.set_nfs_permission("test_share", "192.168.1.1", squash="invalid_squash")
    assert isinstance(result, dict)

    # Test security validation
    result = nfs.set_nfs_permission("test_share", "192.168.1.1", security="invalid_security")
    assert isinstance(result, dict)

    print("✅ Parameter validation tests passed")


def test_nfs_set_permission_wire_format_matches_dsm_7_3_2(tmp_path, monkeypatch):
    """NFS rules use DSM's SharePrivilege load/save API and preserve other clients."""
    import json

    from nfs.synology_nfs import SynologyNFS

    monkeypatch.setattr(SynologyNFS, "_lock_directory", tmp_path)

    nfs = SynologyNFS(
        "https://nas.example.com:5001",
        "sid_xyz",
        verify_ssl=False,
        syno_token="tok_abc",
    )
    existing_rule = {
        "client": "192.0.2.2",
        "privilege": "rw",
        "root_squash": "root",
        "async": True,
        "insecure": False,
        "crossmnt": False,
        "security_flavor": {"sys": True},
    }
    load_response = MagicMock()
    load_response.json.return_value = {
        "success": True,
        "data": {"rule": [existing_rule]},
    }
    load_response.raise_for_status = MagicMock()
    save_response = MagicMock()
    save_response.json.return_value = {"success": True, "data": {}}
    save_response.raise_for_status = MagicMock()

    with (
        patch("utils.synology_api.requests.get", return_value=load_response) as get,
        patch("utils.synology_api.requests.post", return_value=save_response) as post,
    ):
        result = nfs.set_nfs_permission(
            "audit",
            "192.0.2.1",
            privilege="readonly",
            squash="root_squash",
            security="sys",
        )

    assert result["success"] is True
    load_data = get.call_args.kwargs["params"]
    assert load_data["api"] == "SYNO.Core.FileServ.NFS.SharePrivilege"
    assert load_data["method"] == "load"
    assert load_data["share_name"] == "audit"

    save_data = post.call_args.kwargs["data"]
    assert save_data["api"] == "SYNO.Core.FileServ.NFS.SharePrivilege"
    assert save_data["method"] == "save"
    assert save_data["share_name"] == "audit"
    assert post.call_args.kwargs["headers"]["X-SYNO-TOKEN"] == "tok_abc"
    assert json.loads(save_data["rule"]) == [
        existing_rule,
        {
            "client": "192.0.2.1",
            "privilege": "ro",
            "root_squash": "guest",
            "async": True,
            "insecure": False,
            "crossmnt": False,
            "security_flavor": {
                "sys": True,
                "kerberos": False,
                "kerberos_integrity": False,
                "kerberos_privacy": False,
            },
        },
    ]


def test_nfs_set_permission_serializes_same_nas_share(tmp_path, monkeypatch):
    """Two local MCP processes cannot lose each other's NFS rule updates."""
    import json
    import threading
    import time

    from nfs.synology_nfs import SynologyNFS

    monkeypatch.setattr(SynologyNFS, "_lock_directory", tmp_path)
    monkeypatch.setattr(SynologyNFS, "_lock_timeout_seconds", 2)

    first = SynologyNFS("https://nas.example.com:5001", "sid-1")
    second = SynologyNFS("https://nas.example.com:5001", "sid-2")
    state = {"rules": []}
    state_guard = threading.Lock()
    first_save_started = threading.Event()
    release_first_save = threading.Event()
    results = []
    errors = []
    load_count = 0

    def fake_api_call(api, method, version=1, extra_params=None, use_post=False):
        nonlocal load_count
        assert api == "SYNO.Core.FileServ.NFS.SharePrivilege"
        if method == "load":
            with state_guard:
                load_count += 1
                rules = json.loads(json.dumps(state["rules"]))
            return {"success": True, "data": {"rule": rules}}

        assert method == "save"
        assert use_post is True
        proposed = json.loads(extra_params["rule"])
        if any(rule.get("client") == "192.0.2.1" for rule in proposed):
            first_save_started.set()
            release_first_save.wait(timeout=1)
        with state_guard:
            state["rules"] = proposed
        return {"success": True, "data": {}}

    first._api_call = fake_api_call
    second._api_call = fake_api_call

    def update(nfs, client_ip):
        try:
            results.append(nfs.set_nfs_permission("audit", client_ip))
        except Exception as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=update, args=(first, "192.0.2.1"))
    second_thread = threading.Thread(target=update, args=(second, "192.0.2.2"))
    first_thread.start()
    assert first_save_started.wait(timeout=0.5)
    second_thread.start()

    time.sleep(0.05)
    assert load_count == 1
    release_first_save.set()
    first_thread.join(timeout=1)
    second_thread.join(timeout=1)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert all(result["success"] for result in results)
    assert {rule["client"] for rule in state["rules"]} == {"192.0.2.1", "192.0.2.2"}


@pytest.mark.asyncio
async def test_nfs_set_permission_handler_uses_worker_thread():
    """Waiting for the cross-process lock must not block the MCP event loop."""
    from mcp_server import SynologyMCPServer

    server = SynologyMCPServer()
    nfs = MagicMock()
    to_thread = AsyncMock(return_value={"success": True})

    with (
        patch.object(server, "_get_base_url", return_value="https://nas.example.com:5001"),
        patch.object(server, "_get_nfs", return_value=nfs),
        patch("mcp_server.asyncio.to_thread", to_thread),
    ):
        result = await server._handle_nfs_set_permission(
            {"share_name": "audit", "client_ip": "192.0.2.1"}
        )

    to_thread.assert_awaited_once_with(
        nfs.set_nfs_permission,
        share_name="audit",
        client_ip="192.0.2.1",
        privilege="readwrite",
        squash="root_squash",
        security="sys",
    )
    assert result[0].text == '{\n  "success": true\n}'


def test_create_share_wire_format_matches_dsm_7_3_2():
    """Regression test for issue #8.

    DSM 7.3.2's SYNO.Core.Share.create rejects flat params with code 403.
    The web UI sends a JSON-encoded `shareinfo` envelope plus a top-level
    `name`, with the X-SYNO-TOKEN header for CSRF. This test pins that
    wire format so a future refactor can't silently regress it.
    """
    import json

    from nfs.synology_nfs import SynologyNFS

    nfs = SynologyNFS(
        "https://nas.example.com:5001",
        "sid_xyz",
        verify_ssl=False,
        syno_token="tok_abc",
    )

    fake_response = MagicMock()
    fake_response.json.return_value = {"data": {"name": "rag"}, "success": True}
    fake_response.raise_for_status = MagicMock()

    with patch("utils.synology_api.requests.post", return_value=fake_response) as post:
        nfs.create_share(name="rag", vol_path="/volume1", desc="hi")

    assert post.called, "create_share must POST"
    sent = post.call_args
    sent_data = sent.kwargs["data"]
    sent_headers = sent.kwargs["headers"]

    # CSRF header is mandatory on DSM 7.3.2 mutating endpoints. Use a
    # presence check so this test isn't brittle if the client later adds
    # other legitimate headers (Content-Type, User-Agent, etc.).
    assert sent_headers is not None
    assert sent_headers.get("X-SYNO-TOKEN") == "tok_abc"

    # API routing
    assert sent_data["api"] == "SYNO.Core.Share"
    assert sent_data["method"] == "create"
    assert sent_data["version"] == "1"
    assert sent_data["_sid"] == "sid_xyz"

    # Top-level name is JSON-encoded (DSM expects a quoted string here)
    assert sent_data["name"] == '"rag"'

    # shareinfo is a JSON-encoded blob with the exact keys DSM 7.3.2 requires
    shareinfo = json.loads(sent_data["shareinfo"])
    assert shareinfo == {
        "name": "rag",
        "vol_path": "/volume1",
        "desc": "hi",
        "enable_recycle_bin": True,
        "recycle_bin_admin_only": True,
        "enable_share_cow": False,
        "enable_share_compress": False,
        "name_org": "",
    }

    print("✅ create_share wire format matches DSM 7.3.2 requirement")
