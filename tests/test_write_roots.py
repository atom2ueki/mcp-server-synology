"""File Station writes: share-root refusal and the optional roots allow-list.

The policy runs before any network call, so these tests run the real handlers on
a `__new__` server: if the guard did not fire first, the missing `_get_base_url`
would surface as an AttributeError instead of a ToolFailureError.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import config  # noqa: E402
from mcp_server import SynologyMCPServer, ToolFailureError  # noqa: E402
from paths import (  # noqa: E402
    PathOutsideRootsError,
    admit_write,
    copy_target,
    create_directory_target,
    ensure_within_roots,
    normalize_roots,
    rename_target,
    validate_write_path,
)

ROOT = "/media"


BASE_URL = "https://nas.example:5001"


def make_server(monkeypatch, roots):
    srv = SynologyMCPServer.__new__(SynologyMCPServer)
    srv._get_base_url = lambda arguments: BASE_URL
    srv._get_filestation = lambda url: (_ for _ in ()).throw(
        AssertionError("write reached File Station")
    )
    monkeypatch.setattr(
        config, "file_station_write_paths_for", lambda base_url: tuple(roots)
    )
    return srv


class TestNormalizeRoots:
    def test_canonicalizes_and_dedupes(self):
        assert normalize_roots([" /volume1/media/ ", "/volume1/media", "/volume1//backup"]) == (
            "/volume1/media",
            "/volume1/backup",
        )

    def test_empty_means_no_allow_list(self):
        assert normalize_roots([]) == ()
        assert normalize_roots(None) == ()

    @pytest.mark.parametrize("bad", ["media", "/", "/../etc", "/volume1/../etc"])
    def test_rejects_widening_or_ambiguous_roots(self, bad):
        with pytest.raises(ValueError):
            normalize_roots([bad])


class TestStructuralPolicy:
    def test_share_and_volume_roots_are_refused(self):
        for path in ("/", "/media", "/volume1", "/volume1/media"):
            with pytest.raises(ValueError):
                validate_write_path(path)

    def test_protected_system_paths_are_refused(self):
        with pytest.raises(ValueError, match="protected"):
            validate_write_path("/etc/passwd")

    def test_ordinary_paths_pass_without_roots(self):
        assert validate_write_path("/volume1/media/movies/x.mkv") == (
            "/volume1/media/movies/x.mkv"
        )

    def test_allow_share_root_still_refuses_volume_and_filesystem_roots(self):
        with pytest.raises(ValueError):
            validate_write_path("/volume1", allow_share_root=True)
        with pytest.raises(ValueError):
            validate_write_path("/", allow_share_root=True)

    def test_move_destination_may_be_a_share_root(self):
        # DSM reads an existing destination as "move into this folder", so the
        # entry written is <destination>/<source name>, never the root itself.
        assert validate_write_path("/volume1/media", allow_share_root=True) == (
            "/volume1/media"
        )


class TestRootsAllowList:
    def test_inside_root_passes(self):
        assert ensure_within_roots("/media/movies/x.mkv", [ROOT]) == (
            "/media/movies/x.mkv"
        )

    def test_sibling_with_shared_prefix_is_outside(self):
        with pytest.raises(PathOutsideRootsError):
            ensure_within_roots("/media2/x.mkv", [ROOT])

    def test_dotdot_is_refused_before_the_root_check(self):
        with pytest.raises(ValueError, match="canonical"):
            validate_write_path("/volume1/media/../backup/x", [ROOT])


class TestComposedTargets:
    def test_create_directory_uses_the_cleaned_name(self):
        assert create_directory_target("/volume1/media", "new/movies") == (
            "/volume1/media/newmovies"
        )

    def test_create_directory_refuses_dot_names(self):
        with pytest.raises(ValueError):
            create_directory_target("/volume1/media", "..")

    def test_copy_target_preserves_the_source_name(self):
        assert copy_target("/volume1/media/in", "/volume1/media/out/x.mkv") == (
            "/volume1/media/in/x.mkv"
        )

    def test_rename_target_stays_a_sibling(self):
        assert rename_target("/volume1/media/x.mkv", "y.mkv") == "/volume1/media/y.mkv"
        with pytest.raises(ValueError):
            rename_target("/volume1/media/x.mkv", "..")


class TestAdmitWrite:
    def test_returns_canonical_arguments_for_each_tool(self):
        roots = [ROOT]
        assert admit_write("create_file", {"path": "/media/a.txt"}, roots) == {
            "path": "/media/a.txt"
        }
        assert admit_write(
            "rename_file", {"path": "/media/a", "new_name": "b"}, roots
        ) == {"path": "/media/b"}
        assert admit_write(
            "move_file",
            {"source_path": "/media/a", "destination_path": "/media/dst"},
            roots,
        ) == {"source_path": "/media/a", "destination_path": "/media/dst"}
        assert admit_write(
            "copy_file", {"source_path": "/media/a", "destination_folder": "/media"},
            roots,
        ) == {"destination_folder": "/media"}
        assert admit_write(
            "create_directory", {"folder_path": "/media", "name": "x"}, roots
        ) == {"folder_path": "/media", "name": "x"}

    def test_unknown_tool_is_not_silently_admitted(self):
        with pytest.raises(KeyError):
            admit_write("delete", {"path": "/media/a"}, [ROOT])


class TestHandlers:
    def test_create_file_outside_roots_is_refused_before_any_call(self, monkeypatch):
        srv = make_server(monkeypatch, [ROOT])
        with pytest.raises(ToolFailureError) as caught:
            asyncio.run(srv._handle_create_file({"path": "/backup/x.txt"}))
        assert caught.value.payload["error"]["code"] == "path_outside_roots"
        assert "write paths" in caught.value.payload["error"]["message"]

    def test_delete_outside_roots_is_refused(self, monkeypatch):
        srv = make_server(monkeypatch, [ROOT])
        with pytest.raises(ToolFailureError) as caught:
            asyncio.run(srv._handle_delete({"path": "/backup/x.txt"}))
        assert caught.value.payload["error"]["code"] == "path_outside_roots"

    def test_move_refuses_a_source_outside_roots(self, monkeypatch):
        srv = make_server(monkeypatch, [ROOT])
        with pytest.raises(ToolFailureError) as caught:
            asyncio.run(
                srv._handle_move_file(
                    {
                        "source_path": "/backup/x.mkv",
                        "destination_path": "/media/x.mkv",
                    }
                )
            )
        assert caught.value.payload["error"]["code"] == "path_outside_roots"

    def test_copy_refuses_a_target_outside_roots(self, monkeypatch):
        srv = make_server(monkeypatch, [ROOT])
        with pytest.raises(ToolFailureError) as caught:
            asyncio.run(
                srv._handle_copy_file(
                    {
                        "source_path": "/media/x.mkv",
                        "destination_folder": "/backup",
                    }
                )
            )
        assert caught.value.payload["error"]["code"] == "path_outside_roots"

    def test_create_directory_refuses_a_target_outside_roots(self, monkeypatch):
        srv = make_server(monkeypatch, [ROOT])
        with pytest.raises(ToolFailureError) as caught:
            asyncio.run(
                srv._handle_create_directory(
                    {"folder_path": "/backup", "name": "x"}
                )
            )
        assert caught.value.payload["error"]["code"] == "path_outside_roots"

    def test_rename_refuses_a_dot_name(self, monkeypatch):
        srv = make_server(monkeypatch, [])
        with pytest.raises(ToolFailureError) as caught:
            asyncio.run(
                srv._handle_rename_file(
                    {"path": "/volume1/media/x.mkv", "new_name": ".."}
                )
            )
        assert caught.value.payload["error"]["code"] == "invalid_write_path"
