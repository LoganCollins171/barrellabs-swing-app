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
    """Ships with nobody approved -- adding an email is the owner's own,
    explicit, committed-code action, never a default-on bypass."""
    from entitlements import PILOT_ALLOWLIST
    assert len(PILOT_ALLOWLIST) == 0
