"""Integration tests for the delete handler: confirmation -> trash -> audit.

Runs the real `_handle_delete` against an in-memory FileStation, so the parts
that a unit test of `trash.soft_delete` cannot cover are pinned here: which
failures fall back to a hard delete, which must refuse, and what the audit
trail records about the placement.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audit import AuditLog  # noqa: E402
from config import config  # noqa: E402
from confirm import ConfirmationTokens  # noqa: E402
from mcp_server import SynologyMCPServer, ToolFailureError  # noqa: E402


class FakeFileStation:
    def __init__(self, dirs=(), files=()):
        self.dirs = set(dirs)
        self.files = set(files)
        self.deleted = []
        self.fail_create = False
        self.fail_move = False
        self.pretend_move = False

    def _exists(self, path):
        return path in self.dirs or path in self.files

    def get_file_info(self, path):
        if path in self.dirs:
            return {"type": "directory", "path": path}
        if path in self.files:
            return {"type": "file", "path": path}
        raise FileNotFoundError(path)

    def create_directory(self, parent, name, force_parent=False):
        if self.fail_create:
            raise RuntimeError("permission denied")
        path = f"{parent.rstrip('/')}/{name}"
        if path in self.dirs:
            raise RuntimeError("already exists")
        if force_parent:
            parts = path.strip("/").split("/")
            for index in range(1, len(parts)):
                self.dirs.add("/" + "/".join(parts[:index]))
        self.dirs.add(path)
        return {"success": True, "full_path": path}

    def move_file(self, source, destination, overwrite=False):
        if self.fail_move:
            raise RuntimeError("move failed")
        if self.pretend_move:
            return {"success": True}
        if self._exists(destination):
            raise RuntimeError("destination exists")
        if source in self.files:
            self.files.remove(source)
            self.files.add(destination)
        elif source in self.dirs:
            self.dirs.remove(source)
            self.dirs.add(destination)
        else:
            raise FileNotFoundError(source)
        return {"success": True, "destination": destination}

    def delete(self, path):
        self.deleted.append(path)
        self.files.discard(path)
        self.dirs.discard(path)
        return {"success": True, "path": path}


def make_server(tmp_path, fs):
    srv = SynologyMCPServer.__new__(SynologyMCPServer)
    srv._tool_registry = {}
    srv.run_id = "test"
    srv.sessions = {"http://nas.example:5000": "test-session"}
    srv.nas_name_map = {}
    srv.audit = AuditLog(tmp_path / "audit.jsonl", run_id="test")
    srv.confirm = ConfirmationTokens(b"0" * 32, ttl_seconds=300)
    srv._register_tool(
        "delete",
        "Delete a file or directory",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        srv._handle_delete,
    )
    srv._get_base_url = lambda arguments: "http://nas.example:5000"
    srv._get_filestation = lambda base_url: fs
    return srv


def audit_events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


def delete(srv, path):
    from mcp import types

    from tests.test_confirmation_flow import FakeContext
    args = {"path": path}
    first = asyncio.run(srv._dispatch_tool("delete", args, FakeContext()))
    retry = types.CallToolRequestParams(
        name="delete", arguments=args, request_state=first.request_state,
        input_responses={"confirm": {"action": "accept", "content": {"approve": True}}},
    )
    result = asyncio.run(srv._dispatch_tool("delete", args, FakeContext(), retry))
    return json.loads(result[0].text.split(": ", 1)[1])


class TestSoftDelete:
    def test_file_lands_in_trash_and_audit_records_it(self, tmp_path):
        fs = FakeFileStation(dirs={"/media", "/media/library"}, files={"/media/library/a.mkv"})
        srv = make_server(tmp_path, fs)
        payload = delete(srv, "/media/library/a.mkv")

        assert payload["soft_delete"] is True
        assert payload["trash_path"].startswith("/media/#trash/")
        assert "/media/library/a.mkv" not in fs.files
        assert fs.deleted == []  # nothing was unlinked

        placement = [e for e in audit_events(tmp_path) if e["event"] == "delete_placement"]
        assert placement[-1]["outcome"] == "soft"
        assert placement[-1]["trash_path"] == payload["trash_path"]


class TestFallback:
    def test_unwritable_share_falls_back_to_hard_delete(self, tmp_path):
        fs = FakeFileStation(dirs={"/media", "/media/library"}, files={"/media/library/a.mkv"})
        fs.fail_create = True
        srv = make_server(tmp_path, fs)
        payload = delete(srv, "/media/library/a.mkv")

        assert payload["soft_delete"] is False
        assert fs.deleted == ["/media/library/a.mkv"]
        placement = [e for e in audit_events(tmp_path) if e["event"] == "delete_placement"]
        assert placement[-1]["outcome"] == "hard"

    def test_failed_move_keeps_source_and_refuses_hard_delete(self, tmp_path):
        fs = FakeFileStation(dirs={"/media"}, files={"/media/a.bin"})
        fs.fail_move = True
        srv = make_server(tmp_path, fs)
        with pytest.raises(ToolFailureError) as caught:
            delete(srv, "/media/a.bin")
        assert caught.value.payload["error"]["code"] == "trash_verification_failed"
        assert fs.deleted == []
        assert "/media/a.bin" in fs.files


class TestRefusals:
    def test_always_mode_refuses_when_trash_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "trash_mode", "always")
        fs = FakeFileStation(dirs={"/media"}, files={"/media/a.bin"})
        fs.fail_create = True
        srv = make_server(tmp_path, fs)

        with pytest.raises(ToolFailureError) as caught:
            delete(srv, "/media/a.bin")
        assert caught.value.payload["error"]["code"] == "trash_unavailable"
        assert fs.deleted == []

    def test_ambiguous_move_is_not_hard_deleted(self, tmp_path):
        fs = FakeFileStation(dirs={"/media"}, files={"/media/a.bin"})
        fs.pretend_move = True
        srv = make_server(tmp_path, fs)

        with pytest.raises(ToolFailureError) as caught:
            delete(srv, "/media/a.bin")
        assert caught.value.payload["error"]["code"] == "trash_verification_failed"
        assert fs.deleted == []

    def test_never_mode_deletes_without_touching_the_trash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "trash_mode", "never")
        fs = FakeFileStation(dirs={"/media"}, files={"/media/a.bin"})
        srv = make_server(tmp_path, fs)
        payload = delete(srv, "/media/a.bin")

        assert payload["soft_delete"] is False
        assert fs.deleted == ["/media/a.bin"]


@pytest.mark.parametrize("path", ["/", "/media", "/volume2", "/volume2/media", "/media/../photos", "/media//x"])
@pytest.mark.parametrize("mode", ["auto", "always", "never"])
def test_roots_and_traversal_never_reach_the_nas(tmp_path, monkeypatch, path, mode):
    monkeypatch.setattr(config, "trash_mode", mode)
    fs = FakeFileStation()
    srv = make_server(tmp_path, fs)
    srv._get_base_url = lambda _: pytest.fail("invalid path reached NAS resolution")
    with pytest.raises(ToolFailureError) as caught:
        delete(srv, path)
    assert caught.value.payload["error"]["code"] == "invalid_delete_path"
    assert fs.deleted == []


@pytest.mark.parametrize("share", ["volumes", "volume-media", "volume2archive"])
def test_volume_prefixed_share_files_can_be_deleted(tmp_path, share):
    path = f"/{share}/report.txt"
    fs = FakeFileStation(dirs={f"/{share}"}, files={path})
    payload = delete(make_server(tmp_path, fs), path)
    assert payload["soft_delete"] is True
    assert path not in fs.files
