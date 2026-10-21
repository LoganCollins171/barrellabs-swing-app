"""BL-008C.14/C.15/C.16/C.17 -- the backend behind the user-guided
swing-selection workflow.

A reviewer selects an approximate (start_frame, end_frame). This module
answers four SEPARATE questions, in order, never conflating them
(BL-008C.17 Deliverable 1):

  A. Is the selected interval structurally valid (ordering, bounds,
     minimum length)? -- checked first, below, independent of any
     motion evidence.
  B. Is there observable evidence of a single, clearly-bounded MOVEMENT
     in the selection? -- engine_v2.user_selection_validation. This is
     NOT "is it a swing": BL-008C.16 confirmed on real footage that this
     evidence is equally satisfied by running or a body turn.
  C. Did the user explicitly confirm the selected movement is their
     intended swing? -- a statement by the user, never inferred, never
     treated as scientific proof (Deliverable 2). Nothing is measured
     without this confirmation, even when (B) found clean evidence of a
     movement.
  D. Which specific measurements are technically eligible for THIS exact
     interval (landmark coverage, normalization stability, etc.)? --
     `_build_interval_descriptor` below + engine_v2.measurement_policy
     downstream. Confirmation (C) never bypasses this gate.

It NEVER:
  - claims automatic swing verification -- the best possible result is
    labeled "experimental_result" with a mandatory disclaimer, never a
    bare "ok" that could read as "verified";
  - invents contact timing (_CONTACT_DETECTION_VALIDATED stays False,
    this module never imports engine_v2.segment/measure);
  - applies engine_v2.boundary_refinement's suggested boundaries to the
    actual measurement interval (BL-008C.13 found that mechanism
    net-negative; surfaced only as a clearly-labeled, non-applied note).

BL-008C.15 Deliverable 1 correction (read before changing this file
again): an earlier version of this module measured over the MATCHED
CANDIDATE's own (start_frame, end_frame) -- a real substitution bug.
The interval actually measured is ALWAYS the reviewer's own
`(approx_start, approx_end)`, exactly as validated at the top of
`analyze_user_selection`.

BL-008C.17 Deliverable 2 migration note (breaking change, disclosed):
`analyze_user_selection`/`analyze_uploaded_video` used to measure
automatically whenever question B passed (status="ok"), with a separate
`reviewer_confirmed_experimental` override for weaker evidence. That
conflated "good movement evidence" with "verified swing" -- exactly what
Deliverable 1 asked to be corrected. Measurement now ALWAYS requires
explicit confirmation (`user_confirmed_swing=True`), regardless of how
strong the movement evidence is. The old `reviewer_confirmed_experimental`
kwarg is kept as a deprecated alias (ORed with `user_confirmed_swing`) so
existing callers are not broken by this rename, but new code should use
`user_confirmed_swing`. The old status strings `"ok"` and
`"experimental_reviewer_confirmed"` are both replaced by a single
`"experimental_result"` status (always disclaimed, never implying
verification); a new `"needs_confirmation"` status is returned instead
of silently measuring when evidence exists but confirmation hasn't
happened yet.
"""
from __future__ import annotations

import dataclasses
import os

import numpy as np

from engine_v2.types import LandmarkFrame
from engine_v2.event_candidates import EventCandidate, SCHEMA_VERSION as _EC_SCHEMA_VERSION, _landmark_coverage
from engine_v2.target_track import _body_centroid_and_scale
from engine_v2.event_candidates import extract_event_candidates
from engine_v2.user_selection_validation import (
    validate_user_selection,
    STATE_SINGLE_MOVEMENT_DETECTED, STATE_AMBIGUOUS_MOVEMENT, STATE_MULTIPLE_MOVEMENTS,
    STATE_INSUFFICIENT_EVIDENCE, STATE_INVALID_SELECTION,
)
from engine_v2.boundary_refinement import refine_candidate_boundaries
from engine_v2.selected_candidate_measurement import (
    compute_measurements_for_selected_candidate,
    SelectedCandidateResult,
)

SCHEMA_VERSION = "user_guided_flow_v4"

# Same floor engine_v2.event_candidates uses to reject a discovered
# candidate outright -- reused here, on the USER'S interval, as the
# single source of truth for "is this selection measurable at all".
MIN_COVERAGE_PCT = 50.0
MIN_SELECTION_FRAMES = 5  # matches event_candidates' own "too short" floor

