"""Offline tests for capability-detected soft delete.

The fake FileStation mirrors the three methods the trash uses, so the tests pin
the placement rules without a NAS: where a file lands, what happens on a name
collision, and — most importantly — which failures may fall back to a hard
delete and which must refuse.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trash import (  # noqa: E402
    TrashPolicy,
    TrashUnavailableError,
    TrashVerificationError,
    soft_delete,
)

NOW = datetime(2026, 10, 26, 21, 30, 0)


class FakeFileStation:
    """Just enough FileStation: existence, mkdir, move."""

    def __init__(self, dirs=(), files=()):
        self.dirs = set(dirs)
        self.files = set(files)
        self.fail_create = False
        self.fail_move = False
        self.drop_source_on_failure = False
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
            if self.drop_source_on_failure:
                self.files.discard(source)
                self.dirs.discard(source)
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


POLICY = TrashPolicy()


class TestPlacement:
    def test_file_moves_into_dated_trash(self):
        fs = FakeFileStation(dirs={"/videos", "/videos/plex"}, files={"/videos/plex/movie.mkv"})
        result = soft_delete(fs, "/videos/plex/movie.mkv", POLICY, now=NOW)
        assert result["soft_delete"] is True
        assert result["trash_path"].startswith("/videos/#trash/2026-10-26/")
        assert result["trash_path"].endswith("/movie.mkv")
        assert result["trash_root"] == "/videos/#trash"
        assert "/videos/plex/movie.mkv" not in fs.files
        assert result["trash_path"] in fs.files

    def test_directory_moves_into_trash(self):
        fs = FakeFileStation(
            dirs={"/videos", "/videos/plex", "/videos/plex/show"}, files=set()
        )
        result = soft_delete(fs, "/videos/plex/show", POLICY, now=NOW)
        assert result["trash_path"].startswith("/videos/#trash/2026-10-26/")
        assert result["trash_path"].endswith("/show")
        assert "/videos/plex/show" not in fs.dirs
        assert result["trash_path"] in fs.dirs

    def test_trash_folders_are_created_on_demand(self):
        fs = FakeFileStation(dirs={"/videos"}, files={"/videos/a.bin"})
        soft_delete(fs, "/videos/a.bin", POLICY, now=NOW)
        assert "/videos/#trash" in fs.dirs
        assert "/videos/#trash/2026-10-26" in fs.dirs

    def test_name_collision_preserves_previous_recovery(self):
        fs = FakeFileStation(
            dirs={"/videos", "/videos/#trash", "/videos/#trash/2026-10-26"},
            files={
                "/videos/a.mkv",
                "/videos/#trash/2026-10-26/a.mkv",
            },
        )
        result = soft_delete(fs, "/videos/a.mkv", POLICY, now=NOW)
        assert result["trash_path"].startswith("/videos/#trash/2026-10-26/")
        assert result["trash_path"].endswith("/a.mkv")
        assert "/videos/#trash/2026-10-26/a.mkv" in fs.files

    def test_custom_directory_name(self):
        fs = FakeFileStation(dirs={"/videos"}, files={"/videos/a.bin"})
        result = soft_delete(fs, "/videos/a.bin", TrashPolicy(dir_name=".trash"), now=NOW)
        assert result["trash_root"] == "/videos/.trash"
        assert result["trash_path"].startswith("/videos/.trash/2026-10-26/")
        assert result["trash_path"].endswith("/a.bin")

    def test_absolute_path_template_for_read_only_share_roots(self):
        # Accounts that cannot write <share>/ can still use a trash inside the
        # library; {share} keeps one installation usable across shares.
        fs = FakeFileStation(dirs={"/videos", "/videos/plex"}, files={"/videos/plex/a.bin"})
        policy = TrashPolicy(dir_name="/videos/plex/.trash/{share}")
        result = soft_delete(fs, "/videos/plex/a.bin", policy, now=NOW)
        assert result["trash_root"] == "/videos/plex/.trash/videos"
        assert result["trash_path"].startswith("/videos/plex/.trash/videos/2026-10-26/")
        assert result["trash_path"].endswith("/a.bin")


class TestRefusals:
    def test_path_already_in_trash(self):
        fs = FakeFileStation(files={"/videos/#trash/2026-10-26/a.bin"})
        with pytest.raises(TrashUnavailableError, match="already inside"):
            soft_delete(fs, "/videos/#trash/2026-10-26/a.bin", POLICY, now=NOW)

    def test_filesystem_root(self):
        with pytest.raises(TrashUnavailableError, match="root"):
            soft_delete(FakeFileStation(), "/", POLICY, now=NOW)

    def test_relative_path(self):
        with pytest.raises(TrashUnavailableError, match="absolute"):
            soft_delete(FakeFileStation(), "videos/a.bin", POLICY, now=NOW)


class TestFailureHandling:
    def test_unwritable_share_is_unavailable_before_any_move(self):
        fs = FakeFileStation(dirs={"/videos", "/videos/plex"}, files={"/videos/plex/a.mkv"})
        fs.fail_create = True
        with pytest.raises(TrashUnavailableError):
            soft_delete(fs, "/videos/plex/a.mkv", POLICY, now=NOW)
        assert "/videos/plex/a.mkv" in fs.files  # nothing moved

    def test_failed_move_keeps_source_is_unavailable(self):
        fs = FakeFileStation(dirs={"/videos"}, files={"/videos/a.bin"})
        fs.fail_move = True
        with pytest.raises(TrashVerificationError):
            soft_delete(fs, "/videos/a.bin", POLICY, now=NOW)
        assert "/videos/a.bin" in fs.files

    def test_failed_move_that_lost_source_is_not_fallback_safe(self):
        fs = FakeFileStation(dirs={"/videos"}, files={"/videos/a.bin"})
        fs.fail_move = True
        fs.drop_source_on_failure = True
        with pytest.raises(TrashVerificationError, match="refusing to delete"):
            soft_delete(fs, "/videos/a.bin", POLICY, now=NOW)

    def test_move_that_claimed_success_without_destination_is_not_fallback_safe(self):
        fs = FakeFileStation(dirs={"/videos"}, files={"/videos/a.bin"})
        fs.pretend_move = True
        with pytest.raises(TrashVerificationError, match="missing"):
            soft_delete(fs, "/videos/a.bin", POLICY, now=NOW)


class TestPolicy:
    @pytest.mark.parametrize(
        "mode,enabled,required",
        [("auto", True, False), ("always", True, True), ("never", False, False)],
    )
    def test_modes(self, mode, enabled, required):
        policy = TrashPolicy(mode=mode)
        assert policy.enabled is enabled
        assert policy.required is required


def test_concurrent_same_named_directories_remain_separate_recovery_entries():
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    class RealisticMove(FakeFileStation):
        def __init__(self):
            super().__init__(dirs={"/videos", "/videos/A/show", "/videos/B/show"})
            self.ready = threading.Barrier(2)
            self.lock = threading.Lock()
        def move_file(self, source, destination, overwrite=False):
            self.ready.wait()
            with self.lock:
                if destination in self.dirs:
                    destination += "/" + os.path.basename(source)
                super().move_file(source, destination, overwrite)
                return {"success": True, "destination_path": destination}
    fs = RealisticMove()
    paths = ["/videos/A/show", "/videos/B/show"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda path: soft_delete(fs, path, POLICY, now=NOW), paths))
    recovered = [r["trash_path"] for r in results]
    assert len(set(recovered)) == 2
    assert all(path in fs.dirs for path in recovered)
    assert not any(a.startswith(b + "/") for a in recovered for b in recovered if a != b)


def test_reported_move_destination_must_match_the_recovery_entry():
    class Misreported(FakeFileStation):
        def move_file(self, source, destination, overwrite=False):
            super().move_file(source, destination, overwrite)
            return {"destination_path": destination + "/nested"}
    fs = Misreported(dirs={"/videos"}, files={"/videos/a.bin"})
    with pytest.raises(TrashVerificationError):
        soft_delete(fs, "/videos/a.bin", POLICY, now=NOW)
