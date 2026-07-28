"""Offline unit tests for FileStation size parsing and search task handling.

These use a stubbed `_make_request`, so they run without a NAS. They pin
behaviour that was verified live against DSM 7.3.2:

* the byte size of an entry arrives inside `additional`, not at the top level
* `SYNO.FileStation.Search` has no `status` method; `list` reports completion
* DSM intermittently discards a freshly started search task, answering
  `{"finished": true}` with no `total`/`files` — the client must retry
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from filestation.synology_filestation import SynologyFileStation  # noqa: E402


def make_client():
    return SynologyFileStation("https://nas.example.com:5001", "SID")


def entry(name, *, size=None, isdir=False, path=None):
    """Build a FileStation entry shaped the way DSM returns one."""
    item = {
        "name": name,
        "path": path or f"/share/{name}",
        "isdir": isdir,
        "additional": {
            "time": {"crtime": 1, "mtime": 2, "atime": 3},
            "owner": {"user": "takmd", "group": "users"},
            "perm": {"posix": 777},
        },
    }
    if size is not None:
        item["additional"]["size"] = size
    return item


class TestSizeParsing:
    """Sizes live in `additional.size`; reading only the top level yields 0."""

    def test_list_directory_reports_real_size(self, monkeypatch):
        fs = make_client()
        monkeypatch.setattr(
            fs,
            "_make_request",
            lambda *a, **k: {"files": [entry("clip.mp4", size=1_234_567_890)]},
        )

        (item,) = fs.list_directory("/share")

        assert item["size"] == 1_234_567_890
        assert item["type"] == "file"
        assert item["owner"] == "takmd"

    def test_get_file_info_reports_real_size(self, monkeypatch):
        fs = make_client()
        monkeypatch.setattr(
            fs, "_make_request", lambda *a, **k: {"files": [entry("clip.mp4", size=42)]}
        )

        assert fs.get_file_info("/share/clip.mp4")["size"] == 42

    def test_falls_back_to_top_level_size(self):
        """Older/other API versions may inline `size`; keep honouring that."""
        assert SynologyFileStation._extract_size({"size": 99}) == 99

    def test_missing_size_is_zero(self):
        assert SynologyFileStation._extract_size({"additional": {}}) == 0
        assert SynologyFileStation._extract_size({"additional": {"size": None}}) == 0


class SearchStub:
    """Scripted stand-in for `_make_request` against SYNO.FileStation.Search.

    `task_scripts` is a list of per-task behaviours, consumed in start order.
    Each is either the literal string "ghost" (DSM discarded the task) or a list
    of entries to hand back once the task finishes.
    """

    def __init__(self, task_scripts, page_size=1000):
        self.task_scripts = list(task_scripts)
        self.page_size = page_size
        self.started = 0
        self.calls = []
        self._scripts = {}

    def __call__(self, api, version, method, **params):
        self.calls.append((api, method, params))
        assert api == "SYNO.FileStation.Search", api
        assert method != "status", "DSM has no `status` method (error 103)"

        if method == "start":
            task_id = f"TASK{self.started}"
            self._scripts[task_id] = self.task_scripts[self.started]
            self.started += 1
            return {"taskid": task_id}

        if method == "list":
            script = self._scripts[params["taskid"]]
            if script == "ghost":
                # Exactly what DSM returns for a discarded/unknown taskid.
                return {"finished": True}
            offset = params.get("offset", 0)
            limit = params.get("limit", self.page_size)
            return {
                "finished": True,
                "total": len(script),
                "files": script[offset : offset + limit],
            }

        if method in ("stop", "clean"):
            return {}

        raise AssertionError(f"unexpected method {method}")


class TestSearch:
    def test_never_calls_status_and_returns_matches(self, monkeypatch):
        fs = make_client()
        stub = SearchStub([[entry("report.pdf", size=10), entry("reports", isdir=True)]])
        monkeypatch.setattr(fs, "_make_request", stub)

        results = fs.search_files("/share", "report")

        assert [r["name"] for r in results] == ["report.pdf", "reports"]
        assert results[0]["size"] == 10
        assert results[1]["type"] == "directory"
        assert "status" not in [m for _api, m, _p in stub.calls]

    def test_retries_when_dsm_discards_the_task(self, monkeypatch):
        fs = make_client()
        stub = SearchStub(["ghost", "ghost", [entry("found.txt", size=1)]])
        monkeypatch.setattr(fs, "_make_request", stub)
        monkeypatch.setattr("filestation.synology_filestation.time.sleep", lambda _s: None)

        results = fs.search_files("/share", "found")

        assert [r["name"] for r in results] == ["found.txt"]
        assert stub.started == 3, "each retry must start a fresh task"

    def test_ghost_needs_two_consecutive_replies(self, monkeypatch):
        """One odd reply shouldn't abandon a task that is actually alive."""
        fs = make_client()
        calls = {"n": 0}

        def flaky(api, version, method, **params):
            if method == "start":
                return {"taskid": "T"}
            if method == "list":
                calls["n"] += 1
                if calls["n"] == 1:
                    return {"finished": True}  # single blip
                return {"finished": True, "total": 1, "files": [entry("a.txt", size=5)]}
            return {}

        monkeypatch.setattr(fs, "_make_request", flaky)
        monkeypatch.setattr("filestation.synology_filestation.time.sleep", lambda _s: None)

        assert [r["name"] for r in fs.search_files("/share", "a")] == ["a.txt"]

    def test_gives_up_after_max_attempts(self, monkeypatch):
        fs = make_client()
        stub = SearchStub(["ghost"] * SynologyFileStation.SEARCH_MAX_ATTEMPTS)
        monkeypatch.setattr(fs, "_make_request", stub)
        monkeypatch.setattr("filestation.synology_filestation.time.sleep", lambda _s: None)

        with pytest.raises(Exception, match="discarded the search task"):
            fs.search_files("/share", "nope")

        assert stub.started == SynologyFileStation.SEARCH_MAX_ATTEMPTS

    def test_empty_result_is_not_mistaken_for_a_discarded_task(self, monkeypatch):
        """A genuine zero-match search returns `total: 0` and must not retry."""
        fs = make_client()
        stub = SearchStub([[]])
        monkeypatch.setattr(fs, "_make_request", stub)

        assert fs.search_files("/share", "cervical") == []
        assert stub.started == 1

    def test_pages_through_large_result_sets(self, monkeypatch):
        fs = make_client()
        many = [entry(f"f{i}.mpg", size=i) for i in range(2500)]
        stub = SearchStub([many])
        monkeypatch.setattr(fs, "_make_request", stub)

        results = fs.search_files("/share", ".mpg")

        assert len(results) == 2500
        assert results[-1]["name"] == "f2499.mpg"

    def test_always_cleans_up_the_task(self, monkeypatch):
        fs = make_client()
        stub = SearchStub([[entry("x.txt", size=1)]])
        monkeypatch.setattr(fs, "_make_request", stub)

        fs.search_files("/share", "x")

        methods = [m for _api, m, _p in stub.calls]
        assert "stop" in methods and "clean" in methods

    def test_cleanup_failures_do_not_mask_results(self, monkeypatch):
        fs = make_client()

        def blow_up_on_cleanup(api, version, method, **params):
            if method == "start":
                return {"taskid": "T"}
            if method == "list":
                return {"finished": True, "total": 1, "files": [entry("ok.txt", size=7)]}
            raise RuntimeError("cleanup exploded")

        monkeypatch.setattr(fs, "_make_request", blow_up_on_cleanup)

        assert [r["name"] for r in fs.search_files("/share", "ok")] == ["ok.txt"]
