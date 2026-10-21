"""BL-008C.16 Deliverables 1-4 -- user-selection MOVEMENT validation.

BL-008C.17 Deliverable 1 update: this module's states were renamed
(STATE_SUPPORTED_SINGLE_SWING -> STATE_SINGLE_MOVEMENT_DETECTED,
STATE_MULTIPLE_SWINGS -> STATE_MULTIPLE_MOVEMENTS, old names kept as
aliases) because real-footage testing (BL-008C.16) confirmed this
module's geometric evidence cannot distinguish a swing from running or
a body turn -- it only ever established "a single, clearly-bounded
movement" or "more than one distinct movement." Whether a detected
movement is the user's intended SWING is now a separate, explicit
question asked of the user (engine_v2.user_guided_flow's confirmation
step), never inferred from this module's output.

ROOT CAUSE (Deliverable 1, full trace in
validation/BL008C16_FINAL_REPORT.md), established empirically against
kyle_tucker_swing.mp4 before any code changed:

  - A frame-by-frame printout of the combined motion signal across
    frames 595-710 shows several comparable-height local bumps
    throughout BOTH the correct swing selection (600-630) and the
    "no swing" selection (650-700) -- the signal is genuinely noisy and
    multi-modal in this clip. Naive peak-counting cannot reliably tell
    these apart, and was NOT used as the fix (tested and rejected this
    session -- see report).
  - The real, usable signal is CANDIDATE STRUCTURE: candidate 4
    ([607,619], 13 frames) is a NORMAL-SIZED candidate (this clip's live
    candidates have widths 9-97, median 29.5) fully contained inside the
    correct (600,630) selection, and sits immediately adjacent to
    candidate 5 ([621,694]) with only a 1-frame gap -- strong evidence
    they are fragments of ONE continuous event, not two. Candidate 5
    itself is anomalously OVERSIZED (74 frames, 2.5x the clip's median)
    -- a known Tucker-clip fragmentation pattern (BL-008C.11/12) where a
    swing's follow-through gets fused with later, unrelated motion
    because the burst-detector's quiet threshold never dipped low enough
    to split them.
  - The false rejection (BL-008C.15) happened because the OLD ambiguity
    check (engine_v2.approximate_selection) grouped candidates by raw
    frame-overlap only, treating candidate 4 and candidate 5 as
    automatically "distinct" (no frame overlap between them) with no
    adjacency tolerance -- fragmentation, not interval-overlap logic
    itself, was the direct cause.
  - The false acceptance happened because the OLD existence check
    ("any overlap with any live candidate") treated the (650,700)
    selection's 45-frame overlap with the back two-thirds of oversized
    candidate 5 as equally strong evidence as a selection that actually
    brackets a candidate's own start-to-end span. It is not: the
    selection never captures candidate 5's own start (621), i.e. it only
    grazes the tail of an oversized, already-suspect candidate -- no
    other mechanism (identity, landmark coverage) was involved; target
    identity and landmark coverage are both stable and 100% in this
    whole region (confirmed in the trace).

FIX (Deliverables 2/3): replaces raw-overlap grouping with a frame-
linked, temporal-adjacency + relative-size evidence rule. Deliberately
NOT a classifier (no training, no learned weights -- just this clip's
own descriptive statistics: median live-candidate width) and NOT a
re-run of the reverted BL-008C.10 translation-ratio filter (no
translation ratio is read here at all). All constants are fixed,
round numbers, stated and justified below, not tuned to make this one
case pass -- tested against the full BL-008C.16 Deliverable 6 decision
matrix before being declared safe.
"""
from __future__ import annotations

import dataclasses
import statistics

from engine_v2.types import LandmarkFrame
from engine_v2.event_candidates import EventCandidate

SCHEMA_VERSION = "user_selection_validation_v1"

