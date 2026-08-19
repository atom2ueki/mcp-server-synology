"""User/group management module tests.

Covers the issue #88 group-write verification: SYNO.Core.User.Group join is an
async batch task, so add_user_to_group/remove_user_from_group must distinguish
"queued" from "applied" by polling the group listing before reporting success.
"""

import json
from unittest.mock import MagicMock, patch

from usermanagement.synology_users import SynologyUserManager


def _make_manager() -> SynologyUserManager:
    return SynologyUserManager(
        "https://nas.example.com:5001",
        "sid_xyz",
        verify_ssl=False,
        syno_token="tok_abc",
    )


def _join_response(task_id="@administrators/groupbatch178692337813AF5596"):
    resp = MagicMock()
    resp.json.return_value = {"data": {"task_id": task_id}, "success": True}
    resp.raise_for_status = MagicMock()
    return resp


def _member_response(members):
    """SYNO.Core.Group.Member list response; members = list of (name, uid)."""
    resp = MagicMock()
    resp.json.return_value = {
        "success": True,
        "data": {
            "total": len(members),
            "users": [{"name": name, "uid": uid} for name, uid in members],
        },
    }
    resp.raise_for_status = MagicMock()
    return resp


def _run_group_write(members_sequence):
    """Run add_user_to_group with a canned sequence of member-listing responses.

    Returns (result, post_mock, get_mock). Each poll round consumes one entry
    of members_sequence; a None entry yields an error response (unreadable).
    """
    mgr = _make_manager()
    get_responses = []
    for members in members_sequence:
        if members is None:
            resp = MagicMock()
            resp.json.return_value = {"success": False, "error": {"code": 105}}
            resp.raise_for_status = MagicMock()
        else:
            resp = _member_response(members)
        get_responses.append(resp)
    with (
        patch("utils.synology_api.requests.post", return_value=_join_response()) as post,
        patch("utils.synology_api.requests.get", side_effect=get_responses) as get,
        patch("usermanagement.synology_users.time"),
    ):
        result = mgr.add_user_to_group("authelia", ["docker"])
    return result, post, get


def test_add_user_to_group_wire_format_and_verification():
    """Join POSTs the right wire format and verifies membership once visible."""
    result, post, _ = _run_group_write([[("authelia", 1026)]])

    assert result["success"] is True
    assert result["verified"] is True
    assert "warning" not in result

    sent = post.call_args
    assert sent.kwargs["data"]["api"] == "SYNO.Core.User.Group"
    assert sent.kwargs["data"]["method"] == "join"
    assert sent.kwargs["data"]["_sid"] == "sid_xyz"
    assert sent.kwargs["data"]["name"] == "authelia"
    assert json.loads(sent.kwargs["data"]["join_groups"]) == ["docker"]
    assert sent.kwargs["headers"]["X-SYNO-TOKEN"] == "tok_abc"

    print("✅ add_user_to_group wire format and verification passed")


def test_add_user_to_group_warns_when_task_never_applies():
    """Regression for issue #88: queued-but-never-applied must not read as clean.

    DSM accepted the join (success + task_id) but the membership never showed
    up in the group listing — exactly the failure the issue reporter hit. The
    result must carry verified: false plus a warning naming the group.
    """
    # Six poll rounds, user never appears (mirrors the 33s-unchanged report).
    result, _, get = _run_group_write([[("admin", 1024)]] * 6)

    assert result["success"] is True
    assert result["verified"] is False
    assert result["unverified_groups"] == ["docker"]
    assert "queued" in result["warning"]
    assert get.call_count == 6

    print("✅ queued-but-unapplied join surfaces verified=false and warning")


def test_add_user_to_group_polls_until_visible():
    """The task may land mid-polling; verification must keep retrying."""
    result, _, get = _run_group_write(
        [[("admin", 1024)]] * 3 + [[("admin", 1024), ("authelia", 1026)]]
    )

    assert result["verified"] is True
    assert "warning" not in result
    assert get.call_count == 4

    print("✅ join verification retries until membership is visible")


def test_add_user_to_group_treats_unreadable_listing_as_unverified():
    """A group listing that errors can't confirm the change — stay unverified."""
    result, _, _ = _run_group_write([None] * 6)

    assert result["verified"] is False
    assert result["unverified_groups"] == ["docker"]

    print("✅ unreadable member listing reports unverified")


