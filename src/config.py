# src/config.py - Configuration management
# Loads NAS credentials from ~/.pipeline/secrets.json (multi-NAS support).
# Non-sensitive settings still come from .env / environment variables.

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv


SECRETS_PATH = Path.home() / '.pipeline' / 'secrets.json'


class SynologyConfig:
    """Configuration manager for Synology MCP Server."""

    def __init__(self, env_file: Optional[str] = None):
        """Initialize configuration from secrets.json and environment variables."""
        if env_file:
            load_dotenv(env_file)
        elif os.path.exists('.env'):
            load_dotenv('.env')

        self._load_env_settings()
        self._load_secrets()

    def _load_env_settings(self):
        """Load non-sensitive settings from environment / .env."""
        self.server_name = os.getenv('MCP_SERVER_NAME', 'synology-mcp-server')
        self.server_version = os.getenv('MCP_SERVER_VERSION', '1.0.0')
        self.default_session_timeout = int(os.getenv('SESSION_TIMEOUT', '3600'))
        self.auto_login = os.getenv('AUTO_LOGIN', 'true').lower() == 'true'
        self.verify_ssl = os.getenv('VERIFY_SSL', 'false').lower() == 'true'
        self.debug = os.getenv('DEBUG', 'false').lower() == 'true'
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

        # Legacy single-NAS env vars (still supported as fallback)
        self.synology_url = os.getenv('SYNOLOGY_URL')
        self.synology_username = os.getenv('SYNOLOGY_USERNAME')
        self.synology_password = os.getenv('SYNOLOGY_PASSWORD')

    def _load_secrets(self):
        """Load NAS credentials from ~/.pipeline/secrets.json."""
        self.nas_configs: Dict[str, Dict[str, Any]] = {}

        if SECRETS_PATH.exists():
            try:
                data = json.loads(SECRETS_PATH.read_text())
                synology_section = data.get('synology', {})

                for nas_name, nas_info in synology_section.items():
                    host = nas_info.get('host', '')
                    port = nas_info.get('port', 5000)
                    username = nas_info.get('username', '')
                    password = nas_info.get('password', '')

                    if not host or not username or not password:
                        continue

                    scheme = 'https' if port == 5001 else 'http'
                    base_url = f"{scheme}://{host}:{port}"

                    self.nas_configs[nas_name] = {
                        'base_url': base_url,
                        'username': username,
                        'password': password,
                        'verify_ssl': self.verify_ssl,
                        'note': nas_info.get('note', ''),
                    }
            except (json.JSONDecodeError, OSError) as e:
                import sys
                print(f"⚠️  Failed to read {SECRETS_PATH}: {e}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

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
            'base_url': self.synology_url,
            'username': self.synology_username,
            'password': self.synology_password,
            'verify_ssl': self.verify_ssl,
        }

    def resolve_base_url(self, nas_name: str) -> Optional[str]:
        """Get the base_url for a NAS name, or None if not found."""
        cfg = self.nas_configs.get(nas_name)
        return cfg['base_url'] if cfg else None

    def validate_config(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        if not self.has_synology_credentials():
            errors.append("No Synology credentials found in secrets.json or .env")
        if self.default_session_timeout < 60:
            errors.append("SESSION_TIMEOUT must be at least 60 seconds")
        return errors

    def __str__(self) -> str:
        nas_names = ', '.join(self.nas_configs.keys()) if self.nas_configs else 'none'
        return f"SynologyConfig(nas=[{nas_names}], auto_login={self.auto_login})"


# Global config instance
config = SynologyConfig()
