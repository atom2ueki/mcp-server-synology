"""Pytest configuration for real Synology Download Station testing."""

import sys
from pathlib import Path

import pytest

# Add src directory to Python path
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

# Import our modules
from auth.synology_auth import SynologyAuth
from config import config
from downloadstation.synology_downloadstation import SynologyDownloadStation


@pytest.fixture(scope="session")
def env_check():
    """Check environment setup before running tests."""
    if not config.has_synology_credentials():
        pytest.skip(
            "❌ Missing Synology credentials in .env file. Required: SYNOLOGY_URL, SYNOLOGY_USERNAME, SYNOLOGY_PASSWORD"
        )

    # Validate configuration
    errors = config.validate_config()
    if errors:
        pytest.fail(f"❌ Configuration errors: {', '.join(errors)}")

    print(f"\n✅ Environment ready: {config.synology_url}")
    return True


@pytest.fixture(scope="session")
def synology_auth(env_check):
    """Authenticate with Synology and return auth instance."""
    print(f"🔐 Authenticating with {config.synology_url}...")

    auth = SynologyAuth(config.synology_url)

    # Attempt login to Download Station
    try:
        result = auth.login_download_station(config.synology_username, config.synology_password)

        if not result.get("success"):
            pytest.fail(f"❌ Download Station login failed: {result}")

        print(f"✅ Authenticated as {config.synology_username}")
        return auth

    except Exception as e:
        pytest.fail(f"❌ Authentication error: {e}")


@pytest.fixture(scope="session")
def session_info(synology_auth):
    """Get session information from successful authentication."""
    # Re-login to get fresh session data
    result = synology_auth.login_download_station(
        config.synology_username, config.synology_password
    )

    if not result.get("success"):
        pytest.fail(f"❌ Failed to get session info: {result}")

    session_data = {
        "base_url": config.synology_url,
        "session_id": result["data"]["sid"],
        "auth": synology_auth,
    }

    print(f"✅ Session ID: {session_data['session_id'][:10]}...")

    yield session_data

    # Cleanup: logout when tests complete
    try:
        # Use the improved logout method - it will use current session info automatically
        logout_result = synology_auth.logout()

        if logout_result.get("success"):
            print("✅ Logged out successfully")
        else:
            error_info = logout_result.get("error", {})
            error_code = error_info.get("code", "unknown")
            error_msg = error_info.get("message", "Unknown error")

            # Handle expected session expiration gracefully
            if error_code in ["105", "106", "no_session"]:
                print("⚠️  Session already expired or invalid (this is normal)")
            else:
                print(f"⚠️  Logout failed: {error_code} - {error_msg}")

    except Exception as e:
        print(f"⚠️  Logout exception: {e}")


@pytest.fixture
def download_station(session_info):
    """Get working Download Station client."""
    ds = SynologyDownloadStation(session_info["base_url"], session_info["session_id"])

    # Quick connectivity test
    try:
        info = ds.get_info()
        print(f"✅ Download Station ready: {info.get('version_string', 'Connected')}")
        return ds
    except Exception as e:
        pytest.fail(f"❌ Download Station not accessible: {e}")


@pytest.fixture
def sample_download_url():
    """Provide a safe test download URL."""
    # Small, legitimate test file (Ubuntu torrent)
    return "https://releases.ubuntu.com/22.04/ubuntu-22.04.3-desktop-amd64.iso.torrent"


@pytest.fixture
def test_destination(download_station):
    """Get a valid test destination folder."""
    # Check common destinations and return the first one that exists
    common_destinations = download_station.get_common_destinations()

    for dest in common_destinations:
        if download_station._check_destination_exists(dest):
            print(f"✅ Using test destination: {dest}")
            return dest

    # If no common destinations exist, use 'downloads' and hope for the best
    print("⚠️  No common destinations found, using 'downloads'")
    return "downloads"


# Test markers for categorizing tests
def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line("markers", "real_nas: tests that require real NAS connection")
    config.addinivalue_line("markers", "destructive: tests that modify NAS state")
    config.addinivalue_line("markers", "slow: tests that may take a long time")


# Custom test collection - only run if credentials are available
def pytest_collection_modifyitems(config, items):
    """Modify test collection based on environment.

    Only skip tests marked as 'real_nas' when no credentials are available.
    Unit tests that don't require a NAS will still run.
    """
    # Check if we have credentials before collecting tests
    # Note: config here is pytest's config object, not our global config
    from config import config as synology_config

    if not synology_config.has_synology_credentials():
        for item in items:
            # Only skip tests that require real NAS connection
            if "real_nas" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="No Synology credentials configured"))


# Helpful output
def pytest_sessionstart(session):
    """Print helpful information at test session start."""
    print("\n" + "=" * 60)
    print("🏠 SYNOLOGY MCP SERVER INTEGRATION TESTS")
    print("=" * 60)

    if config.has_synology_credentials():
        print(f"📡 Target NAS: {config.synology_url}")
        print(f"👤 Username: {config.synology_username}")
        print(f"🔒 SSL Verify: {config.verify_ssl}")
    else:
        print("❌ No credentials found - tests will be skipped")
        print("💡 Create .env file with: SYNOLOGY_URL, SYNOLOGY_USERNAME, SYNOLOGY_PASSWORD")

    print("=" * 60)


def pytest_sessionfinish(session, exitstatus):
    """Print summary at test session end."""
    print("\n" + "=" * 60)
    if exitstatus == 0:
        print("✅ All tests completed successfully!")
    else:
        print("⚠️  Some tests failed - check output above")
    print("=" * 60)
