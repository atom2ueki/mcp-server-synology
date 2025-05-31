# src/config.py - Configuration management

import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv


class SynologyConfig:
    """Configuration manager for Synology MCP Server."""
    
    def __init__(self, env_file: Optional[str] = None):
        """Initialize configuration from environment variables and .env file."""
        # Load .env file if specified or if .env exists
        if env_file:
            load_dotenv(env_file)
        elif os.path.exists('.env'):
            load_dotenv('.env')
        
        self._load_config()
    
    def _load_config(self):
        """Load configuration from environment variables."""
        # Synology connection settings
        self.synology_url = os.getenv('SYNOLOGY_URL')
        self.synology_username = os.getenv('SYNOLOGY_USERNAME')
        self.synology_password = os.getenv('SYNOLOGY_PASSWORD')
        
        # Server settings
        self.server_name = os.getenv('MCP_SERVER_NAME', 'synology-mcp-server')
        self.server_version = os.getenv('MCP_SERVER_VERSION', '1.0.0')
        
        # Optional settings
        self.default_session_timeout = int(os.getenv('SESSION_TIMEOUT', '3600'))  # 1 hour
        self.auto_login = os.getenv('AUTO_LOGIN', 'false').lower() == 'true'
        self.verify_ssl = os.getenv('VERIFY_SSL', 'false').lower() == 'true'  # Default false for self-signed certs
        
        # Debug settings
        self.debug = os.getenv('DEBUG', 'false').lower() == 'true'
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    
    def has_synology_credentials(self) -> bool:
        """Check if Synology credentials are configured."""
        return bool(self.synology_url and self.synology_username and self.synology_password)
    
    def get_synology_config(self) -> Dict[str, Any]:
        """Get Synology connection configuration."""
        return {
            'base_url': self.synology_url,
            'username': self.synology_username,
            'password': self.synology_password,
            'verify_ssl': self.verify_ssl
        }
    
    def validate_config(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        if not self.synology_url:
            errors.append("SYNOLOGY_URL is required")
        elif not self.synology_url.startswith(('http://', 'https://')):
            errors.append("SYNOLOGY_URL must start with http:// or https://")
        
        if not self.synology_username:
            errors.append("SYNOLOGY_USERNAME is required")
        
        if not self.synology_password:
            errors.append("SYNOLOGY_PASSWORD is required")
        
        if self.default_session_timeout < 60:
            errors.append("SESSION_TIMEOUT must be at least 60 seconds")
        
        return errors
    
    def __str__(self) -> str:
        """String representation of config (without sensitive data)."""
        return f"SynologyConfig(url={self.synology_url}, user={self.synology_username}, auto_login={self.auto_login})"


# Global config instance
config = SynologyConfig() 