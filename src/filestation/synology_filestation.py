# src/synology_filestation.py - Synology FileStation API utilities

import requests
from typing import Dict, List, Any, Optional
from urllib.parse import quote
import os


class SynologyFileStation:
    """Handles Synology FileStation API operations."""
    
    def __init__(self, base_url: str, session_id: str):
        self.base_url = base_url.rstrip('/')
        self.session_id = session_id
        self.api_url = f"{self.base_url}/webapi/entry.cgi"
    
    def _make_request(self, api: str, version: str, method: str, **params) -> Dict[str, Any]:
        """Make a request to Synology API."""
        request_params = {
            'api': api,
            'version': version,
            'method': method,
            '_sid': self.session_id,
            **params
        }
        
        response = requests.get(self.api_url, params=request_params)
        response.raise_for_status()
        
        data = response.json()
        if not data.get('success'):
            error_code = data.get('error', {}).get('code', 'unknown')
            raise Exception(f"Synology API error: {error_code}")
        
        return data.get('data', {})
    
    def _format_path(self, path: str) -> str:
        """Format path for Synology API."""
        if not path.startswith('/'):
            path = '/' + path
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')
        return path
    
    def list_shares(self) -> List[Dict[str, Any]]:
        """List all available shares."""
        data = self._make_request('SYNO.FileStation.List', '2', 'list_share')
        shares = data.get('shares', [])
        
        return [{
            'name': share.get('name'),
            'path': share.get('path'),
            'description': share.get('desc', ''),
            'is_writable': share.get('iswritable', False)
        } for share in shares]
    
    def list_directory(self, path: str, additional_info: bool = True) -> List[Dict[str, Any]]:
        """List contents of a directory."""
        formatted_path = self._format_path(path)
        
        params = {
            'folder_path': formatted_path
        }
        
        if additional_info:
            params['additional'] = 'time,size,owner,perm'
        
        data = self._make_request('SYNO.FileStation.List', '2', 'list', **params)
        files = data.get('files', [])
        
        result = []
        for file_info in files:
            item = {
                'name': file_info.get('name'),
                'path': file_info.get('path'),
                'type': 'directory' if file_info.get('isdir') else 'file',
                'size': file_info.get('size', 0)
            }
            
            # Add additional info if available
            if 'additional' in file_info:
                additional = file_info['additional']
                
                if 'time' in additional:
                    time_info = additional['time']
                    item.update({
                        'created': time_info.get('crtime'),
                        'modified': time_info.get('mtime'),
                        'accessed': time_info.get('atime')
                    })
                
                if 'owner' in additional:
                    owner_info = additional['owner']
                    item.update({
                        'owner': owner_info.get('user', 'unknown'),
                        'group': owner_info.get('group', 'unknown')
                    })
                
                if 'perm' in additional:
                    perm_info = additional['perm']
                    item['permissions'] = perm_info.get('posix', 'unknown')
            
            result.append(item)
        
        return result
    
    def get_file_info(self, path: str) -> Dict[str, Any]:
        """Get detailed information about a file or directory."""
        formatted_path = self._format_path(path)
        
        data = self._make_request(
            'SYNO.FileStation.List', '2', 'getinfo',
            path=formatted_path,
            additional='time,size,owner,perm'
        )
        
        files = data.get('files', [])
        if not files:
            raise Exception(f"File not found: {path}")
        
        file_info = files[0]
        result = {
            'name': file_info.get('name'),
            'path': file_info.get('path'),
            'type': 'directory' if file_info.get('isdir') else 'file',
            'size': file_info.get('size', 0)
        }
        
        # Add additional info
        if 'additional' in file_info:
            additional = file_info['additional']
            
            if 'time' in additional:
                time_info = additional['time']
                result.update({
                    'created': time_info.get('crtime'),
                    'modified': time_info.get('mtime'),
                    'accessed': time_info.get('atime')
                })
            
            if 'owner' in additional:
                owner_info = additional['owner']
                result.update({
                    'owner': owner_info.get('user', 'unknown'),
                    'group': owner_info.get('group', 'unknown')
                })
            
            if 'perm' in additional:
                perm_info = additional['perm']
                result['permissions'] = perm_info.get('posix', 'unknown')
        
        return result
    
    def search_files(self, path: str, pattern: str) -> List[Dict[str, Any]]:
        """Search for files matching a pattern."""
        formatted_path = self._format_path(path)
        
        # Start search
        start_data = self._make_request(
            'SYNO.FileStation.Search', '2', 'start',
            folder_path=formatted_path,
            pattern=pattern
        )
        
        task_id = start_data.get('taskid')
        if not task_id:
            raise Exception("Failed to start search task")
        
        try:
            # Wait for search to complete
            import time
            while True:
                status_data = self._make_request(
                    'SYNO.FileStation.Search', '2', 'status',
                    taskid=task_id
                )
                
                if status_data.get('finished'):
                    break
                
                time.sleep(0.5)
            
            # Get results
            result_data = self._make_request(
                'SYNO.FileStation.Search', '2', 'list',
                taskid=task_id
            )
            
            files = result_data.get('files', [])
            return [{
                'name': file_info.get('name'),
                'path': file_info.get('path'),
                'type': 'directory' if file_info.get('isdir') else 'file',
                'size': file_info.get('size', 0)
            } for file_info in files]
            
        finally:
            # Clean up search task
            try:
                self._make_request(
                    'SYNO.FileStation.Search', '2', 'stop',
                    taskid=task_id
                )
            except:
                pass  # Ignore cleanup errors
    
    def rename_file(self, path: str, new_name: str) -> Dict[str, Any]:
        """Rename a file or directory.
        
        Args:
            path: Full path to the file/directory to rename
            new_name: New name for the file/directory (just the name, not full path)
        
        Returns:
            Dict with operation result
        """
        formatted_path = self._format_path(path)
        
        # Validate new name
        if not new_name or new_name.strip() == '':
            raise Exception("New name cannot be empty")
        
        # Remove any path separators from new name
        new_name = new_name.strip().replace('/', '').replace('\\', '')
        
        if not new_name:
            raise Exception("Invalid new name")
        
        # Use the rename API
        data = self._make_request(
            'SYNO.FileStation.Rename', '2', 'rename',
            path=formatted_path,
            name=new_name
        )
        
        # Get the parent directory path
        parent_dir = os.path.dirname(formatted_path)
        new_path = os.path.join(parent_dir, new_name).replace('\\', '/')
        
        return {
            'success': True,
            'old_path': formatted_path,
            'new_path': new_path,
            'old_name': os.path.basename(formatted_path),
            'new_name': new_name,
            'message': f"Successfully renamed '{os.path.basename(formatted_path)}' to '{new_name}'"
        }
    
    def move_file(self, source_path: str, destination_path: str, overwrite: bool = False) -> Dict[str, Any]:
        """Move a file or directory to a new location.
        
        Args:
            source_path: Full path to the file/directory to move
            destination_path: Destination path (can be directory or full path with new name)
            overwrite: Whether to overwrite existing files at destination
        
        Returns:
            Dict with operation result
        """
        formatted_source = self._format_path(source_path)
        formatted_dest = self._format_path(destination_path)
        
        # Validate paths
        if not formatted_source or formatted_source == '/':
            raise Exception("Invalid source path")
        
        if not formatted_dest or formatted_dest == '/':
            raise Exception("Invalid destination path")
        
        # Start the move operation
        start_data = self._make_request(
            'SYNO.FileStation.CopyMove', '3', 'start',
            path=formatted_source,
            dest_folder_path=formatted_dest,
            overwrite=overwrite,
            remove_src=True  # This makes it a move operation instead of copy
        )
        
        task_id = start_data.get('taskid')
        if not task_id:
            raise Exception("Failed to start move task")
        
        try:
            # Wait for move to complete
            import time
            max_wait_time = 60  # Maximum wait time in seconds
            wait_time = 0
            
            while wait_time < max_wait_time:
                status_data = self._make_request(
                    'SYNO.FileStation.CopyMove', '3', 'status',
                    taskid=task_id
                )
                
                if status_data.get('finished'):
                    # Check if there were any errors
                    if 'error' in status_data:
                        error_info = status_data['error']
                        raise Exception(f"Move failed: {error_info}")
                    
                    # Determine the final destination path
                    source_name = os.path.basename(formatted_source)
                    if formatted_dest.endswith('/') or not os.path.splitext(formatted_dest)[1]:
                        # Destination is a directory
                        final_dest = os.path.join(formatted_dest, source_name).replace('\\', '/')
                    else:
                        # Destination includes the new filename
                        final_dest = formatted_dest
                    
                    return {
                        'success': True,
                        'source_path': formatted_source,
                        'destination_path': final_dest,
                        'task_id': task_id,
                        'message': f"Successfully moved '{formatted_source}' to '{final_dest}'"
                    }
                
                time.sleep(0.5)
                wait_time += 0.5
            
            raise Exception(f"Move operation timed out after {max_wait_time} seconds")
            
        except Exception as e:
            # Try to stop the task if it's still running
            try:
                self._make_request(
                    'SYNO.FileStation.CopyMove', '3', 'stop',
                    taskid=task_id
                )
            except:
                pass  # Ignore cleanup errors
            raise e 