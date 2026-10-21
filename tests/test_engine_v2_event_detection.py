"""BL-008C.1 Section 10 -- regression tests for the three pilot failures.

These pin the FIXED behavior directly (not just "it runs"): the segmentation
hysteresis fix, the fixed-hand-side fix, and the ambiguity/abstention gating
that stops the pipeline from confidently asserting a wrong contact frame.

Full pilot clips aren't available in CI (private/large binaries, gitignored
under validation/private_dev/ and the legacy baseball-swing-app/ directory),
so these tests use synthetic landmark sequences that reproduce the exact
signal shapes diagnosed in validation/BL008C1_EVENT_DETECTION.md:
  - a segmentation burst with a single-frame noise dip (regression test for
    the premature-truncation bug)
  - a two-peak forward-extension signal of comparable height (regression
    test for the Torkelson/portrait-Judge ambiguity cases)
  - a hand-visibility flip mid-segment (regression test for the spurious
    extension spike that caused the first "fix" to still fail)

Run: .venv/bin/python -m pytest tests/test_engine_v2_event_detection.py -q
"""
from __future__ import annotations

from engine_v2.segment import (
    find_primary_swing_segment,
    _classify_contact,
    _choose_fixed_bat_hand_side,
    _forward_extension_series,
)
from engine_v2.types import LandmarkFrame, EventStatus
import numpy as np


def _lf(i, left_wrist=None, right_wrist=None, left_shoulder=(0.4, 0.3, 0.9),
        right_shoulder=(0.6, 0.3, 0.9)):
    landmarks = {"left_shoulder": left_shoulder, "right_shoulder": right_shoulder}
    if left_wrist is not None:
        landmarks["left_wrist"] = left_wrist
    if right_wrist is not None:
        landmarks["right_wrist"] = right_wrist
    return LandmarkFrame(frame_index=i, pts_s=None, landmarks=landmarks, mean_visibility=0.9)


def test_segmentation_survives_single_frame_noise_dip():
    """Regression for root cause #1: a lone below-threshold frame inside a
    genuinely sustained burst must not truncate the segment. Builds a speed
    series with a wide elevated plateau (frames 20-80) containing one
    single-frame dip near the peak, matching the diagnosed judge_swing-copy
    shape (dip at frame 168 inside burst ~128-183)."""
    def speed_at(j):
        if j == 65:
            return 0.002  # the lone noise dip, below the 15%-of-peak threshold
        if 20 <= j <= 80:
            return 0.006
        return 0.0002

    frames = []
    x = 0.1
    for i in range(120):
        x = min(0.99, x + speed_at(i))
        frames.append(_lf(i, right_wrist=(x, 0.5, 0.9)))

    seg = find_primary_swing_segment(frames)
    # the segment must cover most of the true 20-80 burst, not truncate at
    # the frame-65 dip the way the BL-008C version did (which would have
    # produced a segment confined to one side of the dip).
    assert seg.start_frame <= 25, f"segment start {seg.start_frame} truncated the burst (expected <= 25)"
    assert seg.end_frame >= 75, f"segment end {seg.end_frame} truncated the burst (expected >= 75)"


def test_fixed_hand_side_does_not_flip_mid_segment():
    """Regression for root cause #3 (BL-008C.1 iteration 2): per-frame
    'most visible wrist' selection caused a spurious mid-segment hand flip
    that fabricated a fake extension spike. Builds a segment where the
    right wrist is consistently more visible for 8 of 10 frames, with the
    left wrist spiking to higher visibility for 2 frames near the end
    (reproducing the diagnosed judge_swing-copy flip at frame 173)."""
    frames = []
    for i in range(10):
        if i in (8, 9):
            # left wrist "wins" visibility briefly, far from the shoulder --
            # this must NOT be allowed to hijack the extension series.
            frames.append(_lf(i, left_wrist=(0.05, 0.5, 0.95), right_wrist=(0.62, 0.5, 0.3)))
        else:
            frames.append(_lf(i, left_wrist=(0.45, 0.5, 0.3), right_wrist=(0.65, 0.5, 0.9)))

    side = _choose_fixed_bat_hand_side(frames, 0, 9)
    assert side == "right", "majority-vote hand selection should pick the consistently-tracked side"

    ext = _forward_extension_series(frames, 0, 9)
    # with the fix, every frame uses the right wrist -> extension should be
    # roughly constant (~0.15), not spike to ~0.45 on frames 8-9 the way the
    # per-frame-flip version did.
    assert np.nanmax(ext[:8]) < 0.25
    assert ext[8] < 0.25 and ext[9] < 0.25, "extension series spiked on the hand-flip frames -- regression"


def test_contact_abstains_when_two_peaks_are_comparable():
    """Regression for the Torkelson and portrait-Judge AMBIGUOUS cases:
    when multiple local maxima are within _AMBIGUITY_RATIO of each other,
    the detector must abstain (status=AMBIGUOUS, no asserted frame) rather
    than silently pick the taller one and risk confidently returning the
    wrong event -- this is the exact failure mode that produced the
    Torkelson regression in BL-008C.1 iteration 2."""
    ext = np.array([0.05, 0.1, 0.3, 0.1, 0.05, 0.05, 0.05, 0.1, 0.29, 0.1, 0.05])  # peaks at idx 2 and 8, separated >= _PEAK_MIN_SEPARATION
    cand = _classify_contact(ext, s=100)
    assert cand.status == EventStatus.AMBIGUOUS
    assert cand.best_estimate_frame is None, "must not assert a specific frame when genuinely ambiguous"
    assert 100 in [] or True  # candidate_frames populated, sanity no-op to keep this explicit
    assert len(cand.candidate_frames) >= 2


def test_contact_not_observable_with_no_landmarks():
    """Negative control: a clip with zero usable extension samples must
    report NOT_OBSERVABLE, never a fabricated frame."""
    ext = np.full(20, np.nan)
    cand = _classify_contact(ext, s=0)
    assert cand.status == EventStatus.NOT_OBSERVABLE
    assert cand.best_estimate_frame is None


def test_single_dominant_peak_is_estimated_not_claimed_certain():
    """A single clearly-dominant peak is reported as ESTIMATED_INTERVAL,
    not OBSERVED -- this engine has not independently validated the
    forward-extension signal's accuracy even when it's unambiguous (the
    Judge-copy clip had a single dominant peak at frame 157 that was STILL
    wrong on manual review -- see validation/BL008C1_EVENT_DETECTION.md).
    Claiming OBSERVED there would overstate confidence this engine hasn't
    earned yet."""
    ext = np.array([0.05, 0.08, 0.35, 0.09, 0.05])
    cand = _classify_contact(ext, s=50)
    assert cand.status == EventStatus.ESTIMATED_INTERVAL
    assert cand.best_estimate_frame == 52
    assert cand.earliest_plausible_frame <= 52 <= cand.latest_plausible_frame
