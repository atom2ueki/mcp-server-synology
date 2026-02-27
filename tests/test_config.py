"""Configuration module tests."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


# Force reimport of config module to avoid cached global instance
def reload_config():
    """Reload the config module to get fresh state."""
    modules_to_remove = [k for k in sys.modules.keys() if k.startswith("config")]
    for mod in modules_to_remove:
        del sys.modules[mod]


class TestSynologyConfig:
    """Test Synology configuration loading and validation."""

    def test_env_fallback(self):
        """Test that .env values are used as fallback."""
        # Clear any cached config
        reload_config()

        with patch.dict(
            os.environ,
            {
                "SYNOLOGY_URL": "http://test.local:5000",
                "SYNOLOGY_USERNAME": "testuser",
                "SYNOLOGY_PASSWORD": "testpass",
            },
        ):
            with patch("config.SECRETS_FILE", Path("/nonexistent/secrets.json")):
                with patch.object(Path, "exists", return_value=False):
                    from config import SynologyConfig

                    config = SynologyConfig()

                    assert config.synology_url == "http://test.local:5000"
                    assert config.synology_username == "testuser"
                    assert config.synology_password == "testpass"

    def test_default_values(self):
        """Test default configuration values."""
        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", Path("/nonexistent/secrets.json")):
                with patch.object(Path, "exists", return_value=False):
                    from config import SynologyConfig

                    config = SynologyConfig()

                    assert config.server_name == "synology-mcp-server"
                    assert config.server_version == "1.0.0"
                    assert config.default_session_timeout == 3600
                    assert config.auto_login is True
                    assert config.verify_ssl is False

    def test_has_credentials_with_secrets(self, tmp_path):
        """Test credential detection with secrets.json."""
        secrets_data = {
            "synology": {
                "test_nas": {
                    "host": "192.168.1.100",
                    "port": 5000,
                    "username": "admin",
                    "password": "pass123",
                }
            }
        }

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                assert cfg.has_synology_credentials() is True
                assert "test_nas" in cfg.nas_configs
                assert cfg.nas_configs["test_nas"]["base_url"] == "http://192.168.1.100:5000"

    def test_get_nas_names(self, tmp_path):
        """Test getting NAS names from secrets.json."""
        secrets_data = {
            "synology": {
                "nas1": {"host": "192.168.1.1", "port": 5000, "username": "a", "password": "b"},
                "nas2": {"host": "192.168.1.2", "port": 5001, "username": "c", "password": "d"},
            }
        }

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                names = cfg.get_nas_names()
                assert len(names) == 2
                assert "nas1" in names
                assert "nas2" in names

    def test_get_synology_config_with_nas_name(self, tmp_path):
        """Test getting config for specific NAS."""
        secrets_data = {
            "synology": {
                "primary": {
                    "host": "192.168.1.100",
                    "port": 5001,
                    "username": "admin",
                    "password": "secret",
                }
            }
        }

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                specific = cfg.get_synology_config("primary")
                assert specific["base_url"] == "https://192.168.1.100:5001"
                assert specific["username"] == "admin"

    def test_validate_config_no_credentials(self):
        """Test validation fails with no credentials."""
        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", Path("/nonexistent/secrets.json")):
                with patch.object(Path, "exists", return_value=False):
                    from config import SynologyConfig

                    cfg = SynologyConfig()

                    errors = cfg.validate_config()
                    assert len(errors) > 0
                    assert "No Synology credentials" in errors[0]

    def test_validate_config_timeout_too_low(self):
        """Test validation fails with low timeout."""
        reload_config()

        with patch.dict(
            os.environ,
            {
                "SYNOLOGY_URL": "http://test.local:5000",
                "SYNOLOGY_USERNAME": "user",
                "SYNOLOGY_PASSWORD": "pass",
                "SESSION_TIMEOUT": "30",
            },
            clear=False,
        ):
            with patch("config.SECRETS_FILE", Path("/nonexistent/secrets.json")):
                with patch.object(Path, "exists", return_value=False):
                    from config import SynologyConfig

                    cfg = SynologyConfig()
                    errors = cfg.validate_config()

                    assert any("SESSION_TIMEOUT" in e for e in errors)

    def test_missing_required_fields_in_secrets(self, tmp_path, capsys):
        """Test handling of missing required fields in secrets."""
        secrets_data = {
            "synology": {
                "incomplete_nas": {
                    "host": "192.168.1.100"
                    # missing username, password
                }
            }
        }

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                # Should not add incomplete NAS to configs
                assert (
                    "incomplete_nas" not in cfg.nas_configs
                    or cfg.nas_configs.get("incomplete_nas") is None
                )

    def test_invalid_json_in_secrets(self, tmp_path, capsys):
        """Test handling of invalid JSON in secrets file."""
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text("{ invalid json }")

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                # Should handle gracefully and not crash
                assert cfg.nas_configs == {}

    def test_resolve_base_url(self, tmp_path):
        """Test resolving base URL from NAS name."""
        secrets_data = {
            "synology": {
                "office_nas": {
                    "host": "office.example.com",
                    "port": 5000,
                    "username": "admin",
                    "password": "pass",
                }
            }
        }

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                url = cfg.resolve_base_url("office_nas")
                assert url == "http://office.example.com:5000"

                # Test non-existent NAS
                url = cfg.resolve_base_url("nonexistent")
                assert url is None


class TestFilePermissions:
    """Test file permission checking."""

    def test_permission_warning_for_open_permissions(self, tmp_path, capsys):
        """Test that warning is printed for overly open permissions."""
        # Create a file with open permissions
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text("{}")

        # Make it world-readable
        os.chmod(str(secrets_file), 0o644)

        reload_config()

        with patch.dict(os.environ, {}, clear=True):
            with patch("config.SECRETS_FILE", secrets_file):
                from config import SynologyConfig

                _cfg = SynologyConfig()

                # Should have printed a warning about permissions
                captured = capsys.readouterr()
                # Check for permission warning in stderr
                assert "permission" in captured.err.lower() or "Warning" in captured.err


def test_config_str_representation():
    """Test string representation of config."""
    reload_config()

    with patch.dict(
        os.environ,
        {
            "SYNOLOGY_URL": "http://test.local:5000",
            "SYNOLOGY_USERNAME": "user",
            "SYNOLOGY_PASSWORD": "pass",
        },
    ):
        with patch("config.SECRETS_FILE", Path("/nonexistent/secrets.json")):
            with patch.object(Path, "exists", return_value=False):
                from config import SynologyConfig

                cfg = SynologyConfig()
                cfg_str = str(cfg)

                assert "SynologyConfig" in cfg_str
                assert "auto_login" in cfg_str
