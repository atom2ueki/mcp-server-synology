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


# Variables Windows needs in order to resolve a home directory. Clearing the
# whole environment breaks Path("~").expanduser() on Windows -- POSIX falls back
# to the pwd database, Windows has nothing to fall back to and raises
# RuntimeError("Could not determine home directory"), which failed 9 tests here
# for reasons unrelated to what they assert. Keep these; none of them influence
# any SYNOLOGY_* lookup under test.
_HOME_VARS = ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "SYSTEMROOT", "SystemRoot")


def clear_env(**overrides):
    """patch.dict(os.environ, ..., clear=True) that still leaves a home directory."""
    preserved = {k: os.environ[k] for k in _HOME_VARS if k in os.environ}
    preserved.update(overrides)
    return patch.dict(os.environ, preserved, clear=True)


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
            with patch("config.SETTINGS_FILE", Path("/nonexistent/secrets.json")):
                with patch.object(Path, "exists", return_value=False):
                    from config import SynologyConfig

                    config = SynologyConfig()

                    assert config.synology_url == "http://test.local:5000"
                    assert config.synology_username == "testuser"
                    assert config.synology_password == "testpass"

    def test_default_values(self):
        """Test default configuration values."""
        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", Path("/nonexistent/secrets.json")):
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
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
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
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
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
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                specific = cfg.get_synology_config("primary")
                assert specific["base_url"] == "https://192.168.1.100:5001"
                assert specific["username"] == "admin"

    def test_validate_config_no_credentials(self):
        """Test validation fails with no credentials."""
        reload_config()

        # patch.dict clear=True wipes os.environ but SynologyConfig calls
        # load_dotenv(".env") at construction time, which re-injects whatever
        # is in the developer's local .env. Patch os.path.exists so the loader
        # treats the project as having no .env.
        with clear_env():
            with patch("config.SETTINGS_FILE", Path("/nonexistent/secrets.json")):
                with patch("config.os.path.exists", return_value=False):
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
            with patch("config.SETTINGS_FILE", Path("/nonexistent/secrets.json")):
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
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
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

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
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
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
                from config import SynologyConfig

                cfg = SynologyConfig()

                url = cfg.resolve_base_url("office_nas")
                assert url == "http://office.example.com:5000"

                # Test non-existent NAS
                url = cfg.resolve_base_url("nonexistent")
                assert url is None


class TestFilePermissions:
    """Test file permission checking."""

    def test_permission_warning_for_open_permissions(self, tmp_path, caplog):
        """Test that warning is logged for overly open permissions."""
        import logging

        # Create a file with open permissions
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text("{}")

        # Make it world-readable
        os.chmod(str(secrets_file), 0o644)

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
                from config import SynologyConfig

                with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                    _cfg = SynologyConfig()

                # Permission warning is emitted via logger.warning, not stderr
                assert any("permission" in rec.message.lower() for rec in caplog.records)