# Two candidates separated by a gap no larger than this FRACTION of the
# clip's own median live-candidate width are treated as likely fragments
# of ONE continuous event rather than automatically distinct swings.
# BL-008C.16 Deliverable 6 generalization fix: an earlier, fixed-frame
# version (10 frames) correctly merged Tucker's 1-frame fragment gap but
# failed on IMG_8601.MOV, whose single real swing legitimately shows two
# sub-phase bursts (candidate 2 at [49,66], candidate 0 at [82,111]) with
# a genuine 16-frame gap between them -- a fixed absolute threshold does
# not generalize across clips with different swing durations/frame
# rates. A relative threshold does, but the margin is narrower than it
# first looks: 0.75x was tried first and rejected -- it ALSO merged
# Tucker's own two genuinely SEPARATE swings (candidate 5 ends 694,
# candidate 2 starts 713, a real 19-frame gap; 0.75*29.5=22 >= 19, a
# false merge, caught by re-running the full test suite before declaring
# this fixed). 0.5x is the value actually used: it still merges
# IMG_8601's real 16-frame sub-phase gap (0.5*32=16 >= 16) while staying
# below Tucker's real 19-frame inter-swing gap (0.5*29.5=14.75 < 19) --
# both re-verified after this correction, not assumed.
ADJACENCY_GAP_RATIO = 0.5

