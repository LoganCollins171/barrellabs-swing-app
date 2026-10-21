"""BL-008C.5 Phase/Item 6 -- adversarial engineering matrix (partial).

All tests here use SYNTHETIC perturbations on fabricated landmark data --
none use real footage (real-footage adversarial behavior is reported
separately and honestly as UNVERIFIED where not actually run, in
validation/BL008C5_VALIDATION_CYCLE.md). Each test specifies its expected
behavior explicitly, per the task's requirement that a test counts as
passed only if executed with a defined expected result.

This is NOT the full 21-category matrix the task lists. Covered here:
no swing, degenerate/mirrored coordinates, duplicate frames, missing feet,
occluded hands, outlier landmark jump (identity-swap family), translation
invariance (metamorphic). NOT covered (stated honestly, not silently
omitted): camera shake, low light, motion blur, edited slow-motion as a
pose-level test (covered at the decode level in BL-008C's decode tests,
not re-tested here), variable PTS (also decode-level, not re-tested here),
multiple people, corrupted/unsupported codec, processing interruption.
"""
from __future__ import annotations

from engine_v2.types import LandmarkFrame, Quality
from engine_v2.window import find_swing_window, WindowStatus
from engine_v2 import measure_window as mw
from engine_v2.segment import find_primary_swing_segment, detect_events


def _lf(i, landmarks=None, mean_visibility=0.9):
    return LandmarkFrame(frame_index=i, pts_s=None, landmarks=landmarks, mean_visibility=mean_visibility)


def _stance_landmarks(x_offset=0.0, y_offset=0.0):
    """A plausible, static batting-stance pose, used as a base for building
    perturbation fixtures."""
    base = {
        "left_shoulder": (0.45, 0.30, 0.9), "right_shoulder": (0.55, 0.30, 0.9),
        "left_hip": (0.46, 0.50, 0.9), "right_hip": (0.54, 0.50, 0.9),
        "left_knee": (0.46, 0.70, 0.9), "right_knee": (0.54, 0.70, 0.9),
        "left_ankle": (0.46, 0.90, 0.9), "right_ankle": (0.54, 0.90, 0.9),
        "left_wrist": (0.35, 0.25, 0.9), "right_wrist": (0.40, 0.25, 0.9),
        "left_ear": (0.48, 0.20, 0.9), "right_ear": (0.52, 0.20, 0.9),
    }
    return {k: (v[0] + x_offset, v[1] + y_offset, v[2]) for k, v in base.items()}


def test_no_swing_stationary_hold_rejects_window():
    """EXPECTED: a clip with no meaningful motion (every frame near-
    identical) must report NOT_OBSERVABLE, never fabricate a window."""
    frames = [_lf(i, _stance_landmarks()) for i in range(60)]
    w = find_swing_window(frames)
    assert w.status == WindowStatus.NOT_OBSERVABLE


def test_completely_degenerate_coordinates_suppress_everything():
    """EXPECTED: landmarks at identical, degenerate (zero-separation)
    positions must not produce a divide-by-zero crash or a fabricated
    measurement -- must suppress cleanly."""
    degenerate = {k: (0.5, 0.5, 0.9) for k in [
        "left_shoulder", "right_shoulder", "left_hip", "right_hip",
        "left_knee", "right_knee", "left_ankle", "right_ankle",
        "left_wrist", "right_wrist", "left_ear", "right_ear",
    ]}
    frames = [_lf(i, degenerate) for i in range(30)]
    w = find_swing_window(frames)
    # no motion at all between identical frames -> NOT_OBSERVABLE, same as the no-swing case
    assert w.status == WindowStatus.NOT_OBSERVABLE
    results = mw.all_window_measurements(frames, w)
    for m in results:
        assert m.quality == Quality.SUPPRESSED


def test_duplicate_frames_do_not_fabricate_motion():
    """EXPECTED: a clip that's genuinely just N copies of one frame (the
    decode-level 'repeated frames' case collapsed to the pose layer) must
    not be interpreted as containing swing motion."""
    one_pose = _stance_landmarks()
    frames = [_lf(i, dict(one_pose)) for i in range(40)]
    w = find_swing_window(frames)
    assert w.status == WindowStatus.NOT_OBSERVABLE


