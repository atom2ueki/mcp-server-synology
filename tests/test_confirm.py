"""Offline tests for confirmation tokens (argument-bound, expiring, single-use)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import confirm as confirm_module  # noqa: E402
from confirm import (  # noqa: E402
    ConfirmationError,
    ConfirmationTokens,
    canonical_args,
    load_secret,
)

SECRET = b"0" * 32


def tokens(ttl=300) -> ConfirmationTokens:
    return ConfirmationTokens(SECRET, ttl_seconds=ttl)


class TestBinding:
    def test_round_trip(self):
        t = tokens()
        token = t.issue("delete", {"path": "/videos/x"})
        assert t.verify(token, "delete", {"path": "/videos/x"}) > time.time()

    def test_arguments_are_part_of_the_signature(self):
        t = tokens()
        token = t.issue("delete", {"path": "/videos/x"})
        with pytest.raises(ConfirmationError, match="does not match"):
            t.verify(token, "delete", {"path": "/videos/y"})

    def test_tool_is_part_of_the_signature(self):
        t = tokens()
        token = t.issue("delete", {"path": "/videos/x"})
        with pytest.raises(ConfirmationError, match="does not match"):
            t.verify(token, "synology_container_delete", {"path": "/videos/x"})

    def test_argument_order_is_irrelevant(self):
        assert canonical_args({"b": 2, "a": 1}) == canonical_args({"a": 1, "b": 2})


class TestSingleUse:
    def test_second_consume_fails(self):
        t = tokens()
        token = t.issue("delete", {"path": "/videos/x"})
        t.consume(token, "delete", {"path": "/videos/x"})
        with pytest.raises(ConfirmationError, match="already been used"):
            t.consume(token, "delete", {"path": "/videos/x"})

    def test_verify_does_not_burn(self):
        t = tokens()
        token = t.issue("delete", {"path": "/videos/x"})
        t.verify(token, "delete", {"path": "/videos/x"})
        assert t.verify(token, "delete", {"path": "/videos/x"})


class TestExpiry:
    def test_expired_token_is_refused(self, monkeypatch):
        t = tokens(ttl=10)
        now = time.time()
        monkeypatch.setattr(confirm_module.time, "time", lambda: now)
        token = t.issue("delete", {"path": "/videos/x"})
        monkeypatch.setattr(confirm_module.time, "time", lambda: now + 11)
        with pytest.raises(ConfirmationError, match="expired"):
            t.verify(token, "delete", {"path": "/videos/x"})

    def test_prune_drops_expired_entries(self, monkeypatch):
        t = tokens(ttl=10)
        now = time.time()
        monkeypatch.setattr(confirm_module.time, "time", lambda: now)
        token = t.issue("delete", {"path": "/videos/x"})
        t.consume(token, "delete", {"path": "/videos/x"})
        monkeypatch.setattr(confirm_module.time, "time", lambda: now + 11)
        t.issue("delete", {"path": "/videos/y"})  # issue prunes
        assert t._used == {}


class TestMalformed:
    @pytest.mark.parametrize("bad", ["", "nope", "v2.1.abc", "v1.soon.abc", "v1.1"])
    def test_rejects_garbage(self, bad):
        with pytest.raises(ConfirmationError):
            tokens().verify(bad, "delete", {})


class TestState:
    def test_state_round_trip(self):
        tok = tokens().issue("delete", {"path": "/videos/x"})
        state = ConfirmationTokens.encode_state(tok)
        assert state != tok
        assert ConfirmationTokens.decode_state(state) == tok

    def test_malformed_state(self):
        with pytest.raises(ConfirmationError):
            ConfirmationTokens.decode_state("!!!")


class TestSecret:
    def test_explicit_secret_wins(self, tmp_path):
        assert load_secret("abc", tmp_path / "s") == b"abc"

    def test_persisted_secret_is_reused(self, tmp_path):
        path = tmp_path / "secret"
        first = load_secret("", path)
        second = load_secret("", path)
        assert first == second
        assert path.stat().st_mode & 0o777 == 0o600


class TestReplayBoundaries:
    def test_reissued_approval_has_a_fresh_nonce(self):
        t = tokens()
        args = {"path": "/videos/x"}
        first = t.issue("delete", args)
        t.consume(first, "delete", args)
        second = t.issue("delete", args)
        assert second != first
        t.consume(second, "delete", args)

    def test_restart_invalidates_old_state_even_with_persisted_secret(self):
        first = tokens()
        state = first.issue("delete", {"path": "/videos/x"})
        restarted = tokens()
        with pytest.raises(ConfirmationError, match="does not match"):
            restarted.consume(state, "delete", {"path": "/videos/x"})

    def test_concurrent_consumption_runs_once(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        t = tokens()
        args = {"path": "/videos/x"}
        token = t.issue("delete", args)
        ready = threading.Barrier(12)
        def attempt(_):
            ready.wait()
            try:
                t.consume(token, "delete", args)
                return True
            except ConfirmationError:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            assert sum(pool.map(attempt, range(12))) == 1