EXPERIMENTAL_DISCLAIMER = (
    "These are experimental, contact-independent 2D movement measurements, not a validated baseball-swing "
    "analysis. You confirmed this selection contains your intended swing -- that confirmation is your own "
    "statement, not something BarrelLabs independently verified. Treat the numbers below as rough, "
    "exploratory estimates, not proof of anything about your mechanics."
)

# Question B's result never, by itself, unlocks measurement -- it only
# determines whether confirmation is even offered (MULTIPLE_MOVEMENTS is
# a hard block, since the selection structurally cannot be a single
# measurable event regardless of what the user confirms) or must be
# requested first (every other state).
_HARD_BLOCK_STATES = {STATE_MULTIPLE_MOVEMENTS}
_HARD_BLOCK_STATUS = {
    STATE_MULTIPLE_MOVEMENTS: "multiple_movements",
}


@dataclasses.dataclass
class UserGuidedAnalysisResult:
    schema_version: str
    status: str
    # "rejected_input" | "multiple_movements" | "needs_confirmation" |
    # "poor_quality" | "experimental_result"
    reason: str                       # human-readable explanation, always set when status != "experimental_result"
    requested_interval: tuple | None  # exactly what the reviewer selected
    resolved_interval: tuple | None   # the interval actually measured -- ALWAYS == requested_interval when measured at all
    validation_state: str | None      # raw state from engine_v2.user_selection_validation (question B), for programmatic callers
    validation_evidence: dict | None
    user_confirmed_swing: bool        # echoes what the caller passed for question C -- always present, never inferred
    candidate_suggestion: dict | None   # informational only -- a nearby discovered candidate's own interval, if any; never applied
    refinement_suggestion: dict | None  # informational only -- see module docstring; never applied
    experimental_disclaimer: str | None  # set only when status == "experimental_result"
    result: SelectedCandidateResult | None


def _reject(status, reason, requested_interval=None, user_confirmed_swing=False):
    return UserGuidedAnalysisResult(
        schema_version=SCHEMA_VERSION, status=status, reason=reason,
        requested_interval=requested_interval, resolved_interval=None,
        validation_state=STATE_INVALID_SELECTION if status == "rejected_input" else None,
        validation_evidence=None, user_confirmed_swing=user_confirmed_swing,
        candidate_suggestion=None, refinement_suggestion=None, experimental_disclaimer=None, result=None,
    )


def _build_interval_descriptor(frames: list[LandmarkFrame], source_file: str, s: int, e: int,
                                target_track_id: int | None) -> EventCandidate:
    """Builds an EventCandidate-shaped quality descriptor for the EXACT
    interval about to be measured -- never a discovered candidate's own
    boundaries. This is the sole authority for question D (per-interval
    measurement eligibility); nothing here can cause a boundary swap, and
    nothing upstream (including user confirmation) can bypass it."""
    coverage_pct, mean_vis = _landmark_coverage(frames, s, e)
    c0, scale0 = _body_centroid_and_scale(frames[s])
    c1, _ = _body_centroid_and_scale(frames[e])
    translation_ratio = None
    if c0 is not None and c1 is not None and scale0:
        translation_ratio = float(np.linalg.norm(c1 - c0)) / scale0

    rejection_reason = None
    if e - s < MIN_SELECTION_FRAMES:
        rejection_reason = f"selected interval is only {e - s + 1} frames -- too short to measure reliably"
    elif coverage_pct < MIN_COVERAGE_PCT:
        rejection_reason = f"core landmark coverage only {coverage_pct:.0f}% over the selected interval"

    return EventCandidate(
        schema_version=_EC_SCHEMA_VERSION, source_file=source_file, candidate_id=-1,
        start_frame=s, end_frame=e, peak_frame=(s + e) // 2, target_track_id=target_track_id,
        motion_energy=0.0,
        motion_signal_provenance="not applicable -- this descriptor represents the user's exact selection, not a discovered motion candidate",
        landmark_coverage_pct=round(coverage_pct, 1), mean_visibility=round(mean_vis, 3),
        net_translation_ratio=round(translation_ratio, 4) if translation_ratio is not None else None,
        boundary_uncertainty_frames=0,  # the user's selection is exact by definition -- nothing was padded or trimmed
        overlaps_candidate_ids=[], rejection_reason=rejection_reason,
    )


def _nearest_candidate(candidates: list[EventCandidate], validation_evidence: dict | None) -> EventCandidate | None:
    """Extracts whichever candidate the validation evidence pointed to
    (if any) purely for the informational candidate_suggestion/
    refinement_suggestion fields -- never used as a measurement source."""
    if not validation_evidence:
        return None
    detail = validation_evidence.get("supporting_candidate") or validation_evidence.get("best_candidate")
    if not detail:
        return None
    cid = detail.get("candidate_id")
    return next((c for c in candidates if c.candidate_id == cid), None)


