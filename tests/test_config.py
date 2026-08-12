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

    def test_explicit_url_overrides_host_port(self, tmp_path):
        """Test that an explicit url wins over host/port (reverse-proxy setups)."""
        secrets_data = {
            "synology": {
                "proxied": {
                    "url": "https://nas.example.com/",
                    "host": "ignored.example.com",
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

                # url is used verbatim (trailing slash stripped); host/port ignored
                assert cfg.nas_configs["proxied"]["base_url"] == "https://nas.example.com"

    def test_non_string_url_entry_skipped(self, tmp_path, caplog):
        """Test that a non-string url skips just that entry, not the whole file."""
        import logging

        secrets_data = {
            "synology": {
                "bad": {"url": 123, "username": "a", "password": "b"},
                "good": {"host": "192.168.1.1", "port": 5000, "username": "a", "password": "b"},
            }
        }

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
                from config import SynologyConfig

                with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                    cfg = SynologyConfig()

                assert "bad" not in cfg.nas_configs
                assert "good" in cfg.nas_configs
                assert any("Invalid 'url'" in rec.message for rec in caplog.records)

    def test_empty_url_falls_back_to_host_port(self, tmp_path):
        """Test that an empty url is ignored and host/port builds the base URL."""
        secrets_data = {
            "synology": {
                "nas": {
                    "url": "",
                    "host": "192.168.1.5",
                    "port": 5001,
                    "username": "a",
                    "password": "b",
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

                assert cfg.nas_configs["nas"]["base_url"] == "https://192.168.1.5:5001"

    def test_missing_host_and_url_skipped(self, tmp_path, caplog):
        """Test that an entry with neither host nor url is skipped with a warning."""
        import logging

        secrets_data = {"synology": {"noaddr": {"username": "a", "password": "b"}}}

        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(json.dumps(secrets_data))
        os.chmod(str(secrets_file), 0o600)  # config refuses insecure-perm files

        reload_config()

        with clear_env():
            with patch("config.SETTINGS_FILE", secrets_file):
                from config import SynologyConfig

                with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                    cfg = SynologyConfig()

                assert "noaddr" not in cfg.nas_configs
                assert any("Missing 'host' (or 'url')" in rec.message for rec in caplog.records)


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
    """Test Windows ACL enforcement.

    pywin32 is Windows-only and not installed in CI/dev on macOS/Linux, so we
    drive _check_windows_file_permissions() in isolation. Each test injects
    fake ``win32security`` / ``win32api`` / ``ntsecuritycon`` modules (or none)
    into sys.modules via patch.dict so the method's optional-dependency import
    either succeeds with a stub or raises ImportError, exercising both
    branches. Setting a sys.modules entry to ``None`` makes the import raise
    ModuleNotFoundError, which we use for the missing-pywin32 tests.
    """

    CURRENT_SID = "S-1-5-21-currentuser"
    SYSTEM_SID = "S-1-5-18"
    ADMINS_SID = "S-1-5-32-544"
    EVERYONE_SID = "S-1-1-0"

    def _load_fresh_config(self):
        """Import a fresh SynologyConfig class (avoids module-level caching)."""
        reload_config()
        import config as config_mod

        return config_mod

    @staticmethod
    def _ace(trustee_sid_str, ace_type=0, ace_flags=0):
        """Build a fake ACE tuple matching pywin32's real shape.

        Real pywin32 ACE shapes returned by PyACL.GetAce(i):
          conventional: ((ace_type, ace_flags), mask, sid)
          object:       ((ace_type, ace_flags), mask, object_type,
                         inherited_object_type, sid)
        Per WinNT.h:
          0 = ACCESS_ALLOWED_ACE_TYPE, 1 = ACCESS_DENIED_ACE_TYPE,
          5 = ACCESS_ALLOWED_OBJECT_ACE_TYPE, 6 = ACCESS_DENIED_OBJECT_ACE_TYPE.
        ace_flags includes INHERITED_ACE (0x10) for ACEs inherited from a
        parent folder — the case Codex flagged as misclassified by the old
        ace[0][1] read.
        """
        header = (ace_type, ace_flags)
        mask = 0x1F01FF  # full control placeholder
        if ace_type in (5, 6):
            # Object ACE: ((type, flags), mask, obj_type, inh_obj_type, sid)
            return (header, mask, None, None, trustee_sid_str)
        return (header, mask, trustee_sid_str)

    def _build_fakes(self, *, owner_sid, dacl_aces=None, dacl_is_null=False):
        """Build fake win32security/win32api/ntsecuritycon modules.

        The fakes mirror pywin32's real API shape so the tests exercise the
        same code paths that run on Windows:

        - GetTokenInformation(TokenUser) returns a (sid, attributes) tuple.
        - GetSecurityDescriptorDacl() returns a PyACL-like object exposing
          GetAceCount() and GetAce(i) (NOT direct iteration).
        - ACE tuples are ((ace_type, ace_flags), mask, trustee_sid).
        """
        import types

        class _FakeAcl:
            """Minimal PyACL stand-in: GetAceCount + GetAce(i)."""

            def __init__(self, aces):
                self._aces = list(aces or [])

            def GetAceCount(self):
                return len(self._aces)

            def GetAce(self, i):
                return self._aces[i]

        fake_win32security = types.ModuleType("win32security")
        fake_win32security.OWNER_SECURITY_INFORMATION = 1
        fake_win32security.DACL_SECURITY_INFORMATION = 4
        fake_win32security.TOKEN_QUERY = 8
        fake_win32security.TokenUser = 1

        def _get_file_security(_path, _info):
            def _owner():
                return owner_sid

            def _dacl():
                if dacl_is_null:
                    return None
                return _FakeAcl(dacl_aces or [])

            return types.SimpleNamespace(
                GetSecurityDescriptorOwner=_owner,
                GetSecurityDescriptorDacl=_dacl,
            )

        fake_win32security.GetFileSecurity = _get_file_security
        fake_win32security.OpenProcessToken = lambda _p, _a: "token"
        # Real pywin32: GetTokenInformation(TokenUser) -> (sid, attributes).
        fake_win32security.GetTokenInformation = lambda _t, _c: (
            self.CURRENT_SID,
            0,
        )

        def _lookup(_sys, name):
            # Map well-known names back to their SIDs.
            mapping = {
                "SYSTEM": self.SYSTEM_SID,
                "BUILTIN\\Administrators": self.ADMINS_SID,
            }
            return (mapping.get(name, "S-1-0-0"), None, None)

        fake_win32security.LookupAccountName = _lookup

        fake_win32api = types.ModuleType("win32api")
        fake_win32api.GetCurrentProcess = lambda: "proc"

        fake_ntsecuritycon = types.ModuleType("ntsecuritycon")
        fake_ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE = 0
        # Real WinNT.h value is 5 (ACCESS_ALLOWED_OBJECT_ACE_TYPE).
        fake_ntsecuritycon.ACCESS_ALLOWED_OBJECT_ACE_TYPE = 5

        return {
            "win32security": fake_win32security,
            "win32api": fake_win32api,
            "ntsecuritycon": fake_ntsecuritycon,
        }

    def test_missing_pywin32_fails_closed_by_default(self, tmp_path, caplog):
        """Without pywin32, the check fails closed (returns False)."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        # Setting sys.modules entries to None forces ImportError on import.
        blocked = {
            "win32security": None,
            "win32api": None,
            "ntsecuritycon": None,
            "win32file": None,
        }

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, blocked):
            with clear_env():
                with caplog.at_level(logging.ERROR, logger="synology-mcp"):
                    result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "pywin32 not installed" in rec.message.lower() for rec in caplog.records
        )

    def test_missing_pywin32_opt_in_returns_true(self, tmp_path, caplog):
        """With the opt-in env var, missing pywin32 is treated as acceptable."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        blocked = {
            "win32security": None,
            "win32api": None,
            "ntsecuritycon": None,
            "win32file": None,
        }

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, blocked):
            with clear_env(SYNOLOGY_MCP_ALLOW_UNVERIFIED_WINDOWS_ACL="true"):
                with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                    result = cfg._check_windows_file_permissions(secrets_file)

        assert result is True
        assert any(
            "skipped" in rec.message.lower()
            and "allow_unverified" in rec.message.lower()
            for rec in caplog.records
        ) or any(
            "allow_unverified_windows_acl=true" in rec.message.lower()
            for rec in caplog.records
        )

    def test_owner_match_with_clean_dacl_returns_true(self, tmp_path, caplog):
        """Owner == current user AND only allowlisted ACEs → pass."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_aces=[
                self._ace(self.CURRENT_SID),
                self._ace(self.SYSTEM_SID),
                self._ace(self.ADMINS_SID),
            ],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.INFO, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is True
        assert any(
            "windows acl check passed" in rec.message.lower() for rec in caplog.records
        )

    def test_owner_mismatch_returns_false(self, tmp_path, caplog):
        """Owner SID != current user → fail."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid="S-1-5-21-someone-else",
            dacl_aces=[self._ace(self.CURRENT_SID)],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "owner sid does not match" in rec.message.lower() for rec in caplog.records
        )

    def test_null_dacl_returns_false(self, tmp_path, caplog):
        """A NULL DACL (everyone full access) is rejected."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_is_null=True,
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "null dacl" in rec.message.lower() for rec in caplog.records
        )

    def test_foreign_allow_ace_returns_false(self, tmp_path, caplog):
        """An allow-ACE for Everyone (outside allowlist) fails the check.

        This is the core of issue #72: owner matches, but the file is still
        readable by Everyone via an inherited ACE.
        """
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_aces=[
                self._ace(self.CURRENT_SID),
                self._ace(self.EVERYONE_SID),
            ],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "outside the allowlist" in rec.message.lower() for rec in caplog.records
        )

    def test_inherited_foreign_allow_ace_returns_false(self, tmp_path, caplog):
        """An INHERITED allow-ACE for Everyone must still fail the check.

        Regression guard: the old code read ace[0][1] (flags) instead of
        ace[0][0] (type), so an inherited allow-ACE (type=0, flags=0x10
        INHERITED_ACE) was misclassified as "not allowed" and skipped —
        letting an inherited Everyone grant through. This test fails under
        that bug and passes with the correct ace[0][0] read.
        """
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_aces=[
                self._ace(self.CURRENT_SID),
                # type=0 (ALLOWED), flags=0x10 (INHERITED_ACE) → real shape of
                # an ACE inherited from a parent folder granting Everyone read.
                self._ace(self.EVERYONE_SID, ace_type=0, ace_flags=0x10),
            ],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "outside the allowlist" in rec.message.lower() for rec in caplog.records
        )

    def test_deny_ace_does_not_fail_check(self, tmp_path, caplog):
        """Deny-ACEs only narrow access; they must not fail the check."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_aces=[
                self._ace(self.CURRENT_SID),
                # 1 = ACCESS_DENIED_ACE_TYPE — narrowing, should be ignored.
                self._ace(self.EVERYONE_SID, ace_type=1),
            ],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.INFO, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is True

    def test_foreign_object_allow_ace_returns_false(self, tmp_path, caplog):
        """An ACCESS_ALLOWED_OBJECT_ACE_TYPE for Everyone must fail the check.

        Object ACEs carry the trustee SID at ace[-1] (not ace[2]). The audit
        must treat type 5 (ACCESS_ALLOWED_OBJECT_ACE_TYPE per WinNT.h) as an
        allow ACE and read the trustee from the right index, otherwise a
        foreign object-allow grant is silently skipped.
        """
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_aces=[
                self._ace(self.CURRENT_SID),
                # 5 = ACCESS_ALLOWED_OBJECT_ACE_TYPE — widening, foreign trustee.
                self._ace(self.EVERYONE_SID, ace_type=5),
            ],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "outside the allowlist" in rec.message.lower() for rec in caplog.records
        )

    def test_allowlisted_object_ace_passes(self, tmp_path, caplog):
        """An object-allow ACE for an allowlisted principal passes."""
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        fakes = self._build_fakes(
            owner_sid=self.CURRENT_SID,
            dacl_aces=[
                self._ace(self.CURRENT_SID),
                self._ace(self.ADMINS_SID, ace_type=5),
            ],
        )

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(sys.modules, fakes):
            with caplog.at_level(logging.INFO, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is True

    def test_unrecognised_ace_type_fails_closed(self, tmp_path, caplog):
        """An ACE of unrecognised type fails closed (no silent bypass).

        Covers callback/conditional/dynamic ACE types (9, 11, …) that could
        widen access but whose shape we don't model. Rather than skip them
        and risk a foreign grant slipping through, the check rejects the
        file. Also covers a totally unknown type (e.g. 99).
        """
        import logging
        import sys

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        for unknown_type in (9, 11, 99):
            fakes = self._build_fakes(
                owner_sid=self.CURRENT_SID,
                dacl_aces=[
                    self._ace(self.CURRENT_SID),
                    self._ace(self.EVERYONE_SID, ace_type=unknown_type),
                ],
            )

            config_mod = self._load_fresh_config()
            cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

            with patch.dict(sys.modules, fakes):
                with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                    result = cfg._check_windows_file_permissions(secrets_file)

            assert result is False, f"type {unknown_type} should fail closed"
            assert any(
                "unrecognised" in rec.message.lower() for rec in caplog.records
            )

    def test_win32_runtime_failure_returns_false(self, tmp_path, caplog):
        """A runtime error from win32security fails the check (fail-closed)."""
        import logging
        import sys
        import types

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

        def _boom(*_args, **_kwargs):
            raise OSError("denied")

        fake_win32security = types.ModuleType("win32security")
        fake_win32security.OWNER_SECURITY_INFORMATION = 1
        fake_win32security.DACL_SECURITY_INFORMATION = 4
        fake_win32security.TOKEN_QUERY = 8
        fake_win32security.TokenUser = 1
        fake_win32security.GetFileSecurity = _boom
        fake_win32security.OpenProcessToken = _boom
        fake_win32security.GetTokenInformation = _boom
        fake_win32security.LookupAccountName = _boom

        fake_win32api = types.ModuleType("win32api")
        fake_win32api.GetCurrentProcess = lambda: "proc"

        fake_ntsecuritycon = types.ModuleType("ntsecuritycon")
        fake_ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE = 0

        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        with patch.dict(
            sys.modules,
            {
                "win32security": fake_win32security,
                "win32api": fake_win32api,
                "ntsecuritycon": fake_ntsecuritycon,
            },
        ):
            with caplog.at_level(logging.WARNING, logger="synology-mcp"):
                result = cfg._check_windows_file_permissions(secrets_file)

        assert result is False
        assert any(
            "windows acl check failed" in rec.message.lower() for rec in caplog.records
        )

    def test_dispatch_on_nt_calls_windows_check(self, tmp_path):
        """_check_file_permissions routes to the Windows check when os.name == 'nt'."""
        config_mod = self._load_fresh_config()
        cfg = config_mod.SynologyConfig.__new__(config_mod.SynologyConfig)

        called = {"count": 0}

        def _stub(_path):
            called["count"] += 1
            return True

        secrets_file = tmp_path / "settings.json"
        secrets_file.write_text("{}")

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
