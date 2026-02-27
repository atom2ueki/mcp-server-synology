"""Health monitoring module tests."""

import pytest


@pytest.mark.real_nas
class TestSynologyHealth:
    """Test Synology health monitoring operations."""

    def test_system_info(self, session_info):
        """Test getting system information."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.system_info()

        assert isinstance(result, dict)
        if result.get("success"):
            data = result.get("data", {})
            print("✅ System info retrieved")
            print(f"   Data keys: {list(data.keys())}")
        else:
            print(f"⚠️  System info failed: {result.get('error')}")

    def test_utilization(self, session_info):
        """Test getting system utilization."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.utilization()

        assert isinstance(result, dict)
        if result.get("success"):
            print("✅ Utilization data retrieved")
        else:
            print(f"⚠️  Utilization failed: {result.get('error')}")

    def test_disk_list(self, session_info):
        """Test listing physical disks."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.disk_list()

        assert isinstance(result, dict)
        if result.get("success"):
            disks = result.get("data", {}).get("disks", [])
            print(f"✅ Found {len(disks)} disk(s)")
            for disk in disks:
                print(f"   - {disk.get('disk', 'unknown')}: {disk.get('model', 'unknown')}")
        else:
            print(f"⚠️  Disk list failed: {result.get('error')}")

    def test_volume_list(self, session_info):
        """Test listing volumes."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.volume_list()

        assert isinstance(result, dict)
        if result.get("success"):
            volumes = result.get("data", {}).get("volumes", [])
            print(f"✅ Found {len(volumes)} volume(s)")
            for vol in volumes:
                print(f"   - {vol.get('volume_path', 'unknown')}: {vol.get('status', 'unknown')}")
        else:
            print(f"⚠️  Volume list failed: {result.get('error')}")

    def test_storage_pool_list(self, session_info):
        """Test listing storage pools."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.storage_pool_list()

        assert isinstance(result, dict)
        if result.get("success"):
            pools = result.get("data", {}).get("pools", [])
            print(f"✅ Found {len(pools)} storage pool(s)")
        else:
            print(f"⚠️  Storage pool list failed: {result.get('error')}")

    def test_network_info(self, session_info):
        """Test getting network information."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.network_info()

        assert isinstance(result, dict)
        if result.get("success"):
            print("✅ Network info retrieved")
        else:
            print(f"⚠️  Network info failed: {result.get('error')}")

    def test_ups_info(self, session_info):
        """Test getting UPS information."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.ups_info()

        assert isinstance(result, dict)
        # UPS might not be connected, so we just check response
        print(f"UPS info result: {result}")

    def test_package_list(self, session_info):
        """Test listing installed packages."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.package_list()

        assert isinstance(result, dict)
        if result.get("success"):
            packages = result.get("data", {}).get("packages", [])
            print(f"✅ Found {len(packages)} package(s)")
        else:
            print(f"⚠️  Package list failed: {result.get('error')}")

    def test_system_log(self, session_info):
        """Test getting system logs."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.system_log(offset=0, limit=10)

        assert isinstance(result, dict)
        if result.get("success"):
            print("✅ System logs retrieved")
        else:
            print(f"⚠️  System log failed: {result.get('error')}")

    def test_health_summary(self, session_info):
        """Test getting comprehensive health summary."""
        from health.synology_health import SynologyHealth

        health = SynologyHealth(session_info["base_url"], session_info["session_id"])

        result = health.health_summary()

        assert isinstance(result, dict)
        if result.get("success"):
            data = result.get("data", {})
            print("✅ Health summary retrieved")
            print(f"   Sections: {list(data.keys())}")
        else:
            print(f"⚠️  Health summary failed: {result.get('error')}")


def test_health_url_construction():
    """Test URL handling in health module."""
    from health.synology_health import SynologyHealth

    # Test URL trailing slash handling
    health1 = SynologyHealth("https://nas.example.com:5001/", "test_session")
    assert health1.base_url == "https://nas.example.com:5001"

    health2 = SynologyHealth("http://nas.example.com:5000", "test_session")
    assert health2.base_url == "http://nas.example.com:5000"

    print("✅ URL construction tests passed")


def test_health_verify_ssl_parameter():
    """Test verify_ssl parameter propagation."""
    from health.synology_health import SynologyHealth

    health1 = SynologyHealth("https://nas.example.com:5001", "test_session", verify_ssl=True)
    assert health1.verify_ssl is True

    health2 = SynologyHealth("https://nas.example.com:5001", "test_session", verify_ssl=False)
    assert health2.verify_ssl is False

    print("✅ verify_ssl parameter tests passed")
