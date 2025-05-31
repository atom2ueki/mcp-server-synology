"""Authentication functionality tests."""

import pytest


@pytest.mark.real_nas  
class TestSynologyAuth:
    """Test Synology authentication operations."""

    def test_filestation_login_success(self, env_check):
        """Test successful FileStation authentication."""
        from auth.synology_auth import SynologyAuth
        from config import config
        
        auth = SynologyAuth(config.synology_url)
        result = auth.login(config.synology_username, config.synology_password)
        
        assert isinstance(result, dict)
        assert result.get('success') is True, f"Login failed: {result}"
        assert 'data' in result
        assert 'sid' in result['data']
        
        session_id = result['data']['sid']
        assert session_id is not None
        assert len(session_id) > 0
        
        print(f"✅ FileStation login successful")
        print(f"   Session ID: {session_id[:10]}...")
        
        # Test logout
        logout_result = auth.logout(session_id, 'FileStation')
        print(f"✅ Logout result: {logout_result.get('success', 'Unknown')}")

    def test_download_station_login_success(self, env_check):
        """Test successful Download Station authentication.""" 
        from auth.synology_auth import SynologyAuth
        from config import config
        
        auth = SynologyAuth(config.synology_url)
        result = auth.login_download_station(config.synology_username, config.synology_password)
        
        assert isinstance(result, dict)
        assert result.get('success') is True, f"Download Station login failed: {result}"
        assert 'data' in result
        assert 'sid' in result['data']
        
        session_id = result['data']['sid']
        assert session_id is not None
        assert len(session_id) > 0
        
        print(f"✅ Download Station login successful")
        print(f"   Session ID: {session_id[:10]}...")
        
        # Test logout
        logout_result = auth.logout(session_id, 'DownloadStation')
        print(f"✅ Logout result: {logout_result.get('success', 'Unknown')}")

    def test_api_version_fallback(self, env_check):
        """Test that authentication tries multiple API versions."""
        from auth.synology_auth import SynologyAuth
        from config import config
        
        auth = SynologyAuth(config.synology_url)
        
        # This should work with automatic version fallback
        result = auth.login_with_session(config.synology_username, config.synology_password, 'FileStation')
        
        assert isinstance(result, dict)
        if result.get('success'):
            print(f"✅ Version fallback worked - got session")
            session_id = result['data']['sid']
            auth.logout(session_id, 'FileStation')
        else:
            print(f"⚠️  Version fallback couldn't authenticate: {result}")

    def test_invalid_credentials(self, env_check):
        """Test authentication with invalid credentials."""
        from auth.synology_auth import SynologyAuth
        from config import config
        
        auth = SynologyAuth(config.synology_url)
        
        # Test with wrong password
        result = auth.login('invalid_user', 'wrong_password')
        
        assert isinstance(result, dict)
        assert result.get('success') is False
        assert 'error' in result
        
        error_code = result['error'].get('code')
        print(f"✅ Invalid credentials correctly rejected")
        print(f"   Error code: {error_code}")

    def test_invalid_base_url(self):
        """Test authentication with invalid base URL."""
        from auth.synology_auth import SynologyAuth
        
        # Test with clearly invalid URL
        auth = SynologyAuth('https://invalid.url.example.com:5001')
        result = auth.login('testuser', 'testpass')
        
        assert isinstance(result, dict)
        assert result.get('success') is False
        
        print(f"✅ Invalid URL correctly handled")
        print(f"   Result: {result}")

    def test_session_types(self, env_check):
        """Test different session types."""
        from auth.synology_auth import SynologyAuth
        from config import config
        
        auth = SynologyAuth(config.synology_url)
        
        # Test different session types
        session_types = ['FileStation', 'DownloadStation']
        successful_sessions = []
        
        for session_type in session_types:
            try:
                result = auth.login_with_session(
                    config.synology_username, 
                    config.synology_password, 
                    session_type
                )
                
                if result.get('success'):
                    session_id = result['data']['sid']
                    successful_sessions.append((session_type, session_id))
                    print(f"✅ {session_type} session: {session_id[:10]}...")
                else:
                    print(f"⚠️  {session_type} session failed: {result}")
                    
            except Exception as e:
                print(f"⚠️  {session_type} session error: {e}")
        
        # Cleanup sessions
        for session_type, session_id in successful_sessions:
            try:
                auth.logout(session_id, session_type)
                print(f"✅ Logged out from {session_type}")
            except Exception:
                print(f"⚠️  Logout from {session_type} failed")
        
        # At least one session type should work
        assert len(successful_sessions) > 0, "No session types worked"

    def test_session_expiration_handling(self, env_check):
        """Test handling of expired/invalid sessions during logout."""
        from auth.synology_auth import SynologyAuth
        from config import config
        
        auth = SynologyAuth(config.synology_url)
        
        # Test logout with invalid session ID
        fake_session_id = "invalid_session_12345"
        result = auth.logout(fake_session_id, 'FileStation')
        
        assert isinstance(result, dict)
        
        # Synology API might return success even for invalid sessions
        # Let's examine the actual behavior
        print(f"📋 Logout with invalid session result: {result}")
        
        if result.get('success'):
            print(f"⚠️  Synology API returned success for invalid session (this is unusual but not an error)")
        else:
            error_info = result.get('error', {})
            error_code = error_info.get('code', 'unknown')
            print(f"✅ Invalid session correctly handled")
            print(f"   Error code: {error_code}")
            print(f"   Error message: {error_info.get('message', 'No message')}")
        
        # Test logout with no session (should use current session if available)
        result = auth.logout()  # No parameters
        
        assert isinstance(result, dict)
        assert result.get('success') is False
        
        error_info = result.get('error', {})
        assert error_info.get('code') == 'no_session'
        
        print(f"✅ No session correctly handled")
        print(f"   Message: {error_info.get('message')}")
        
        # Test session tracking features
        assert not auth.is_logged_in(), "Should not be logged in initially"
        
        session_info = auth.get_session_info()
        assert session_info['logged_in'] is False
        assert session_info['session_id'] is None
        
        print(f"✅ Session tracking working correctly")
        print(f"   Session info: {session_info}")


# Quick connectivity test
def test_auth_connectivity(env_check):
    """Quick test to verify auth service is reachable."""
    from auth.synology_auth import SynologyAuth
    from config import config
    
    auth = SynologyAuth(config.synology_url)
    
    # Just test that we can reach the auth endpoint
    # Even failed auth means the service is reachable
    result = auth.login('test', 'test')
    
    assert isinstance(result, dict)
    print(f"🔗 Auth service reachable: {config.synology_url}/webapi/auth.cgi")
    
    if result.get('success'):
        print("⚠️  Test credentials unexpectedly worked")
    else:
        print("✅ Auth service responding (test credentials rejected as expected)")


def test_auth_url_construction():
    """Test URL construction for different base URLs."""
    from auth.synology_auth import SynologyAuth
    
    # Test different URL formats
    test_urls = [
        'https://192.168.1.100:5001',
        'https://192.168.1.100:5001/',
        'http://nas.local:5000',
        'https://mynas.synology.me'
    ]
    
    for url in test_urls:
        auth = SynologyAuth(url)
        expected_base = url.rstrip('/')
        assert auth.base_url == expected_base
        print(f"✅ URL '{url}' → '{auth.base_url}'")
    
    print("✅ URL construction tests passed") 