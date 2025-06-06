# src/synology_filestation.py - Synology FileStation API utilities

import requests
from typing import Dict, List, Any, Optional
from urllib.parse import quote
import os
import tempfile
import json
import unicodedata


class SynologyFileStation:
    """Handles Synology FileStation API operations."""
    
    def __init__(self, base_url: str, session_id: str):
        self.base_url = base_url.rstrip('/')
        self.session_id = session_id
        self.api_url = f"{self.base_url}/webapi/entry.cgi"
    
    def _make_request(self, api: str, version: str, method: str, use_post: bool = False, **params) -> Dict[str, Any]:
        """Make a request to Synology API."""
        request_params = {
            'api': api,
            'version': version,
            'method': method,
            '_sid': self.session_id,
            **params
        }
        
        if use_post:
            # For POST requests, ensure UTF-8 encoding for Unicode characters
            response = requests.post(
                self.api_url, 
                data=request_params,
                headers={'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'}
            )
        else:
            response = requests.get(self.api_url, params=request_params)
        response.raise_for_status()
        
        data = response.json()
        if not data.get('success'):
            error_code = data.get('error', {}).get('code', 'unknown')
            error_info = data.get('error', {})
            
            # Include detailed error information if available
            error_message = f"Synology API error: {error_code}"
            
            # Check for detailed errors array as mentioned in documentation
            if 'errors' in error_info and error_info['errors']:
                detailed_errors = []
                for err in error_info['errors']:
                    err_detail = f"Code {err.get('code', 'unknown')}"
                    if 'path' in err:
                        err_detail += f" for path: {err['path']}"
                    detailed_errors.append(err_detail)
                error_message += f" - Details: {'; '.join(detailed_errors)}"
            
            raise Exception(error_message)
        
        return data.get('data', {})
    
    def _make_upload_request(self, api: str, version: str, method: str, files: Dict[str, Any], **params) -> Dict[str, Any]:
        """Make an upload request to Synology API."""
        request_params = {
            'api': api,
            'version': version,
            'method': method,
            '_sid': self.session_id,
            **params
        }
        
        response = requests.post(self.api_url, params=request_params, files=files)
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
            
        # Normalize Unicode characters to NFC form (most common for filesystems)
        path = unicodedata.normalize('NFC', path)
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
        
        # According to official Synology API docs, path and name must be JSON arrays even for single values
        # The parameters should be formatted as: path=["/path"] and name=["name"]
        # Let requests library handle URL encoding automatically
        
        # Create JSON arrays without manual URL encoding - let requests handle it
        path_array = json.dumps([formatted_path])
        name_array = json.dumps([new_name])
        
        # Use GET request as specified in official documentation        
        data = self._make_request(
            'SYNO.FileStation.Rename', '2', 'rename',
            use_post=False,  # Official docs specify GET
            path=path_array,
            name=name_array
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
    
    def create_file(self, path: str, content: str = "", overwrite: bool = False) -> Dict[str, Any]:
        """Create a new file with specified content.
        
        Args:
            path: Full path where the file should be created (must start with /)
            content: Content to write to the file (default: empty string)
            overwrite: Whether to overwrite existing file (default: False)
        
        Returns:
            Dict with operation result
        """
        formatted_path = self._format_path(path)
        
        # Validate path
        if not formatted_path or formatted_path == '/':
            raise Exception("Invalid file path")
        
        # Get directory and filename
        directory = os.path.dirname(formatted_path)
        filename = os.path.basename(formatted_path)
        
        if not filename:
            raise Exception("Invalid filename")
        
        # Create temporary file with content
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            # Use direct session approach like the working implementation
            session = requests.session()
            
            with open(temp_file_path, 'rb') as payload:
                # Build URL with parameters
                url = f"{self.api_url}?api=SYNO.FileStation.Upload&version=2&method=upload&_sid={self.session_id}"
                
                # Create multipart data
                files = {
                    'file': (filename, payload, 'text/plain')
                }
                
                data = {
                    'path': directory,
                    'create_parents': 'true',
                    'overwrite': str(overwrite).lower()
                }
                
                # Make the request
                response = session.post(url, files=files, data=data)
                response.raise_for_status()
                
                result = response.json()
                
                if not result.get('success'):
                    error_code = result.get('error', {}).get('code', 'unknown')
                    raise Exception(f"Upload failed with error: {error_code}")
            
            session.close()
            
            return {
                'success': True,
                'path': formatted_path,
                'filename': filename,
                'directory': directory,
                'size': len(content.encode('utf-8')),
                'message': f"Successfully created file '{filename}' at '{directory}'"
            }
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except:
                pass  # Ignore cleanup errors
    
    def create_directory(self, folder_path: str, name: str, force_parent: bool = False) -> Dict[str, Any]:
        """Create a new directory.
        
        Args:
            folder_path: Parent directory path where the new folder should be created (must start with /)
            name: Name of the new directory to create
            force_parent: Whether to create parent directories if they don't exist (default: False)
        
        Returns:
            Dict with operation result
        """
        formatted_folder_path = self._format_path(folder_path)
        
        # Validate folder path
        if not formatted_folder_path:
            raise Exception("Invalid folder path")
        
        # Validate name
        if not name or name.strip() == '':
            raise Exception("Directory name cannot be empty")
        
        # Remove any path separators from name
        clean_name = name.strip().replace('/', '').replace('\\', '')
        
        if not clean_name:
            raise Exception("Invalid directory name")
        
        # Use the exact working pattern from the user's request
        data = self._make_request(
            'SYNO.FileStation.CreateFolder', '2', 'create',
            folder_path=formatted_folder_path,
            name=clean_name,
            force_parent=force_parent
        )
        
        folders = data.get('folders', [])
        if not folders:
            raise Exception("Failed to create directory - no folder data returned")
        
        created_folder = folders[0]
        full_path = created_folder.get('path', f"{formatted_folder_path}/{clean_name}")
        
        return {
            'success': True,
            'folder_path': formatted_folder_path,
            'name': clean_name,
            'full_path': full_path,
            'is_directory': created_folder.get('isdir', True),
            'force_parent': force_parent,
            'message': f"Successfully created directory '{clean_name}' at '{formatted_folder_path}'"
        }
    
    def delete(self, path: str) -> Dict[str, Any]:
        """Delete a file or directory (auto-detects type).
        
        Args:
            path: Full path to the file/directory to delete (must start with /)
        
        Returns:
            Dict with operation result
        """
        formatted_path = self._format_path(path)
        
        # Validate path
        if not formatted_path or formatted_path == '/':
            raise Exception("Invalid path - cannot delete root")
        
        # Safety check for critical paths
        critical_paths = ['/volume1', '/homes', '/var', '/etc', '/usr', '/bin', '/sbin']
        if any(formatted_path.startswith(critical_path) for critical_path in critical_paths):
            if formatted_path in critical_paths:
                raise Exception(f"Cannot delete critical system path: {formatted_path}")
        
        # Auto-detect if this is a file or directory
        try:
            file_info = self.get_file_info(formatted_path)
            recursive = file_info.get('type') == 'directory'
        except:
            recursive = False  # Default to file behavior if can't determine
        
        item_name = os.path.basename(formatted_path)
        item_type = "directory" if recursive else "file"
        
        # Use the correct API format according to documentation
        path_array = json.dumps([formatted_path])
        
        # Start the delete task (async operation)
        start_data = self._make_request(
            'SYNO.FileStation.Delete', '2', 'start',
            path=path_array,
            accurate_progress='true',
            recursive=str(recursive).lower()
        )
        
        task_id = start_data.get('taskid')
        if not task_id:
            raise Exception("Failed to start delete task")
        
        try:
            # Wait for delete to complete
            import time
            max_wait_time = 120  # Maximum wait time (2 minutes)
            wait_time = 0
            
            while wait_time < max_wait_time:
                status_data = self._make_request(
                    'SYNO.FileStation.Delete', '2', 'status',
                    taskid=task_id
                )
                
                if status_data.get('finished'):
                    # Check if there were any errors
                    if 'error' in status_data:
                        error_info = status_data['error']
                        raise Exception(f"Delete failed: {error_info}")
                    
                    return {
                        'success': True,
                        'path': formatted_path,
                        'item_name': item_name,
                        'item_type': item_type,
                        'recursive': recursive,
                        'task_id': task_id,
                        'message': f"Successfully deleted {item_type} '{item_name}'"
                    }
                
                time.sleep(0.5)
                wait_time += 0.5
            
            raise Exception(f"Delete operation timed out after {max_wait_time} seconds")
            
        except Exception as e:
            # Try to stop the task if it's still running
            try:
                self._make_request(
                    'SYNO.FileStation.Delete', '2', 'stop',
                    taskid=task_id
                )
            except:
                pass  # Ignore cleanup errors
            raise e
    
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