# Download Station module for DSM 7.0+ using modern APIs

import requests
import json
from typing import Dict, List, Any, Optional
import sys


class SynologyDownloadStation:
    """Handles Synology Download Station API operations using DSM 7.0+ modern APIs."""
    
    def __init__(self, base_url: str, session_id: str):
        self.base_url = base_url.rstrip('/')
        self.session_id = session_id
        
        # DSM 7.0+ modern API endpoints (the only ones that work)
        self.api_url = f"{self.base_url}/webapi/entry.cgi"
        self.task_api = "SYNO.DownloadStation2.Task"
        self.task_version = "2"
        self.info_api = "SYNO.DownloadStation.Info"
        self.info_version = "2"
        self.stat_api = "SYNO.DownloadStation.Statistic"
        self.stat_version = "1"
        
        # Default destination preference
        self.preferred_default_destination = "downloads"
    
    def _make_request(self, api: str, version: str, method: str, **params) -> Dict[str, Any]:
        """Make a request to Synology Download Station API."""
        request_params = {
            'api': api,
            'version': version,
            'method': method,
            '_sid': self.session_id,
            **params
        }
        
        # Use entry.cgi for modern APIs, specific paths for legacy info/stats
        if api.startswith('SYNO.DownloadStation2'):
            endpoint_url = self.api_url
        elif api == 'SYNO.DownloadStation.Info':
            endpoint_url = f"{self.base_url}/webapi/DownloadStation/info.cgi"
        elif api == 'SYNO.DownloadStation.Statistic':
            endpoint_url = f"{self.base_url}/webapi/DownloadStation/statistic.cgi"
        else:
            endpoint_url = self.api_url
        
        try:
            # Use POST for create operations, GET for others
            if method == 'create':
                response = requests.post(endpoint_url, data=request_params, verify=False)
            else:
                response = requests.get(endpoint_url, params=request_params, verify=False)
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get('success'):
                error_code = data.get('error', {}).get('code', 'unknown')
                error_msg = self._get_error_message(error_code)
                raise Exception(f"Download Station API error {error_code}: {error_msg}")
            
            return data.get('data', {})
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {e}")
    
    def _get_error_message(self, error_code: str) -> str:
        """Get human-readable error message for error codes."""
        error_messages = {
            '100': 'Unknown error',
            '101': 'Invalid parameter',
            '102': 'The requested API does not exist',
            '103': 'The requested method does not exist',
            '104': 'The requested version does not support the functionality',
            '105': 'The logged in session does not have permission',
            '106': 'Session timeout',
            '107': 'Session interrupted by duplicate login',
            '120': 'Invalid task id or task not found',
            '400': 'File upload failed',
            '401': 'Max number of tasks reached',
            '402': 'Destination denied',
            '403': 'Destination does not exist',
            '404': 'Invalid task id',
            '405': 'Invalid task action',
            '406': 'No default destination',
            '407': 'Set destination failed',
            '408': 'File does not exist',
            '409': 'Task already exists',
            '410': 'Task already finished'
        }
        return error_messages.get(str(error_code), f'Unknown error: {error_code}')
    
    def get_info(self) -> Dict[str, Any]:
        """Get Download Station information."""
        try:
            data = self._make_request(self.info_api, self.info_version, 'getinfo')
            return {
                'version': data.get('version'),
                'version_string': data.get('version_string'),
                'is_manager': data.get('is_manager', False),
                'hostname': data.get('hostname', 'Synology NAS')
            }
        except Exception as e:
            # Fallback response if API fails
            return {
                'version': 'Unknown',
                'version_string': 'Download Station Available',
                'is_manager': True,
                'hostname': 'Synology NAS',
                'note': f'Limited info: {str(e)}'
            }
    
    def list_tasks(self, offset: int = 0, limit: int = -1, additional: Optional[str] = None) -> Dict[str, Any]:
        """List download tasks using modern Download Station API."""
        params = {
            'offset': offset,
            'limit': limit if limit > 0 else 100
        }
        
        # Use additional parameters for detailed task info
        if additional:
            params['additional'] = additional
        else:
            params['additional'] = 'detail,transfer'
        
        try:
            data = self._make_request(self.task_api, self.task_version, 'list', **params)
        except Exception as e:
            # If version 2 fails, try version 1
            if "doesn't exist" in str(e) or "102" in str(e) or "104" in str(e):
                print(f"⚠️  {self.task_api} v{self.task_version} failed, trying v1", file=sys.stderr)
                try:
                    basic_params = {'offset': offset, 'limit': params['limit']}
                    data = self._make_request(self.task_api, "1", 'list', **basic_params)
                except Exception as e2:
                    print(f"⚠️  No task APIs available: {e2}", file=sys.stderr)
                    return {'total': 0, 'offset': offset, 'tasks': []}
            else:
                raise
        
        tasks = []
        for task in data.get('tasks', []):
            task_info = {
                'id': task.get('id'),
                'type': task.get('type'),
                'username': task.get('username'),
                'title': task.get('title'),
                'size': task.get('size'),
                'status': task.get('status'),
                'status_extra': task.get('status_extra', {}),
                'create_time': task.get('create_time'),
                'started_time': task.get('started_time'),
                'completed_time': task.get('completed_time')
            }
            
            # Parse additional info if available
            if 'additional' in task:
                additional_info = task['additional']
                
                if 'detail' in additional_info:
                    detail = additional_info['detail']
                    task_info.update({
                        'destination': detail.get('destination'),
                        'uri': detail.get('uri'),
                        'priority': detail.get('priority'),
                        'total_peers': detail.get('total_peers'),
                        'connected_seeders': detail.get('connected_seeders'),
                        'connected_leechers': detail.get('connected_leechers')
                    })
                
                if 'transfer' in additional_info:
                    transfer = additional_info['transfer']
                    task_info.update({
                        'size_downloaded': transfer.get('size_downloaded'),
                        'size_uploaded': transfer.get('size_uploaded'),
                        'speed_download': transfer.get('speed_download'),
                        'speed_upload': transfer.get('speed_upload')
                    })
            
            tasks.append(task_info)
        
        return {
            'total': data.get('total', len(tasks)),
            'offset': data.get('offset', offset),
            'tasks': tasks
        }
    
    def get_config(self) -> Dict[str, Any]:
        """Get Download Station configuration."""
        try:
            data = self._make_request(self.info_api, '1', 'getconfig')
            return data
        except Exception:
            return {
                'default_destination': '',
                'emule_enabled': False,
                'bt_max_download': 0,
                'bt_max_upload': 0
            }
    
    def create_task(self, uri: str, destination: Optional[str] = None, 
                   username: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        """Create a new download task using modern Download Station API.
        
        Note: The destination folder MUST already exist on the NAS.
        If no destination is provided, will use 'downloads' as default.
        """
        
        # Get valid destination if not provided
        if not destination:
            destination = self.get_default_destination()
        
        # ✅ VALIDATE DESTINATION EXISTS BEFORE CREATING TASK
        print(f"🔍 Validating destination folder exists: {destination}", file=sys.stderr)
        if not self._check_destination_exists(destination):
            # Get suggestions for existing folders
            common_destinations = self.get_common_destinations()
            available_suggestions = []
            for common_dest in common_destinations:
                if self._check_destination_exists(common_dest):
                    available_suggestions.append(common_dest)
            
            error_msg = f"Destination folder '{destination}' does not exist on the NAS."
            if available_suggestions:
                error_msg += f" Available folders: {', '.join(available_suggestions)}"
            else:
                error_msg += " Please create the 'downloads' folder first in File Station."
            
            raise Exception(error_msg)
        
        print(f"✅ Destination '{destination}' exists, proceeding with task creation", file=sys.stderr)
        
        # Use the exact format captured from real NAS operation
        params = {
            'type': 'url',
            'destination': destination,
            'create_list': 'true',
            'url': json.dumps([uri])  # URL as JSON array
        }
        
        # Add optional authentication parameters if provided
        if username:
            params['username'] = username
        if password:
            params['password'] = password
        
        try:
            print(f"🔧 Creating task with real NAS format", file=sys.stderr)
            print(f"   URI: {uri}", file=sys.stderr)
            print(f"   Destination: {destination}", file=sys.stderr)
            
            data = self._make_request(self.task_api, self.task_version, 'create', **params)
            
            print(f"✅ Task created successfully!", file=sys.stderr)
            print(f"   Task IDs: {data.get('task_id', [])}", file=sys.stderr)
            print(f"   List IDs: {data.get('list_id', [])}", file=sys.stderr)
            
            return data
            
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️  Create task failed: {e}", file=sys.stderr)
            
            # Fallback: Try with version 1 if version 2 failed
            if self.task_version != "1":
                try:
                    print("🔧 Trying with DownloadStation2.Task v1", file=sys.stderr)
                    fallback_params = {
                        'uri': uri,
                        'destination': destination
                    }
                    if username:
                        fallback_params['username'] = username
                    if password:
                        fallback_params['password'] = password
                        
                    data = self._make_request(self.task_api, "1", 'create', **fallback_params)
                    print("✅ Create successful with v1", file=sys.stderr)
                    return data
                except Exception as e2:
                    print(f"⚠️  v1 also failed: {e2}", file=sys.stderr)
            
            # Enhanced error message
            raise Exception(f"Task creation failed: {e}. Make sure the URL is valid and you have permission to create downloads.")
    
    def delete_tasks(self, task_ids: List[str], force_complete: bool = False) -> Dict[str, Any]:
        """Delete download tasks."""
        params = {
            'id': ','.join(task_ids),
            'force_complete': force_complete
        }
        return self._make_request(self.task_api, self.task_version, 'delete', **params)
    
    def pause_tasks(self, task_ids: List[str]) -> Dict[str, Any]:
        """Pause download tasks."""
        params = {'id': ','.join(task_ids)}
        return self._make_request(self.task_api, self.task_version, 'pause', **params)
    
    def resume_tasks(self, task_ids: List[str]) -> Dict[str, Any]:
        """Resume download tasks."""
        params = {'id': ','.join(task_ids)}
        return self._make_request(self.task_api, self.task_version, 'resume', **params)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get Download Station statistics."""
        try:
            data = self._make_request(self.stat_api, self.stat_version, 'getinfo')
            return {
                'speed_download': data.get('speed_download', 0),
                'speed_upload': data.get('speed_upload', 0),
                'emule_speed_download': data.get('emule_speed_download', 0),
                'emule_speed_upload': data.get('emule_speed_upload', 0)
            }
        except Exception:
            # Fallback: calculate from task list
            try:
                tasks_data = self.list_tasks(limit=100)
                total_down_speed = sum(task.get('speed_download', 0) for task in tasks_data.get('tasks', []))
                total_up_speed = sum(task.get('speed_upload', 0) for task in tasks_data.get('tasks', []))
                
                return {
                    'speed_download': total_down_speed,
                    'speed_upload': total_up_speed,
                    'note': 'Calculated from active tasks'
                }
            except Exception:
                return {
                    'speed_download': 0,
                    'speed_upload': 0,
                    'error': 'Statistics not available'
                }
    
    def _check_destination_exists(self, destination: str) -> bool:
        """Check if destination folder exists using FileStation API."""
        try:
            # Use FileStation API to check if folder exists
            request_params = {
                'api': 'SYNO.FileStation.List',
                'version': '2',
                'method': 'getinfo',
                'path': f'/{destination}',
                '_sid': self.session_id
            }
            
            response = requests.get(self.api_url, params=request_params, verify=False)
            response.raise_for_status()
            data = response.json()
            
            # Check if the request was successful and returned file info
            if data.get('success') and data.get('data', {}).get('files'):
                files = data['data']['files']
                if files and files[0].get('isdir'):
                    return True
            
            return False
            
        except Exception:
            # If we can't check, assume it doesn't exist
            return False

    def get_common_destinations(self) -> List[str]:
        """Get a list of commonly used destination folders.
        
        Returns common folder names that typically exist on Synology NAS.
        Note: Actual availability depends on your NAS configuration.
        """
        return [
            self.preferred_default_destination,  # Always try preferred first
            'video',         # Common for video content
            'music',         # Common for audio content
            'software',      # Common for applications/software
            'documents',     # Common for document files
            'photos',        # Common for image files
            'backup'         # Common for backup files
        ]
    
    def get_default_destination(self) -> str:
        """Get the best available default destination.
        
        Returns the preferred default destination if it exists,
        otherwise returns the first available common destination.
        """
        # First, try our preferred default
        if self._check_destination_exists(self.preferred_default_destination):
            return self.preferred_default_destination
        
        # Try other common destinations
        for dest in self.get_common_destinations()[1:]:  # Skip first since it's preferred
            if self._check_destination_exists(dest):
                print(f"⚠️  Preferred destination '{self.preferred_default_destination}' not found, using '{dest}'", file=sys.stderr)
                return dest
        
        # If nothing exists, return preferred anyway (will cause validation error later)
        print(f"⚠️  No common destinations found, defaulting to '{self.preferred_default_destination}'", file=sys.stderr)
        return self.preferred_default_destination
    
    def set_default_destination(self, destination: str) -> bool:
        """Set the preferred default destination.
        
        Args:
            destination: The folder name to use as default
            
        Returns:
            True if the destination exists, False otherwise
        """
        exists = self._check_destination_exists(destination)
        
        if exists:
            self.preferred_default_destination = destination
            print(f"✅ Default destination set to '{destination}'", file=sys.stderr)
        else:
            print(f"⚠️  Destination '{destination}' does not exist, not setting as default", file=sys.stderr)
        
        return exists
    
    def ensure_downloads_folder(self) -> bool:
        """Ensure the 'downloads' folder exists and is set as default.
        
        Returns:
            True if downloads folder exists or was created successfully
        """
        if self._check_destination_exists('downloads'):
            self.preferred_default_destination = 'downloads'
            print("✅ 'downloads' folder exists and is set as default", file=sys.stderr)
            return True
        else:
            print("⚠️  'downloads' folder does not exist. Please create it in File Station.", file=sys.stderr)
            print("   Typical path: Control Panel > Shared Folder > Create > Name: 'downloads'", file=sys.stderr)
            return False
