"""Offline regression tests for lossless File Station content and CopyMove."""

import asyncio
import base64
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from filestation.synology_filestation import SynologyFileStation
from mcp_server import SynologyMCPServer


def make_client():
    return SynologyFileStation("https://nas.example.com:5001", "SID", syno_token="TOKEN")


class DownloadResponse:
    def __init__(self, content, *, content_type="application/octet-stream", chunks=None):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        if self._chunks is not None:
            yield from self._chunks
            return
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def json(self):
        return json.loads(self.content)

    def close(self):
        self.closed = True


def prepare_download(monkeypatch, raw, *, reported_size=None, response=None):
    fs = make_client()
    monkeypatch.setattr(
        fs,
        "get_file_info",
        lambda path: {"path": path, "type": "file", "size": len(raw) if reported_size is None else reported_size},
    )
    response = response or DownloadResponse(raw)
    monkeypatch.setattr("filestation.synology_filestation.requests.get", lambda *a, **k: response)
    return fs, response


class TestLosslessDownload:
    def test_binary_base64_roundtrip_is_exact(self, monkeypatch):
        raw = bytes(range(256)) + b"\x00\xffSQLite\x80\x81"
        fs, response = prepare_download(monkeypatch, raw)

        result = fs.get_file_content("/docker/audit/sample.db", "base64", 4096)

        assert base64.b64decode(result["content"], validate=True) == raw
        assert result["size"] == len(raw)
        assert result["sha256"] == hashlib.sha256(raw).hexdigest()
        assert result["mime_type"] == "application/octet-stream"
        assert response.closed

    def test_text_is_strict_utf8_and_suggests_base64(self, monkeypatch):
        fs, response = prepare_download(monkeypatch, b"valid\xffinvalid")

        with pytest.raises(ValueError, match="encoding='base64'"):
            fs.get_file_content("/docker/audit/binary.bin")

        assert response.closed

    def test_valid_utf8_is_returned_unchanged(self, monkeypatch):
        text = "LØFT — blåbær 🫐"
        fs, _ = prepare_download(monkeypatch, text.encode())

        assert fs.get_file_content("/docker/audit/note.txt") == text

    def test_reported_size_limit_fails_before_download(self, monkeypatch):
        fs = make_client()
        monkeypatch.setattr(
            fs,
            "get_file_info",
            lambda path: {"path": path, "type": "file", "size": 1025},
        )
        monkeypatch.setattr(
            "filestation.synology_filestation.requests.get",
            lambda *a, **k: pytest.fail("oversize files must not be downloaded"),
        )

        with pytest.raises(ValueError, match="exceeding max_bytes"):
            fs.get_file_content("/docker/audit/large.bin", "base64", 1024)

    def test_stream_limit_stops_a_growing_file(self, monkeypatch):
        response = DownloadResponse(b"", chunks=[b"1234", b"5678"])
        fs, response = prepare_download(monkeypatch, b"1234", response=response)

        with pytest.raises(ValueError, match="Downloaded content exceeds"):
            fs.get_file_content("/docker/audit/growing.bin", "base64", 6)

        assert response.closed

    def test_size_mismatch_fails_closed(self, monkeypatch):
        fs, _ = prepare_download(monkeypatch, b"abc", reported_size=4)

        with pytest.raises(IOError, match="expected 4, got 3"):
            fs.get_file_content("/docker/audit/truncated.bin", "base64")

    @pytest.mark.parametrize("max_bytes", [0, -1, True, 8 * 1024 * 1024 + 1])
    def test_invalid_limits_are_rejected(self, monkeypatch, max_bytes):
        fs = make_client()
        with pytest.raises(ValueError, match="max_bytes"):
            fs.get_file_content("/docker/audit/file.bin", "base64", max_bytes)


class UploadResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"success": True}


class UploadSession:
    def __init__(self, capture):
        self.capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, *, files, data, headers, verify, timeout):
        filename, payload, media_type = files["file"]
        self.capture.update(
            filename=filename,
            payload=payload.read(),
            media_type=media_type,
            data=data,
            headers=headers,
        )
        return UploadResponse()


