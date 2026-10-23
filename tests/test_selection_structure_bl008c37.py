"""BL-008C.37 -- the selection movement check (engine_v2.selection_structure)
and the decision policy built on it (engine_v2.user_guided_flow).

Two layers:
  * SYNTHETIC clips (tests/_synthetic_swings.py) -- always run; each
    reproduces one structural situation (one swing with several speed
    peaks, two separate swings, partial swing, walking off, tracking
    dropouts, glitches, missing timestamps, 30/54/60 fps).
  * REAL FOOTAGE -- pose landmarks prepared from the owner's private clips
    (never committed: this repo is public). They live outside the repo in
    $BL_PRIVATE_FIXTURES (default ~/barrellabs-private-fixtures/bl008c37);
    those tests skip when the files are absent. Labels are a single
    reviewer's frame-by-frame visual review (see
    validation/BL008C37_REPORT.md), flagged there for owner review.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from engine_v2.selection_structure import (
    analyze_selection_structure, DECISION_ACCEPTED, DECISION_UNCERTAIN, DECISION_MULTIPLE, DECISION_UNUSABLE,
)
from engine_v2.user_guided_flow import analyze_user_selection
from tests._synthetic_swings import make_clip, frame_at


def _check(frames, s, e, aspect=None):
    return analyze_selection_structure(frames, s, e, frame_aspect=aspect)


# ---------------------------------------------------------------- synthetic

@pytest.mark.parametrize("fps", [30, 54, 60, 29.97])
def test_one_swing_with_several_speed_peaks_is_accepted_at_any_frame_rate(fps):
    clip = make_clip(5, fps, [1.5])
    for s_t, e_t in [(0.9, 2.4), (0.0, 4.99), (1.0, 2.2)]:
        r = _check(clip, frame_at(clip, s_t), frame_at(clip, e_t))
        assert r.decision == DECISION_ACCEPTED, (fps, s_t, e_t, r.reasons, r.phases)
        assert len([p for p in r.phases if p.major]) == 1


def test_two_separate_swings_are_called_multiple_with_their_times():
    clip = make_clip(6, 30, [1.2, 3.6])
    r = _check(clip, 0, len(clip) - 1)
    assert r.decision == DECISION_MULTIPLE
    majors = [p for p in r.phases if p.major]
    assert len(majors) == 2
    assert majors[0].start_s < 1.6 < 3.4 < majors[1].start_s
    assert f"{majors[0].start_s:.2f}s" in r.summary and f"{majors[1].start_s:.2f}s" in r.summary  # WHERE each one is


def test_selection_is_respected_not_the_whole_clip():
    """Same clip as above: bracketing ONE of the two swings is accepted, so
    the check reads the selected frames, not the whole upload."""
    clip = make_clip(6, 30, [1.2, 3.6])
    assert _check(clip, frame_at(clip, 0.7), frame_at(clip, 2.2)).decision == DECISION_ACCEPTED
    assert _check(clip, frame_at(clip, 3.1), frame_at(clip, 4.6)).decision == DECISION_ACCEPTED


def test_close_second_movement_is_uncertain_never_multiple():
    """Swings starting 1.7 s apart (only ~0.5-0.8 s of stillness between
    them): too close to be sure they are separate -> warn, don't block."""
    clip = make_clip(5, 30, [1.0, 2.7])
    r = _check(clip, 0, len(clip) - 1)
    assert r.decision == DECISION_UNCERTAIN
    assert "possible_second_movement" in r.reasons


def test_known_limitation_back_to_back_swings_merge():
    """DOCUMENTED LIMITATION (report section M): two swings started within
    ~1.5 s of each other leave no pause longer than BRIDGE_GAP_S, so they
    read as ONE continuous movement. Not blocked and not flagged; the user's
    own confirmation is the safeguard. Pinned here so a change is noticed."""
    clip = make_clip(5, 30, [1.0, 2.0])
    assert _check(clip, 0, len(clip) - 1).decision == DECISION_ACCEPTED


def test_swing_then_walking_off_is_still_one_movement():
    clip = make_clip(6, 30, [1.5], walk_from=2.7)
    assert _check(clip, 0, len(clip) - 1).decision == DECISION_ACCEPTED


def test_partial_swing_is_flagged_not_blocked():
    clip = make_clip(5, 30, [1.5])
    r = _check(clip, frame_at(clip, 0.9), frame_at(clip, 1.6))   # ends mid-swing
    assert r.decision == DECISION_UNCERTAIN and "edge_cut_end" in r.reasons
    r = _check(clip, frame_at(clip, 1.62), frame_at(clip, 2.8))  # starts mid-swing
    assert r.decision == DECISION_UNCERTAIN and "edge_cut_start" in r.reasons


def test_stance_only_has_no_clear_movement_and_is_not_blocked():
    clip = make_clip(5, 30, [1.5])
    r = _check(clip, 0, frame_at(clip, 0.9))
    assert r.decision == DECISION_UNCERTAIN and "no_clear_movement" in r.reasons


