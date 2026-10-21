"""BL-008C.16 -- regression tests for engine_v2.user_selection_validation.

Pin the two confirmed real-footage failures directly, using the exact
candidate structure traced from kyle_tucker_swing.mp4 (real footage not
available in CI; see test_engine_v2_event_detection.py for the same
established convention of reproducing the diagnosed signal/structure
synthetically rather than skipping). Also covers the Deliverable 4
adversarial categories: static stance, running/walking, body turning,
repositioning, follow-through without the main swing, single swing, two
distinct swings.
"""
from __future__ import annotations

from engine_v2.event_candidates import EventCandidate
from engine_v2.user_selection_validation import (
    validate_user_selection,
    STATE_SUPPORTED_SINGLE_SWING, STATE_AMBIGUOUS_MOVEMENT, STATE_MULTIPLE_SWINGS,
    STATE_INSUFFICIENT_EVIDENCE,
)

SCHEMA = "event_candidates_v1"


def _cand(cid, s, e, peak, rejection_reason=None):
    return EventCandidate(
        schema_version=SCHEMA, source_file="test", candidate_id=cid,
        start_frame=s, end_frame=e, peak_frame=peak, target_track_id=0, motion_energy=0.1,
        motion_signal_provenance="test", landmark_coverage_pct=100.0, mean_visibility=0.9,
        net_translation_ratio=0.05, boundary_uncertainty_frames=5, overlaps_candidate_ids=[],
        rejection_reason=rejection_reason,
    )


# Exact real candidate structure traced from kyle_tucker_swing.mp4
# (scripts/bl008c16_trace_failures.py output, BL-008C.16 Deliverable 1).
TUCKER_CANDIDATES = [
    _cand(0, 494, 519, 500), _cand(1, 521, 529, 527), _cand(2, 713, 809, 751),
    _cand(3, 811, 873, 864), _cand(4, 607, 619, 613), _cand(5, 621, 694, 654),
    _cand(6, 357, 401, 378), _cand(7, 403, 417, 410), _cand(8, 419, 448, 425),
    _cand(9, 450, 478, 470),
]


def test_tucker_correct_swing_selection_is_no_longer_falsely_rejected():
    """BL-008C.16 Deliverable 1/4 regression: the real failure -- a
    correctly selected swing (600-630) was rejected as 'ambiguous'
    because discovery fragmented it into adjacent candidates 4 and 5."""
    r = validate_user_selection([], TUCKER_CANDIDATES, 600, 630)
    assert r.state == STATE_SUPPORTED_SINGLE_SWING


def test_tucker_non_swing_gap_is_no_longer_falsely_accepted():
    """BL-008C.16 Deliverable 1/4 regression: the real failure -- a
    selection (650-700) containing no actual swing (a gap between
    pitches) was silently accepted and measured because it grazed the
    tail of oversized candidate 5."""
    r = validate_user_selection([], TUCKER_CANDIDATES, 650, 700)
    assert r.state != STATE_SUPPORTED_SINGLE_SWING
    assert r.state == STATE_AMBIGUOUS_MOVEMENT


def test_tucker_other_two_real_swings_still_accepted():
    """Guards against a narrower fix that only patches the exact traced
    case: Tucker's other two annotated swings must also still validate,
    even though their own candidates (2, 3) are independently oversized."""
    r2 = validate_user_selection([], TUCKER_CANDIDATES, 735, 765)
    assert r2.state == STATE_SUPPORTED_SINGLE_SWING
    r3 = validate_user_selection([], TUCKER_CANDIDATES, 840, 870)
    assert r3.state == STATE_SUPPORTED_SINGLE_SWING


def test_tucker_selection_spanning_two_real_swings_is_multiple_swings():
    r = validate_user_selection([], TUCKER_CANDIDATES, 600, 765)
    assert r.state == STATE_MULTIPLE_SWINGS


def test_no_overlapping_candidate_is_insufficient_evidence():
    """Static stance / no motion at all near the selection."""
    r = validate_user_selection([], TUCKER_CANDIDATES, 200, 230)
    assert r.state == STATE_INSUFFICIENT_EVIDENCE


def test_running_walking_sustained_motion_single_wide_candidate_is_ambiguous_not_swing():
    """Running/walking produces a single, very wide, sustained-motion
    candidate (no settle) -- a selection that only grazes a slice of it,
    off-center from its peak, must not be confidently confirmed."""
    cands = [_cand(0, 0, 300, 150)]  # one huge sustained-motion candidate
    r = validate_user_selection([], cands, 250, 290)  # far from the peak, near the tail
    assert r.state == STATE_AMBIGUOUS_MOVEMENT


def test_body_turn_candidate_evaluated_same_as_any_other_candidate():
    """This module makes no swing/non-swing semantic judgment (that is
    event_candidates.movement_label's job) -- a short, well-bracketed
    body-turn candidate still validates as SUPPORTED on pure geometric
    evidence, same as any other short candidate. The point: this module
    does not and cannot know 'body turn' vs 'swing' from geometry alone
    -- documented as a known limitation, not silently hidden."""
    cands = [_cand(0, 100, 115, 107)]
    r = validate_user_selection([], cands, 95, 120)
    assert r.state == STATE_SUPPORTED_SINGLE_SWING


def test_repositioning_selection_only_touching_candidate_edge_is_ambiguous():
    """A selection that only touches the quiet edge of a candidate (no
    peak inside it) -- e.g. a reviewer accidentally selecting a
    repositioning step just before the real motion starts."""
    cands = [_cand(0, 50, 100, 75)]
    r = validate_user_selection([], cands, 95, 110)  # selection starts right at the candidate's tail
    assert r.state == STATE_AMBIGUOUS_MOVEMENT


def test_follow_through_without_main_swing_oversized_tail_only_is_ambiguous():
    """Mirrors the exact real Tucker non-swing case structurally: an
    oversized candidate (follow-through fused with later motion) where
    the selection only captures the tail, off-center from the peak."""
    cands = [_cand(0, 0, 10, 5), _cand(1, 100, 174, 110)]  # candidate 1 is 75 frames, oversized vs median
    r = validate_user_selection([], cands, 150, 174)  # grabs only the back quarter, far from peak 110
    assert r.state == STATE_AMBIGUOUS_MOVEMENT


def test_single_well_bracketed_candidate_is_supported():
    cands = [_cand(0, 40, 60, 50)]
    r = validate_user_selection([], cands, 38, 62)
    assert r.state == STATE_SUPPORTED_SINGLE_SWING


def test_two_distinct_well_separated_swings_is_multiple_swings():
    cands = [_cand(0, 40, 60, 50), _cand(1, 150, 170, 160)]
    r = validate_user_selection([], cands, 35, 175)
    assert r.state == STATE_MULTIPLE_SWINGS


def test_rejected_candidates_are_never_counted_as_evidence():
    cands = [_cand(0, 40, 60, 50, rejection_reason="too short")]
    r = validate_user_selection([], cands, 38, 62)
    assert r.state == STATE_INSUFFICIENT_EVIDENCE
