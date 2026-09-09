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
    _resolve_lun_type,
    _uuid_list,
)
from mcp_server import ToolFailure  # noqa: E402
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

    pytest-asyncio is declared in the project's test extra, but the coroutines
    here are driven directly anyway: a missing plugin turns an async test into a
    silent skip, and a destructive-operation guard that quietly stops running is
    the last thing that should be able to happen.
    """

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    @pytest.mark.parametrize("confirm", ["false", "true", 1, 0, "", "yes", None, [], "False"])
    def test_only_boolean_true_confirms(self, confirm):
        """The JSON string "false" is truthy in Python - it must not confirm."""
        server = self._server()
        assert server._is_confirmed({"confirm": confirm}) is (confirm is True)

    def test_boolean_true_confirms(self):
        assert self._server()._is_confirmed({"confirm": True}) is True

    def test_absent_confirm_does_not_confirm(self):
        assert self._server()._is_confirmed({}) is False

    @pytest.mark.parametrize(
        "handler_name,arguments",
        [
            ("_handle_lun_delete", {"uuid": "u", "confirm": "false"}),
            ("_handle_target_delete", {"target_id": 1, "confirm": "false"}),
            ("_handle_target_unmap_lun", {"target_id": 1, "lun_uuids": ["u"], "confirm": "false"}),
        ],
    )
    def test_string_false_does_not_reach_the_nas(self, handler_name, arguments):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            with pytest.raises(ToolFailure) as raised:
                asyncio.run(getattr(server, handler_name)(arguments))
        assert raised.value.payload["error"]["code"] == "confirmation_required"
        get_iscsi.assert_not_called()

    def test_unmapping_is_gated_too(self):
        """Unmapping disconnects live storage; it is destructive."""
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            with pytest.raises(ToolFailure):
                asyncio.run(
                    server._handle_target_unmap_lun({"target_id": 1, "lun_uuids": ["u"]})
                )
        get_iscsi.assert_not_called()

    def test_refusal_sets_the_protocol_error_flag(self):
        """A refusal must not arrive as a success at the MCP layer."""
        failure = ToolFailure({"success": False, "error": {"code": "confirmation_required"}})
        assert failure.payload["success"] is False


class TestUndeclaredArguments:
    """An argument the handler does not read must be refused, never dropped.

    Dropping one is not a failed call: for target_create it produces a target
    with NO authentication while the caller believes it supplied some, and for
    lun_create it silently takes the default type. An enumerated denylist of
    misspellings was the first attempt and could not work - the next name nobody
    listed still went through - so the check is against the DECLARED schema.
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
            {"name": "t", "chapUser": "u", "chapPassword": "p"},
            {"name": "t", "auth_type": 1},
            {"name": "t", "secret": "p"},
            {"name": "t", "CHAP_USER": "u"},
        ],
    )
    def test_misnamed_credentials_create_nothing(self, arguments):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            with pytest.raises(ToolFailure) as raised:
                asyncio.run(server._dispatch_tool("synology_target_create", arguments))
        assert raised.value.payload["error"]["code"] == "unknown_argument"
        get_iscsi.assert_not_called()

    def test_lun_type_instead_of_type_is_refused_not_defaulted(self):
        """`lun_type` is the module's kwarg; the TOOL declares `type`."""
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            with pytest.raises(ToolFailure) as raised:
                asyncio.run(
                    server._dispatch_tool(
                        "synology_lun_create",
                        {"name": "n", "location": "/volume2", "size": 1, "lun_type": "THIN"},
                    )
                )
        assert raised.value.payload["error"]["code"] == "unknown_argument"
        get_iscsi.assert_not_called()

    def test_the_message_lists_what_is_accepted(self):
        server = self._server()
        refusal = server._reject_undeclared_arguments("synology_target_create", {"user": "u"})
        assert "chap_user" in refusal["error"]["message"]
        assert "chap_password" in refusal["error"]["message"]

    def test_declared_arguments_pass(self):
        server = self._server()
        assert (
            server._reject_undeclared_arguments(
                "synology_target_create",
                {"name": "t", "chap_user": "u", "chap_password": "p", "nas_name": "x"},
            )
            is None
        )

    def test_protocol_meta_keys_are_not_refused(self):
        server = self._server()
        assert (
            server._reject_undeclared_arguments("synology_target_create", {"_meta": {}}) is None
        )

    def test_every_new_tool_is_covered(self):
        """The strict set must name every tool added with this mechanism.

        Listed explicitly rather than derived from _STRICT_ARG_TOOLS, which
        would make this test shrink exactly when the set does.
        """
        from mcp_server import SynologyMCPServer

        expected = {
            "synology_lun_create",
            "synology_lun_delete",
            "synology_target_list",
            "synology_target_get",
            "synology_target_create",
            "synology_target_delete",
            "synology_target_map_lun",
            "synology_target_unmap_lun",
        }
        assert expected <= SynologyMCPServer._STRICT_ARG_TOOLS


