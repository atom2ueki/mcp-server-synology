# src/synology_filestation.py - Synology FileStation API utilities

import base64
import binascii
import hashlib
import json
import logging
import mimetypes
import os
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SynologyFileStation:
    """Handles Synology FileStation API operations."""

    DEFAULT_MAX_BYTES = 1024 * 1024
    MAX_CONTENT_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        base_url: str,
        session_id: str,
        verify_ssl: bool = False,
        syno_token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.verify_ssl = verify_ssl
        self.syno_token = syno_token
        self.api_url = f"{self.base_url}/webapi/entry.cgi"

    def _csrf_headers(self, *, post: bool) -> Dict[str, str]:
        """Build request headers, including X-SYNO-TOKEN for DSM 7.3.2+ CSRF.

        Always sets the UTF-8 charset on POSTs (preserves prior Unicode behavior).
        """
        headers: Dict[str, str] = {}
        if post:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        if self.syno_token:
            headers["X-SYNO-TOKEN"] = self.syno_token
        return headers

    def _relogin_and_refresh(self) -> bool:
        """Re-authenticate via the SynologyAuth registered for this base_url.

        On success, refreshes this instance's `_sid` / X-SYNO-TOKEN and returns
        True. Returns False when no auth is registered (e.g. standalone use) or
        the relogin fails, in which case the caller keeps the original error.

        Import is deferred to runtime to avoid a circular import
        (auth -> filestation). `stale_session_id` is passed through to
        `SynologyAuth.relogin()` so that concurrent recoveries collapse into a
        single relogin (matching SynologyAPIClient's behavior).
        """
        stale_session_id = self.session_id
        try:
            from auth.synology_auth import get_auth_for_url
        except ImportError as exc:
            # Deferred to dodge a circular import; a real failure here (e.g. a
            # syntax error introduced in synology_auth) would otherwise silently
            # leave the caller stuck on the 119. Log it so it's observable.
            logger.warning("Cannot recover from DSM error 119 — auth module unavailable: %s", exc)
            return False
        auth = get_auth_for_url(self.base_url)
        if auth is None or not auth.relogin(stale_session_id):
            return False
        self.session_id = auth.current_session_id
        self.syno_token = auth.current_syno_token
        return True

    def _make_request(
        self, api: str, version: str, method: str, use_post: bool = False, **params
    ) -> Dict[str, Any]:
        """Make a request to Synology API.

        Recovers transparently from DSM error 119 ("SID not found") — returned
        once the server-side session dies (idle expiry, or a DSM reboot which
        drops every session). FileStation keeps its own SID, so it needs the
        same single-retry re-auth that SynologyAPIClient performs; otherwise the
        SID stays dead until the process is restarted.
        """
        data = self._send(api, version, method, use_post, params)

        if not data.get("success") and data.get("error", {}).get("code") == 119:
            if self._relogin_and_refresh():
                data = self._send(api, version, method, use_post, params)

        if not data.get("success"):
            error_code = data.get("error", {}).get("code", "unknown")
            error_info = data.get("error", {})

            # Include detailed error information if available
            error_message = f"Synology API error: {error_code}"

            # Check for detailed errors array as mentioned in documentation
            if "errors" in error_info and error_info["errors"]:
                detailed_errors = []
                for err in error_info["errors"]:
                    err_detail = f"Code {err.get('code', 'unknown')}"
                    if "path" in err:
                        err_detail += f" for path: {err['path']}"
                    detailed_errors.append(err_detail)
                error_message += f" - Details: {'; '.join(detailed_errors)}"

            raise Exception(error_message)

        return data.get("data", {})

    def _send(
        self, api: str, version: str, method: str, use_post: bool, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Internal: perform the HTTP call and return the parsed body, no retry."""
        request_params = {
            "api": api,
            "version": version,
            "method": method,
            "_sid": self.session_id,
            **params,
        }

        if use_post:
            response = requests.post(
                self.api_url,
                data=request_params,
                headers=self._csrf_headers(post=True),
                verify=self.verify_ssl,
                timeout=15,
            )
        else:
            response = requests.get(
                self.api_url,
                params=request_params,
                headers=self._csrf_headers(post=False) or None,
                verify=self.verify_ssl,
                timeout=15,
            )
        response.raise_for_status()

        return response.json()

    def _make_upload_request(
        self, api: str, version: str, method: str, files: Dict[str, Any], **params
    ) -> Dict[str, Any]:
        """Make an upload request to Synology API.

        NOTE: this path deliberately does NOT recover from DSM error 119, unlike
        `_make_request`. Uploads are multipart and `files` holds file streams
        that have already been consumed by the first attempt; a blind replay
        would send truncated content. Retrying safely needs stream rewinding or
        re-opening the file, which is a larger change than this method should
        take on. A 119 here surfaces to the caller as-is.
        """
        request_params = {
            "api": api,
            "version": version,
            "method": method,
            "_sid": self.session_id,
            **params,
        }

        # Multipart upload — let requests set Content-Type with the boundary;
        # we only thread the X-SYNO-TOKEN header (no charset override here).
        upload_headers = {"X-SYNO-TOKEN": self.syno_token} if self.syno_token else None
        response = requests.post(
            self.api_url,
            params=request_params,
            files=files,
            headers=upload_headers,
            verify=self.verify_ssl,
            timeout=15,
        )
        response.raise_for_status()

        data = response.json()
        if not data.get("success"):
            error_code = data.get("error", {}).get("code", "unknown")
            raise Exception(f"Synology API error: {error_code}")

        return data.get("data", {})

    @staticmethod
    def _extract_size(file_info: Dict[str, Any]) -> int:
        """Pull the byte size out of a FileStation file entry.

        DSM returns the size inside the `additional` block (because we ask for
        the `size` field there), not at the top level of the entry. Reading only
        the top-level key made every file report 0 bytes. Keep the top-level
        lookup as a fallback for API versions that do inline it.
        """
        additional = file_info.get("additional") or {}
        size = additional.get("size")
        if size is None:
            size = file_info.get("size", 0)
        return size or 0

    def _format_path(self, path: str) -> str:
        """Format path for Synology API."""
        if not path.startswith("/"):
            path = "/" + path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # Normalize Unicode characters to NFC form (most common for filesystems)
        path = unicodedata.normalize("NFC", path)
        return path

    def list_shares(self) -> List[Dict[str, Any]]:
        """List all available shares, with timestamps.

        The `time` additional is requested because a share's access time is
        often the only externally visible proof that a scheduled job touched it.
        Active Backup for Google Workspace exposes no task-state API at all
        (verified against SYNO.API.Info query=all on DSM 7.3.2 — only the
        restore-portal APIs exist), so the backup destination share's atime is
        the sole signal available through this server that last night's run
        happened. Dropping these fields forced callers out to hand-rolled DSM
        API scripts to get at them.

        DSM 7.3.2 silently ignores the comma-string form of `additional`; it
        must be a JSON array (the same quirk already documented in
        list_directory).
        """
        data = self._make_request(
            "SYNO.FileStation.List", "2", "list_share", additional=json.dumps(["time"])
        )
        shares = data.get("shares", [])

        result: List[Dict[str, Any]] = []
        for share in shares:
            entry: Dict[str, Any] = {
                "name": share.get("name"),
                "path": share.get("path"),
                "description": share.get("desc", ""),
                "is_writable": share.get("iswritable", False),
            }
            times = (share.get("additional") or {}).get("time") or {}
            for key in ("atime", "mtime", "ctime", "crtime"):
                epoch = times.get(key)
                if epoch is None:
                    continue
                entry[key] = epoch
                try:
                    # UTC, with an explicit offset. A naive local-time string
                    # would be read in whatever timezone the consumer assumes,
                    # which is wrong whenever the MCP host and the reader differ
                    # (a container running in UTC is the common case) and can
                    # land on the wrong date entirely. The raw epoch is kept
                    # alongside it for anyone who wants to localise themselves.
                    entry[f"{key}_iso"] = (
                        datetime.fromtimestamp(epoch, tz=timezone.utc)
                        .isoformat(timespec="seconds")
                        .replace("+00:00", "Z")
                    )
                except (OverflowError, OSError, ValueError, TypeError):
                    # A nonsense epoch must not take down the whole listing.
                    pass
            result.append(entry)
        return result

    def list_directory(self, path: str, additional_info: bool = True) -> List[Dict[str, Any]]:
        """List contents of a directory."""
        formatted_path = self._format_path(path)

        params: Dict[str, Any] = {"folder_path": formatted_path}

        if additional_info:
            # DSM 7.3.2 silently drops the comma-string form; expects a JSON array.
            # Verified live: comma-string → no `additional` field in response;
            # JSON array → returns time/size/owner/perm as documented.
            params["additional"] = json.dumps(["time", "size", "owner", "perm"])

        data = self._make_request("SYNO.FileStation.List", "2", "list", **params)
        files = data.get("files", [])

        result = []
        for file_info in files:
            item = {
                "name": file_info.get("name"),
                "path": file_info.get("path"),
                "type": "directory" if file_info.get("isdir") else "file",
                "size": self._extract_size(file_info),
            }

            # Add additional info if available
            if "additional" in file_info:
                additional = file_info["additional"]

                if "time" in additional:
                    time_info = additional["time"]
                    item.update(
                        {
                            "created": time_info.get("crtime"),
                            "modified": time_info.get("mtime"),
                            "accessed": time_info.get("atime"),
                        }
                    )

                if "owner" in additional:
                    owner_info = additional["owner"]
                    item.update(
                        {
                            "owner": owner_info.get("user", "unknown"),
                            "group": owner_info.get("group", "unknown"),
                        }
                    )

                if "perm" in additional:
                    perm_info = additional["perm"]
                    item["permissions"] = perm_info.get("posix", "unknown")

            result.append(item)

        return result

    def get_file_info(self, path: str) -> Dict[str, Any]:
        """Get detailed information about a file or directory."""
        formatted_path = self._format_path(path)

        data = self._make_request(
            "SYNO.FileStation.List",
            "2",
            "getinfo",
            path=formatted_path,
            # DSM 7.3.2 requires JSON array; comma-string is silently ignored.
            additional=json.dumps(["time", "size", "owner", "perm"]),
        )

        files = data.get("files", [])
        if not files:
            raise FileNotFoundError(f"File not found: {path}")

        file_info = files[0]
        # DSM does not fail the request for a path that doesn't exist. It answers
        # `success: true` with a per-entry error instead — `{"code": 408, "path": ...}`
        # and no `name`/`isdir`. Reporting that as a real file made every existence
        # check say yes, so surface it as the error it is.
        if "code" in file_info and "name" not in file_info:
            raise FileNotFoundError(f"File not found: {path} (Synology API error: {file_info['code']})")

        result = {
            "name": file_info.get("name"),
            "path": file_info.get("path"),
            "type": "directory" if file_info.get("isdir") else "file",
            "size": self._extract_size(file_info),
        }

        # Add additional info
        if "additional" in file_info:
            additional = file_info["additional"]

            if "time" in additional:
                time_info = additional["time"]
                result.update(
                    {
                        "created": time_info.get("crtime"),
                        "modified": time_info.get("mtime"),
                        "accessed": time_info.get("atime"),
                    }
                )

            if "owner" in additional:
                owner_info = additional["owner"]
                result.update(
                    {
                        "owner": owner_info.get("user", "unknown"),
                        "group": owner_info.get("group", "unknown"),
                    }
                )

            if "perm" in additional:
                perm_info = additional["perm"]
                result["permissions"] = perm_info.get("posix", "unknown")

        return result

    # SYNO.FileStation.Search intermittently discards a task right after `start`
    # returns its id. Measured live on DSM 7.3.2 over 120 tasks: ~40% of tasks
    # survive, and the failure is independent of session reuse, cleanup strategy
    # and inter-task delay (0s/1s/2s/4s/8s spacing all landed in the 25-67%
    # band). The longest run of consecutive discards observed in 60 back-to-back
    # tasks was 9, so retry generously: at ~60% discard odds, 20 attempts leaves
    # a ~1-in-30,000 chance of spurious failure. Since spacing doesn't help,
    # back off only enough to stay polite.
    SEARCH_MAX_ATTEMPTS = 20
    SEARCH_RETRY_DELAY = 0.2
    SEARCH_PAGE_SIZE = 1000

    def search_files(self, path: str, pattern: str, timeout: float = 60.0) -> List[Dict[str, Any]]:
        """Search for files and folders whose name contains `pattern`.

        DSM matches `pattern` as a case-insensitive substring of the entry name;
        wildcards carry no special meaning (verified live: `*.dcm`, `dcm` and
        `*dcm*` all return the same 24 entries). Searching is recursive.
        """
        formatted_path = self._format_path(path)

        for _attempt in range(self.SEARCH_MAX_ATTEMPTS):
            results = self._run_search_task(formatted_path, pattern, timeout)
            if results is not None:
                return results
            # Waiting longer doesn't improve the odds; just don't hammer.
            time.sleep(self.SEARCH_RETRY_DELAY)

        raise Exception(
            f"Search failed: DSM discarded the search task "
            f"{self.SEARCH_MAX_ATTEMPTS} times in a row. This is a known "
            f"FileStation quirk — retrying usually succeeds."
        )

    def _run_search_task(
        self, formatted_path: str, pattern: str, timeout: float
    ) -> Optional[List[Dict[str, Any]]]:
        """Run one search task to completion.

        Returns the matches, or None if DSM discarded the task (caller retries).
        """
        start_data = self._make_request(
            "SYNO.FileStation.Search",
            "2",
            "start",
            folder_path=formatted_path,
            pattern=pattern,
        )

        task_id = start_data.get("taskid")
        if not task_id:
            raise Exception("Failed to start search task")

        try:
            deadline = time.monotonic() + timeout
            # A live task always echoes `total`/`files`, even while running. A
            # discarded one answers `{"finished": true}` with neither key — the
            # same body DSM returns for a taskid that never existed. Require two
            # consecutive such replies so a momentary blip isn't mistaken for it.
            consecutive_missing = 0

            while time.monotonic() < deadline:
                page = self._search_page(task_id, offset=0)

                if page is None:
                    consecutive_missing += 1
                    if consecutive_missing >= 2:
                        return None
                    time.sleep(0.3)
                    continue

                consecutive_missing = 0
                if not page.get("finished"):
                    time.sleep(0.5)
                    continue

                return self._collect_search_results(task_id, page)

            raise Exception(f"Search timed out after {timeout:.0f}s")

        finally:
            self._cleanup_search_task(task_id)

    def _search_page(self, task_id: str, offset: int) -> Optional[Dict[str, Any]]:
        """Fetch one page of search results, or None if the task is gone."""
        data = self._make_request(
            "SYNO.FileStation.Search",
            "2",
            "list",
            taskid=task_id,
            additional=json.dumps(["size", "time", "owner", "perm"]),
            offset=offset,
            limit=self.SEARCH_PAGE_SIZE,
        )

        if "total" not in data and "files" not in data:
            return None
        return data

    def _collect_search_results(
        self, task_id: str, first_page: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """Page through a finished task and format every match.

        Returns None if the task vanishes mid-collection, so the caller
        restarts with a fresh task — partial results are never returned,
        since `total` proving more matches exist would make a truncated
        list look like a complete one. A single odd reply is tolerated;
        two consecutive missing pages mean the task is gone.
        """
        files = list(first_page.get("files", []))
        total = first_page.get("total", len(files))

        consecutive_missing = 0
        while len(files) < total:
            page = self._search_page(task_id, offset=len(files))
            if page is None:
                consecutive_missing += 1
                if consecutive_missing >= 2:
                    return None
                time.sleep(0.3)
                continue
            consecutive_missing = 0
            batch = page.get("files", [])
            if not batch:
                break
            files.extend(batch)

        return [
            {
                "name": file_info.get("name"),
                "path": file_info.get("path"),
                "type": "directory" if file_info.get("isdir") else "file",
                "size": self._extract_size(file_info),
            }
            for file_info in files
        ]

    def _cleanup_search_task(self, task_id: str) -> None:
        """Release a search task. `stop` halts it, `clean` frees its slot."""
        for method in ("stop", "clean"):
            try:
                self._make_request("SYNO.FileStation.Search", "2", method, taskid=task_id)
            except Exception:
                pass  # Best-effort cleanup; a failure here must not mask results.

    def rename_file(self, path: str, new_name: str) -> Dict[str, Any]:
        """Rename a file or directory.

        Args:
            path: Full path to the file/directory to rename
            new_name: New name for the file/directory (just the name, not full path)

        Returns:
            Dict with operation result
        """
        formatted_path = self._format_path(path)

        # Check for critical paths
        self._check_critical_path(formatted_path)

        # Validate new name
        if not new_name or new_name.strip() == "":
            raise Exception("New name cannot be empty")

        # Remove any path separators from new name
        new_name = new_name.strip().replace("/", "").replace("\\", "")

        if not new_name:
            raise Exception("Invalid new name")

        # According to official Synology API docs, path and name must be JSON arrays even for single values
        # The parameters should be formatted as: path=["/path"] and name=["name"]
        # Let requests library handle URL encoding automatically

        # Create JSON arrays without manual URL encoding - let requests handle it
        path_array = json.dumps([formatted_path])
        name_array = json.dumps([new_name])

        # Use GET request as specified in official documentation
        self._make_request(
            "SYNO.FileStation.Rename",
            "2",
            "rename",
            use_post=False,  # Official docs specify GET
            path=path_array,
            name=name_array,
        )

        # Get the parent directory path
        parent_dir = os.path.dirname(formatted_path)
        new_path = os.path.join(parent_dir, new_name).replace("\\", "/")

        return {
            "success": True,
            "old_path": formatted_path,
            "new_path": new_path,
            "old_name": os.path.basename(formatted_path),
            "new_name": new_name,
            "message": f"Successfully renamed '{os.path.basename(formatted_path)}' to '{new_name}'",
        }

    def create_file(
        self,
        path: str,
        content: str = "",
        overwrite: bool = False,
        encoding: str = "text",
    ) -> Dict[str, Any]:
        """Create a new file with specified content.

        Args:
            path: Full path where the file should be created (must start with /)
            content: Content to write to the file (default: empty string)
            overwrite: Whether to overwrite existing file (default: False)
            encoding: ``text`` for UTF-8 text or ``base64`` for arbitrary bytes

        Returns:
            Dict with operation result
        """
        formatted_path = self._format_path(path)

        # Validate path
        if not formatted_path or formatted_path == "/":
            raise Exception("Invalid file path")

        # Get directory and filename
        directory = os.path.dirname(formatted_path)
        filename = os.path.basename(formatted_path)

        if not filename:
            raise Exception("Invalid filename")

        if encoding == "text":
            if not isinstance(content, str):
                raise ValueError("Text content must be a string")
            payload_bytes = content.encode("utf-8")
            media_type = "text/plain; charset=utf-8"
        elif encoding == "base64":
            if not isinstance(content, str):
                raise ValueError("Base64 content must be a string")
            try:
                payload_bytes = base64.b64decode(content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid base64 content") from exc
            media_type = "application/octet-stream"
        else:
            raise ValueError("encoding must be 'text' or 'base64'")

        if len(payload_bytes) > self.MAX_CONTENT_BYTES:
            raise ValueError(
                f"Decoded content exceeds the {self.MAX_CONTENT_BYTES}-byte upload limit"
            )

        # Keep the multipart upload stream seekable and deterministic.
        with tempfile.NamedTemporaryFile(mode="wb", delete=False) as temp_file:
            temp_file.write(payload_bytes)
            temp_file_path = temp_file.name

        try:
            # Use context manager for session to prevent resource leak
            with requests.Session() as session:
                with open(temp_file_path, "rb") as payload:
                    # Build URL with parameters
                    url = f"{self.api_url}?api=SYNO.FileStation.Upload&version=2&method=upload&_sid={self.session_id}"

                    # Create multipart data
                    files = {"file": (filename, payload, media_type)}

                    data = {
                        "path": directory,
                        "create_parents": "true",
                        "overwrite": str(overwrite).lower(),
                    }

                    # Make the request — thread X-SYNO-TOKEN for DSM 7.3.2+ CSRF;
                    # let requests set Content-Type with the multipart boundary.
                    upload_headers = {"X-SYNO-TOKEN": self.syno_token} if self.syno_token else None
                    response = session.post(
                        url,
                        files=files,
                        data=data,
                        headers=upload_headers,
                        verify=self.verify_ssl,
                        timeout=15,
                    )
                    response.raise_for_status()

                    result = response.json()

                    if not result.get("success"):
                        error_code = result.get("error", {}).get("code", "unknown")
                        raise Exception(f"Upload failed with error: {error_code}")

            return {
                "success": True,
                "path": formatted_path,
                "filename": filename,
                "directory": directory,
                "size": len(payload_bytes),
                "encoding": encoding,
                "message": f"Successfully created file '{filename}' at '{directory}'",
            }

        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass  # Ignore cleanup errors

    def create_directory(
        self, folder_path: str, name: str, force_parent: bool = False
    ) -> Dict[str, Any]:
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
        if not name or name.strip() == "":
            raise Exception("Directory name cannot be empty")

        # Remove any path separators from name
        clean_name = name.strip().replace("/", "").replace("\\", "")

        if not clean_name:
            raise Exception("Invalid directory name")

        # Use the exact working pattern from the user's request
        data = self._make_request(
            "SYNO.FileStation.CreateFolder",
            "2",
            "create",
            # DSM expects arrays even for one folder/name. Plain strings can
            # return error 400 or silently create nothing on recent DSM builds.
            folder_path=json.dumps([formatted_folder_path]),
            name=json.dumps([clean_name]),
            force_parent=str(force_parent).lower(),
        )

        folders = data.get("folders", [])
        if not folders:
            raise Exception("Failed to create directory - no folder data returned")

        created_folder = folders[0]
        full_path = created_folder.get("path", f"{formatted_folder_path}/{clean_name}")

        return {
            "success": True,
            "folder_path": formatted_folder_path,
            "name": clean_name,
            "full_path": full_path,
            "is_directory": created_folder.get("isdir", True),
            "force_parent": force_parent,
            "message": f"Successfully created directory '{clean_name}' at '{formatted_folder_path}'",
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
        if not formatted_path or formatted_path == "/":
            raise Exception("Invalid path - cannot delete root")

        # Safety check for critical paths
        critical_paths = ["/volume1", "/homes", "/var", "/etc", "/usr", "/bin", "/sbin"]
        # Check if path IS or STARTS WITH any critical path (with / to prevent /volume11 bypass)
        if any(
            formatted_path == cp or formatted_path.startswith(cp + "/") for cp in critical_paths
        ):
            raise Exception(f"Cannot delete critical system path: {formatted_path}")

        # Auto-detect if this is a file or directory
        try:
            file_info = self.get_file_info(formatted_path)
            recursive = file_info.get("type") == "directory"
        except Exception:
            recursive = False  # Default to file behavior if can't determine

        item_name = os.path.basename(formatted_path)
        item_type = "directory" if recursive else "file"

        # Use the correct API format according to documentation
        path_array = json.dumps([formatted_path])

        # Start the delete task (async operation)
        start_data = self._make_request(
            "SYNO.FileStation.Delete",
            "2",
            "start",
            path=path_array,
            accurate_progress="true",
            recursive=str(recursive).lower(),
        )

        task_id = start_data.get("taskid")
        if not task_id:
            raise Exception("Failed to start delete task")

        try:
            # Wait for delete to complete
            import time

            max_wait_time = 120  # Maximum wait time (2 minutes)
            wait_time = 0.0

            while wait_time < max_wait_time:
                status_data = self._make_request(
                    "SYNO.FileStation.Delete", "2", "status", taskid=task_id
                )

                if status_data.get("finished"):
                    # Check if there were any errors
                    if "error" in status_data:
                        error_info = status_data["error"]
                        raise Exception(f"Delete failed: {error_info}")

                    return {
                        "success": True,
                        "path": formatted_path,
                        "item_name": item_name,
                        "item_type": item_type,
                        "recursive": recursive,
                        "task_id": task_id,
                        "message": f"Successfully deleted {item_type} '{item_name}'",
                    }

                time.sleep(0.5)
                wait_time += 0.5

            raise Exception(f"Delete operation timed out after {max_wait_time} seconds")

        except Exception as e:
            # Try to stop the task if it's still running
            try:
                self._make_request("SYNO.FileStation.Delete", "2", "stop", taskid=task_id)
            except Exception:
                pass  # Ignore cleanup errors
            raise e

    def _check_critical_path(self, path: str) -> None:
        """Check if path is critical and raise exception if so.

        Args:
            path: Formatted path to check

        Raises:
            Exception: If path is a critical system path
        """
        critical_paths = ["/volume1", "/homes", "/var", "/etc", "/usr", "/bin", "/sbin"]
        if path in critical_paths:
            raise Exception(f"Cannot access critical system path: {path}")

    def get_file_content(
        self,
        path: str,
        encoding: str = "text",
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> Any:
        """Read a file losslessly as strict UTF-8 text or structured base64."""
        formatted_path = self._format_path(path)

        # Check for critical paths
        self._check_critical_path(formatted_path)

        if encoding not in {"text", "base64"}:
            raise ValueError("encoding must be 'text' or 'base64'")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if max_bytes > self.MAX_CONTENT_BYTES:
            raise ValueError(f"max_bytes cannot exceed {self.MAX_CONTENT_BYTES}")

        info = self.get_file_info(formatted_path)
        if info.get("type") != "file":
            raise ValueError(f"Path is not a regular file: {formatted_path}")
        expected_size = info.get("size")
        if not isinstance(expected_size, int) or expected_size < 0:
            raise ValueError("FileStation returned an invalid file size")
        if expected_size > max_bytes:
            raise ValueError(
                f"File is {expected_size} bytes, exceeding max_bytes={max_bytes}"
            )

        # Use the download API and consume raw bytes. response.text is lossy for
        # binary data because requests decodes it before the MCP sees it.
        download_headers = {"X-SYNO-TOKEN": self.syno_token} if self.syno_token else None
        response = requests.get(
            f"{self.base_url}/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.Download",
                "version": "2",
                "method": "download",
                "path": formatted_path,
                "_sid": self.session_id,
            },
            headers=download_headers,
            verify=self.verify_ssl,
            stream=True,
            timeout=15,
        )
        try:
            response.raise_for_status()

            # Check for API error in the headers (download API is special).
            if "application/json" in response.headers.get("Content-Type", ""):
                error_data = response.json()
                if not error_data.get("success"):
                    error_code = error_data.get("error", {}).get("code", "unknown")
                    raise Exception(f"Synology API error: {error_code}")

            chunks = []
            downloaded = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise ValueError(f"Downloaded content exceeds max_bytes={max_bytes}")
                chunks.append(chunk)
            raw = b"".join(chunks)
        finally:
            response.close()

        if len(raw) != expected_size:
            raise IOError(
                f"Downloaded byte count changed or was truncated: expected {expected_size}, got {len(raw)}"
            )

        if encoding == "text":
            try:
                return raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    "File is not valid UTF-8 text; retry with encoding='base64'"
                ) from exc

        mime_type = mimetypes.guess_type(formatted_path)[0] or "application/octet-stream"
        return {
            "path": formatted_path,
            "encoding": "base64",
            "mime_type": mime_type,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": base64.b64encode(raw).decode("ascii"),
        }

    def _is_directory(self, path: str) -> bool:
        """True if `path` exists and is a directory."""
        try:
            return self.get_file_info(path).get("type") == "directory"
        except FileNotFoundError:
            return False

    def move_file(
        self, source_path: str, destination_path: str, overwrite: bool = False
    ) -> Dict[str, Any]:
        """Move a file or directory to a new location.

        Args:
            source_path: Full path to the file/directory to move
            destination_path: An existing directory to move into, or a full path
                whose last segment is the new name
            overwrite: Whether to overwrite existing files at destination

        Returns:
            Dict with operation result
        """
        formatted_source = self._format_path(source_path)
        formatted_dest = self._format_path(destination_path)

        # SYNO.FileStation.CopyMove takes `dest_folder_path` and cannot rename on
        # the way, so a destination ending in a new filename used to fail with
        # 1002/408. Honour the documented "full path with new name" form by
        # renaming around the move.
        if formatted_dest != formatted_source and not self._is_directory(formatted_dest):
            dest_parent, _, new_name = formatted_dest.rpartition("/")
            dest_parent = dest_parent or "/"
            if not self._is_directory(dest_parent):
                raise Exception(f"Destination directory does not exist: {dest_parent}")

            src_parent, _, src_name = formatted_source.rpartition("/")
            src_parent = src_parent or "/"

            if src_parent == dest_parent:
                return self.rename_file(formatted_source, new_name)

            # Same basename, different directory — plain move, no rename needed.
            # Without this guard, `staged` would equal `formatted_source` itself,
            # making `_path_exists(staged)` trivially True and routing into the
            # move→rename collision fallback, which then tries to rename the
            # just-moved file to the name it already has.
            if new_name == src_name:
                return self._move_into_folder(formatted_source, dest_parent, overwrite)

            # Pre-check: if overwrite is False and the target already exists, fail
            # before any mutation so the source isn't left in a renamed state.
            if not overwrite and self._path_exists(f"{dest_parent}/{new_name}"):
                raise Exception(f"Destination path already exists: {dest_parent}/{new_name}")

            # Rename first so the move can't collide with a same-named file in the
            # destination; if that name is taken here, move first and rename after.
            staged = f"{src_parent}/{new_name}"
            if self._is_directory(staged) or self._path_exists(staged):
                self._move_into_folder(formatted_source, dest_parent, overwrite)
                return self.rename_file(f"{dest_parent}/{src_name}", new_name)

            self.rename_file(formatted_source, new_name)
            return self._move_into_folder(staged, dest_parent, overwrite)

        return self._move_into_folder(formatted_source, formatted_dest, overwrite)

    def copy_file(
        self, source_path: str, destination_folder: str, overwrite: bool = False
    ) -> Dict[str, Any]:
        """Copy one regular file server-side into an existing directory.

        This verifies the resulting path and byte count. It does not make a
        transactionally consistent snapshot of a live database such as SQLite.
        """
        formatted_source = self._format_path(source_path)
        formatted_dest = self._format_path(destination_folder)
        source_info = self.get_file_info(formatted_source)
        if source_info.get("type") != "file":
            raise ValueError("copy_file only supports regular files")
        if not self._is_directory(formatted_dest):
            raise ValueError(f"Destination directory does not exist: {formatted_dest}")
        return self._copy_move_into_folder(
            formatted_source,
            formatted_dest,
            overwrite,
            remove_source=False,
            source_info=source_info,
        )

    def _path_exists(self, path: str) -> bool:
        try:
            self.get_file_info(path)
            return True
        except FileNotFoundError:
            return False

    def _move_into_folder(
        self, formatted_source: str, formatted_dest: str, overwrite: bool
    ) -> Dict[str, Any]:
        """Move `formatted_source` into the existing folder `formatted_dest`."""

        return self._copy_move_into_folder(
            formatted_source,
            formatted_dest,
            overwrite,
            remove_source=True,
        )

    def _copy_move_into_folder(
        self,
        formatted_source: str,
        formatted_dest: str,
        overwrite: bool,
        *,
        remove_source: bool,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run and verify one DSM CopyMove task."""

        # Check for critical paths
        self._check_critical_path(formatted_source)
        self._check_critical_path(formatted_dest)

        # Validate paths
        if not formatted_source or formatted_source == "/":
            raise Exception("Invalid source path")

        if not formatted_dest or formatted_dest == "/":
            raise Exception("Invalid destination path")

        source_info = source_info or self.get_file_info(formatted_source)
        if not self._is_directory(formatted_dest):
            raise ValueError(f"Destination directory does not exist: {formatted_dest}")
        final_dest = os.path.join(formatted_dest, os.path.basename(formatted_source)).replace(
            "\\", "/"
        )
        if final_dest == formatted_source:
            raise ValueError("Source and destination resolve to the same path")
        if not overwrite and self._path_exists(final_dest):
            raise FileExistsError(f"Destination path already exists: {final_dest}")

        # DSM requires a JSON array for path. A scalar may return a completed
        # task with found_file_num=0 and no copied/moved file.
        start_data = self._make_request(
            "SYNO.FileStation.CopyMove",
            "3",
            "start",
            path=json.dumps([formatted_source]),
            dest_folder_path=formatted_dest,
            overwrite=str(overwrite).lower(),
            remove_src=str(remove_source).lower(),
            accurate_progress="true",
        )

        task_id = start_data.get("taskid")
        if not task_id:
            operation = "move" if remove_source else "copy"
            raise Exception(f"Failed to start {operation} task")

        try:
            # Wait for move to complete
            import time

            max_wait_time = 60  # Maximum wait time in seconds
            wait_time = 0.0

            while wait_time < max_wait_time:
                status_data = self._make_request(
                    "SYNO.FileStation.CopyMove", "3", "status", taskid=task_id
                )

                if status_data.get("finished"):
                    # Check if there were any errors
                    if "error" in status_data:
                        error_info = status_data["error"]
                        raise Exception(f"Move failed: {error_info}")

                    # found_file_num is unreliable on DSM: successful tasks can
                    # report zero. Verify the actual target path and byte count.
                    target_info = self.get_file_info(final_dest)
                    if target_info.get("type") != source_info.get("type"):
                        raise IOError(f"Destination type does not match source: {final_dest}")
                    if source_info.get("type") == "file" and target_info.get(
                        "size"
                    ) != source_info.get("size"):
                        raise IOError(
                            f"Destination byte count does not match source: {final_dest}"
                        )
                    if remove_source and self._path_exists(formatted_source):
                        raise IOError(f"Source still exists after move: {formatted_source}")

                    operation = "moved" if remove_source else "copied"

                    return {
                        "success": True,
                        "source_path": formatted_source,
                        "destination_path": final_dest,
                        "size": target_info.get("size"),
                        "verified": True,
                        "task_id": task_id,
                        "message": f"Successfully {operation} '{formatted_source}' to '{final_dest}'",
                    }

                time.sleep(0.5)
                wait_time += 0.5

            operation = "Move" if remove_source else "Copy"
            raise Exception(f"{operation} operation timed out after {max_wait_time} seconds")

        except Exception as e:
            # Try to stop the task if it's still running
            try:
                self._make_request("SYNO.FileStation.CopyMove", "3", "stop", taskid=task_id)
            except Exception:
                pass  # Ignore cleanup errors
            raise