# Two frames of tolerance (beyond the selection's own bounds) for
# counting a candidate's peak as "inside" the selection, scaled the same
# way event_candidates.py already scales its own boundary_uncertainty_frames
# (max(5, width // 4)) -- not a new, unrelated magic number. Fixes a
# second real generalization gap: IMG_8608.mov's true swing selection
# (28-44) correctly brackets its candidate's BODY but that candidate's
# peak (48) sits 4 frames past the selection's own end, because peak
# detection on the raw signal doesn't perfectly align with a human
# annotator's visually-judged boundary.
def _peak_tolerance(selection_width: int) -> int:
    return max(5, selection_width // 4)

# A candidate wider than this many multiples of the clip's own median
# live-candidate width is treated as a lower-trust "oversized" candidate
# -- this project has repeatedly observed oversized candidates bundling
# a real swing's follow-through with unrelated later motion (Tucker's
# candidate 5 here; Bregman's wide pre-swing candidate in BL-008C.10/13).
OVERSIZE_RATIO = 2.0

# A selection must capture at least this fraction of a NORMAL-sized
# candidate's own frames to count as strong, well-supported evidence.
NORMAL_CONTAINMENT_THRESHOLD = 0.8
# An OVERSIZED candidate is trusted only if the selection captures
# nearly all of it (reduces the chance that a selection merely grazing
# an oversized candidate's tail counts as strong evidence).
OVERSIZE_CONTAINMENT_THRESHOLD = 0.9

# Second, independent evidence signal (Deliverable 3 testing found
# containment alone insufficient -- see module docstring update below):
# how close a candidate's peak frame sits to the CENTER of the user's
# selection, as a fraction of the selection's half-width (1.0 = exactly
# centered, 0.0 = exactly at an edge). A selection whose dominant motion
# is mostly past-peak tail (the false-acceptance failure mode -- the
# selection grabs the DECAY of an event, not the event itself) scores
# low here even when containment-fraction looks moderate. Threshold
# derived directly from this session's traced cases (Tucker's three real
# swings score 0.40-0.93; the confirmed non-swing selection scores 0.16)
# -- disclosed plainly as evidence-derived, not chosen blind, and
# re-validated against the full BL-008C.16 Deliverable 6 decision matrix
# before being treated as safe.
PEAK_CENTRALITY_THRESHOLD = 0.3

# BL-008C.17 Deliverable 1 rename. The old name STATE_SUPPORTED_SINGLE_SWING
# was found to be misleading: BL-008C.16 confirmed on real footage (running,
# a body turn) that this state describes ONLY "good geometric evidence of
# ONE coherent motion event" -- it is satisfied just as readily by a clean
# non-swing movement as by a real swing, and nothing in this module's
# evidence (candidate shape/size/timing) can tell those apart. Calling it
# a "supported swing" claimed a verification this module cannot make.
# Renamed to name exactly what IS established: a single, well-evidenced
# movement was detected -- no more. Whether that movement is the user's
# INTENDED swing is now Deliverable 2's separate, explicit question, asked
# of the user, never inferred from this state.
#
# Old names kept as aliases (not removed) so existing callers/tests are
# not broken by this rename -- see each alias's own comment for the exact
# mapping. New code should use the STATE_* names on the left.
STATE_SINGLE_MOVEMENT_DETECTED = "SINGLE_MOVEMENT_DETECTED"
STATE_AMBIGUOUS_MOVEMENT = "AMBIGUOUS_MOVEMENT"
STATE_MULTIPLE_MOVEMENTS = "MULTIPLE_MOVEMENTS"  # was STATE_MULTIPLE_SWINGS -- evidence of >1 distinct event, not necessarily >1 "swing"
STATE_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
STATE_INVALID_SELECTION = "INVALID_SELECTION"

# Deprecated aliases -- DO NOT use in new code, kept only so an external
# caller or test importing the old name does not break on upgrade.
STATE_SUPPORTED_SINGLE_SWING = STATE_SINGLE_MOVEMENT_DETECTED
STATE_MULTIPLE_SWINGS = STATE_MULTIPLE_MOVEMENTS


@dataclasses.dataclass
class UserSelectionValidation:
    schema_version: str
    state: str
    reason: str
    evidence: dict


def _overlap_frames(a_s, a_e, b_s, b_e) -> int:
    return max(0, min(a_e, b_e) - max(a_s, b_s) + 1)


def _group_candidates(live: list[EventCandidate], adjacency_gap_frames: float) -> list[list[EventCandidate]]:
    """Groups candidates whose gap to the previous candidate in the group
    is <= adjacency_gap_frames -- NOT raw frame-overlap, which is what
    the old approximate_selection.py used and what caused the false
    rejection."""
    ordered = sorted(live, key=lambda c: c.start_frame)
    groups: list[list[EventCandidate]] = []
    for c in ordered:
        if groups and c.start_frame - groups[-1][-1].end_frame <= adjacency_gap_frames:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def validate_user_selection(
    frames: list[LandmarkFrame], candidates: list[EventCandidate], approx_start: int, approx_end: int,
) -> UserSelectionValidation:
    """BL-008C.17 Deliverable 1: answers ONLY "does the selected interval
    contain sufficient observable evidence of a single, clearly-bounded
    MOVEMENT?" -- never "is this movement a baseball swing" (BL-008C.16
    confirmed this module's evidence cannot tell a swing from running or
    a body turn; that judgment belongs to the user, via the separate,
    explicit confirmation step in engine_v2.user_guided_flow). Also never
    question A (structural validity, handled by the caller before this is
    even invoked) or question D (per-metric eligibility, handled
    downstream in measurement_policy.py)."""
    live = [c for c in candidates if c.rejection_reason is None]
    overlapping = [c for c in live if c.start_frame <= approx_end and approx_start <= c.end_frame]

    if not overlapping:
        return UserSelectionValidation(
            schema_version=SCHEMA_VERSION, state=STATE_INSUFFICIENT_EVIDENCE,
            reason="no detected motion overlaps this selection at all -- no evidence of any clear movement here",
            evidence={"overlapping_candidate_ids": []},
        )

    widths = [c.end_frame - c.start_frame + 1 for c in live]
    median_width = statistics.median(widths) if widths else 0
    oversize_cutoff = median_width * OVERSIZE_RATIO
    adjacency_gap_frames = median_width * ADJACENCY_GAP_RATIO
    peak_tol = _peak_tolerance(approx_end - approx_start + 1)

    groups = _group_candidates(live, adjacency_gap_frames)
    # keep only groups that actually overlap the selection
    relevant_groups = [g for g in groups if any(c in overlapping for c in g)]

    def group_span(g):
        return min(c.start_frame for c in g), max(c.end_frame for c in g)

    sel_center = (approx_start + approx_end) / 2.0
    sel_half_width = max((approx_end - approx_start) / 2.0, 1e-6)

    def group_is_well_supported(g) -> tuple[bool, dict]:
        best = None
        for c in g:
            width = c.end_frame - c.start_frame + 1
            ov = _overlap_frames(approx_start, approx_end, c.start_frame, c.end_frame)
            frac = ov / width if width else 0.0
            is_oversized = width > oversize_cutoff
            containment_threshold = OVERSIZE_CONTAINMENT_THRESHOLD if is_oversized else NORMAL_CONTAINMENT_THRESHOLD
            centrality = max(0.0, 1.0 - abs(c.peak_frame - sel_center) / sel_half_width)
            # either strong containment OR a well-centered peak is
            # accepted as sufficient evidence on its own -- they capture
            # different legitimate cases (a small candidate fully
            # bracketed vs. a selection correctly centered on an
            # oversized/merged candidate's real event)
            supported = frac >= containment_threshold or centrality >= PEAK_CENTRALITY_THRESHOLD
            detail = {"candidate_id": c.candidate_id, "width": width, "containment_fraction": round(frac, 3),
                      "peak_centrality": round(centrality, 3), "oversized": is_oversized,
                      "containment_threshold_used": containment_threshold, "supported": supported}
            if best is None or (frac, centrality) > (best["containment_fraction"], best["peak_centrality"]):
                best = detail
            if supported:
                return True, detail
        return False, best

    # distinct groups whose own PEAK frame falls inside the selection --
    # the clearest sign the user's selection is centered on that group's
    # event, not just brushing an edge of it.
    groups_with_peak_in_selection = [
        g for g in relevant_groups
        if any(approx_start - peak_tol <= c.peak_frame <= approx_end + peak_tol for c in g)
    ]

    if len(groups_with_peak_in_selection) >= 2:
        spans = [group_span(g) for g in groups_with_peak_in_selection]
        # require the groups to actually be temporally distinct (not
        # merged by the adjacency grouping above, which already handles
        # near-adjacent fragments)
        return UserSelectionValidation(
            schema_version=SCHEMA_VERSION, state=STATE_MULTIPLE_MOVEMENTS,
            reason=f"selection contains {len(groups_with_peak_in_selection)} temporally distinct motion events "
                   f"(spans {spans}) -- this looks like more than one movement, not a single one",
            evidence={"group_spans": spans, "median_live_candidate_width": median_width},
        )

    if len(groups_with_peak_in_selection) == 1:
        g = groups_with_peak_in_selection[0]
        supported, detail = group_is_well_supported(g)
        if supported:
            return UserSelectionValidation(
                schema_version=SCHEMA_VERSION, state=STATE_SINGLE_MOVEMENT_DETECTED,
                reason=(f"selection is well-supported by candidate {detail['candidate_id']} "
                        f"(captures {detail['containment_fraction']*100:.0f}% of its {detail['width']}-frame "
                        f"extent; peak-centrality {detail['peak_centrality']:.2f}) -- good evidence of ONE "
                        f"coherent movement. This is NOT a determination that the movement is a baseball "
                        f"swing specifically -- running or a body turn can produce the same evidence pattern "
                        f"(confirmed on real footage, BL-008C.16) -- only that the user selected a single, "
                        f"clearly-bounded movement rather than a vague or compound one."),
                evidence={"supporting_candidate": detail, "median_live_candidate_width": median_width},
            )
        return UserSelectionValidation(
            schema_version=SCHEMA_VERSION, state=STATE_AMBIGUOUS_MOVEMENT,
            reason=(f"selection overlaps motion (candidate {detail['candidate_id']}, "
                    f"{'oversized' if detail['oversized'] else 'normal-sized'}, "
                    f"{detail['width']} frames) but only captures "
                    f"{detail['containment_fraction']*100:.0f}% of it with peak-centrality "
                    f"{detail['peak_centrality']:.2f} -- "
                    f"not strong enough evidence of a single, clearly-bounded movement here (the selection may "
                    f"be mostly follow-through/tail after the real movement rather than the movement itself)"),
            evidence={"best_candidate": detail, "median_live_candidate_width": median_width},
        )

    # overlapping candidates exist, but none of their peaks fall inside
    # the selection -- the selection only touches a candidate's quiet
    # edge/tail, the weakest form of overlap evidence.
    return UserSelectionValidation(
        schema_version=SCHEMA_VERSION, state=STATE_AMBIGUOUS_MOVEMENT,
        reason="selection overlaps detected motion only at its edge (no candidate's peak falls inside the "
               "selection) -- too weak to confirm a clear, single movement is actually present here",
        evidence={"overlapping_candidate_ids": [c.candidate_id for c in overlapping],
                  "median_live_candidate_width": median_width},
    )
