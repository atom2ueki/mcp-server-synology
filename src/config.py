# src/config.py - Configuration management
# Loads all settings from XDG standard config directory (~/.config/synology-mcp/settings.json).
# Supports multiple NAS, Xiaozhi integration, and server settings.

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Setup logger
logger = logging.getLogger("synology-mcp")

# XDG Base Directory Specification: ~/.config/synology-mcp/
XDG_CONFIG_HOME: Path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = XDG_CONFIG_HOME / "synology-mcp"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

# Example settings.json structure for documentation
SETTINGS_JSON_EXAMPLE = """
{
  "synology": {
    "nas1": {
      "host": "192.168.1.100",
      "port": 5000,
      "username": "admin",
      "password": "your_password",
      "note": "Primary NAS at home"
    },
    "nas2": {
      "host": "192.168.1.200",
      "port": 5001,
      "username": "admin",
      "password": "your_password",
      "note": "Backup NAS"
    }
  },
  "xiaozhi": {
    "enabled": false,
    "token": "your_xiaozhi_token",
    "endpoint": "wss://api.xiaozhi.me/mcp/"
  },
  "server": {
    "auto_login": true,
    "verify_ssl": false,
    "session_timeout": 3600,
    "debug": false,
    "log_level": "INFO"
  }
}
"""