def analyze_user_selection(
    frames: list[LandmarkFrame],
    source_file: str,
    approx_start: int,
    approx_end: int,
    selected_by: str = "human_reviewer:dev_ui",
    user_confirmed_swing: bool = False,
    reviewer_confirmed_experimental: bool = False,  # deprecated alias for user_confirmed_swing -- see module docstring
) -> UserGuidedAnalysisResult:
    """`frames` must already be decoded+pose-extracted by the caller (this
    module does no I/O itself -- see analyze_uploaded_video below for the
    file-handling wrapper that does). `source_file` is a label only, for
    provenance -- never a path this module re-reads.

    `user_confirmed_swing`: question C, Deliverable 2 -- a statement BY
    THE USER that the selected interval contains their intended swing.
    Required for measurement regardless of how strong question B's
    (automatic) movement evidence is. Never set this from inferred
    evidence; it must come from an explicit user action (a confirmation
    click/step), never a default or a heuristic."""
    confirmed = bool(user_confirmed_swing or reviewer_confirmed_experimental)
    requested = (approx_start, approx_end)

    if not isinstance(approx_start, int) or not isinstance(approx_end, int):
        return _reject("rejected_input", "start/end frame must be integers", requested, confirmed)
    if approx_start >= approx_end:
        return _reject("rejected_input",
                        f"selection start ({approx_start}) must be before end ({approx_end})", requested, confirmed)
    n = len(frames)
    if approx_start < 0 or approx_end >= n:
        return _reject("rejected_input",
                        f"selection [{approx_start},{approx_end}] is outside the clip's frame range [0,{n - 1}]",
                        requested, confirmed)
    if approx_end - approx_start < 3:
        return _reject("rejected_input",
                        f"selection is only {approx_end - approx_start + 1} frames -- too short to contain a swing",
                        requested, confirmed)

    candidates = extract_event_candidates(frames, source_file=source_file)
    validation = validate_user_selection(frames, candidates, approx_start, approx_end)

    nearest = _nearest_candidate(candidates, validation.evidence)
    candidate_suggestion = None
    if nearest is not None:
        candidate_suggestion = {
            "suggested_interval": (nearest.start_frame, nearest.end_frame),
            "note": ("automatically detected motion near your selection -- informational only, "
                     "NOT used as the measurement interval"),
        }

    refinement_suggestion = None
    if nearest is not None and nearest.rejection_reason is None:
        r = refine_candidate_boundaries(frames, nearest)
        if not r.abstained and (r.refined_start, r.refined_end) != (approx_start, approx_end):
            refinement_suggestion = {
                "suggested_interval": (r.refined_start, r.refined_end),
                "start_evidence": r.start_evidence,
                "end_evidence": r.end_evidence,
                "note": ("experimental boundary-tightening suggestion, NOT applied to the measurement below -- "
                         "BL-008C.13 found this mechanism regresses some real events; shown for information only"),
            }

    # Question B hard block: a selection with evidence of MORE THAN ONE
    # distinct movement cannot be a single measurable event no matter
    # what the user confirms -- ask for a corrected selection instead.
    if validation.state in _HARD_BLOCK_STATES:
        return UserGuidedAnalysisResult(
            schema_version=SCHEMA_VERSION, status=_HARD_BLOCK_STATUS[validation.state], reason=validation.reason,
            requested_interval=requested, resolved_interval=None,
            validation_state=validation.state, validation_evidence=validation.evidence,
            user_confirmed_swing=confirmed,
            candidate_suggestion=candidate_suggestion, refinement_suggestion=refinement_suggestion,
            experimental_disclaimer=None, result=None,
        )

    # Question C: nothing is ever measured without explicit confirmation,
    # regardless of how strong question B's evidence is (Deliverable 1/2
    # -- "do not claim automatic swing verification").
    if not confirmed:
        return UserGuidedAnalysisResult(
            schema_version=SCHEMA_VERSION, status="needs_confirmation",
            reason=("Please review the selected clip and confirm it contains your intended swing before "
                    "we analyze it. " + validation.reason),
            requested_interval=requested, resolved_interval=None,
            validation_state=validation.state, validation_evidence=validation.evidence,
            user_confirmed_swing=False,
            candidate_suggestion=candidate_suggestion, refinement_suggestion=refinement_suggestion,
            experimental_disclaimer=None, result=None,
        )

    # Question D: confirmation NEVER bypasses the per-interval quality gate.
    target_track_id = nearest.target_track_id if nearest is not None else None
    interval_descriptor = _build_interval_descriptor(frames, source_file, approx_start, approx_end, target_track_id)

    if interval_descriptor.rejection_reason is not None:
        return UserGuidedAnalysisResult(
            schema_version=SCHEMA_VERSION, status="poor_quality", reason=interval_descriptor.rejection_reason,
            requested_interval=requested, resolved_interval=None,
            validation_state=validation.state, validation_evidence=validation.evidence,
            user_confirmed_swing=True,
            candidate_suggestion=candidate_suggestion, refinement_suggestion=refinement_suggestion,
            experimental_disclaimer=None, result=None,
        )

    measurement_result = compute_measurements_for_selected_candidate(
        frames, interval_descriptor, selected_by=selected_by, target_identity="unknown",
    )

    return UserGuidedAnalysisResult(
        schema_version=SCHEMA_VERSION, status="experimental_result", reason="",
        requested_interval=requested, resolved_interval=(approx_start, approx_end),
        validation_state=validation.state, validation_evidence=validation.evidence,
        user_confirmed_swing=True,
        candidate_suggestion=candidate_suggestion, refinement_suggestion=refinement_suggestion,
        experimental_disclaimer=EXPERIMENTAL_DISCLAIMER, result=measurement_result,
    )