def test_mostly_untracked_selection_is_unusable():
    clip = make_clip(5, 30, [1.5], drop=(0.5, 3.5))
    r = _check(clip, frame_at(clip, 0.8), frame_at(clip, 2.4))
    assert r.decision == DECISION_UNUSABLE and r.coverage < 0.5


def test_short_tracking_dropout_inside_the_swing_warns():
    clip = make_clip(5, 30, [1.5], drop=(1.55, 1.85))
    r = _check(clip, frame_at(clip, 0.9), frame_at(clip, 2.6))
    assert r.decision == DECISION_UNCERTAIN and "tracking_gap_in_movement" in r.reasons


def test_single_frame_pose_glitch_is_ignored():
    clip = make_clip(5, 30, [1.5], glitch_at=0.6)
    assert _check(clip, frame_at(clip, 0.3), frame_at(clip, 2.6)).decision == DECISION_ACCEPTED


def test_without_timestamps_a_moderate_split_is_only_uncertain():
    """Without timestamps, frame gaps are read as if the clip were 120 fps:
    ~40 frames of stillness is not enough to call two movements."""
    clip = make_clip(6, 30, [1.2, 3.6], timestamps=False)
    r = _check(clip, 0, len(clip) - 1)
    assert r.decision == DECISION_UNCERTAIN and "assumed_timing" in r.reasons


def test_check_reports_the_exact_selection_and_stays_inside_it():
    clip = make_clip(5, 30, [1.5])
    s, e = frame_at(clip, 0.9), frame_at(clip, 2.4)
    r = _check(clip, s, e)
    assert r.selection == (s, e)
    assert all(s <= p.start_frame <= p.end_frame <= e for p in r.phases)


def test_landmarks_are_never_modified():
    clip = make_clip(5, 30, [1.5], glitch_at=0.6)
    before = [dict(f.landmarks) if f.landmarks else None for f in clip]
    _check(clip, 0, len(clip) - 1)
    assert before == [dict(f.landmarks) if f.landmarks else None for f in clip]


# ------------------------------------------------------------ decision policy

def test_uncertain_selection_can_be_confirmed_and_measured():
    clip = make_clip(5, 30, [1.0, 2.7])
    r = analyze_user_selection(clip, "x", 0, len(clip) - 1)
    assert r.validation_state == "AMBIGUOUS_MOVEMENT"
    assert r.status == "needs_confirmation" and r.result is None
    r = analyze_user_selection(clip, "x", 0, len(clip) - 1, user_confirmed_swing=True)
    assert r.status == "experimental_result" and r.result is not None
    assert r.resolved_interval == (0, len(clip) - 1)


def test_high_confidence_multiple_blocks_even_when_confirmed():
    clip = make_clip(6, 30, [1.2, 3.6])
    for confirmed in (False, True):
        r = analyze_user_selection(clip, "x", 0, len(clip) - 1, user_confirmed_swing=confirmed)
        assert r.status == "multiple_movements" and r.result is None
        assert r.validation_evidence["phases"], "the block must carry where each movement is"


def test_unusable_tracking_blocks_even_when_confirmed():
    clip = make_clip(5, 30, [1.5], drop=(0.5, 3.5))
    for confirmed in (False, True):
        r = analyze_user_selection(clip, "x", frame_at(clip, 0.8), frame_at(clip, 2.4),
                                   user_confirmed_swing=confirmed)
        assert r.status == "poor_quality" and r.result is None


def test_accepted_selection_still_needs_explicit_confirmation():
    clip = make_clip(5, 30, [1.5])
    r = analyze_user_selection(clip, "x", frame_at(clip, 0.9), frame_at(clip, 2.4))
    assert r.status == "needs_confirmation" and r.validation_state == "SINGLE_MOVEMENT_DETECTED"


# ------------------------------------------------------------ real footage

FIXTURES = Path(os.environ.get("BL_PRIVATE_FIXTURES", Path.home() / "barrellabs-private-fixtures" / "bl008c37"))

