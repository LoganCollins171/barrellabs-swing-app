"""BL-008C.4 Phase 1 -- regression test for the shoulder-width normalization
defect. Diagnosed on real footage (see validation/BL008C4_ANKLE_ANOMALY.md):
single-frame shoulder-width normalization inflated distance measurements by
up to ~5x on spencer_torkelson_swing.mp4 because shoulder width varies
12-13x across a swing window (real trunk rotation, not tracking error).

This synthetic fixture reproduces the exact shape: shoulder width starts
small (foreshortened/closed stance), grows through the window (opening up),
with the ankle moving a small, fixed, independently-known amount throughout.
Hand-calculated expected value: using the FIRST frame's shoulder width
(the old, buggy behavior) would give a much larger ratio than using the
median (the fix) -- this test pins the fix, not just "a number came out."
"""
from __future__ import annotations

import math

from engine_v2.types import LandmarkFrame, Quality
from engine_v2.window import SwingWindow, WindowStatus
from engine_v2 import measure_window as mw


def _lf(i, left_shoulder, right_shoulder, left_ankle=None):
    landmarks = {"left_shoulder": left_shoulder, "right_shoulder": right_shoulder}
    if left_ankle is not None:
        landmarks["left_ankle"] = left_ankle
    return LandmarkFrame(frame_index=i, pts_s=None, landmarks=landmarks, mean_visibility=0.9)


def _observed_window(n):
    return SwingWindow(WindowStatus.OBSERVED, start_frame=0, end_frame=n - 1)