def decode_and_cleanup(tmp_video_path: str):
    """BL-008C.15 Deliverable 7 -- decodes+pose-extracts a (typically
    temporary) video file and GUARANTEES the file is deleted afterward,
    success or failure, since the only reason a private video would ever
    sit in a system temp directory (e.g. dev_tools' Streamlit uploader)
    is to hand a path to this decode step -- once pose landmarks are
    extracted into memory, the raw video bytes on disk serve no further
    purpose and must not linger.

    Returns (frames_or_None, fps_or_None, error_message_or_None). Never
    raises -- callers (a Streamlit app, a test) get a tuple to check.
    """
    try:
        from engine_v2.decode import decode_with_timing
        from engine_v2.pose import extract_landmarks

        try:
            raw_frames, pts, timing = decode_with_timing(tmp_video_path)
            lf, tracking = extract_landmarks(raw_frames)
            fps = timing.fps if hasattr(timing, "fps") and timing.fps else 30.0
            return lf, fps, None
        except Exception as exc:
            return None, None, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.remove(tmp_video_path)
        except OSError:
            pass


def analyze_uploaded_video(
    video_path: str, approx_start: int, approx_end: int, selected_by: str = "human_reviewer:dev_ui",
    user_confirmed_swing: bool = False,
    reviewer_confirmed_experimental: bool = False,  # deprecated alias for user_confirmed_swing
) -> UserGuidedAnalysisResult:
    """File-handling wrapper: validates the path exists and is readable,
    decodes, extracts pose, then delegates to analyze_user_selection.
    Never persists the video anywhere outside its original location --
    this function only reads it.

    BL-008C.16 Deliverable 8: error messages below report the filename
    only, never the full path -- a full local filesystem path can reveal
    directory structure (e.g. a username) and has no reason to appear in
    user-facing output."""
    confirmed = bool(user_confirmed_swing or reviewer_confirmed_experimental)
    display_name = os.path.basename(video_path) if video_path else "(no file)"
    if not video_path or not os.path.isfile(video_path):
        return _reject("rejected_input", f"video file not found or unreadable: {display_name!r}", None, confirmed)

    from engine_v2.decode import decode_with_timing
    from engine_v2.pose import extract_landmarks

    try:
        frames, pts, timing = decode_with_timing(video_path)
    except Exception as exc:  # corrupted/unsupported video -- never let this crash the caller
        return _reject("rejected_input", f"could not decode video ({type(exc).__name__}: {exc})", None, confirmed)

    if not frames:
        return _reject("rejected_input", "video decoded to zero frames", None, confirmed)

    try:
        lf, tracking = extract_landmarks(frames)
    except Exception as exc:
        return _reject("rejected_input", f"pose extraction failed ({type(exc).__name__}: {exc})", None, confirmed)

    source_file = os.path.basename(video_path)
    return analyze_user_selection(lf, source_file, approx_start, approx_end, selected_by=selected_by,
                                   user_confirmed_swing=confirmed)