class TestWindowsAclFallback:
    """Test Windows ACL check fallback when pywin32 is unavailable.

    pywin32 is Windows-only and not installed in CI/dev on macOS/Linux, so we
    drive _check_windows_file_permissions() in isolation. Each test injects a
    fake ``win32security`` / ``win32file`` module (or none) into sys.modules so
    the method's optional-dependency import either succeeds with a stub or
    raises ImportError, exercising both branches.
    """

    def _load_fresh_config(self):
        """Import a fresh SynologyConfig class (avoids module-level caching)."""
        reload_config()
        import config as config_mod

        return config_mod

    def test_fallback_returns_true_without_pywin32(self, tmp_path, caplog):
        """Without pywin32 the check is skipped and returns True (unverified)."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        # Ensure no pywin32 modules are importable
        removed = {}
        for mod_name in ("win32security", "win32file"):
            if mod_name in sys.modules:
                removed[mod_name] = sys.modules.pop(mod_name)

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        try:
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

            assert result is True
            assert any(
                "pywin32 not installed" in rec.message.lower()
                or "pywin32" in rec.message.lower()
                for rec in caplog.records
            )
        finally:
            sys.modules.update(removed)

    def test_owner_match_returns_true(self, tmp_path, caplog):
        """When owner SID equals current user SID, the check passes."""
        import logging
        import sys
        import types

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        same_sid = "S-1-5-21-currentuser"

        # Build a fake win32security module with the constants/callables the
        # method uses. Constants can be arbitrary ints; only equality matters.
        fake_win32security = types.ModuleType("win32security")
        fake_win32security.OWNER_SECURITY_INFORMATION = 1
        fake_win32security.TOKEN_QUERY = 8
        fake_win32security.TokenUser = 1

        def _get_file_security(_path, _info):
            sd = types.SimpleNamespace(GetSecurityDescriptorOwner=lambda: same_sid)
            return sd

        def _get_current_process():
            return "fake-process-handle"

        def _open_process_token(_proc, _access):
            return "fake-token-handle"

        def _get_token_information(_token, _class):
            return {"UserSid": same_sid}

        fake_win32security.GetFileSecurity = _get_file_security
        fake_win32security.GetCurrentProcess = _get_current_process
        fake_win32security.OpenProcessToken = _open_process_token
        fake_win32security.GetTokenInformation = _get_token_information

        fake_win32file = types.ModuleType("win32file")

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(
            sys.modules,
            {"win32security": fake_win32security, "win32file": fake_win32file},
        ):
            with caplog.at_level(logging.INFO, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is True
        assert any(
            "windows acl check passed" in rec.message.lower() for rec in caplog.records
        )

    def test_owner_mismatch_returns_false(self, tmp_path, caplog):
        """When owner SID differs from current user SID, the check fails."""
        import logging
        import sys
        import types

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        owner_sid = "S-1-5-21-someone-else"
        current_sid = "S-1-5-21-currentuser"

        fake_win32security = types.ModuleType("win32security")
        fake_win32security.OWNER_SECURITY_INFORMATION = 1
        fake_win32security.TOKEN_QUERY = 8
        fake_win32security.TokenUser = 1

        def _get_file_security(_path, _info):
            sd = types.SimpleNamespace(GetSecurityDescriptorOwner=lambda: owner_sid)
            return sd

        fake_win32security.GetFileSecurity = _get_file_security
        fake_win32security.GetCurrentProcess = lambda: "proc"
        fake_win32security.OpenProcessToken = lambda _p, _a: "token"
        fake_win32security.GetTokenInformation = lambda _t, _c: {"UserSid": current_sid}

        fake_win32file = types.ModuleType("win32file")

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(
            sys.modules,
            {"win32security": fake_win32security, "win32file": fake_win32file},
        ):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "owner sid does not match" in rec.message.lower() for rec in caplog.records
        )

    def test_win32_runtime_failure_returns_false(self, tmp_path, caplog):
        """A runtime error from win32security fails the check (fail-closed)."""
        import logging
        import sys
        import types

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fake_win32security = types.ModuleType("win32security")
        fake_win32security.OWNER_SECURITY_INFORMATION = 1
        fake_win32security.TOKEN_QUERY = 8
        fake_win32security.TokenUser = 1

        def _boom(*_args, **_kwargs):
            raise OSError("denied")

        fake_win32security.GetFileSecurity = _boom
        fake_win32security.GetCurrentProcess = _boom
        fake_win32security.OpenProcessToken = _boom
        fake_win32security.GetTokenInformation = _boom

        fake_win32file = types.ModuleType("win32file")

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(
            sys.modules,
            {"win32security": fake_win32security, "win32file": fake_win32file},
        ):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "windows acl check failed" in rec.message.lower() for rec in caplog.records
        )

    def test_dispatch_on_nt_calls_windows_check(self, tmp_path):
        """_check_file_permissions routes to the Windows check when os.name == 'nt'."""
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        called = {"count": 0}

        def _stub(_path):
            called["count"] += 1
            return True

        with patch.object(config_mod.os, "name", "nt"):
            with patch.object(cfg, "_check_windows_file_permissions", _stub):
                result = cfg._check_file_permissions(secrets_file)

        assert result is True
        assert called["count"] == 1


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
        with patch("config.SETTINGS_FILE", Path("/nonexistent/secrets.json")):
            with patch.object(Path, "exists", return_value=False):
                from config import SynologyConfig

                cfg = SynologyConfig()
                cfg_str = str(cfg)

                assert "SynologyConfig" in cfg_str
                assert "auto_login" in cfg_str