class TestChapCredentials:
    """Omitted and supplied-but-empty are different things."""

    @pytest.mark.parametrize(
        "user,password",
        [("u", None), (None, "p" * 12), ("u", ""), ("", "p" * 12), ("", ""), ("  ", "  ")],
    )
    def test_incomplete_or_blank_credentials_create_nothing(self, user, password):
        """An empty credential must never become a target with no auth."""
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.target_create("dev", chap_user=user, chap_password=password)
        assert result["success"] is False
        assert result["error"]["code"] == "chap_incomplete"
        mock.assert_not_called()

    def test_both_omitted_is_an_unauthenticated_target_on_purpose(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.target_create("dev")
        assert mock.call_args[0][3]["auth_type"] == "0"

    def test_empty_iqn_is_refused_rather_than_replaced(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.target_create("dev", iqn="")
        assert result["error"]["code"] == "invalid_argument"
        mock.assert_not_called()


class TestInputCoercion:
    """int() would silently change caller input; these must be refused."""

    @pytest.mark.parametrize("bad_size", [True, False, 0.9, "abc", None, [1]])
    def test_bad_size_is_refused_before_any_request(self, bad_size):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.lun_create("dev", "/volume2", bad_size)
        assert result["success"] is False
        assert result["error"]["code"] == "invalid_argument"
        mock.assert_not_called()

    def test_true_would_have_become_a_one_byte_lun(self):
        """int(True) == 1. The regression guard for the coercion."""
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            iscsi.lun_create("dev", "/volume2", True)
        mock.assert_not_called()

    @pytest.mark.parametrize("good,expected", [(1073741824, "1073741824"), (1024.0, "1024"), ("2048", "2048")])
    def test_whole_numbers_are_accepted(self, good, expected):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", good)
        assert mock.call_args[0][3]["size"] == expected

    @pytest.mark.parametrize("bad", [{"uuid-1": False}, "uuid-1", 5, [""], [None], [123]])
    def test_bad_uuid_list_shapes_are_refused(self, bad):
        """A dict iterates its KEYS; a string iterates characters."""
        with pytest.raises(ValueError):
            _uuid_list(bad, "lun_uuids")

    def test_good_uuid_list_is_stripped(self):
        assert _uuid_list([" a ", "b"], "lun_uuids") == ["a", "b"]


class TestExplicitOptionalValues:
    """An explicitly supplied value is honoured, not replaced by the default."""

    def test_empty_additional_is_honoured_not_defaulted(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value={"success": True}) as mock:
            iscsi.lun_list(additional=[])
        assert mock.call_args[0][3] == {"additional": "[]"}

    def test_empty_additional_on_targets_is_honoured(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value={"success": True}) as mock:
            iscsi.target_list(additional=[])
        assert mock.call_args[0][3] == {"additional": "[]"}

    def test_omitted_additional_still_gets_the_default(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value={"success": True}) as mock:
            iscsi.lun_list()
        assert mock.call_args[0][3] == {"additional": '["status", "is_mapped"]'}


class TestEmptyMappingRequest:
    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    def test_empty_lun_uuids_is_refused_not_reported_as_success(self):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            with pytest.raises(ToolFailure) as raised:
                asyncio.run(
                    server._handle_target_map_lun({"target_id": 1, "lun_uuids": []})
                )
        assert raised.value.payload["error"]["code"] == "empty_selection"
        get_iscsi.assert_not_called()

    def test_malformed_lun_uuids_never_reaches_a_write(self):
        server = self._server()
        with patch.object(server, "_get_iscsi") as get_iscsi:
            with pytest.raises(ToolFailure) as raised:
                asyncio.run(
                    server._handle_target_map_lun(
                        {"target_id": 1, "lun_uuids": {"real-uuid": False}}
                    )
                )
        assert raised.value.payload["error"]["code"] == "invalid_argument"
        get_iscsi.assert_not_called()

    def test_partial_failure_sets_the_error_flag(self):
        """One LUN maps, one fails: the call must not report success."""
        server = self._server()
        iscsi = MagicMock()
        iscsi.lun_map_targets.side_effect = [
            {"success": True},
            {"success": False, "error": {"code": 105}},
        ]
        with (
            patch.object(server, "_get_base_url", return_value="https://nas.example"),
            patch.object(server, "_get_iscsi", return_value=iscsi),
        ):
            with pytest.raises(ToolFailure) as raised:
                asyncio.run(
                    server._handle_target_map_lun({"target_id": 1, "lun_uuids": ["a", "b"]})
                )
        payload = raised.value.payload
        assert payload["success"] is False
        # The per-LUN evidence must survive the raise.
        assert [r["lun_uuid"] for r in payload["data"]["results"]] == ["a", "b"]
        assert payload["data"]["results"][0]["success"] is True
        assert payload["data"]["results"][1]["success"] is False


class TestSessionRace:
    """Recovery must name the SID the failed request actually used."""

    def test_relogin_is_told_the_sid_that_was_attempted(self):
        from utils.synology_api import SynologyAPIClient

        client = SynologyAPIClient("https://nas.example", "S0", syno_token="t0")
        expired = {"success": False, "error": {"code": 106}}

        def displace(*_a, **_k):
            # Another caller sharing this client recovers first, moving it to S1
            # while our request is still in flight.
            client.session_id = "S1"
            return expired

        with (
            patch.object(client, "_do_request", side_effect=displace),
            patch("utils.synology_api._try_relogin", return_value=(None, None)) as relogin,
        ):
            client.request(LUN_API, "list")

        # S0 is the session that failed. Reporting S1 would tell the auth layer
        # the CURRENT session is dead, so its dedupe guard would open a second
        # session instead of reusing the good one.
        relogin.assert_called_once_with("https://nas.example", "S0")


class TestWriteTimeouts:
    """A LUN write must not use the default read timeout.

    Observed on 2026-09-08: SYNO.Core.ISCSI.LUN/create returned a read timeout
    to the caller and created the LUN anyway. A client-side timeout does not
    cancel what DSM is already doing, so the uuid was lost and the LUN was
    invisible to the caller -- found only by listing everything.
    """

    def test_create_uses_the_long_timeout(self):
        from utils.synology_api import DEFAULT_TIMEOUT, LUN_WRITE_TIMEOUT

        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_create("dev", "/volume2", 1024)
        assert mock.call_args.kwargs.get("timeout") == LUN_WRITE_TIMEOUT
        assert LUN_WRITE_TIMEOUT > DEFAULT_TIMEOUT

    def test_delete_uses_the_long_timeout(self):
        from utils.synology_api import LUN_WRITE_TIMEOUT

        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post", return_value={"success": True}) as mock:
            iscsi.lun_delete("uuid-1")
        assert mock.call_args.kwargs.get("timeout") == LUN_WRITE_TIMEOUT

    def test_reads_do_not_pay_the_long_timeout(self):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "get", return_value={"success": True}) as mock:
            iscsi.lun_list()
        assert "timeout" not in mock.call_args.kwargs

    def test_the_client_actually_passes_it_through(self):
        """The parameter must reach requests, not just be accepted."""
        from utils.synology_api import SynologyAPIClient

        client = SynologyAPIClient("https://nas.example", "sid")
        with patch("utils.synology_api.requests.post") as post:
            post.return_value.json.return_value = {"success": True}
            post.return_value.raise_for_status = MagicMock()
            client.post(LUN_API, "create", 1, {"name": "x"}, timeout=120)
        assert post.call_args.kwargs["timeout"] == 120


# ---------------------------------------------------------------------------
# Raised in review of the upstream PR (2026-09-09)
# ---------------------------------------------------------------------------


class TestDeclaredButWrongType:
    """Being declared is not being validated.

    _dispatch_tool refuses an argument that was not DECLARED; it does not check
    the type of one that was. A non-string `type` or `iqn` reached .strip() and
    raised AttributeError, which the MCP wrapper renders as a generic
    "Error executing ..." string - losing the structured invalid_argument
    response every other bad input here gets.
    """

    @pytest.mark.parametrize("bad_type", [1, True, None, ["BLUN"], {"t": "BLUN"}])
    def test_non_string_lun_type_is_refused_structurally(self, bad_type):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.lun_create("dev", "/volume2", 1024, lun_type=bad_type)
        assert result["success"] is False
        assert result["error"]["code"] == "invalid_argument"
        mock.assert_not_called()

    @pytest.mark.parametrize("bad_iqn", [1, True, ["iqn"], {"iqn": "x"}])
    def test_non_string_iqn_is_refused_structurally(self, bad_iqn):
        iscsi = _make_iscsi()
        with patch.object(iscsi._api, "post") as mock:
            result = iscsi.target_create("dev", iqn=bad_iqn)
        assert result["success"] is False
        assert result["error"]["code"] == "invalid_argument"
        mock.assert_not_called()

    def test_resolve_lun_type_raises_rather_than_attributeerror(self):
        with pytest.raises(ValueError):
            _resolve_lun_type(1)


class TestNonDictError:
    """annotate_error tolerates a non-dict `error`; request() must agree.

    They disagreed, and the disagreement did not degrade gracefully: `.get` on a
    string raised AttributeError out of request(), so the caller got an
    exception instead of the failure dict this client promises.
    """

    @pytest.mark.parametrize("weird", ["boom", ["boom"], 42, None])
    def test_request_survives_a_non_dict_error(self, weird):
        from utils.synology_api import SynologyAPIClient

        client = SynologyAPIClient("https://nas.example", "sid")
        with patch.object(
            client, "_do_request", return_value={"success": False, "error": weird}
        ):
            result = client.request(LUN_API, "list")
        assert result["success"] is False

    def test_annotate_error_and_request_agree(self):
        from utils.synology_api import annotate_error

        annotated = annotate_error({"success": False, "error": "boom"}, LUN_API, "list", 1)
        assert annotated["error"]["api"] == LUN_API


class TestEventLoopIsNotBlocked:
    """Synchronous `requests` calls must not run on the event loop.

    These handlers are `async def`, so awaiting them runs their body ON the
    loop. A LUN create holds it for up to LUN_WRITE_TIMEOUT (120s), during which
    the server answers nothing at all - not even the MCP protocol. Raising that
    timeout from 15s to 120s made this materially worse, so it is fixed here
    rather than left as the pre-existing shape.
    """

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    def _ticks_during(self, coro_factory, blocking_call_duration=0.30):
        """Run a handler whose network call sleeps; count event-loop ticks during it.

        The measurement is the NUMBER OF TICKS the loop managed while the call
        was in flight, not the largest gap between them. Gap size looked like
        the obvious metric and is useless here: when the loop is blocked the
        watchdog does not run at all, so it records nothing during the call, and
        it is cancelled as soon as the handler returns -- before it can register
        the one late tick. Measured directly: offloaded gives ~18 ticks with a
        24ms worst gap; blocking gives 0 ticks and a 19ms worst gap. Asserting
        on the gap therefore passed in BOTH cases.
        """
        import time

        async def main():
            gaps = []

            async def watchdog():
                last = time.perf_counter()
                try:
                    while True:
                        await asyncio.sleep(0.01)
                        now = time.perf_counter()
                        gaps.append(now - last)
                        last = now
                except asyncio.CancelledError:
                    raise

            task = asyncio.ensure_future(watchdog())
            # Let it tick before the handler starts, so "ticks during" is
            # measured against a watchdog already running.
            while len(gaps) < 2:
                await asyncio.sleep(0.01)
            before = len(gaps)
            try:
                await coro_factory(blocking_call_duration)
            finally:
                during = len(gaps) - before
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return during

        return asyncio.run(main())

    def _sleeping_iscsi(self, duration_holder):
        import time

        iscsi = MagicMock()

        def slow(*_a, **_k):
            time.sleep(duration_holder[0])
            return {"success": True, "data": {"uuid": "u", "lun_id": 1}}

        for name in (
            "lun_create",
            "lun_delete",
            "lun_list",
            "lun_get",
            "target_get",
            "target_create",
            "target_delete",
            "lun_map_targets",
            "lun_unmap_targets",
        ):
            getattr(iscsi, name).side_effect = slow
        return iscsi

    @pytest.mark.parametrize(
        "handler_name,arguments",
        [
            ("_handle_lun_create", {"name": "n", "location": "/volume2", "size": 1024}),
            ("_handle_lun_delete", {"uuid": "u", "confirm": True}),
            ("_handle_target_create", {"name": "t"}),
            ("_handle_target_delete", {"target_id": 1, "confirm": True}),
            ("_handle_target_map_lun", {"target_id": 1, "lun_uuids": ["a"]}),
            (
                "_handle_target_unmap_lun",
                {"target_id": 1, "lun_uuids": ["a"], "confirm": True},
            ),
        ],
    )
    def test_handler_does_not_hold_the_event_loop(self, handler_name, arguments):
        server = self._server()
        duration = [0.30]
        iscsi = self._sleeping_iscsi(duration)

        async def call(_d):
            with (
                patch.object(server, "_get_base_url", return_value="https://nas.example"),
                patch.object(server, "_get_iscsi", return_value=iscsi),
            ):
                return await getattr(server, handler_name)(arguments)

        ticks = self._ticks_during(call, duration[0])
        # The call sleeps 300ms against a 10ms tick. Offloaded, the loop ticks
        # ~18 times; blocking, exactly 0. Five is far below the former and far
        # above the latter, so it does not depend on machine speed.
        assert ticks >= 5, (
            f"{handler_name} held the event loop: only {ticks} tick(s) during a "
            f"{duration[0] * 1000:.0f}ms call. Its synchronous request is not offloaded."
        )

    def test_mapping_stays_sequential(self):
        """Offloading must not turn one target's mapping into a race."""
        server = self._server()
        order = []
        iscsi = MagicMock()

        def record(uuid, _targets):
            order.append(("start", uuid))
            import time

            time.sleep(0.02)
            order.append(("end", uuid))
            return {"success": True}

        iscsi.lun_map_targets.side_effect = record
        with (
            patch.object(server, "_get_base_url", return_value="https://nas.example"),
            patch.object(server, "_get_iscsi", return_value=iscsi),
        ):
            asyncio.run(
                server._handle_target_map_lun({"target_id": 1, "lun_uuids": ["a", "b"]})
            )
        assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]


