"""BL-008C.13 Section 3 -- experimental, separate boundary-refinement stage.

Explicitly separate from candidate DISCOVERY (engine_v2/event_candidates.py,
engine_v2/window.py): this module takes an already-found candidate and
tries to tighten its boundaries, or explicitly abstains. It never creates
new candidates and never changes which candidate is "the" one -- that
remains either a human/future-selector decision (BL-008C.12's
selected_candidate_measurement.py) or, for discovery itself, unchanged.

Reuses the ONE boundary-trim mechanism this project has already validated
not to regress the development set: BL-008C.9's noise-floor calibration
(a genuine "not moving" reference derived from the candidate's OWN quiet
flanking region, not a magic constant copied across clips). Applied here
to BOTH ends symmetrically, since BL-008C.9 only applied it to the end.
The start-side application is NEW and untested before this session --
tested below against the full development set, with the BL-008C.10
lesson in mind: if it regresses recall or IoU anywhere, it does not ship
silently as a win.

Does NOT attempt phase-structure segmentation (preparation/load/stride/
acceleration/follow-through/hold) as named, separately-returned phases --
investigated as a hypothesis (see validation/BL008C13_BOUNDARY_REFINEMENT.md)
and found not to have reliable frame-linked signal support beyond what
the noise-floor trim already captures, so not implemented as a separate
feature to avoid asserting structure the evidence doesn't support.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from engine_v2.types import LandmarkFrame
from engine_v2.window import _series, _rotation_rate_series, _normalize
from engine_v2.event_candidates import EventCandidate

SCHEMA_VERSION = "boundary_refinement_v1"


@dataclasses.dataclass
class RefinementResult:
    schema_version: str
    candidate_id: int
    original_interval: tuple
    refined_start: int | None
    refined_end: int | None
    start_evidence: str
    end_evidence: str
    abstained: bool
    abstain_reason: str


def _combined_signal(frames: list[LandmarkFrame]) -> np.ndarray:
    wrist = _normalize(_series(frames, ["left_wrist", "right_wrist"]))
    ankle = _normalize(_series(frames, ["left_ankle", "right_ankle"]))
    rotation = _normalize(_rotation_rate_series(frames))
    return wrist * 0.5 + ankle * 0.2 + rotation * 0.3


def refine_candidate_boundaries(frames: list[LandmarkFrame], candidate: EventCandidate) -> RefinementResult:
    """Trims each end of `candidate`'s interval back toward its own peak
    while the signal is at or below a noise floor calibrated from a
    FLANKING quiet region (outside the candidate, on that side) -- the
    same principle validated in BL-008C.9, applied to both ends here.
    Abstains (returns the original interval, abstained=True) when no
    usable flanking region exists (e.g. the candidate starts at frame 0,
    so there's no pre-candidate baseline to calibrate against)."""
    n = len(frames)
    s, e, peak = candidate.start_frame, candidate.end_frame, candidate.peak_frame
    combined = _combined_signal(frames)

    if candidate.rejection_reason is not None:
        return RefinementResult(
            schema_version=SCHEMA_VERSION, candidate_id=candidate.candidate_id,
            original_interval=(s, e), refined_start=None, refined_end=None,
            start_evidence="", end_evidence="",
            abstained=True, abstain_reason=f"candidate already rejected at discovery: {candidate.rejection_reason}",
        )

    flank_width = max(5, (e - s) // 4)
    pre_region = combined[max(0, s - flank_width):s]
    post_region = combined[e + 1:min(n, e + 1 + flank_width)]

    refined_start, start_evidence = s, "no flanking pre-region available (candidate starts at or near frame 0) -- start boundary not refined"
    if len(pre_region) >= 3:
        noise_floor = float(np.median(pre_region))
        settle = max(noise_floor * 3.0, combined[peak] * 0.05)
        new_s = s
        while new_s < peak and combined[new_s] <= settle:
            new_s += 1
        refined_start = new_s
        start_evidence = f"trimmed from {s} to {new_s} using pre-region noise floor {noise_floor:.4f} (settle threshold {settle:.4f})"

    refined_end, end_evidence = e, "no flanking post-region available (candidate ends at or near the clip's last frame) -- end boundary not refined"
    if len(post_region) >= 3:
        noise_floor = float(np.median(post_region))
        settle = max(noise_floor * 3.0, combined[peak] * 0.05)
        new_e = e
        while new_e > peak and combined[new_e] <= settle:
            new_e -= 1
        refined_end = new_e
        end_evidence = f"trimmed from {e} to {new_e} using post-region noise floor {noise_floor:.4f} (settle threshold {settle:.4f})"

    abstained = refined_start >= refined_end
    if abstained:
        refined_start, refined_end = s, e
        start_evidence = end_evidence = "refinement would have produced an empty/inverted interval -- reverted to original"

    return RefinementResult(
        schema_version=SCHEMA_VERSION, candidate_id=candidate.candidate_id,
        original_interval=(s, e), refined_start=refined_start, refined_end=refined_end,
        start_evidence=start_evidence, end_evidence=end_evidence,
        abstained=abstained, abstain_reason="" if not abstained else "inverted interval",
    )