def test_ankle_displacement_uses_median_not_first_frame_shoulder_width():
    """10-frame synthetic window. Shoulder width grows linearly from 0.015
    (frame 0, reproducing the diagnosed Torkelson first-frame value) to
    0.090 across the window (reproducing the diagnosed ~0.094 max).
    Median of a linear ramp from 0.015 to 0.090 over 10 points is
    (0.015+0.090)/2 roughly -- hand-calculated below exactly from the
    actual sequence. Ankle moves a fixed, known 0.035 total (matching the
    real diagnosed Torkelson displacement) between frame 0 and frame 9.

    The OLD behavior (sw = first frame = 0.015) would give
    0.035 / 0.015 = 2.33 -- reproducing the original bug's output almost
    exactly. The FIX must NOT reproduce that number.
    """
    n = 10
    widths = [0.015 + (0.090 - 0.015) * i / (n - 1) for i in range(n)]
    frames = []
    for i in range(n):
        hw = widths[i] / 2
        ls = (0.5 - hw, 0.3, 0.9)
        rs = (0.5 + hw, 0.3, 0.9)
        ankle_x = 0.30 + (0.035 if i == n - 1 else 0.0)  # all displacement happens in the last step
        frames.append(_lf(i, ls, rs, left_ankle=(ankle_x, 0.8, 0.9)))

    window = SwingWindow(WindowStatus.OBSERVED, start_frame=0, end_frame=n - 1)
    m = mw.stride_foot_displacement(frames, window, "left")

    expected_median = sorted(widths)[n // 2 - 1 : n // 2 + 1]
    expected_median_val = sum(expected_median) / 2  # even-length median
    expected_ratio = 0.035 / expected_median_val
    old_buggy_ratio = 0.035 / widths[0]  # what the pre-fix code would have returned

    assert m.value is not None
    assert math.isclose(m.value, expected_ratio, rel_tol=0.01), (
        f"expected median-normalized ratio ~{expected_ratio:.3f}, got {m.value:.3f}"
    )
    assert not math.isclose(m.value, old_buggy_ratio, rel_tol=0.05), (
        f"measurement reproduces the OLD buggy first-frame-normalized value "
        f"({old_buggy_ratio:.3f}) -- the fix did not take effect"
    )
    # the synthetic fixture's shoulder width varies ~6x (0.015 to 0.090),
    # above the instability threshold -> must be flagged DEGRADED, not OK
    assert m.quality == Quality.DEGRADED
    assert "varied" in m.uncertainty_note


def test_normalizer_shoulder_width_median_matches_hand_calculation():
    """Directly tests _normalizer_shoulder_width's median/ratio output
    against a hand-calculated expected value from known inputs."""
    widths = [0.02, 0.04, 0.06, 0.08, 0.10]  # median = 0.06, ratio = 0.10/0.02 = 5.0
    frames = []
    for i, w in enumerate(widths):
        hw = w / 2
        frames.append(_lf(i, (0.5 - hw, 0.3, 0.9), (0.5 + hw, 0.3, 0.9)))
    median, ratio, n = mw._normalizer_shoulder_width(frames, 0, len(frames) - 1)
    assert math.isclose(median, 0.06, abs_tol=1e-6)
    assert math.isclose(ratio, 5.0, abs_tol=1e-6)
    assert n == 5


def test_identity_swap_suppresses_stride_displacement():
    """BL-008C.5 -- regression for the Judge anomaly's real root cause:
    a mediapipe left/right ankle identity swap, confirmed on real footage
    (judge_swing.mp4, frame 533: left_ankle.x held ~0.518 for 9+ frames,
    then crossed to ~0.33 over 3 frames while right_ankle.x did the mirror
    move -- the two feet's positions didn't change, the labels swapped).
    This synthetic fixture reproduces that shape: left_ankle holds steady,
    then jumps a full shoulder-width in one step (implausible real motion),
    then holds steady at the new position. Must suppress, not report a
    fabricated net displacement spanning the swap."""
    frames = []
    shoulder_width = 0.15
    for i in range(10):
        ls, rs = (0.5 - shoulder_width / 2, 0.2, 0.9), (0.5 + shoulder_width / 2, 0.2, 0.9)
        if i < 5:
            ankle_x = 0.52
        else:
            ankle_x = 0.30  # one-frame jump > shoulder_width between i=4 and i=5
        frames.append(_lf(i, ls, rs, left_ankle=(ankle_x, 0.8, 0.9)))

    window = _observed_window(10)
    m = mw.stride_foot_displacement(frames, window, "left")
    assert m.value is None
    assert m.quality == Quality.SUPPRESSED
    assert "identity swap" in m.suppression_reason


def test_gradual_real_motion_is_not_flagged_as_identity_swap():
    """Negative control: genuine stride motion spread across many small
    frame-to-frame steps (each well under the jump threshold) must NOT be
    suppressed -- confirms the swap detector doesn't false-positive on
    real, fast-but-gradual motion (verified against real footage too:
    spencer_torkelson_swing.mp4's ankle measurements were unaffected by
    this fix, see validation/BL008C5_VALIDATION_CYCLE.md)."""
    frames = []
    shoulder_width = 0.15
    for i in range(10):
        ls, rs = (0.5 - shoulder_width / 2, 0.2, 0.9), (0.5 + shoulder_width / 2, 0.2, 0.9)
        ankle_x = 0.52 - 0.02 * i  # 0.02/frame, 0.18 total over 9 steps -- well under 1.0x shoulder-width per step
        frames.append(_lf(i, ls, rs, left_ankle=(ankle_x, 0.8, 0.9)))

    window = _observed_window(10)
    m = mw.stride_foot_displacement(frames, window, "left")
    assert m.value is not None
    assert m.quality != Quality.SUPPRESSED


def test_contact_acceptance_disabled_pending_validation():
    """BL-008C.7 regression: contact-dependent acceptance must stay
    disabled (accepted=False) regardless of events being found, until a
    human deliberately re-enables _CONTACT_DETECTION_VALIDATED based on
    real frame-linked evidence. Guards against this silently flipping
    back (e.g. via an unrelated refactor) without that review happening."""
    from engine_v2 import pipeline
    assert pipeline._CONTACT_DETECTION_VALIDATED is False, (
        "contact detection was re-enabled -- this must only happen after independent "
        "frame-linked re-validation, documented in validation/BL008C7_REALWORLD_VALIDATION.md "
        "or a successor doc, not silently"
    )