class TestLazyLoginIsAlsoOffloaded:
    """The offload must cover the login, not just the DSM call after it.

    Every iSCSI handler resolves the NAS before it offloads anything, and
    `_get_base_url` performs a lazy DSM login when a configured nas_name has no
    session yet. That login is a synchronous HTTP round trip, so it ran ON the
    event loop -- the same defect as the calls below it, one step earlier in the
    same handler.

    TestEventLoopIsNotBlocked could not see it: it patches `_get_base_url`, so
    its scope excluded exactly the path in question. This class removes that
    patch, which is the whole point of it existing separately.
    """

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    @staticmethod
    def _slow_login(server, duration, calls):
        """Stand in for _get_base_url with a lazy login that takes `duration`.

        Mirrors the real semantics: with allow_login=False it raises (no session
        yet), and with a login permitted it performs a blocking round trip.
        """
        import time

        def fake(arguments, *, allow_login=True):
            if not allow_login:
                raise Exception("No active session")
            calls.append(1)
            time.sleep(duration)
            return "https://nas.example"

        return patch.object(server, "_get_base_url", side_effect=fake)

    def test_lazy_login_does_not_hold_the_event_loop(self):
        import time

        server = self._server()
        calls = []
        iscsi = MagicMock()
        iscsi.lun_list.return_value = {"success": True, "data": {"luns": []}}

        async def main():
            gaps = []

            async def watchdog():
                last = time.perf_counter()
                try:
                    while True:
                        await asyncio.sleep(0.01)
                        gaps.append(time.perf_counter() - last)
                        last = time.perf_counter()
                except asyncio.CancelledError:
                    raise

            task = asyncio.ensure_future(watchdog())
            while len(gaps) < 2:
                await asyncio.sleep(0.01)
            before = len(gaps)
            try:
                with (
                    self._slow_login(server, 0.30, calls),
                    patch.object(server, "_get_iscsi", return_value=iscsi),
                ):
                    await server._handle_iscsi_call({"nas_name": "nas1"}, "lun_list")
            finally:
                during = len(gaps) - before
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return during

        ticks = asyncio.run(main())
        assert calls, "the lazy-login path was never taken - the test proves nothing"
        assert ticks >= 5, (
            f"the lazy login held the event loop: only {ticks} tick(s) during a "
            "300ms login"
        )

    def test_concurrent_handlers_log_in_once(self):
        """Offloading without a lock would strand a session on the NAS.

        Two handlers arriving with no session would both miss it and both log
        in, and the second would overwrite the first's SID -- leaving the first
        session open on the NAS and unreachable by logout, which is what the
        guard inside _get_base_url exists to prevent.
        """
        import time

        server = self._server()
        calls = []
        iscsi = MagicMock()
        iscsi.lun_list.return_value = {"success": True, "data": {"luns": []}}
        logged_in = []

        def fake(arguments, *, allow_login=True):
            if not allow_login:
                if logged_in:
                    return "https://nas.example"
                raise Exception("No active session")
            calls.append(1)
            time.sleep(0.20)
            logged_in.append(1)
            return "https://nas.example"

        async def main():
            with (
                patch.object(server, "_get_base_url", side_effect=fake),
                patch.object(server, "_get_iscsi", return_value=iscsi),
            ):
                await asyncio.gather(
                    server._handle_iscsi_call({"nas_name": "nas1"}, "lun_list"),
                    server._handle_iscsi_call({"nas_name": "nas1"}, "lun_list"),
                    server._handle_iscsi_call({"nas_name": "nas1"}, "lun_list"),
                )

        asyncio.run(main())
        assert len(calls) == 1, (
            f"{len(calls)} concurrent logins for one NAS; each extra one strands "
            "a session open on the NAS"
        )

    def test_different_nas_names_do_not_serialise_on_each_other(self):
        """The lock is per NAS, not global -- one slow NAS must not block another."""
        server = self._server()
        assert server._login_locks == {}
        iscsi = MagicMock()
        iscsi.lun_list.return_value = {"success": True, "data": {"luns": []}}

        def fake(arguments, *, allow_login=True):
            if not allow_login:
                raise Exception("No active session")
            return "https://nas.example"

        async def main():
            with (
                patch.object(server, "_get_base_url", side_effect=fake),
                patch.object(server, "_get_iscsi", return_value=iscsi),
            ):
                await server._handle_iscsi_call({"nas_name": "nas1"}, "lun_list")
                await server._handle_iscsi_call({"nas_name": "nas2"}, "lun_list")

        asyncio.run(main())
        assert set(server._login_locks) == {"nas1", "nas2"}


