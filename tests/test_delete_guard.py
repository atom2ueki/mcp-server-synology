"""`delete` must refuse a share root, however the path is spelled.

File Station paths are share-relative (`/photos/2024/a.jpg`), and `delete` is
recursive for directories. The old guard listed `/volume1`, `/etc` and other
filesystem paths, which never appear in a File Station path, so it did not
stop `delete("/photos")` from removing a whole share. These tests check that
no Delete task is ever started on a share root -- including spellings that
normalize to one, and a path whose type cannot be read -- while a file
directly inside a share root and ordinary paths deeper in a share still go
through.

No live NAS required.
"""

from unittest.mock import patch

import pytest

from filestation.synology_filestation import SynologyFileStation


def _fs():
    return SynologyFileStation("https://nas.example.com:5001", "SID")


@pytest.mark.parametrize(
    "path",
    [
        "/photos",
        "/photos/",
        "photos",
        "//photos",
        "/photos//",
        "/./photos",
        "/photos/.",
        "/photos/2024/..",
        "/photos/../video",
    ],
)
def test_share_root_is_refused_no_delete_task_is_started(path):
    fs = _fs()
    with (
        patch.object(fs, "_make_request") as request,
        patch.object(fs, "get_file_info", return_value={"type": "directory"}) as info,
    ):
        with pytest.raises(Exception, match="share"):
            fs.delete(path)
    # The type read happens, but no SYNO.FileStation.Delete request may go out.
    request.assert_not_called()
    info.assert_called_once()


@pytest.mark.parametrize("path", ["/homes", "/etc", "/volume1/foo"])
def test_critical_path_is_refused(path):
    fs = _fs()
    with patch.object(fs, "_make_request") as request:
        with pytest.raises(Exception, match="critical system path"):
            fs.delete(path)
    request.assert_not_called()

@pytest.mark.parametrize("path", ["/photos", "/photo.txt", "photos", "//photos"])
def test_unreadable_share_root_path_is_refused(path):
    """A one-segment path whose type cannot be read could be the share itself."""
    fs = _fs()
    with (
        patch.object(fs, "_make_request") as request,
        patch.object(fs, "get_file_info", side_effect=FileNotFoundError("gone")),
    ):
        with pytest.raises(Exception, match="cannot be told apart"):
            fs.delete(path)
    request.assert_not_called()


@pytest.mark.parametrize("path", ["/readme.txt", "readme.txt", "//readme.txt"])
def test_file_directly_in_a_share_root_is_deleted(path):
    """A one-segment path positively read as a file is an ordinary delete."""
    fs = _fs()
    responses = [{"taskid": "t1"}, {"finished": True}]
    with (
        patch.object(fs, "_make_request", side_effect=responses) as request,
        patch.object(fs, "get_file_info", return_value={"type": "file"}),
    ):
        result = fs.delete(path)
    assert result["success"] is True
    assert result["item_type"] == "file"
    start = request.call_args_list[0]
    assert start.args[:3] == ("SYNO.FileStation.Delete", "2", "start")
    assert start.kwargs["path"] == '["/readme.txt"]'
    assert start.kwargs["recursive"] == "false"


@pytest.mark.parametrize("path", ["/", "", "//", "/.", "/.."])
def test_root_is_refused(path):
    fs = _fs()
    with patch.object(fs, "_make_request") as request:
        with pytest.raises(Exception):
            fs.delete(path)
    request.assert_not_called()


@pytest.mark.parametrize("path", ["/photos/a.jpg", "/photos/2024", "/video/x/y.mkv"])
def test_path_inside_a_share_is_deleted(path):
    fs = _fs()
    responses = [{"taskid": "t1"}, {"finished": True}]
    with (
        patch.object(fs, "_make_request", side_effect=responses) as request,
        patch.object(fs, "get_file_info", return_value={"type": "file"}),
    ):
        result = fs.delete(path)
    assert result["success"] is True
    start = request.call_args_list[0]
    assert start.args[:3] == ("SYNO.FileStation.Delete", "2", "start")
    assert start.kwargs["path"] == f'["{path}"]'
