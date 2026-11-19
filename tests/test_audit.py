"""Offline tests for the tool-call audit trail.

The failure policy is the part worth pinning: a destructive call must be
*refused* when the trail cannot be written, while a lost line after the action
must not turn a success into an error.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audit import (  # noqa: E402
    MAX_VALUE_CHARS,
    AuditLog,
    AuditUnavailableError,
    redact,
    target_of,
)


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_log(tmp_path, **kwargs) -> AuditLog:
    return AuditLog(tmp_path / "audit.jsonl", run_id="testrun", **kwargs)


class TestRedaction:
    def test_credentials_are_masked_by_key(self):
        out = redact({
            "username": "mcp-bot",
            "password": "hunter2",
            "passwd": "hunter2",
            "token": "abc",
            "chap_password": "x",
            "session": "SID123",
            "device_id": "trusted-device-secret",
            "did": "trusted-device-secret",
        })
        assert out["username"] == "mcp-bot"
        for key in ("password", "passwd", "token", "chap_password", "session", "device_id", "did"):
            assert out[key] == "***", key

    def test_nested_credentials_are_masked(self):
        out = redact({"auth": {"api_key": "k", "user": "u"}, "items": [{"secret": "s"}]})
        assert out["auth"] == {"api_key": "***", "user": "u"}
        assert out["items"] == [{"secret": "***"}]

    def test_credential_aliases_stay_masked(self):
        # Names the old bridge allowlist covered; the shared matcher must too.
        for key in ("_sid", "session_id", "synotoken", "credentials", "secrets"):
            assert redact({key: "SECRET"})[key] == "***", key

    def test_url_userinfo_is_masked(self):
        assert redact({"uri": "sftp://user:pass@host/path"})["uri"] == "sftp://***@host/path"

    def test_share_path_is_recorded_as_the_target(self):
        assert target_of({"share_path": "/videos/plex"}) == "/videos/plex"

    def test_named_header_records_mask_their_value(self):
        # custom_headers entries are {name, value}; neither key alone matches
        # the secret pattern, so the sibling name decides.
        out = redact({"custom_headers": [
            {"name": "Authorization", "value": "Bearer secret-token"},
            {"name": "X-Trace-Id", "value": "abc123"},
        ]})
        assert out["custom_headers"][0] == {"name": "Authorization", "value": "***"}
        assert out["custom_headers"][1] == {"name": "X-Trace-Id", "value": "abc123"}

    def test_long_payloads_are_truncated_not_copied(self):
        payload = "x" * (MAX_VALUE_CHARS * 2)
        out = redact({"content": payload})
        assert len(out["content"]) < len(payload)
        assert out["content"].startswith("x" * MAX_VALUE_CHARS)
        assert "chars" in out["content"]

    def test_scalars_survive(self):
        assert redact({"confirm": True, "count": 3, "ratio": 1.5, "none": None}) == {
            "confirm": True, "count": 3, "ratio": 1.5, "none": None,
        }


class TestTarget:
    def test_prefers_path_like_keys(self):
        assert target_of({"path": "/media/x", "folder_path": "/other"}) == "/media/x"
        assert target_of({"folder_path": "/dir"}) == "/dir"
        assert target_of({"uuid": "abc"}) is None


class TestRecord:
    def test_writes_one_json_line(self, tmp_path):
        log = make_log(tmp_path)
        log.record(
            tool="delete",
            arguments={"path": "/media/library/x", "password": "nope"},
            outcome="ok",
            duration_ms=12.34,
            risk="destructive",
        )
        lines = read_lines(log.path)
        assert len(lines) == 1
        entry = lines[0]
        assert entry["tool"] == "delete"
        assert entry["risk"] == "destructive"
        assert entry["target"] == "/media/library/x"
        assert entry["outcome"] == "ok"
        assert entry["duration_ms"] == 12.3
        assert entry["run"] == "testrun"
        assert entry["args"]["password"] == "***"
        assert entry["ts"].endswith("+00:00")

    def test_error_is_recorded_without_changing_outcome_shape(self, tmp_path):
        log = make_log(tmp_path)
        log.record(
            tool="delete", arguments={"path": "/x"}, outcome="error",
            duration_ms=1.0, risk="destructive", error="ToolFailureError: denied",
        )
        assert read_lines(log.path)[0]["error"] == "ToolFailureError: denied"

    def test_file_is_owner_only(self, tmp_path):
        log = make_log(tmp_path)
        log.record(tool="list_directory", arguments={}, outcome="ok",
                   duration_ms=1, risk="read")
        mode = stat.S_IMODE(os.stat(log.path).st_mode)
        assert mode == 0o600

    def test_disabled_writes_nothing(self, tmp_path):
        log = make_log(tmp_path, enabled=False)
        log.record(tool="delete", arguments={}, outcome="ok", duration_ms=1, risk="destructive")
        log.ensure_writable()  # must not create anything either
        assert not log.path.exists()


class TestRotation:
    def test_rotates_at_size_limit_and_keeps_n_files(self, tmp_path):
        log = make_log(tmp_path, max_bytes=400, keep=2)
        for _ in range(10):
            log.record(tool="list_directory", arguments={"path": "/media/x"},
                       outcome="ok", duration_ms=1, risk="read")
        assert log.path.exists()
        assert log.path.with_name("audit.jsonl.1").exists()
        assert log.path.with_name("audit.jsonl.2").exists()
        assert not log.path.with_name("audit.jsonl.3").exists()
        # Every surviving file is still valid JSONL.
        for name in ("audit.jsonl", "audit.jsonl.1", "audit.jsonl.2"):
            for line in log.path.with_name(name).read_text(encoding="utf-8").splitlines():
                json.loads(line)


class TestFailClosed:
    def test_ensure_writable_creates_parent(self, tmp_path):
        log = AuditLog(tmp_path / "nested" / "deeper" / "audit.jsonl")
        log.ensure_writable()
        assert (tmp_path / "nested" / "deeper").is_dir()

    def test_ensure_writable_raises_when_path_is_not_a_directory(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")
        log = AuditLog(blocker / "audit.jsonl")
        with pytest.raises(AuditUnavailableError):
            log.ensure_writable()

    def test_record_is_best_effort_after_the_fact(self, tmp_path, monkeypatch):
        log = make_log(tmp_path)
        monkeypatch.setattr(AuditLog, "_append", lambda self, line: (_ for _ in ()).throw(OSError("disk full")))
        # The action already happened; a lost line must not raise into the caller.
        log.record(tool="delete", arguments={}, outcome="ok", duration_ms=1, risk="destructive")


    def test_required_record_refuses_when_append_fails(self, tmp_path, monkeypatch):
        log = make_log(tmp_path)
        def fail(*_):
            raise OSError("disk full")
        monkeypatch.setattr(log, "_append", fail)
        with pytest.raises(AuditUnavailableError):
            log.record(tool="delete", arguments={}, outcome="started", duration_ms=0,
                       risk="destructive", required=True)

    def test_read_call_creates_a_missing_parent(self, tmp_path):
        log = AuditLog(tmp_path / "fresh" / "audit.jsonl")
        log.record(tool="synology_status", arguments={}, outcome="ok", duration_ms=0, risk="read")
        assert read_lines(log.path)[0]["outcome"] == "ok"

    def test_independent_writers_rotate_without_losing_records(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor
        def write(index):
            log = AuditLog(tmp_path / "shared.jsonl", max_bytes=300, keep=25)
            log.record(tool="delete", arguments={"path": f"/media/{index}"},
                       outcome="started", duration_ms=0, risk="destructive", required=True)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(write, range(20)))
        records = []
        for path in tmp_path.glob("shared.jsonl*"):
            if path.suffix != ".lock":
                records.extend(read_lines(path))
        assert {row["target"] for row in records} == {f"/media/{i}" for i in range(20)}