class SynologyConfig:
    """Configuration manager for Synology MCP Server."""

    def __init__(self, env_file: Optional[str] = None):
        """Initialize configuration from settings.json."""
        # Legacy .env support (deprecated - use settings.json instead)
        if env_file:
            load_dotenv(env_file)
        elif os.path.exists(".env"):
            load_dotenv(".env")

        self._load_env_settings()
        self._load_settings()

    def _load_env_settings(self):
        """Load non-sensitive settings from environment / .env."""
        self.server_name = os.getenv("MCP_SERVER_NAME", "synology-mcp-server")
        self.server_version = os.getenv("MCP_SERVER_VERSION", "1.0.0")
        self.default_session_timeout = int(os.getenv("SESSION_TIMEOUT", "3600"))
        self.auto_login = os.getenv("AUTO_LOGIN", "true").lower() == "true"
        self.verify_ssl = os.getenv("VERIFY_SSL", "false").lower() == "true"
        self.debug = os.getenv("DEBUG", "false").lower() == "true"
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        # Legacy single-NAS env vars (still supported as fallback)
        self.synology_url = os.getenv("SYNOLOGY_URL")
        self.synology_username = os.getenv("SYNOLOGY_USERNAME")
        self.synology_password = os.getenv("SYNOLOGY_PASSWORD")

    def _check_file_permissions(self, path: Path) -> bool:
        """Check if secrets file has safe permissions (0600 or stricter).

        Returns True if permissions are safe, False otherwise.
        Prints warning if permissions are too open.
        """
        try:
            file_stat = path.stat()
            mode = file_stat.st_mode

            # Check if file is owned by current user
            if os.getuid() != file_stat.st_uid:
                logger.warning(f"{path} is owned by a different user (uid={file_stat.st_uid})")
                return False

            # Check for group/other read/write permissions
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                logger.warning(f"{path} has overly permissive permissions (mode={oct(mode)})")
                logger.warning(f"Recommended: chmod 600 {path}")
                return False

            return True
        except OSError as e:
            logger.warning(f"Could not check permissions for {path}: {e}")
            return False

    def _load_settings(self):
        """Load all settings from XDG config directory (~/.config/synology-mcp/settings.json)."""
        self.nas_configs: Dict[str, Dict[str, Any]] = {}

        # Default values for xiaozhi and server settings
        self.xiaozhi_enabled = False
        self.xiaozhi_token = ""
        self.xiaozhi_endpoint = "wss://api.xiaozhi.me/mcp/"

        if SETTINGS_FILE.exists():
            # Check file permissions - refuse to load if insecure
            if not self._check_file_permissions(SETTINGS_FILE):
                logger.error("Refusing to load settings with insecure permissions")
                return

            try:
                data = json.loads(SETTINGS_FILE.read_text())

                # Load Synology NAS credentials
                synology_section = data.get("synology", {})

                if not synology_section:
                    logger.warning(f"No 'synology' section found in {SETTINGS_FILE}")

                for nas_name, nas_info in synology_section.items():
                    if not isinstance(nas_info, dict):
                        logger.warning(
                            f"Invalid entry for NAS '{nas_name}' - expected object, got {type(nas_info)}"
                        )
                        continue

                    host = nas_info.get("host", "")
                    port = nas_info.get("port", 5000)
                    username = nas_info.get("username", "")
                    password = nas_info.get("password", "")

                    if not host:
                        logger.warning(f"Missing 'host' for NAS '{nas_name}' in {SETTINGS_FILE}")
                        continue
                    if not username:
                        logger.warning(
                            f"Missing 'username' for NAS '{nas_name}' in {SETTINGS_FILE}"
                        )
                        continue
                    if not password:
                        logger.warning(
                            f"Missing 'password' for NAS '{nas_name}' in {SETTINGS_FILE}"
                        )
                        continue

                    scheme = "https" if port == 5001 else "http"
                    base_url = f"{scheme}://{host}:{port}"

                    self.nas_configs[nas_name] = {
                        "base_url": base_url,
                        "username": username,
                        "password": password,
                        "verify_ssl": self.verify_ssl,
                        "note": nas_info.get("note", ""),
                    }

                # Load Xiaozhi settings
                xiaozhi_section = data.get("xiaozhi", {})
                if xiaozhi_section:
                    self.xiaozhi_enabled = xiaozhi_section.get("enabled", False)
                    self.xiaozhi_token = xiaozhi_section.get("token", "")
                    self.xiaozhi_endpoint = xiaozhi_section.get(
                        "endpoint", "wss://api.xiaozhi.me/mcp/"
                    )

                # Load server settings (override env vars if present)
                server_section = data.get("server", {})
                if server_section:
                    if "auto_login" in server_section:
                        self.auto_login = server_section["auto_login"]
                    if "verify_ssl" in server_section:
                        self.verify_ssl = server_section["verify_ssl"]
                    if "session_timeout" in server_section:
                        self.default_session_timeout = server_section["session_timeout"]
                    if "debug" in server_section:
                        self.debug = server_section["debug"]
                    if "log_level" in server_section:
                        self.log_level = server_section["log_level"].upper()

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse {SETTINGS_FILE}: {e}")
            except OSError as e:
                logger.error(f"Failed to read {SETTINGS_FILE}: {e}")
        else:
            # No settings file
            logger.info(f"No {SETTINGS_FILE} found")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_config_dir() -> Path:
        """Return the XDG config directory path."""
        return CONFIG_DIR

    @staticmethod
    def get_settings_file() -> Path:
        """Return the settings file path."""
        return SETTINGS_FILE

    def get_nas_names(self) -> List[str]:
        """Return the list of configured NAS names."""
        return list(self.nas_configs.keys())

    def has_synology_credentials(self) -> bool:
        """Check if at least one NAS has credentials."""
        return bool(self.nas_configs) or bool(
            self.synology_url and self.synology_username and self.synology_password
        )

    def get_synology_config(self, nas_name: Optional[str] = None) -> Dict[str, Any]:
        """Get connection config for a specific NAS (or the first/legacy one).

        Args:
            nas_name: Key from secrets.json (e.g. 'nas1'). If None, returns
                      the first configured NAS or falls back to .env values.
        """
        if nas_name and nas_name in self.nas_configs:
            return self.nas_configs[nas_name]

        # Return first available from secrets.json
        if self.nas_configs:
            first = next(iter(self.nas_configs.values()))
            return first

        # Legacy .env fallback
        return {
            "base_url": self.synology_url,
            "username": self.synology_username,
            "password": self.synology_password,
            "verify_ssl": self.verify_ssl,
        }

    def resolve_base_url(self, nas_name: str) -> Optional[str]:
        """Get the base_url for a NAS name, or None if not found."""
        cfg = self.nas_configs.get(nas_name)
        return cfg["base_url"] if cfg else None

    def validate_config(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        if not self.has_synology_credentials():
            errors.append("No Synology credentials found in secrets.json or .env")
        if self.default_session_timeout < 60:
            errors.append("SESSION_TIMEOUT must be at least 60 seconds")
        return errors

    def __str__(self) -> str:
        nas_names = ", ".join(self.nas_configs.keys()) if self.nas_configs else "none"
        return f"SynologyConfig(nas=[{nas_names}], auto_login={self.auto_login})"


# Global config instance
config = SynologyConfig()
