# src/synology_auth.py - Simple Synology authentication utilities

import requests
from typing import Dict, Any, Optional


class SynologyAuth:
    """Handles Synology NAS authentication using simple API calls."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.current_session_id: Optional[str] = None
        self.current_session_type: str = 'FileStation'
    
    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate with Synology NAS and return session info."""
        return self.login_with_session(username, password, 'FileStation')
    
    def login_with_session(self, username: str, password: str, session_type: str = 'FileStation') -> Dict[str, Any]:
        """Authenticate with Synology NAS using specific session type."""
        login_url = f"{self.base_url}/webapi/auth.cgi"
        
        # Try common API versions (start with newer versions)
        api_versions = ['7', '6', '3', '2']
        
        for version in api_versions:
            payload = {
                'api': 'SYNO.API.Auth',
                'version': version,
                'method': 'login',
                'account': username,
                'passwd': password,
                'session': session_type,
                'format': 'sid'
            }
            
            try:
                response = requests.get(login_url, params=payload, verify=False)
                response.raise_for_status()
                result = response.json()
                
                if result.get('success'):
                    # Store session info for automatic logout
                    self.current_session_id = result['data']['sid']
                    self.current_session_type = session_type
                    return result
                else:
                    error_code = result.get('error', {}).get('code', 'unknown')
                    # Don't try other versions for auth errors
                    if error_code in [400, 402, 403, 404]:
                        return result
            except Exception:
                continue
        
        # If all versions failed, return the last result
        return {'success': False, 'error': {'code': 'unknown', 'message': 'Authentication failed'}}
    
    def login_download_station(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate specifically for Download Station."""
        return self.login_with_session(username, password, 'DownloadStation')
    
    def logout(self, session_id: Optional[str] = None, session_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Logout from Synology NAS.
        
        Args:
            session_id: Session ID to logout. If None, uses current session.
            session_type: Session type to logout. If None, uses current session type.
        
        Returns:
            Dict with success status and any error details.
        """
        # Use provided parameters or fall back to current session
        logout_session_id = session_id or self.current_session_id
        logout_session_type = session_type or self.current_session_type
        
        if not logout_session_id:
            return {
                'success': False, 
                'error': {'code': 'no_session', 'message': 'No session ID provided or available'}
            }
        
        logout_url = f"{self.base_url}/webapi/auth.cgi"
        
        # Try multiple API versions for logout (same approach as login)
        api_versions = ['7', '6', '3', '2']
        last_error = None
        
        for version in api_versions:
            payload = {
                'api': 'SYNO.API.Auth',
                'version': version,
                'method': 'logout',
                'session': logout_session_type,
                '_sid': logout_session_id
            }
            
            try:
                response = requests.get(logout_url, params=payload, verify=False)
                response.raise_for_status()
                result = response.json()
                
                if result.get('success'):
                    # Clear current session if we logged out our own session
                    if logout_session_id == self.current_session_id:
                        self.current_session_id = None
                        self.current_session_type = 'FileStation'
                    return result
                else:
                    last_error = result
                    error_code = result.get('error', {}).get('code', 'unknown')
                    # For certain errors, don't try other versions
                    if error_code in [105, 106]:  # Invalid session or not logged in
                        break
                        
            except requests.RequestException as e:
                last_error = {
                    'success': False, 
                    'error': {'code': 'network_error', 'message': f'Network error: {str(e)}'}
                }
                continue
            except Exception as e:
                last_error = {
                    'success': False, 
                    'error': {'code': 'unknown_error', 'message': f'Unexpected error: {str(e)}'}
                }
                continue
        
        # If we reach here, all attempts failed
        if last_error:
            return last_error
        else:
            return {
                'success': False, 
                'error': {'code': 'all_versions_failed', 'message': 'Logout failed with all API versions'}
            }
    
    def is_logged_in(self) -> bool:
        """Check if there's an active session."""
        return self.current_session_id is not None
    
    def get_session_info(self) -> Dict[str, Any]:
        """Get current session information."""
        return {
            'session_id': self.current_session_id,
            'session_type': self.current_session_type,
            'logged_in': self.is_logged_in()
        } 