def test_group_write_api_error_skips_verification():
    """A refused join is returned unchanged — nothing to verify."""
    mgr = _make_manager()
    error_resp = MagicMock()
    error_resp.json.return_value = {"success": False, "error": {"code": 105}}
    error_resp.raise_for_status = MagicMock()
    with (
        patch("utils.synology_api.requests.post", return_value=error_resp) as post,
        patch("utils.synology_api.requests.get") as get,
    ):
        result = mgr.add_user_to_group("authelia", ["docker"])

    assert result == {"success": False, "error": {"code": 105}}
    assert "verified" not in result
    assert post.call_count == 1
    assert get.call_count == 0

    print("✅ refused join returns the API error without verification calls")


def test_remove_user_from_group_verifies_departure():
    """Leaving a group verifies the user is gone from the listing."""
    mgr = _make_manager()
    with (
        patch("utils.synology_api.requests.post", return_value=_join_response()) as post,
        patch("utils.synology_api.requests.get", return_value=_member_response([("admin", 1024)])),
        patch("usermanagement.synology_users.time"),
    ):
        result = mgr.remove_user_from_group("authelia", ["docker"])

    assert result["verified"] is True
    assert "warning" not in result
    assert json.loads(post.call_args.kwargs["data"]["leave_groups"]) == ["docker"]

    print("✅ remove_user_from_group verifies departure")


def test_remove_user_from_group_warns_when_still_member():
    """Still-listed after the poll window — warn rather than report clean."""
    mgr = _make_manager()
    with (
        patch("utils.synology_api.requests.post", return_value=_join_response()),
        patch(
            "utils.synology_api.requests.get",
            return_value=_member_response([("admin", 1024), ("authelia", 1026)]),
        ),
        patch("usermanagement.synology_users.time"),
    ):
        result = mgr.remove_user_from_group("authelia", ["docker"])

    assert result["success"] is True
    assert result["verified"] is False
    assert result["unverified_groups"] == ["docker"]
    assert "queued" in result["warning"]

    print("✅ never-applied leave surfaces verified=false and warning")


def test_remove_user_from_group_unknown_payload_does_not_verify():
    """Regression (PR review): an unrecognized payload is unreadable, not empty.

    {'success': true, 'data': {}} carries no membership data; a removal must
    not "verify" absence the response never showed.
    """
    mgr = _make_manager()
    empty_resp = MagicMock()
    empty_resp.json.return_value = {"success": True, "data": {}}
    empty_resp.raise_for_status = MagicMock()
    with (
        patch("utils.synology_api.requests.post", return_value=_join_response()),
        patch("utils.synology_api.requests.get", return_value=empty_resp),
        patch("usermanagement.synology_users.time"),
    ):
        result = mgr.remove_user_from_group("authelia", ["docker"])

    assert result["verified"] is False
    assert result["unverified_groups"] == ["docker"]

    print("✅ removal does not verify on unrecognized payload shape")


def test_group_member_names_tolerates_response_shapes():
    """The parser accepts the data.shapes seen across DSM versions."""
    mgr = _make_manager()

    shapes = [
        {"success": True, "data": {"users": [{"name": "admin", "uid": 1024}]}},
        {"success": True, "data": {"members": [{"name": "admin"}]}},
        {"success": True, "data": ["admin", "guest"]},
        {"success": True, "data": {}},
        {"success": True, "data": {"users": []}},
        {"success": False, "error": {"code": 105}},
    ]
    expected = [{"admin"}, {"admin"}, {"admin", "guest"}, None, set(), None]

    for shape, want in zip(shapes, expected):
        resp = MagicMock()
        resp.json.return_value = shape
        resp.raise_for_status = MagicMock()
        with patch("utils.synology_api.requests.get", return_value=resp):
            assert mgr._group_member_names("administrators") == want

    print("✅ member-name parser tolerates known response shapes")


def test_verification_skips_already_confirmed_groups():
    """Confirmed groups drop out of the working set — no redundant re-polls."""
    mgr = _make_manager()
    users_resp = _member_response([("authelia", 1026)])  # confirmed on round 1
    docker_slow = _member_response([("admin", 1024)])  # authelia not yet applied
    docker_done = _member_response([("admin", 1024), ("authelia", 1026)])
    with (
        patch("utils.synology_api.requests.post", return_value=_join_response()),
        patch(
            "utils.synology_api.requests.get",
            side_effect=[users_resp, docker_slow, docker_slow, docker_done],
        ) as get,
        patch("usermanagement.synology_users.time"),
    ):
        result = mgr.add_user_to_group("authelia", ["users", "docker"])

    assert result["verified"] is True
    # Round 1 polls both groups; rounds 2+ poll only the still-pending 'docker'.
    polled_groups = [c.kwargs["params"]["group"] for c in get.call_args_list]
    assert polled_groups == ["users", "docker", "docker", "docker"]

    print("✅ already-confirmed groups are not re-polled")