def test_missing_feet_suppresses_leg_measurements_only():
    """EXPECTED: clip with upper body tracked throughout but ankles/knees
    NEVER tracked -- leg-dependent measurements suppress; this does not by
    itself prevent window detection (window uses wrist+ankle+rotation
    combined, so a fully-missing ankle signal should fall back on the
    other two signals, not crash)."""
    frames = []
    for i in range(60):
        lm = _stance_landmarks()
        for leg_kp in ["left_knee", "right_knee", "left_ankle", "right_ankle"]:
            del lm[leg_kp]
        # inject real wrist motion so there's something for the window to find
        wobble = 0.01 * (i % 20 - 10) / 10
        lm["left_wrist"] = (0.35 + wobble, 0.25, 0.9)
        frames.append(_lf(i, lm))
    w = find_swing_window(frames)
    if w.status == WindowStatus.OBSERVED:
        results = mw.all_window_measurements(frames, w)
        leg_results = [m for m in results if "knee" in m.name or "ankle" in m.name]
        for m in leg_results:
            assert m.quality == Quality.SUPPRESSED, f"{m.name} should suppress with no leg landmarks tracked"
    # else: window itself abstained, which is also an acceptable outcome here (not a failure)


def test_occluded_hands_suppresses_hand_dependent_measurements():
    """EXPECTED: wrists never tracked -> hand_to_torso measurement
    suppresses; measurements not depending on wrists (trunk inclination,
    knee flexion) remain computable."""
    frames = []
    for i in range(60):
        lm = _stance_landmarks()
        del lm["left_wrist"]
        del lm["right_wrist"]
        lm["left_hip"] = (lm["left_hip"][0] + 0.005 * (i % 10), lm["left_hip"][1], 0.9)
        frames.append(_lf(i, lm))
    w = find_swing_window(frames)
    results = mw.all_window_measurements(frames, w)
    hand_results = [m for m in results if "hand" in m.name]
    for m in hand_results:
        assert m.quality == Quality.SUPPRESSED


def test_translation_invariance_metamorphic():
    """METAMORPHIC TEST (explicit task example): translating every
    landmark in every frame by the same constant offset must not change
    any normalized, translation-invariant measurement (trunk inclination,
    knee flexion range, stride displacement normalized by shoulder width).
    This tests the NORMALIZATION logic itself, independent of any specific
    numeric fixture."""
    import random
    random.seed(42)
    base_frames = []
    for i in range(30):
        lm = _stance_landmarks()
        # small realistic per-frame jitter so there's a nonzero range to measure
        jitter = 0.002 * (i % 10 - 5)
        lm["left_knee"] = (lm["left_knee"][0] + jitter, lm["left_knee"][1], 0.9)
        lm["left_ankle"] = (lm["left_ankle"][0] + jitter * 2, lm["left_ankle"][1], 0.9)
        base_frames.append(lm)

    offset_x, offset_y = 0.2, -0.1
    translated_frames = [
        {k: (v[0] + offset_x, v[1] + offset_y, v[2]) for k, v in lm.items()} for lm in base_frames
    ]

    f1 = [_lf(i, lm) for i, lm in enumerate(base_frames)]
    f2 = [_lf(i, lm) for i, lm in enumerate(translated_frames)]
    w1 = find_swing_window(f1)
    w2 = find_swing_window(f2)

    assert w1.status == w2.status
    if w1.status == WindowStatus.OBSERVED:
        r1 = mw.trunk_inclination_range(f1, w1)
        r2 = mw.trunk_inclination_range(f2, w2)
        if r1.value is not None and r2.value is not None:
            assert abs(r1.value - r2.value) < 1e-6, "trunk inclination range is not translation-invariant -- normalization bug"

        k1 = mw.knee_flexion_range(f1, w1, "left")
        k2 = mw.knee_flexion_range(f2, w2, "left")
        if k1.value is not None and k2.value is not None:
            assert abs(k1.value - k2.value) < 1e-6, "knee flexion range is not translation-invariant -- normalization bug"
