"""BL-008C.3 Phase B -- independent mathematical fixtures for the
contact-independent measurements. Expected values are hand-calculated from
the synthetic landmark geometry, not derived from the implementation under
test (the task's explicit "independently calculated" requirement).

Run: .venv/bin/python -m pytest tests/test_engine_v2_window_measurements.py -q
"""
from __future__ import annotations

import math

from engine_v2.types import LandmarkFrame, Quality
from engine_v2.window import SwingWindow, WindowStatus
from engine_v2 import measure_window as mw


def _lf(i, **landmarks):
    full = {k: v for k, v in landmarks.items()}
    return LandmarkFrame(frame_index=i, pts_s=None, landmarks=full, mean_visibility=0.9)


def _observed_window(n):
    return SwingWindow(WindowStatus.OBSERVED, start_frame=0, end_frame=n - 1,
                        initial_movement_end=0, primary_motion_end=n - 1)


def test_trunk_inclination_range_hand_calculated():
    """Frame 0: perfectly upright (shoulders directly above hips) -> 0deg.
    Frame 1: shoulders shifted so the trunk leans at exactly 45deg from
    vertical (dx == dy). Hand-calculated expected range: 45.0 - 0.0 = 45.0."""
    frames = [
        _lf(0, left_shoulder=(0.45, 0.2, 0.9), right_shoulder=(0.55, 0.2, 0.9),
            left_hip=(0.45, 0.5, 0.9), right_hip=(0.55, 0.5, 0.9)),
        # shoulder midpoint (0.5,0.2) -> hip midpoint (0.5,0.5): dx=0, dy=0.3 => 0 deg (vertical)
        _lf(1, left_shoulder=(0.75, 0.2, 0.9), right_shoulder=(0.85, 0.2, 0.9),
            left_hip=(0.45, 0.5, 0.9), right_hip=(0.55, 0.5, 0.9)),
        # shoulder midpoint (0.8,0.2) -> hip midpoint (0.5,0.5): dx=0.3, dy=0.3 => 45 deg from vertical
    ]
    window = _observed_window(2)
    m = mw.trunk_inclination_range(frames, window)
    assert m.quality == Quality.OK
    assert math.isclose(m.value, 45.0, abs_tol=0.5), f"expected ~45.0 deg range, got {m.value}"


def test_knee_flexion_range_hand_calculated():
    """Frame 0: fully straight leg (hip-knee-ankle collinear) -> 180deg.
    Frame 1: right angle at the knee -> 90deg. Hand-calculated expected
    range: 180 - 90 = 90.0."""
    frames = [
        _lf(0, left_hip=(0.5, 0.3, 0.9), left_knee=(0.5, 0.6, 0.9), left_ankle=(0.5, 0.9, 0.9)),
        # straight vertical line -> 180 deg
        _lf(1, left_hip=(0.5, 0.3, 0.9), left_knee=(0.5, 0.6, 0.9), left_ankle=(0.8, 0.6, 0.9)),
        # hip directly above knee, ankle directly to the side -> 90 deg
    ]
    window = _observed_window(2)
    m = mw.knee_flexion_range(frames, window, "left")
    assert m.quality == Quality.OK
    assert math.isclose(m.value, 90.0, abs_tol=0.5), f"expected 90.0 deg range, got {m.value}"


def test_stride_foot_displacement_hand_calculated():
    """Ankle moves from (0.3, 0.8) to (0.3, 0.5): a 0.3 total displacement
    in normalized units, spread over 6 small, realistic per-frame steps
    (0.05 each -- well under the BL-008C.5 identity-swap jump threshold of
    1.0x shoulder-width, so this tests the displacement arithmetic without
    tripping the (correct) swap detector). Shoulder width is exactly 0.2
    (0.4 to 0.6) throughout. Hand-calculated expected: 0.3 / 0.2 = 1.5
    shoulder-widths."""
    ys = [0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5]
    frames = [
        _lf(i, left_shoulder=(0.4, 0.2, 0.9), right_shoulder=(0.6, 0.2, 0.9), left_ankle=(0.3, y, 0.9))
        for i, y in enumerate(ys)
    ]
    window = _observed_window(len(frames))
    m = mw.stride_foot_displacement(frames, window, "left")
    assert m.quality == Quality.OK
    assert math.isclose(m.value, 1.5, abs_tol=0.01), f"expected 1.5 shoulder-widths, got {m.value}"


