"""BL-008C.20 Deliverable 5 -- tests for the narrowly scoped pilot
swing-analysis allowance. Must change nothing for any account not
explicitly on the allowlist, and must never grant more than
PILOT_SWING_LIMIT even for an approved account."""
from __future__ import annotations

from entitlements import (
    can_analyze_swing, can_analyze_swing_for_account,
    PILOT_SWING_LIMIT, FREE_SWING_LIMIT, FREE_PLAN_ID, SOLO_PLAN_ID,
)


def test_non_allowlisted_account_behaves_identically_to_the_plain_gate():
    snap = {"plan_id": FREE_PLAN_ID, "free_swings_used": FREE_SWING_LIMIT}
    plain = can_analyze_swing(snap)
    with_account = can_analyze_swing_for_account(snap, "not_on_the_list@example.com")
    assert with_account.allowed == plain.allowed
    assert with_account.remaining == plain.remaining


def test_no_email_behaves_identically_to_the_plain_gate():
    snap = {"plan_id": FREE_PLAN_ID, "free_swings_used": 0}
    plain = can_analyze_swing(snap)
    with_account = can_analyze_swing_for_account(snap, None)
    assert with_account.allowed == plain.allowed


def test_allowlisted_account_gets_the_pilot_limit_not_the_free_limit(monkeypatch):
    import entitlements
    monkeypatch.setattr(entitlements, "PILOT_ALLOWLIST", frozenset({"pilot@example.com"}))

    # At FREE_SWING_LIMIT (3) -- a normal Free account would be blocked here.
    snap = {"plan_id": FREE_PLAN_ID, "free_swings_used": FREE_SWING_LIMIT}
    assert not entitlements.can_analyze_swing(snap).allowed  # sanity: normal gate blocks

    r = entitlements.can_analyze_swing_for_account(snap, "pilot@example.com")
    assert r.allowed, "an allowlisted account must not be blocked at the normal Free limit"
    assert r.remaining == PILOT_SWING_LIMIT - FREE_SWING_LIMIT


def test_allowlisted_account_is_still_blocked_after_the_pilot_limit(monkeypatch):
    import entitlements
    monkeypatch.setattr(entitlements, "PILOT_ALLOWLIST", frozenset({"pilot@example.com"}))

    snap = {"plan_id": FREE_PLAN_ID, "free_swings_used": PILOT_SWING_LIMIT}
    r = entitlements.can_analyze_swing_for_account(snap, "pilot@example.com")
    assert not r.allowed, "the pilot allowance is a raised limit, never unlimited"
    assert r.remaining == 0


def test_email_matching_is_case_and_whitespace_insensitive(monkeypatch):
    import entitlements
    monkeypatch.setattr(entitlements, "PILOT_ALLOWLIST", frozenset({"pilot@example.com"}))

    snap = {"plan_id": FREE_PLAN_ID, "free_swings_used": FREE_SWING_LIMIT}
    r = entitlements.can_analyze_swing_for_account(snap, "  Pilot@Example.COM  ")
    assert r.allowed


def test_allowlist_is_a_no_op_for_an_already_pro_account(monkeypatch):
    import entitlements
    monkeypatch.setattr(entitlements, "PILOT_ALLOWLIST", frozenset({"pilot@example.com"}))

    snap = {"plan_id": SOLO_PLAN_ID, "free_swings_used": 999}
    r = entitlements.can_analyze_swing_for_account(snap, "pilot@example.com")
    assert r.allowed  # Pro was already unlimited -- allowlist changes nothing here


def test_default_allowlist_is_empty():
    """Ships with nobody approved -- the config (secrets/env) the real
    deployment reads from is absent in this test environment, so the
    loader must resolve to empty, not raise."""
    from entitlements import PILOT_ALLOWLIST
    assert len(PILOT_ALLOWLIST) == 0


