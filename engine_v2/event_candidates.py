"""BL-008C.11 Deliverable 1 -- multi-event candidate extraction.

Replaces the single-winner-picking architecture of engine_v2/window.py
(which BL-008C.9/C.10 found fails independent evaluation and whose one
attempted fix -- a translation threshold -- regressed the dev set and was
reverted) with an API that returns EVERY materially distinct motion
interval, fully described, and makes NO claim about which one (if any) is
a baseball swing. That classification is a separate decision (Deliverable
3/4 of this milestone), explicitly NOT made by this module.

This is additive and experimental (BL-008C.11 Deliverable 8): nothing in
engine_v2/pipeline.py's existing accepted/window_accepted behavior is
changed by this module's existence. Contact-dependent measurements remain
governed by _CONTACT_DETECTION_VALIDATED = False, untouched.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from engine_v2.types import LandmarkFrame
from engine_v2.window import _series, _rotation_rate_series, _normalize, _find_bursts
from engine_v2.target_track import segment_target_tracks, _body_centroid_and_scale

SCHEMA_VERSION = "event_candidates_v1"


@dataclasses.dataclass
class EventCandidate:
    schema_version: str
    source_file: str
    candidate_id: int
    start_frame: int
    end_frame: int
    peak_frame: int
    target_track_id: int | None
    motion_energy: float
    motion_signal_provenance: str          # which signals contributed, e.g. "wrist+ankle+rotation combined"
    landmark_coverage_pct: float           # % of frames in [start,end] with usable core landmarks
    mean_visibility: float
    net_translation_ratio: float | None    # body-centroid displacement / body-scale over the interval
    boundary_uncertainty_frames: int       # +/- frames, from the padding/trim logic -- NOT a claim of precision
    overlaps_candidate_ids: list           # other candidate_ids whose frame range overlaps this one
    rejection_reason: str | None           # None if this is a live candidate; set if auto-rejected (e.g. too short)
    movement_label: str = "unclassified"   # filled in by Deliverable 4's classifier/filter, not by this module
    movement_label_basis: str = ""         # human-readable reason for the label, or "" if unclassified


def _landmark_coverage(frames: list[LandmarkFrame], s: int, e: int) -> tuple[float, float]:
    core = ["left_shoulder", "right_shoulder", "left_hip", "right_hip"]
    total = e - s + 1
    covered = 0
    vis_vals = []
    for i in range(s, e + 1):
        f = frames[i]
        if f.landmarks and all(f.landmarks.get(c) for c in core):
            covered += 1
        if f.landmarks is not None:
            vis_vals.append(f.mean_visibility)
    coverage_pct = covered / total * 100.0 if total else 0.0
    mean_vis = float(np.mean(vis_vals)) if vis_vals else 0.0
    return coverage_pct, mean_vis


def _track_id_for_frame(tracks, frame_idx: int) -> int | None:
    for t in tracks:
        if t.start_frame <= frame_idx <= t.end_frame:
            return t.track_id
    return None


def extract_event_candidates(frames: list[LandmarkFrame], source_file: str = "") -> list[EventCandidate]:
    """Returns EVERY candidate motion interval found, each fully described,
    making no swing/non-swing judgment. Zero candidates (NOT_OBSERVABLE-
    equivalent), one, or many are all valid, expected outcomes."""
    wrist = _normalize(_series(frames, ["left_wrist", "right_wrist"]))
    ankle = _normalize(_series(frames, ["left_ankle", "right_ankle"]))
    rotation = _normalize(_rotation_rate_series(frames))
    combined = wrist * 0.5 + ankle * 0.2 + rotation * 0.3

    raw_bursts = _find_bursts(combined)  # reuses the validated (BL-008C.1/C.8) segmentation, NOT its winner-picking
    tracks = segment_target_tracks(frames)

    candidates: list[EventCandidate] = []
    for idx, (s, e, peak, val) in enumerate(raw_bursts):
        coverage_pct, mean_vis = _landmark_coverage(frames, s, e)
        c0, scale0 = _body_centroid_and_scale(frames[s])
        c1, _ = _body_centroid_and_scale(frames[e])
        translation_ratio = None
        if c0 is not None and c1 is not None and scale0:
            translation_ratio = float(np.linalg.norm(c1 - c0)) / scale0

        rejection_reason = None
        if e - s < 5:
            rejection_reason = "candidate interval shorter than 5 frames -- likely noise, not a distinct event"
        elif coverage_pct < 50.0:
            rejection_reason = f"core landmark coverage only {coverage_pct:.0f}% over this interval"

        candidates.append(EventCandidate(
            schema_version=SCHEMA_VERSION,
            source_file=source_file,
            candidate_id=idx,
            start_frame=s, end_frame=e, peak_frame=peak,
            target_track_id=_track_id_for_frame(tracks, peak),
            motion_energy=round(float(val), 4),
            motion_signal_provenance="wrist(0.5)+ankle(0.2)+rotation(0.3), normalized per-signal then combined",
            landmark_coverage_pct=round(coverage_pct, 1),
            mean_visibility=round(mean_vis, 3),
            net_translation_ratio=round(translation_ratio, 4) if translation_ratio is not None else None,
            boundary_uncertainty_frames=max(5, (e - s) // 4),  # matches window.py's existing pad -- stated as uncertainty, not precision
            overlaps_candidate_ids=[],
            rejection_reason=rejection_reason,
        ))

    # compute pairwise overlap relationships (informational, not used to merge/reject)
    for a in candidates:
        for b in candidates:
            if a is b:
                continue
            if a.start_frame <= b.end_frame and b.start_frame <= a.end_frame:
                a.overlaps_candidate_ids.append(b.candidate_id)

    return candidates
