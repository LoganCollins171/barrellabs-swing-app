"""BL-008C.13 Section 7 -- approximate user-selected-swing API.

A reviewer (human today, possibly some future UI) points at roughly where
a swing happened -- an approximate frame interval, not a precise one.
This module NEVER treats that approximate interval as exact ground
truth. It:

  1. Finds which already-discovered candidate(s) (engine_v2.event_candidates)
     overlap the approximate selection.
  2. If exactly one live candidate overlaps clearly, hands it to
     engine_v2.boundary_refinement for evidence-based tightening and
     returns the refined result, carrying the candidate's own quality
     flags through untouched.
  3. Abstains, with a stated reason, when the selection is ambiguous
     (no candidate overlaps, more than one DISTINCT -- non-overlapping
     with each other -- candidate overlaps roughly equally, or the
     matched candidate's landmark coverage is already below the
     discovery-stage's own 50% rejection threshold from
     engine_v2.event_candidates).

Does not invent a new coverage threshold: reuses the same 50.0% figure
event_candidates.py already uses to reject a candidate outright, so a
candidate that would have been rejected at discovery is never silently
accepted here just because a reviewer pointed near it.
"""
from __future__ import annotations

import dataclasses

from engine_v2.types import LandmarkFrame
from engine_v2.event_candidates import EventCandidate
from engine_v2.boundary_refinement import refine_candidate_boundaries, RefinementResult

MIN_COVERAGE_PCT = 50.0  # same threshold engine_v2.event_candidates uses to reject at discovery


@dataclasses.dataclass
class ApproximateSelectionResult:
    schema_version: str
    approx_interval: tuple
    matched_candidate_id: int | None
    refinement: RefinementResult | None
    abstained: bool
    abstain_reason: str
    overlapping_candidate_ids: list


def _overlap_frac(a_s, a_e, b_s, b_e) -> float:
    """Fraction of the SHORTER interval's length that the two intervals share."""
    inter = max(0, min(a_e, b_e) - max(a_s, b_s) + 1)
    shorter = min(a_e - a_s + 1, b_e - b_s + 1)
    return inter / shorter if shorter else 0.0


def refine_approximate_selection(
    frames: list[LandmarkFrame],
    candidates: list[EventCandidate],
    approx_start: int,
    approx_end: int,
) -> ApproximateSelectionResult:
    live = [c for c in candidates if c.rejection_reason is None]
    overlapping = [c for c in live if c.start_frame <= approx_end and approx_start <= c.end_frame]

    if not overlapping:
        return ApproximateSelectionResult(
            schema_version="approximate_selection_v1", approx_interval=(approx_start, approx_end),
            matched_candidate_id=None, refinement=None, abstained=True,
            abstain_reason="no live candidate overlaps the approximate selection -- no swing found near this interval",
            overlapping_candidate_ids=[],
        )

    # Distinct, non-overlapping-with-each-other candidates inside the
    # selection means the reviewer's interval likely spans more than one
    # swing (or a swing plus unrelated motion) -- that is for a human to
    # resolve, not for this module to guess at.
    overlapping_sorted = sorted(overlapping, key=lambda c: c.start_frame)
    distinct_groups = []
    for c in overlapping_sorted:
        if distinct_groups and c.start_frame <= distinct_groups[-1][-1].end_frame:
            distinct_groups[-1].append(c)
        else:
            distinct_groups.append([c])

    if len(distinct_groups) > 1:
        return ApproximateSelectionResult(
            schema_version="approximate_selection_v1", approx_interval=(approx_start, approx_end),
            matched_candidate_id=None, refinement=None, abstained=True,
            abstain_reason=(
                f"selection overlaps {len(distinct_groups)} distinct, non-overlapping candidate groups "
                f"({[g[0].candidate_id for g in distinct_groups]}) -- likely spans more than one swing"
            ),
            overlapping_candidate_ids=[c.candidate_id for c in overlapping],
        )

    # One distinct group -- pick the group member with the greatest
    # overlap fraction against the approximate selection as the match.
    group = distinct_groups[0]
    best = max(group, key=lambda c: _overlap_frac(c.start_frame, c.end_frame, approx_start, approx_end))

    if best.landmark_coverage_pct < MIN_COVERAGE_PCT:
        return ApproximateSelectionResult(
            schema_version="approximate_selection_v1", approx_interval=(approx_start, approx_end),
            matched_candidate_id=best.candidate_id, refinement=None, abstained=True,
            abstain_reason=(
                f"matched candidate {best.candidate_id}'s landmark coverage is {best.landmark_coverage_pct:.0f}%, "
                f"below the {MIN_COVERAGE_PCT:.0f}% threshold -- not reliable enough to refine or measure"
            ),
            overlapping_candidate_ids=[c.candidate_id for c in overlapping],
        )

    refinement = refine_candidate_boundaries(frames, best)
    return ApproximateSelectionResult(
        schema_version="approximate_selection_v1", approx_interval=(approx_start, approx_end),
        matched_candidate_id=best.candidate_id, refinement=refinement,
        abstained=False, abstain_reason="",
        overlapping_candidate_ids=[c.candidate_id for c in overlapping],
    )
