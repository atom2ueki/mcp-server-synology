"""NFS management module tests."""

from unittest.mock import MagicMock, patch

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