def test_measurements_suppressed_when_window_not_observed():
    """Negative control: every Phase-B measurement must suppress, with a
    stated reason, when the window itself wasn't observed -- never compute
    from an unreliable/rejected window."""
    rejected = SwingWindow(WindowStatus.AMBIGUOUS, rejection_reason="2 comparable bursts found")
    frames = [_lf(0), _lf(1)]
    results = mw.all_window_measurements(frames, rejected)
    assert len(results) >= 5
    for m in results:
        assert m.quality == Quality.SUPPRESSED, f"{m.name} was not suppressed for a rejected window"
        assert m.suppression_reason and "2 comparable bursts" in m.suppression_reason


def test_knee_flexion_suppressed_with_insufficient_coverage():
    """Negative control: if the knee landmark is missing in most of the
    window, the measurement must suppress rather than compute from a
    tiny, unrepresentative sample."""
    frames = [_lf(i) for i in range(10)]  # no landmarks at all
    window = _observed_window(10)
    m = mw.knee_flexion_range(frames, window, "right")
    assert m.quality == Quality.SUPPRESSED
    assert "tracked in only 0%" in m.suppression_reason


def test_recursive_dip_split_separates_two_real_bursts():
    """BL-008C.8 regression: reproduces the IMG_8608.mov failure shape --
    a combined-motion signal with a small early glitch dip, then the real
    swing, then a genuine separating trough, then a second unrelated
    high-energy region (e.g. the subject turning around). A single-pass
    dip split picks the DEEPEST dip (the small early glitch) and leaves
    the real swing merged with the later unrelated motion; the fix must
    recurse so the real separating trough is also found."""
    from engine_v2.window import _find_bursts
    import numpy as np
    n = 80
    sig = np.full(n, 0.02)
    sig[35:38] = 0.01          # small early glitch dip (deepest single point)
    sig[39:68] = 0.45          # real swing region
    sig[68] = 0.02             # genuine separating trough
    sig[69:80] = 0.55          # later, unrelated high-energy region
    bursts = _find_bursts(sig)
    # the real swing region (around frame 48-55) and the later region
    # (around frame 73) must end up as SEPARATE bursts, not merged into one
    spans = [(s, e) for s, e, p, v in bursts]
    swing_region_covered_alone = any(s <= 50 <= e and e < 69 for s, e in spans)
    later_region_covered_alone = any(s > 68 and s <= 75 <= e for s, e in spans)
    assert swing_region_covered_alone, f"swing region not isolated as its own burst: {spans}"
    assert later_region_covered_alone, f"later region not isolated as its own burst: {spans}"


def test_end_boundary_trims_noise_floor_jitter():
    """BL-008C.9 regression: reproduces the IMG_9005.MOV end-overrun shape
    -- a quiet pre-swing baseline, a real burst, then low-amplitude jitter
    (above the burst's own 15% relative threshold but near the clip's own
    noise floor) extending to the end of the clip. The end boundary must
    trim back toward the real burst, not extend through the jitter tail."""
    from engine_v2.window import find_swing_window
    from engine_v2.types import LandmarkFrame
    import numpy as np

    def lf(i, x):
        # single wrist landmark moving in x -- enough to drive the wrist signal
        return LandmarkFrame(frame_index=i, pts_s=None,
                              landmarks={"left_wrist": (x, 0.5, 0.9), "right_wrist": (x, 0.5, 0.9)},
                              mean_visibility=0.9)

    n = 80
    xs = [0.3] * 10          # quiet pre-swing baseline
    xs += [0.3 + 0.02 * i for i in range(20)]  # real swing burst (steady motion)
    xs += [0.3 + 0.4 + 0.0015 * ((-1) ** i) for i in range(50)]  # settled hold + tiny alternating jitter
    frames = [lf(i, xs[i]) for i in range(n)]

    w = find_swing_window(frames)
    assert w.status == "observed"
    # the real burst is roughly frames 10-29; the window end must not run
    # all the way out to frame ~79 through the jitter tail
    assert w.end_frame < 50, f"window end {w.end_frame} ran through the jitter tail instead of trimming to the real burst"
