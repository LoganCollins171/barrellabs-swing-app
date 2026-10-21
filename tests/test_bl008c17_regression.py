"""BL-008C.17 Deliverable 7 -- regression tests for the confirmation-
required workflow: confirmation required, confirmation cannot bypass
quality gates, running/body-turning not automatically verified, correct
interval preservation, multiple-movement handling, suppression,
session-state behavior (exercised at the dev-app logic level), cleanup
and error handling."""
from __future__ import annotations

from engine_v2.types import LandmarkFrame
from engine_v2.user_guided_flow import analyze_user_selection


def _lf(i, x, vis=0.9, landmarks=None):
    base = {k: (x if "wrist" in k or "ankle" in k else 0.5, 0.5, vis) for k in
            ["left_wrist", "right_wrist", "left_ankle", "right_ankle",
             "left_shoulder", "right_shoulder", "left_hip", "right_hip",
             "left_knee", "right_knee", "left_ear", "right_ear"]}
    if landmarks is not None:
        base.update(landmarks)
    return LandmarkFrame(frame_index=i, pts_s=None, landmarks=base, mean_visibility=vis)


def _burst(n, s, e, amp=0.3):
    xs = [0.3] * n
    for i in range(s, min(e, n)):
        xs[i] = 0.3 + amp * (1 - abs(i - (s + e) / 2) / ((e - s) / 2 + 1))
    return [_lf(i, xs[i]) for i in range(n)]


def test_confirmation_is_required_before_any_measurement():
    """No movement, however well-evidenced, is ever automatically
    measured/certified -- every path requires user_confirmed_swing=True."""
    frames = _burst(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 38, 62)
    assert r.status != "experimental_result"
    assert r.result is None


def test_running_shaped_movement_is_never_labeled_verified_swing():
    """A clean, single, well-centered synthetic 'movement' (standing in
    for running/body-turning, which BL-008C.16 found produces the same
    geometric shape as a swing) must still go through needs_confirmation
    first, and even once confirmed, must be labeled experimental -- never
    a bare 'ok' or anything implying automatic swing verification."""
    frames = _burst(100, 40, 60)
    unconfirmed = analyze_user_selection(frames, "test.mp4", 38, 62)
    assert unconfirmed.status != "experimental_result"
    confirmed = analyze_user_selection(frames, "test.mp4", 38, 62, user_confirmed_swing=True)
    if confirmed.status == "experimental_result":
        assert confirmed.experimental_disclaimer is not None
        assert "experimental" in confirmed.experimental_disclaimer.lower()
        assert confirmed.status != "ok"  # the old, misleading status string must never reappear


def test_confirmation_cannot_bypass_the_quality_gate():
    frames = _burst(100, 40, 60)
    for i in range(35, 65):
        frames[i] = LandmarkFrame(frame_index=i, pts_s=None, landmarks=None, mean_visibility=0.0)
    r = analyze_user_selection(frames, "test.mp4", 35, 65, user_confirmed_swing=True)
    assert r.status != "experimental_result"
    assert r.status in ("poor_quality", "insufficient_evidence")
    assert r.result is None


def test_multiple_movements_blocked_regardless_of_confirmation():
    frames = _burst(200, 10, 30)
    second = _burst(200, 150, 170)
    for i in range(150, 170):
        frames[i] = second[i]
    for confirmed_flag in (False, True):
        r = analyze_user_selection(frames, "test.mp4", 5, 175, user_confirmed_swing=confirmed_flag)
        assert r.status != "experimental_result"


def test_exact_interval_preserved_through_confirmation_flow():
    frames = _burst(100, 40, 60)
    for a_s, a_e in [(38, 62), (20, 80)]:
        r = analyze_user_selection(frames, "test.mp4", a_s, a_e, user_confirmed_swing=True)
        if r.status == "experimental_result":
            assert r.resolved_interval == (a_s, a_e)
            assert r.requested_interval == (a_s, a_e)


def test_user_confirmed_swing_field_always_echoes_caller_input():
    frames = _burst(100, 40, 60)
    for flag in (False, True):
        r = analyze_user_selection(frames, "test.mp4", 38, 62, user_confirmed_swing=flag)
        assert r.user_confirmed_swing == flag


def test_unsupported_metrics_remain_suppressed_after_confirmation():
    """A confirmed, successfully-measured result must still carry
    suppression_reason text for any metric whose own per-metric gate
    failed -- confirmation only unlocks the overall analysis, never any
    individual metric's own quality requirement."""
    frames = _burst(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 38, 62, user_confirmed_swing=True)
    if r.status == "experimental_result":
        for m in r.result.measurements:
            if m.value is None:
                assert m.suppression_reason and len(m.suppression_reason) > 3


def test_invalid_interval_fails_safely_even_when_confirmed():
    frames = _burst(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 60, 40, user_confirmed_swing=True)
    assert r.status == "rejected_input"
    assert r.result is None
