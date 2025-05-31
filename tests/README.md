# Synology MCP Server Tests

Integration tests for each module of the Synology MCP Server.

## Quick Setup

1. **Copy environment file:**
   ```bash
   cp env.example .env
   ```

2. **Edit `.env` with your NAS details:**
   ```env
   SYNOLOGY_URL=https://your-nas-ip:5001
   SYNOLOGY_USERNAME=your-username  
   SYNOLOGY_PASSWORD=your-password
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run tests:**
   ```bash
   pytest
   ```

## Test Files

### `test_auth.py` - Authentication Tests
- ✅ **FileStation login/logout**
- ✅ **Download Station login/logout** 
- ✅ **API version fallback**
- ✅ **Invalid credentials handling**
- ✅ **Different session types**
- ✅ **URL construction**

### `test_download_station.py` - Download Station Tests  
- ✅ **Connection and info**
- ✅ **List current downloads**
- ✅ **Download statistics**
- ✅ **Destination validation**
- ⚠️ **Create downloads** (destructive)
- ✅ **Configuration**

### `test_file_station.py` - File Station Tests
- ✅ **List shares**
- ✅ **Directory listing**
- ✅ **File information**
- 🔍 **File search** (slow)
- ✅ **Path formatting**
- ✅ **Error handling**

## Test Commands

```bash
# Run all tests
pytest

# Run tests by module
pytest tests/test_auth.py
pytest tests/test_download_station.py
pytest tests/test_file_station.py

# Quick connectivity tests only
pytest -k "connectivity"

# Skip destructive tests (no download creation)
pytest -m "not destructive"

# Skip slow tests (no search operations)
pytest -m "not slow"

# Run with live output
pytest -s -v

# Run specific test
pytest tests/test_auth.py::TestSynologyAuth::test_filestation_login_success
```

## Expected Output

```
🏠 SYNOLOGY DOWNLOAD STATION INTEGRATION TESTS
============================================================
📡 Target NAS: https://192.168.1.100:5001
👤 Username: admin
🔒 SSL Verify: False
============================================================

tests/test_auth.py::test_auth_connectivity 
🔗 Auth service reachable: https://192.168.1.100:5001/webapi/auth.cgi
✅ Auth service responding (test credentials rejected as expected)
PASSED

tests/test_auth.py::TestSynologyAuth::test_filestation_login_success 
✅ FileStation login successful
   Session ID: 1a2b3c4d5e...
✅ Logout result: True
PASSED

tests/test_download_station.py::test_basic_connectivity 
🔗 Connected to Download Station: 3.8.16-3566
PASSED

tests/test_file_station.py::test_filestation_connectivity 
🔗 FileStation connected: 3 shares available
PASSED
```

## Troubleshooting

**"No credentials found"**
- Check `.env` file exists and has correct values
- Verify variable names match `env.example`

**"Authentication failed"**  
- Verify NAS IP/port is correct
- Check username/password
- Ensure services are enabled

**"FileStation/Download Station not accessible"**
- User may not have required permissions
- Services may be disabled in DSM
- Network connectivity issues

## Test Organization

Each test file focuses on a specific module:

- **Authentication** (`test_auth.py`) - Core login/logout functionality
- **Download Station** (`test_download_station.py`) - Torrent/download management  
- **File Station** (`test_file_station.py`) - File/directory operations

## Test Markers

- `@pytest.mark.real_nas` - Requires real NAS connection
- `@pytest.mark.destructive` - May create/modify data
- `@pytest.mark.slow` - Takes several seconds to complete

## Adding New Tests

When adding tests for new functionality:

1. Add to the appropriate test file by module
2. Use proper markers and error handling
3. Include helpful print statements for successful operations
4. Handle expected failures gracefully with `pytest.skip()`

Example:
```python
def test_new_feature(self, download_station):
    """Test description."""
    try:
        result = download_station.new_method()
        assert result is not None
        print("✅ New feature works")
    except Exception as e:
        if "permission" in str(e).lower():
            pytest.skip(f"Permission issue (expected): {e}")
        raise