class TestLosslessUpload:
    def test_base64_upload_decodes_exact_bytes(self, monkeypatch):
        raw = bytes(range(256)) + b"\x00\xff"
        capture = {}
        monkeypatch.setattr(
            "filestation.synology_filestation.requests.Session",
            lambda: UploadSession(capture),
        )
        fs = make_client()

        result = fs.create_file(
            "/docker/audit/sample.bin", base64.b64encode(raw).decode(), encoding="base64"
        )

        assert capture["payload"] == raw
        assert capture["media_type"] == "application/octet-stream"
        assert result["size"] == len(raw)
        assert result["encoding"] == "base64"

    def test_invalid_base64_is_rejected_before_upload(self, monkeypatch):
        monkeypatch.setattr(
            "filestation.synology_filestation.requests.Session",
            lambda: pytest.fail("invalid base64 must not upload"),
        )
        with pytest.raises(ValueError, match="Invalid base64"):
            make_client().create_file("/docker/audit/sample.bin", "not base64!", encoding="base64")

    def test_decoded_base64_limit_is_enforced(self, monkeypatch):
        # Five decoded bytes still fit in the same eight encoded characters as
        # the four-byte limit, so the post-decode boundary check must remain.
        monkeypatch.setattr(SynologyFileStation, "MAX_CONTENT_BYTES", 4)
        with pytest.raises(ValueError, match="upload limit"):
            make_client().create_file(
                "/docker/audit/sample.bin", base64.b64encode(b"12345").decode(), encoding="base64"
            )

    def test_oversized_base64_is_rejected_before_decode(self, monkeypatch):
        monkeypatch.setattr(SynologyFileStation, "MAX_CONTENT_BYTES", 3)
        monkeypatch.setattr(
            "filestation.synology_filestation.base64.b64decode",
            lambda *a, **k: pytest.fail("oversized input must not be decoded"),
        )

        with pytest.raises(ValueError, match="encoded limit"):
            make_client().create_file("/docker/audit/sample.bin", "AAAAA", encoding="base64")


def test_create_folder_uses_json_arrays(monkeypatch):
    fs = make_client()
    captured = {}

    def fake_request(api, version, method, **params):
        captured.update(api=api, version=version, method=method, params=params)
        return {"folders": [{"path": "/docker/audit"}]}

    monkeypatch.setattr(fs, "_make_request", fake_request)
    fs.create_directory("/docker", "audit", force_parent=True)

    assert json.loads(captured["params"]["folder_path"]) == ["/docker"]
    assert json.loads(captured["params"]["name"]) == ["audit"]
    assert captured["params"]["force_parent"] == "true"


class CopyMoveFilesystem:
    def __init__(self, *, remove_source, target_size=17, target_exists=True):
        self.remove_source = remove_source
        self.target_size = target_size
        self.target_exists = target_exists
        self.started = False
        self.calls = []

    def info(self, path):
        if path == "/share/src/file.bin" and (not self.started or not self.remove_source):
            return {"path": path, "type": "file", "size": 17}
        if path == "/share/dst":
            return {"path": path, "type": "directory", "size": 0}
        if path == "/share/dst/file.bin" and self.started and self.target_exists:
            return {"path": path, "type": "file", "size": self.target_size}
        raise FileNotFoundError(path)

    def request(self, api, version, method, **params):
        self.calls.append((api, method, params))
        if method == "start":
            self.started = True
            return {"taskid": "TASK"}
        if method == "status":
            # DSM may report zero despite a successful operation.
            return {"finished": True, "progress": 1, "found_file_num": 0}
        if method == "stop":
            return {}
        raise AssertionError(method)


