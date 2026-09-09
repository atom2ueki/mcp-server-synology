"""Unit tests for the SAN Manager (iSCSI) surface. No live NAS required.

Each assertion here corresponds to something that was observed to fail against
DSM 7.3.2-86009 Update 4, not to a guess about the API:

  * target_id must reach DSM as a JSON string literal (`"1"`). Sent bare it
    fails with 18990710 - the same code DSM returns for a target that does not
    exist, so the failure does not point at the cause.
  * a session-lifecycle error (106/107/119) must trigger one relogin and one
    retry. Only 119 did, which is why a 106 reached the caller raw.
  * a failed response must name the api/method that produced it, because DSM
    returns a bare number.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iscsi.synology_iscsi import (  # noqa: E402
    LUN_API,
    TARGET_API,
    SynologyISCSI,
    _json_str,
)
from utils.synology_api import (  # noqa: E402
    SESSION_EXPIRED_CODES,
    annotate_error,
    describe_error_code,
)


def _make_iscsi():
    return SynologyISCSI("https://nas.example", "fake-sid", verify_ssl=False, syno_token="tok")


# ---------------------------------------------------------------------------
# target_id encoding - the actual bug
# ---------------------------------------------------------------------------


class TestTargetIdEncoding:
    def test_json_str_adds_quote_characters(self):
        assert _json_str(1) == '"1"'
        assert _json_str("1") == '"1"'

    @pytest.mark.parametrize("method_name,expected_method", [("target_get", "get")])
    def test_target_get_sends_quoted_id(self, method_name, expected_method):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value={"success": True}) as mock:
            getattr(iscsi, method_name)(7)
        mock.assert_called_once_with(TARGET_API, expected_method, 1, {"target_id": '"7"'})

    def test_target_delete_sends_quoted_id(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.target_delete(7)
        mock.assert_called_once_with(TARGET_API, "delete", 1, {"target_id": '"7"'})

    def test_bare_id_would_be_a_regression(self):
        """Guards the fix: a plain str() would send `7`, which DSM refuses."""
        assert _json_str(7) != "7"


# ---------------------------------------------------------------------------
# LUN creation
# ---------------------------------------------------------------------------


class TestLunCreate:
    def test_default_type_is_btrfs_thin(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1073741824)
        _, _, _, params = mock.call_args[0]
        assert params["type"] == "BLUN"
        assert params["size"] == "1073741824"
        assert params["location"] == "/volume2"
        assert "description" not in params

    @pytest.mark.parametrize(
        "given,expected",
        [("thin", "BLUN"), ("Thin", "BLUN"), ("advanced", "ADV"), ("file", "FILE")],
    )
    def test_friendly_aliases_resolve(self, given, expected):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1, lun_type=given)
        assert mock.call_args[0][3]["type"] == expected

    def test_raw_dsm_type_passes_through(self):
        """An unaliased name is forwarded so another DSM/volume is still reachable."""
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume1", 1, lun_type="BLUN_THICK")
        assert mock.call_args[0][3]["type"] == "BLUN_THICK"

    def test_description_included_when_given(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1, description="notes")
        assert mock.call_args[0][3]["description"] == "notes"


# ---------------------------------------------------------------------------
# Target creation and CHAP
# ---------------------------------------------------------------------------


class TestTargetCreate:
    def test_no_chap_by_default(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.target_create("dev")
        params = mock.call_args[0][3]
        assert params["auth_type"] == "0"
        assert params["iqn"] == "iqn.2000-01.com.synology:dev"

    def test_chap_sets_auth_type_one(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.target_create("dev", chap_user="u", chap_password="p" * 12)
        params = mock.call_args[0][3]
        assert params["auth_type"] == "1"
        assert params["user"] == "u"

    @pytest.mark.parametrize(
        "user,password", [("u", None), (None, "p" * 12), ("u", ""), ("", "p" * 12)]
    )
    def test_half_a_credential_is_refused_without_calling_dsm(self, user, password):
        """Never silently create an unauthenticated target the caller thinks is protected."""
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.target_create("dev", chap_user=user, chap_password=password)
        assert result["success"] is False
        assert result["error"]["code"] == "chap_incomplete"
        mock.assert_not_called()

    def test_explicit_iqn_wins(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.target_create("dev", iqn="iqn.1991-05.com.microsoft:host")
        assert mock.call_args[0][3]["iqn"] == "iqn.1991-05.com.microsoft:host"


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


class TestMapping:
    def test_map_sends_json_array_of_strings(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_map_targets("uuid-1", [3])
        api, method, _, params = mock.call_args[0]
        assert (api, method) == (LUN_API, "map_target")
        assert params == {"uuid": "uuid-1", "target_ids": '["3"]'}

    def test_unmap_uses_unmap_target(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_unmap_targets("uuid-1", [3])
        assert mock.call_args[0][1] == "unmap_target"


# ---------------------------------------------------------------------------
# LUN listing and lookup
# ---------------------------------------------------------------------------


class TestLunListing:
    LIST_OK = {
        "success": True,
        "data": {"luns": [{"name": "dev", "uuid": "abc-123", "size": 1}]},
    }

    def test_list_requests_status_and_is_mapped(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value=self.LIST_OK) as mock:
            iscsi.lun_list()
        assert mock.call_args[0][3] == {"additional": '["status", "is_mapped"]'}

    def test_get_matches_name_or_uuid(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value=self.LIST_OK):
            assert iscsi.lun_get("dev")["data"]["uuid"] == "abc-123"
            assert iscsi.lun_get("abc-123")["data"]["name"] == "dev"

    def test_missing_lun_names_the_api(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value=self.LIST_OK):
            result = iscsi.lun_get("nope")
        assert result["error"]["code"] == "lun_not_found"
        assert result["error"]["api"] == LUN_API

    def test_failed_list_propagates(self):
        iscsi = _make_iscsi()
        failure = {"success": False, "error": {"code": 105}}
        with patch.object(iscsi._api, "get", return_value=failure):
            assert iscsi.lun_get("dev") == failure


# ---------------------------------------------------------------------------
# Error annotation and session recovery
# ---------------------------------------------------------------------------


class TestErrorAnnotation:
    def test_common_codes_are_described(self):
        assert describe_error_code(106) == "Session timeout"
        assert describe_error_code(105) == "The logged-in session does not have permission"

    def test_unknown_code_has_no_invented_description(self):
        assert describe_error_code(4242) is None

    def test_annotation_names_the_failing_call(self):
        annotated = annotate_error({"success": False, "error": {"code": 106}}, LUN_API, "list", 1)
        assert annotated["error"]["api"] == LUN_API
        assert annotated["error"]["method"] == "list"
        assert annotated["error"]["message"] == "Session timeout"

    def test_success_is_untouched(self):
        payload = {"success": True, "data": {"luns": []}}
        assert annotate_error(dict(payload), LUN_API, "list", 1) == payload

    def test_existing_message_is_not_overwritten(self):
        annotated = annotate_error(
            {"success": False, "error": {"code": 106, "message": "mine"}}, LUN_API, "list", 1
        )
        assert annotated["error"]["message"] == "mine"

    def test_failure_without_an_error_object_still_records_the_call(self):
        annotated = annotate_error({"success": False}, LUN_API, "list", 1)
        assert annotated["error"]["api"] == LUN_API


class TestSessionRecovery:
    """106 and 107 must recover exactly as 119 already did."""

    # Deliberately a literal, NOT sorted(SESSION_EXPIRED_CODES): parametrising
    # over the constant under test makes the test shrink whenever the constant
    # shrinks, so narrowing recovery back to {119} would silently stop
    # exercising 106 and 107 instead of failing.
    @pytest.mark.parametrize("code", [106, 107, 119])
    def test_expired_session_triggers_one_relogin_and_one_retry(self, code):
        from utils.synology_api import SynologyAPIClient

        client = SynologyAPIClient("https://nas.example", "stale-sid", syno_token="old")
        expired = {"success": False, "error": {"code": code}}
        recovered = {"success": True, "data": {"luns": []}}

        # Credentials are captured AT THE MOMENT of each call, not read off the
        # client afterwards. Asserting only the final state would still pass if
        # the refresh were moved after the retry - i.e. if the retry went out
        # with the same dead SID that just failed, which is the whole bug.
        seen = []

        def record(*_args, **_kwargs):
            seen.append((client.session_id, client.syno_token))
            return expired if len(seen) == 1 else recovered

        with (
            patch.object(client, "_do_request", side_effect=record) as do,
            patch(
                "utils.synology_api._try_relogin", return_value=("fresh-sid", "new")
            ) as relogin,
        ):
            result = client.request(LUN_API, "list")

        assert result["success"] is True
        assert do.call_count == 2
        relogin.assert_called_once_with("https://nas.example", "stale-sid")
        assert seen[0] == ("stale-sid", "old"), "first attempt should use the original session"
        assert seen[1] == ("fresh-sid", "new"), "retry must use the refreshed session, not the dead one"
        assert client.session_id == "fresh-sid"
        assert client.syno_token == "new"

    def test_106_used_to_be_returned_raw(self):
        """The regression guard: 106 must not be treated as unrecoverable."""
        assert 106 in SESSION_EXPIRED_CODES
        assert 107 in SESSION_EXPIRED_CODES
        assert 119 in SESSION_EXPIRED_CODES

    def test_non_session_error_does_not_relogin(self):
        from utils.synology_api import SynologyAPIClient

        client = SynologyAPIClient("https://nas.example", "sid")
        refused = {"success": False, "error": {"code": 105}}
        with (
            patch.object(client, "_do_request", return_value=refused) as do,
            patch("utils.synology_api._try_relogin") as relogin,
        ):
            result = client.request(LUN_API, "list")

        assert do.call_count == 1
        relogin.assert_not_called()
        assert result["error"]["code"] == 105
        assert result["error"]["api"] == LUN_API

    def test_failed_relogin_returns_the_annotated_original(self):
        from utils.synology_api import SynologyAPIClient

        client = SynologyAPIClient("https://nas.example", "sid")
        expired = {"success": False, "error": {"code": 106}}
        with (
            patch.object(client, "_do_request", return_value=expired) as do,
            patch("utils.synology_api._try_relogin", return_value=(None, None)),
        ):
            result = client.request(LUN_API, "list")

        assert do.call_count == 1
        assert result["error"]["code"] == 106
        assert result["error"]["message"] == "Session timeout"
        assert result["error"]["api"] == LUN_API


class TestHealthDelegation:
    """SynologyHealth.lun_* must stay one implementation with the iSCSI module."""

    def test_health_lun_list_uses_the_shared_client(self):
        from health.synology_health import SynologyHealth

        health = SynologyHealth("https://nas.example", "sid", verify_ssl=False)
        with patch.object(
            health._api, "get", return_value={"success": True, "data": {"luns": []}}
        ) as mock:
            health.lun_list()
        assert mock.call_args[0][0] == LUN_API
        assert health._iscsi()._api is health._api


def test_target_list_asks_for_mapped_lun_singular():
    """`mapped_luns` is ignored by DSM; `mapped_lun` is the field that works."""
    iscsi = _make_iscsi()
    with patch.object(iscsi._api, "get", return_value={"success": True}) as mock:
        iscsi.target_list()
    assert mock.call_args[0][3] == {"additional": '["mapped_lun"]'}


def test_api_client_can_be_shared():
    shared = MagicMock()
    iscsi = SynologyISCSI("https://nas.example", "sid", api_client=shared)
    assert iscsi._api is shared


# ---------------------------------------------------------------------------
# Regressions found in review (2026-09-08)
# ---------------------------------------------------------------------------


class TestLunTypeCollision:
    """`THIN` is a DSM type (7); `thin` is the friendly name for BLUN (263)."""

    def test_uppercase_dsm_thin_is_not_rewritten_to_blun(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1, lun_type="THIN")
        assert mock.call_args[0][3]["type"] == "THIN"

    def test_lowercase_thin_is_still_the_friendly_alias(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1, lun_type="thin")
        assert mock.call_args[0][3]["type"] == "BLUN"

    @pytest.mark.parametrize("dsm_type", ["ADV", "FILE", "BLUN", "BLUN_THICK", "CINDER"])
    def test_every_known_dsm_name_survives_verbatim(self, dsm_type):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1, lun_type=dsm_type)
        assert mock.call_args[0][3]["type"] == dsm_type


class TestEmptySelection:
    """all([]) is True, so an empty list must be refused, not reported as done."""

    def test_map_with_no_targets_makes_no_request(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.lun_map_targets("uuid-1", [])
        assert result["success"] is False
        assert result["error"]["code"] == "empty_selection"
        mock.assert_not_called()

    def test_unmap_with_no_targets_makes_no_request(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.lun_unmap_targets("uuid-1", [])
        assert result["success"] is False
        mock.assert_not_called()


class TestDestructiveGating:
    """The confirm gate must be a gate, not a formality.

    pytest-asyncio is not installed in this venv (four pre-existing tests in
    test_logout.py fail for that reason), so the coroutine is driven directly
    rather than through a marker that would silently skip.
    """

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    def _call(self, handler, arguments):
        return json.loads(asyncio.run(handler(arguments))[0].text)

    @pytest.mark.parametrize("confirm", ["false", "true", 1, 0, "", "yes", None, [], "False"])
    def test_only_boolean_true_confirms(self, confirm):
        """The JSON string "false" is truthy in Python - it must not confirm a delete."""
        server = self._server()
        assert server._is_confirmed({"confirm": confirm}) is (confirm is True)

    def test_boolean_true_confirms(self):
        assert self._server()._is_confirmed({"confirm": True}) is True

    def test_absent_confirm_does_not_confirm(self):
        assert self._server()._is_confirmed({}) is False

    def test_string_false_does_not_reach_the_nas(self):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            result = self._call(server._handle_lun_delete, {"uuid": "u", "confirm": "false"})
        assert result["success"] is False
        assert result["error"]["code"] == "confirmation_required"
        get_iscsi.assert_not_called()

    def test_target_delete_gate_is_the_same_gate(self):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            result = self._call(server._handle_target_delete, {"target_id": 1, "confirm": "false"})
        assert result["error"]["code"] == "confirmation_required"
        get_iscsi.assert_not_called()


class TestChapArgumentAliases:
    """A credential under a name the handler does not read must be refused.

    Discarding it does not fail - it creates a target with no authentication
    while the caller believes it supplied some.
    """

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    @pytest.mark.parametrize(
        "arguments",
        [
            {"name": "t", "user": "u", "password": "p"},
            {"name": "t", "username": "u", "password": "p"},
            {"name": "t", "password": "p"},
            {"name": "t", "chap_username": "u", "chap_password": "p"},
            {"name": "t", "secret": "p"},
        ],
    )
    def test_misnamed_credentials_create_nothing(self, arguments):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            result = json.loads(
                asyncio.run(server._handle_target_create(arguments))[0].text
            )
        assert result["success"] is False
        assert result["error"]["code"] == "unknown_argument"
        get_iscsi.assert_not_called()

    def test_correctly_named_credentials_are_accepted(self):
        server = self._server()
        iscsi = MagicMock()
        iscsi.target_create.return_value = {"success": True, "data": {"target_id": 1}}
        with (
            patch.object(server, "_get_base_url", return_value="https://nas.example"),
            patch.object(server, "_get_iscsi", return_value=iscsi),
        ):
            result = json.loads(
                asyncio.run(
                    server._handle_target_create(
                        {"name": "t", "chap_user": "u", "chap_password": "p" * 12}
                    )
                )[0].text
            )
        assert result["success"] is True
        assert iscsi.target_create.call_args.kwargs["chap_user"] == "u"


class TestEmptyMappingRequest:
    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    def test_empty_lun_uuids_is_refused_not_reported_as_success(self):
        server = self._server()
        with patch.object(server, "_get_base_url", return_value="https://nas.example"):
            with patch.object(server, "_get_iscsi") as get_iscsi:
                result = json.loads(
                    asyncio.run(
                        server._handle_target_map_lun({"target_id": 1, "lun_uuids": []})
                    )[0].text
                )
        assert result["success"] is False
        assert result["error"]["code"] == "empty_selection"
        get_iscsi.assert_not_called()