class TestEveryIscsiHandlerResolvesOffLoop:
    """No iSCSI handler may resolve its NAS synchronously.

    This exists because the edit that introduced `_resolve_base_url` was
    pattern-based: it rewrote `base_url = self._get_base_url(arguments)` only
    where `iscsi = self._get_iscsi(base_url)` was the very next line.
    `_handle_lun_get` has `name = arguments["name"]` in between, so it was
    silently skipped -- and the edit reported the seven it changed, never the
    one it missed. `synology_lun_get` therefore kept blocking the event loop on
    a lazy login, in a change whose whole subject was not doing that.

    So the scope here is DISCOVERED from the tool registry rather than listed.
    A ninth iSCSI tool added tomorrow is covered the day it is registered; a
    hardcoded list would be one more thing to remember to widen.
    """

    #: Prefixes identifying the tools this module owns. Deliberately a prefix
    #: match on the REGISTRY, not a list of handler names.
    ISCSI_TOOL_PREFIXES = ("synology_lun_", "synology_target_")

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    def _iscsi_handlers(self):
        """Source of each iSCSI handler, plus one level of delegation.

        `_handle_target_map_lun` and its unmap twin are one-line wrappers around
        `_map_luns`, which is where the resolution actually happens, so reading
        the handler alone reports them as resolving nothing. The expansion is
        deliberately ONE level: enough for the delegation that exists, and
        honest about not being a call-graph analysis. A handler that hides its
        resolution two levels down would be missed, and would deserve the
        failure this test would then not give.
        """
        import functools
        import inspect
        import re

        server = self._server()
        found = {}
        for name, (_tool, handler) in server._tool_registry.items():
            if not name.startswith(self.ISCSI_TOOL_PREFIXES):
                continue
            fn = handler
            while isinstance(fn, functools.partial):
                fn = fn.func
            src = inspect.getsource(fn)
            for called in set(re.findall(r"self\.(_[A-Za-z0-9_]+)\(", src)):
                # `_resolve_base_url` is the sanctioned wrapper: calling
                # `_get_base_url` is its whole job, and inlining it here would
                # make every handler look like an offender.
                if called == "_resolve_base_url":
                    continue
                target = getattr(type(server), called, None)
                if callable(target):
                    try:
                        src += "\n" + inspect.getsource(target)
                    except (OSError, TypeError):
                        pass
            found[name] = src
        return found

    def test_the_registry_actually_yields_the_iscsi_tools(self):
        """Guard the guard: a prefix that matches nothing would pass vacuously."""
        handlers = self._iscsi_handlers()
        assert len(handlers) >= 10, f"only found {sorted(handlers)}"
        for expected in (
            "synology_lun_list",
            "synology_lun_get",
            "synology_lun_create",
            "synology_lun_delete",
            "synology_target_list",
            "synology_target_get",
            "synology_target_create",
            "synology_target_delete",
            "synology_target_map_lun",
            "synology_target_unmap_lun",
        ):
            assert expected in handlers, f"{expected} is not registered"

    def test_no_iscsi_handler_calls_get_base_url_directly(self):
        offenders = [
            name
            for name, src in self._iscsi_handlers().items()
            if "self._get_base_url(" in src
        ]
        assert not offenders, (
            f"{offenders} resolve the NAS synchronously, so a lazy DSM login "
            "runs on the event loop. Use `await self._resolve_base_url(...)`."
        )

    def test_every_iscsi_handler_resolves_through_the_async_path(self):
        """The positive half: absence of the bad call is not presence of the good one."""
        missing = [
            name
            for name, src in self._iscsi_handlers().items()
            if "_resolve_base_url(" not in src
        ]
        assert not missing, f"{missing} never resolve a base_url at all"