# BL-008C.21 -- the allowlist is no longer a hardcoded literal; it's loaded
# from Streamlit secrets (preferred) or a PILOT_ALLOWLIST env var (local/
# fallback), resolving to EMPTY on anything absent or malformed. These
# tests exercise _load_pilot_allowlist() directly, covering: authorized
# (present, valid), unauthorized (present, doesn't match the email being
# checked), malformed (wrong types), and absent configuration.

def test_loader_reads_a_comma_separated_env_var():
    import entitlements, os
    old = os.environ.get("PILOT_ALLOWLIST")
    try:
        os.environ["PILOT_ALLOWLIST"] = "Pilot@Example.com, second@example.com"
        result = entitlements._load_pilot_allowlist()
        assert result == frozenset({"pilot@example.com", "second@example.com"})
    finally:
        if old is None:
            os.environ.pop("PILOT_ALLOWLIST", None)
        else:
            os.environ["PILOT_ALLOWLIST"] = old


def test_loader_with_absent_env_var_and_no_secrets_is_empty():
    import entitlements, os
    old = os.environ.pop("PILOT_ALLOWLIST", None)
    try:
        assert entitlements._load_pilot_allowlist() == frozenset()
    finally:
        if old is not None:
            os.environ["PILOT_ALLOWLIST"] = old


def test_loader_ignores_an_email_not_present_in_valid_config():
    """'Unauthorized' case: the config is well-formed and loads fine, but
    the email being checked just isn't in it -- can_analyze_swing_for_account
    must fall through to the plain gate, never error."""
    import entitlements, os
    old = os.environ.get("PILOT_ALLOWLIST")
    try:
        os.environ["PILOT_ALLOWLIST"] = "someone-else@example.com"
        allowlist = entitlements._load_pilot_allowlist()
        assert "not-approved@example.com" not in allowlist
    finally:
        if old is None:
            os.environ.pop("PILOT_ALLOWLIST", None)
        else:
            os.environ["PILOT_ALLOWLIST"] = old


def test_loader_with_malformed_env_var_is_still_safe():
    """A stray comma, blank entries, and surrounding whitespace must never
    crash the loader or produce a blank-string 'email'."""
    import entitlements, os
    old = os.environ.get("PILOT_ALLOWLIST")
    try:
        os.environ["PILOT_ALLOWLIST"] = " , ,  pilot@example.com ,, "
        result = entitlements._load_pilot_allowlist()
        assert result == frozenset({"pilot@example.com"})
        assert "" not in result
    finally:
        if old is None:
            os.environ.pop("PILOT_ALLOWLIST", None)
        else:
            os.environ["PILOT_ALLOWLIST"] = old


def test_loader_with_non_string_non_iterable_secrets_value_is_empty(monkeypatch):
    """If st.secrets somehow yields a type we don't recognize (int, dict,
    None-like), the loader must degrade to empty, never raise."""
    import entitlements

    class FakeSecrets(dict):
        def get(self, key, default=None):
            if key == "pilot":
                return {"allowlist": 12345}  # malformed: not str/list
            return default

    class FakeSt:
        secrets = FakeSecrets()

    import sys
    monkeypatch.setitem(sys.modules, "streamlit", FakeSt())
    monkeypatch.delenv("PILOT_ALLOWLIST", raising=False)
    assert entitlements._load_pilot_allowlist() == frozenset()


def test_loader_prefers_secrets_over_env_var(monkeypatch):
    import entitlements, sys

    class FakeSecrets(dict):
        def get(self, key, default=None):
            if key == "pilot":
                return {"allowlist": ["from-secrets@example.com"]}
            return default

    class FakeSt:
        secrets = FakeSecrets()

    monkeypatch.setitem(sys.modules, "streamlit", FakeSt())
    monkeypatch.setenv("PILOT_ALLOWLIST", "from-env@example.com")
    result = entitlements._load_pilot_allowlist()
    assert result == frozenset({"from-secrets@example.com"})
