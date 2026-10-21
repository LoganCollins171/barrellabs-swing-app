"""BL-008C.22 Deliverable 2 -- tests for the ONE shared post-success usage
recording function (subscription_storage.record_successful_swing_analysis),
now called from BOTH the legacy automatic pipeline and the experimental
manual-review pathway. Confirms: Pro accounts are a no-op, Free accounts
call the existing increment RPC exactly once, a failed RPC never raises
out to the caller (soft-fail, matching the pre-existing contract), and
nothing here constitutes a second/independent counter system (it's a thin
wrapper around increment_free_swing_count, verified by call-count, not a
parallel implementation)."""
from __future__ import annotations

from entitlements import FREE_PLAN_ID, SOLO_PLAN_ID
import subscription_storage as ss


def test_pro_account_never_calls_the_increment_rpc(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "increment_free_swing_count", lambda: calls.append(1) or 0)
    ss.record_successful_swing_analysis({"plan_id": SOLO_PLAN_ID})
    assert calls == [], "a Pro account must never touch the free-swing counter"


def test_free_account_calls_the_increment_rpc_exactly_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "increment_free_swing_count", lambda: calls.append(1) or 3)
    ss.record_successful_swing_analysis({"plan_id": FREE_PLAN_ID, "free_swings_used": 2})
    assert calls == [1], "a Free account's successful analysis must increment exactly once"


def test_a_failed_rpc_does_not_raise(monkeypatch):
    def _boom():
        raise RuntimeError("simulated transient DB hiccup")
    monkeypatch.setattr(ss, "increment_free_swing_count", _boom)
    # Must not raise -- a successful analysis must never surface as a
    # visible error to the user just because the usage counter hiccuped.
    ss.record_successful_swing_analysis({"plan_id": FREE_PLAN_ID, "free_swings_used": 0})


def test_rpc_returning_minus_one_does_not_raise(monkeypatch):
    monkeypatch.setattr(ss, "increment_free_swing_count", lambda: -1)
    ss.record_successful_swing_analysis({"plan_id": FREE_PLAN_ID, "free_swings_used": 0})


def test_none_plan_snapshot_is_treated_as_free_and_still_increments(monkeypatch):
    """A missing plan snapshot must fail closed toward counting usage
    (Free-tier behavior), never toward silently granting unlimited,
    uncounted analyses."""
    calls = []
    monkeypatch.setattr(ss, "increment_free_swing_count", lambda: calls.append(1) or 1)
    ss.record_successful_swing_analysis(None)
    assert calls == [1]