@pytest.mark.parametrize("remove_source", [False, True])
def test_copy_move_uses_array_and_verifies_target(monkeypatch, remove_source):
    fs = make_client()
    stub = CopyMoveFilesystem(remove_source=remove_source)
    monkeypatch.setattr(fs, "get_file_info", stub.info)
    monkeypatch.setattr(fs, "_make_request", stub.request)

    if remove_source:
        result = fs.move_file("/share/src/file.bin", "/share/dst")
    else:
        result = fs.copy_file("/share/src/file.bin", "/share/dst")

    start = next(params for _api, method, params in stub.calls if method == "start")
    assert json.loads(start["path"]) == ["/share/src/file.bin"]
    assert start["remove_src"] == str(remove_source).lower()
    assert result["destination_path"] == "/share/dst/file.bin"
    assert result["size"] == 17
    assert result["verified"] is True


@pytest.mark.parametrize(
    ("target_size", "target_exists", "message"),
    [(99, True, "byte count"), (17, False, "file.bin")],
)
def test_copy_fails_when_post_verification_fails(
    monkeypatch, target_size, target_exists, message
):
    fs = make_client()
    stub = CopyMoveFilesystem(
        remove_source=False, target_size=target_size, target_exists=target_exists
    )
    monkeypatch.setattr(fs, "get_file_info", stub.info)
    monkeypatch.setattr(fs, "_make_request", stub.request)

    with pytest.raises((IOError, FileNotFoundError), match=message):
        fs.copy_file("/share/src/file.bin", "/share/dst")


def test_copy_rejects_directories(monkeypatch):
    fs = make_client()
    monkeypatch.setattr(
        fs, "get_file_info", lambda path: {"path": path, "type": "directory", "size": 0}
    )
    with pytest.raises(ValueError, match="regular files"):
        fs.copy_file("/share/src/folder", "/share/dst")


HANDLER_CASES = [
    ("_handle_list_shares", {}, "list_shares", []),
    ("_handle_list_directory", {"path": "/share"}, "list_directory", []),
    ("_handle_get_file_info", {"path": "/share/a"}, "get_file_info", {}),
    ("_handle_search_files", {"path": "/share", "pattern": "a"}, "search_files", []),
    ("_handle_get_file_content", {"path": "/share/a"}, "get_file_content", "text"),
    ("_handle_rename_file", {"path": "/share/a", "new_name": "b"}, "rename_file", {}),
    ("_handle_move_file", {"source_path": "/share/a", "destination_path": "/share/dst"}, "move_file", {}),
    ("_handle_copy_file", {"source_path": "/share/a", "destination_folder": "/share/dst"}, "copy_file", {}),
    ("_handle_create_file", {"path": "/share/a"}, "create_file", {}),
    ("_handle_create_directory", {"folder_path": "/share", "name": "a"}, "create_directory", {}),
    ("_handle_delete", {"path": "/share/a"}, "delete", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("handler_name", "arguments", "method_name", "return_value"), HANDLER_CASES)
async def test_file_station_handlers_keep_event_loop_responsive(
    monkeypatch, handler_name, arguments, method_name, return_value
):
    server = SynologyMCPServer()
    release = threading.Event()
    entered = threading.Event()

    def blocking(*args):
        entered.set()
        release.wait(timeout=1)
        return return_value

    filestation = SimpleNamespace(**{method_name: blocking})
    monkeypatch.setattr(server, "_get_base_url", lambda args: "https://nas.example.com:5001")
    monkeypatch.setattr(server, "_get_filestation", lambda url: filestation)
    timer = threading.Timer(0.15, release.set)
    timer.start()
    started = time.monotonic()

    task = asyncio.create_task(getattr(server, handler_name)(arguments))
    while not entered.is_set():
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.02)
    elapsed = time.monotonic() - started
    await task
    timer.cancel()

    assert elapsed < 0.1, f"{handler_name} blocked the event loop for {elapsed:.3f}s"


def test_copy_tool_schema_and_count():
    server = SynologyMCPServer()
    tools = {tool.name: tool for tool in server._get_tool_definitions()}

    # With no test credentials, login/logout are conditionally exposed too.
    unconditional = set(tools) - {"synology_login", "synology_logout"}
    assert len(unconditional) == 84
    assert tools["copy_file"].input_schema["required"] == [
        "source_path",
        "destination_folder",
    ]
    assert tools["get_file_content"].input_schema["properties"]["encoding"]["enum"] == [
        "text",
        "base64",
    ]
