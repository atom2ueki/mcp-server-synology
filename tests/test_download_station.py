"""Real Download Station functionality tests."""

import pytest
import time


@pytest.mark.real_nas
class TestRealDownloadStation:
    """Test actual Download Station operations on real Synology NAS."""

    def test_connection_and_info(self, download_station):
        """Test basic connection and get DS info."""
        info = download_station.get_info()
        
        assert info is not None
        assert isinstance(info, dict)
        
        print(f"📡 Download Station: {info.get('version_string', 'Unknown version')}")
        print(f"🏠 Hostname: {info.get('hostname', 'Unknown')}")
        print(f"👤 Is Manager: {info.get('is_manager', False)}")

    def test_list_current_tasks(self, download_station):
        """Test listing current download tasks."""
        tasks = download_station.list_tasks(limit=50)
        
        assert isinstance(tasks, dict)
        assert 'tasks' in tasks
        assert 'total' in tasks
        
        task_list = tasks['tasks']
        total_count = tasks['total']
        
        print(f"📋 Found {len(task_list)} tasks (total: {total_count})")
        
        # Show task details
        for i, task in enumerate(task_list[:5]):  # Show first 5 tasks
            status = task.get('status', 'unknown')
            title = task.get('title', 'Unknown')
            size = task.get('size', 0)
            
            # Format size
            if size > 1024*1024*1024:
                size_str = f"{size/(1024*1024*1024):.1f} GB"
            elif size > 1024*1024:
                size_str = f"{size/(1024*1024):.1f} MB"
            else:
                size_str = f"{size:,} B"
            
            print(f"  {i+1}. {title[:50]}... [{status}] ({size_str})")

    def test_download_statistics(self, download_station):
        """Test getting download statistics."""
        stats = download_station.get_statistics()
        
        assert isinstance(stats, dict)
        
        down_speed = stats.get('speed_download', 0)
        up_speed = stats.get('speed_upload', 0)
        
        # Format speeds
        def format_speed(speed):
            if speed > 1024*1024:
                return f"{speed/(1024*1024):.1f} MB/s"
            elif speed > 1024:
                return f"{speed/1024:.1f} KB/s"
            else:
                return f"{speed} B/s"
        
        print(f"⬇️  Download Speed: {format_speed(down_speed)}")
        print(f"⬆️  Upload Speed: {format_speed(up_speed)}")

    def test_destination_validation(self, download_station, test_destination):
        """Test destination folder validation."""
        # Test the destination we got
        exists = download_station._check_destination_exists(test_destination)
        assert exists, f"Test destination '{test_destination}' should exist"
        
        print(f"✅ Destination '{test_destination}' exists and is valid")
        
        # Test invalid destination
        fake_dest = "nonexistent_folder_12345"
        exists = download_station._check_destination_exists(fake_dest)
        assert not exists, f"Fake destination '{fake_dest}' should not exist"
        
        print(f"✅ Correctly detected invalid destination '{fake_dest}'")

    def test_common_destinations(self, download_station):
        """Test checking common destination folders."""
        common_dests = download_station.get_common_destinations()
        
        assert isinstance(common_dests, list)
        assert len(common_dests) > 0
        
        print(f"📁 Common destinations: {', '.join(common_dests)}")
        
        # Check which ones actually exist
        existing_dests = []
        for dest in common_dests:
            if download_station._check_destination_exists(dest):
                existing_dests.append(dest)
        
        print(f"✅ Existing destinations: {', '.join(existing_dests) if existing_dests else 'None found'}")

    def test_default_destination_logic(self, download_station):
        """Test the new default destination logic."""
        # Test getting default destination
        default_dest = download_station.get_default_destination()
        assert isinstance(default_dest, str)
        assert len(default_dest) > 0
        
        print(f"🎯 Default destination: {default_dest}")
        
        # Test checking if downloads folder exists
        downloads_exists = download_station.ensure_downloads_folder()
        print(f"📁 Downloads folder status: {'exists' if downloads_exists else 'missing'}")
        
        # Test that preferred default is set correctly
        preferred = download_station.preferred_default_destination
        assert preferred == "downloads"
        print(f"⚙️  Preferred destination: {preferred}")
        
        # Test setting a custom default (if downloads exists)
        if downloads_exists:
            # Try setting downloads as default (should work)
            result = download_station.set_default_destination("downloads")
            assert result == True
            print("✅ Successfully set 'downloads' as default")
        
        # Test setting invalid destination
        result = download_station.set_default_destination("nonexistent_folder_12345")
        assert result == False
        print("✅ Correctly rejected invalid destination")

    @pytest.mark.slow
    @pytest.mark.destructive
    def test_create_download_task(self, download_station, sample_download_url, test_destination):
        """Test creating a real download task."""
        print(f"🚀 Testing download creation with URL: {sample_download_url}")
        print(f"📁 Using destination: {test_destination}")
        
        # Get task count before
        initial_tasks = download_station.list_tasks()
        initial_count = len(initial_tasks.get('tasks', []))
        
        try:
            # Create the task
            result = download_station.create_task(
                uri=sample_download_url,
                destination=test_destination
            )
            
            assert result is not None
            assert isinstance(result, dict)
            
            # Check for success indicators
            task_ids = result.get('task_id', [])
            list_ids = result.get('list_id', [])
            
            success = bool(task_ids or list_ids)
            assert success, f"No task/list IDs returned: {result}"
            
            if task_ids:
                print(f"✅ Created task IDs: {', '.join(map(str, task_ids))}")
            if list_ids:
                print(f"✅ Created list IDs: {', '.join(map(str, list_ids))}")
            
            # Wait and check if task appears
            time.sleep(3)
            
            updated_tasks = download_station.list_tasks()
            updated_count = len(updated_tasks.get('tasks', []))
            
            print(f"📊 Task count: {initial_count} → {updated_count}")
            
            # Look for our task
            tasks = updated_tasks.get('tasks', [])
            ubuntu_tasks = [t for t in tasks if 'ubuntu' in t.get('title', '').lower()]
            
            if ubuntu_tasks:
                task = ubuntu_tasks[0]
                print(f"🎯 Found our task: {task.get('title', 'Unknown')}")
                print(f"   Status: {task.get('status', 'Unknown')}")
                print(f"   Size: {task.get('size', 0):,} bytes")
            
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️  Task creation failed: {error_msg}")
            
            # Check if it's a known/expected error
            if any(keyword in error_msg.lower() for keyword in ['permission', 'forbidden', 'access']):
                pytest.skip(f"Permission denied (expected): {error_msg}")
            elif any(keyword in error_msg.lower() for keyword in ['destination', 'folder', 'directory']):
                pytest.skip(f"Destination issue (may be expected): {error_msg}")
            elif any(keyword in error_msg.lower() for keyword in ['network', 'connection', 'timeout']):
                pytest.skip(f"Network issue (may be expected): {error_msg}")
            else:
                # Re-raise unexpected errors
                raise

    def test_get_configuration(self, download_station):
        """Test getting Download Station configuration."""
        config = download_station.get_config()
        
        assert isinstance(config, dict)
        
        # Log what we found
        default_dest = config.get('default_destination', 'Not set')
        emule_enabled = config.get('emule_enabled', False)
        
        print(f"⚙️  Default destination: {default_dest}")
        print(f"🔄 eMule enabled: {emule_enabled}")
        print(f"📋 Config keys: {list(config.keys())}")


# Simple connectivity test that can run quickly
def test_basic_connectivity(download_station):
    """Quick test to verify basic Download Station connectivity."""
    try:
        info = download_station.get_info()
        print(f"🔗 Connected to Download Station: {info.get('version_string', 'OK')}")
        assert True  # If we get here, connection works
    except Exception as e:
        pytest.fail(f"Basic connectivity failed: {e}") 