# (clip, start, end, expected, note). expected: "accepted" | "not_blocked" |
# "uncertain" | "multiple". Labels: single reviewer's visual frame review.
REAL_CASES = [
    # The owner's production failure: one swing (load ~35, swing ~40-50, follow-through to 60).
    ("MarioTSwing", 6, 60, "accepted", "owner's exact production selection"),
    ("MarioTSwing", 0, 115, "not_blocked", "whole clip: swing, then walking away"),
    ("MarioTSwing", 30, 44, "uncertain", "partial: ends mid-swing"),
    ("MarioTSwing", 0, 25, "uncertain", "stance only, no swing"),
    ("MarioTSwing", 65, 115, "uncertain", "walking away only"),
    ("IMG_9005", 45, 90, "accepted", "one swing (portrait, three-quarter view)"),
    ("IMG_9005", 0, 102, "not_blocked", "whole clip, one swing"),
    ("IMG_9005", 20, 60, "uncertain", "old label; ends as the swing starts"),
    ("IMG_8605", 90, 135, "accepted", "one swing"),
    ("IMG_8605", 60, 145, "accepted", "one swing, generous padding"),
    ("IMG_8605", 140, 165, "uncertain", "walking/turning after the swing"),
    ("IMG_8608", 28, 74, "accepted", "one swing"),
    ("IMG_8608", 0, 79, "accepted", "whole clip, one swing"),
    ("IMG_8608", 28, 44, "uncertain", "old label; ends before the swing"),
    ("IMG_2271", 35, 95, "accepted", "one swing, front view, 540x960"),
    ("IMG_8436", 48, 75, "accepted", "one swing"),
    ("IMG_8436", 18, 75, "accepted", "one swing, early start (old check: 'multiple')"),
    ("IMG_8436", 48, 103, "accepted", "one swing, late end"),
    ("IMG_8601", 60, 135, "accepted", "swing + bringing the bat back down"),
    ("IMG_8601", 60, 105, "not_blocked", "one swing"),
    ("My_swing", 60, 215, "multiple", "two real swings ~3.5 s apart"),
    ("My_swing", 0, 452, "multiple", "whole multi-swing session"),
    ("My_swing", 175, 220, "accepted", "one swing from the session"),
    ("My_swing", 55, 110, "not_blocked", "one swing, then a fast bat reset"),
    ("My_swing", 120, 430, "multiple", "browser case: START lands in a bat reset's tail"),
    ("sample_swing", 24, 54, "uncertain", "54 fps; no swing in this clip (stance, bat waggle)"),
    ("sample_swing", 0, 322, "not_blocked", "54 fps; no swing in this clip"),
]


def _load_fixture(name):
    p = FIXTURES / f"{name}.json"
    if not p.exists():
        pytest.skip(f"private landmark fixture {name} not present (set BL_PRIVATE_FIXTURES)")
    from engine_v2.types import LandmarkFrame
    d = json.loads(p.read_text())
    frames = []
    for idx, pts_s, lms, vis in d["landmarks"]:
        if lms is not None:
            lms = {k: tuple(v) for k, v in lms.items()}
        frames.append(LandmarkFrame(frame_index=idx, pts_s=pts_s, landmarks=lms, mean_visibility=vis))
    w, h = d["source_size"]
    return frames, w / h


@pytest.mark.parametrize("clip,s,e,expected,note", REAL_CASES, ids=[f"{c[0]}-{c[1]}-{c[2]}" for c in REAL_CASES])
def test_real_footage_decisions(clip, s, e, expected, note):
    frames, aspect = _load_fixture(clip)
    r = _check(frames, s, e, aspect)
    if expected == "accepted":
        assert r.decision == DECISION_ACCEPTED, (note, r.reasons, r.summary)
    elif expected == "uncertain":
        assert r.decision == DECISION_UNCERTAIN, (note, r.decision, r.reasons)
    elif expected == "multiple":
        assert r.decision == DECISION_MULTIPLE, (note, r.decision, r.reasons)
    else:
        assert r.decision in (DECISION_ACCEPTED, DECISION_UNCERTAIN), (note, r.decision, r.reasons)


def test_owner_production_failure_is_fixed_end_to_end():
    """The exact reported case: MarioTSwing frames 6-60 reached a hard
    'more than one distinct movement' block. It must now reach the
    confirmation step, and measure once confirmed."""
    frames, aspect = _load_fixture("MarioTSwing")
    r = analyze_user_selection(frames, "MarioTSwing", 6, 60, frame_aspect=aspect)
    assert r.status == "needs_confirmation" and r.validation_state == "SINGLE_MOVEMENT_DETECTED"
    r = analyze_user_selection(frames, "MarioTSwing", 6, 60, frame_aspect=aspect, user_confirmed_swing=True)
    assert r.status == "experimental_result" and r.resolved_interval == (6, 60)
    # production decoded 115 frames of this clip (local decode: 116) -- same answer
    r = analyze_user_selection(frames[:115], "MarioTSwing", 6, 60, frame_aspect=aspect)
    assert r.status == "needs_confirmation"


def test_old_validator_did_reject_the_owner_case():
    """Documents the root cause on the real clip: the retired whole-clip
    candidate grouping called this one swing 'multiple movements'."""
    frames, _ = _load_fixture("MarioTSwing")
    from engine_v2.event_candidates import extract_event_candidates
    from engine_v2.user_selection_validation import validate_user_selection, STATE_MULTIPLE_MOVEMENTS
    cands = extract_event_candidates(frames, "x")
    swing_pieces = [c for c in cands if 38 <= c.peak_frame <= 50]
    assert swing_pieces and all(c.rejection_reason for c in swing_pieces), \
        "the real swing's bursts were discarded as too short"
    assert validate_user_selection(frames, cands, 6, 60).state == STATE_MULTIPLE_MOVEMENTS
