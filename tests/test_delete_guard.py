"""`delete` must refuse a share root, however the path is spelled.

File Station paths are share-relative (`/photos/2024/a.jpg`), and `delete` is
recursive for directories. The old guard listed `/volume1`, `/etc` and other
filesystem paths, which never appear in a File Station path, so it did not
stop `delete("/photos")` from removing a whole share. These tests check that a
share root is refused before any request reaches the NAS, including spellings
that normalize to one, and that ordinary paths inside a share still go through.

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
        "/homes",
    ],
)
def test_share_root_is_refused_before_any_request(path):
    fs = _fs()
    with (
        patch.object(fs, "_make_request") as request,
        patch.object(fs, "get_file_info") as info,
    ):
        with pytest.raises(Exception, match="share"):
            fs.delete(path)
    request.assert_not_called()
    info.assert_not_called()


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