class TestLoginLockIsNotAnUnboundedCache:
    """The lock key is caller-supplied, so it is not a trusted set.

    A caller naming a different nas_name on every call would otherwise grow
    `_login_locks` without bound -- and every one of those names fails to
    resolve, so nothing would ever remove them.
    """

    @staticmethod
    def _server():
        from mcp_server import SynologyMCPServer

        return SynologyMCPServer()

    def test_unresolvable_names_leave_no_lock_behind(self):
        server = self._server()

        def always_fails(arguments, *, allow_login=True):
            raise Exception("NAS 'whatever' not found")

        async def main():
            with patch.object(server, "_get_base_url", side_effect=always_fails):
                for i in range(50):
                    with pytest.raises(Exception):
                        await server._resolve_base_url({"nas_name": f"nope-{i}"})

        asyncio.run(main())
        assert server._login_locks == {}, (
            f"{len(server._login_locks)} lock(s) retained for names that never "
            "resolved; the dict grows without bound on caller-supplied keys"
        )

    def test_a_resolving_name_keeps_its_lock(self):
        """The cleanup must not throw away the lock that does the serialising."""
        server = self._server()

        def resolves(arguments, *, allow_login=True):
            if not allow_login:
                raise Exception("no session")
            return "https://nas.example"

        async def main():
            with patch.object(server, "_get_base_url", side_effect=resolves):
                await server._resolve_base_url({"nas_name": "nas1"})

        asyncio.run(main())
        assert set(server._login_locks) == {"nas1"}

    def test_cleanup_does_not_strand_a_waiting_caller(self):
        """A failing call must not delete a lock another caller is queued on.

        If it did, a third caller would build a SECOND lock for the same key and
        log in concurrently -- the exact race the lock exists to prevent.
        """
        import time

        server = self._server()
        logins = []

        def slow_then_fail(arguments, *, allow_login=True):
            if not allow_login:
                raise Exception("no session")
            logins.append(arguments.get("nas_name"))
            time.sleep(0.05)
            raise Exception("login refused")

        async def main():
            with patch.object(server, "_get_base_url", side_effect=slow_then_fail):
                results = await asyncio.gather(
                    server._resolve_base_url({"nas_name": "nas1"}),
                    server._resolve_base_url({"nas_name": "nas1"}),
                    server._resolve_base_url({"nas_name": "nas1"}),
                    return_exceptions=True,
                )
            return results

        results = asyncio.run(main())
        assert all(isinstance(r, Exception) for r in results)
        # Serialised, so the logins are sequential rather than three at once.
        assert len(logins) == 3
        # And nothing is retained once they have all finished failing.
        assert server._login_locks == {}
