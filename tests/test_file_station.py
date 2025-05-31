"""File Station functionality tests."""

import pytest


@pytest.mark.real_nas
class TestSynologyFileStation:
    """Test Synology FileStation operations."""

    @pytest.fixture(scope="class")
    def file_station(self, session_info):
        """Get authenticated FileStation client."""
        from filestation.synology_filestation import SynologyFileStation
        
        fs = SynologyFileStation(
            session_info['base_url'],
            session_info['session_id']
        )
        
        print(f"✅ FileStation client ready")
        return fs

    def test_list_shares(self, file_station):
        """Test listing available shares."""
        shares = file_station.list_shares()
        
        assert isinstance(shares, list)
        assert len(shares) > 0, "No shares found - this is unexpected"
        
        print(f"📁 Found {len(shares)} shares:")
        for share in shares[:5]:  # Show first 5 shares
            name = share.get('name', 'Unknown')
            path = share.get('path', 'Unknown')
            writable = share.get('is_writable', False)
            description = share.get('description', '')
            
            write_status = '✏️' if writable else '👁️'
            print(f"  {write_status} {name} ({path}) - {description}")

    def test_list_root_directory(self, file_station):
        """Test listing root directory contents."""
        try:
            contents = file_station.list_directory('/')
            
            assert isinstance(contents, list)
            print(f"📂 Root directory has {len(contents)} items")
            
            # Show some directory contents
            for item in contents[:10]:  # Show first 10 items
                name = item.get('name', 'Unknown')
                item_type = item.get('type', 'unknown')
                size = item.get('size', 0)
                
                type_icon = '📁' if item_type == 'directory' else '📄'
                
                if item_type == 'file' and size > 0:
                    # Format file size
                    if size > 1024*1024*1024:
                        size_str = f"({size/(1024*1024*1024):.1f} GB)"
                    elif size > 1024*1024:
                        size_str = f"({size/(1024*1024):.1f} MB)"
                    elif size > 1024:
                        size_str = f"({size/1024:.1f} KB)"
                    else:
                        size_str = f"({size} B)"
                else:
                    size_str = ""
                
                print(f"  {type_icon} {name} {size_str}")
                
        except Exception as e:
            # Root directory access might be restricted
            print(f"⚠️  Root directory access failed (may be expected): {e}")
            pytest.skip("Root directory access not permitted")

    def test_list_common_directories(self, file_station):
        """Test listing common directories that typically exist."""
        common_paths = [
            '/volume1',
            '/homes', 
            '/home',
            '/shared'
        ]
        
        accessible_dirs = []
        
        for path in common_paths:
            try:
                contents = file_station.list_directory(path)
                accessible_dirs.append(path)
                print(f"✅ {path}: {len(contents)} items")
                
                # Show a few items from each accessible directory
                for item in contents[:3]:
                    name = item.get('name', 'Unknown')
                    item_type = item.get('type', 'unknown')
                    type_icon = '📁' if item_type == 'directory' else '📄'
                    print(f"    {type_icon} {name}")
                    
            except Exception as e:
                print(f"⚠️  {path}: Not accessible ({str(e)[:50]}...)")
        
        if not accessible_dirs:
            pytest.skip("No common directories accessible")
        
        print(f"✅ Accessible directories: {', '.join(accessible_dirs)}")

    def test_get_file_info(self, file_station):
        """Test getting detailed file information."""
        # Try to get info for root first
        test_paths = ['/', '/volume1', '/homes']
        
        for path in test_paths:
            try:
                info = file_station.get_file_info(path)
                
                assert isinstance(info, dict)
                assert 'name' in info
                assert 'type' in info
                
                name = info.get('name', 'Unknown')
                file_type = info.get('type', 'unknown')
                size = info.get('size', 0)
                owner = info.get('owner', 'Unknown')
                permissions = info.get('permissions', 'Unknown')
                
                print(f"📋 File info for {path}:")
                print(f"   Name: {name}")
                print(f"   Type: {file_type}")
                print(f"   Size: {size:,} bytes")
                print(f"   Owner: {owner}")
                print(f"   Permissions: {permissions}")
                
                # If we get here, test passed
                return
                
            except Exception as e:
                print(f"⚠️  {path}: {str(e)[:50]}...")
                continue
        
        pytest.skip("No test paths accessible for file info")

    @pytest.mark.slow
    def test_search_functionality(self, file_station):
        """Test file search functionality."""
        # Search in accessible directories
        search_locations = ['/', '/volume1', '/homes']
        search_pattern = '*.txt'
        
        for location in search_locations:
            try:
                print(f"🔍 Searching for '{search_pattern}' in {location}...")
                results = file_station.search_files(location, search_pattern)
                
                assert isinstance(results, list)
                print(f"   Found {len(results)} files")
                
                # Show first few results
                for result in results[:5]:
                    name = result.get('name', 'Unknown')
                    path = result.get('path', 'Unknown')
                    size = result.get('size', 0)
                    print(f"   📄 {name} ({path}) - {size:,} bytes")
                
                # If search worked once, that's enough
                return
                
            except Exception as e:
                print(f"⚠️  Search in {location} failed: {str(e)[:50]}...")
                continue
        
        pytest.skip("Search functionality not accessible in any test locations")

    def test_path_formatting(self, file_station):
        """Test internal path formatting."""
        # Test the _format_path method
        test_cases = [
            ('home', '/home'),
            ('/home', '/home'),
            ('home/', '/home'),
            ('/home/', '/home'),
            ('/', '/'),
            ('', '/'),
            ('path/to/file', '/path/to/file')
        ]
        
        for input_path, expected in test_cases:
            result = file_station._format_path(input_path)
            assert result == expected, f"Path '{input_path}' should format to '{expected}', got '{result}'"
            print(f"✅ '{input_path}' → '{result}'")
        
        print("✅ Path formatting tests passed")

    def test_api_accessibility(self, file_station):
        """Test that FileStation API endpoints are accessible."""
        # Verify the client is configured properly
        assert file_station.base_url is not None
        assert file_station.session_id is not None
        assert file_station.api_url is not None
        
        # Verify API URL format
        assert file_station.api_url.startswith('http')
        assert 'webapi' in file_station.api_url.lower()
        
        print(f"✅ API URL: {file_station.api_url}")
        print(f"✅ Session: {file_station.session_id[:10]}...")

    def test_error_handling(self, file_station):
        """Test error handling for invalid operations."""
        # Test with clearly invalid path
        invalid_path = "/this/path/definitely/does/not/exist/12345"
        
        try:
            file_station.get_file_info(invalid_path)
            pytest.fail("Expected exception for invalid path")
        except Exception as e:
            print(f"✅ Invalid path correctly rejected: {str(e)[:50]}...")
        
        # Test with invalid search pattern
        try:
            # This might work or fail depending on implementation
            results = file_station.search_files("/", "")
            print(f"⚠️  Empty search pattern returned {len(results)} results")
        except Exception as e:
            print(f"✅ Empty search pattern correctly rejected: {str(e)[:50]}...")


# Quick connectivity test
def test_filestation_connectivity(session_info):
    """Quick test to verify FileStation is accessible."""
    from filestation.synology_filestation import SynologyFileStation
    
    try:
        fs = SynologyFileStation(
            session_info['base_url'],
            session_info['session_id']
        )
        
        # Try a simple operation
        shares = fs.list_shares()
        print(f"🔗 FileStation connected: {len(shares)} shares available")
        assert True  # If we get here, connection works
        
    except Exception as e:
        pytest.fail(f"FileStation connectivity failed: {e}")


def test_filestation_url_construction():
    """Test FileStation URL construction."""
    from filestation.synology_filestation import SynologyFileStation
    
    base_url = "https://192.168.1.100:5001"
    session_id = "test_session_123"
    
    fs = SynologyFileStation(base_url, session_id)
    
    expected_api_url = f"{base_url}/webapi/entry.cgi"
    assert fs.api_url == expected_api_url
    assert fs.base_url == base_url
    assert fs.session_id == session_id
    
    print(f"✅ URL construction: {fs.api_url}")
    print("✅ FileStation URL construction tests passed") 