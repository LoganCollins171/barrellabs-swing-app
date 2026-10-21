"""BL-008C.10 Deliverable 2 -- target-hitter track segmentation.

HONEST SCOPE STATEMENT, read this before anything else: this is NOT
simultaneous multi-person pose estimation. MediaPipe's Pose solution
(the only pose backend this project has ever used -- see engine_v2/pose.py)
tracks exactly ONE skeleton per frame, whichever person its internal
detector locks onto. It does not report multiple people, and switching to
a backend that does (e.g. a person-detector + per-box pose model) is a
real architecture change this module does NOT make, consistent with the
project's repeated practice of not installing new dependencies without
demonstrated need (BL-008C.3 Phase C/D).

What THIS module does, which is real and testable: treats MediaPipe's
single reported skeleton per frame as a stream that may silently jump
between different physical people when more than one is in frame (exactly
the Bregman finding, BL-008C.10: a foreground runner and a background
batter both visible, MediaPipe's pose presumably following one or the
other inconsistently). It detects implausibly large whole-body centroid
jumps -- the same family of check as BL-008C.5's single-landmark
identity-swap guard, generalized to the whole skeleton -- and SEGMENTS the
frame stream into separate TARGET TRACKS at those jump points. Swing-event
detection (engine_v2/window.py) then runs independently per track, so a
track that's actually a different, unintended person produces its own
separate, clearly-labeled candidate rather than contaminating the
intended hitter's event.

This does NOT identify WHICH track is "the" intended hitter -- for a
library clip whose filename implies one player, that requires external
context (source metadata, user selection) this module doesn't have. It
labels tracks with observable roles only (longest-track, earliest-track,
etc.), never a claimed identity, per the explicit instruction not to
identify a person from jersey number or appearance.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from engine_v2.types import LandmarkFrame

_CORE_LANDMARKS = ["left_shoulder", "right_shoulder", "left_hip", "right_hip"]
_CENTROID_JUMP_RATIO = 1.5   # single-frame centroid jump beyond this many "body scales" -> suspected subject change
_MIN_TRACK_LENGTH = 10        # tracks shorter than this are folded into an adjacent track as noise, not reported alone


@dataclasses.dataclass
class TargetTrack:
    track_id: int
    start_frame: int
    end_frame: int
    frame_count: int
    mean_visibility: float
    role: str   # "longest_track" | "earliest_track" | "other_track" -- observable, not an identity claim


def _body_centroid_and_scale(f: LandmarkFrame):
    """Returns (centroid_xy, scale) for one frame, or (None, None) if the
    core landmarks aren't tracked. scale = shoulder-hip diagonal, used to
    make the jump threshold relative to apparent body size (so a
    close-up shot and a wide shot use comparably-scaled thresholds)."""
    if f.landmarks is None:
        return None, None
    pts = []
    for name in _CORE_LANDMARKS:
        lm = f.landmarks.get(name)
        if lm is None:
            return None, None
        pts.append(lm[:2])
    pts = np.array(pts)
    centroid = pts.mean(axis=0)
    scale = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0))) or 1e-6
    return centroid, scale


def segment_target_tracks(frames: list[LandmarkFrame]) -> list[TargetTrack]:
    """Walks the frame stream and cuts a new track whenever the whole-body
    centroid jumps more than _CENTROID_JUMP_RATIO body-scales in one
    frame step -- the same implausible-single-frame-jump logic validated
    for ankle identity swaps (BL-008C.5), applied to the whole skeleton.
    Short resulting tracks (noise, brief occlusion) are merged into the
    PRECEDING track rather than reported as their own tiny candidate.
    """
    n = len(frames)
    if n == 0:
        return []

    cuts = [0]
    prev_centroid = prev_scale = None
    for i in range(n):
        centroid, scale = _body_centroid_and_scale(frames[i])
        if centroid is None:
            prev_centroid = prev_scale = None
            continue
        if prev_centroid is not None:
            step = float(np.linalg.norm(centroid - prev_centroid))
            ref_scale = max(prev_scale, scale)
            if step > _CENTROID_JUMP_RATIO * ref_scale:
                cuts.append(i)
        prev_centroid, prev_scale = centroid, scale

    cuts.append(n)
    cuts = sorted(set(cuts))

    raw_tracks = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        raw_tracks.append((a, b - 1))

    # merge short tracks into the preceding one (a 2-3 frame segment
    # between two jumps is far more likely tracking noise than a real,
    # separate, reportable person)
    merged = []
    for s, e in raw_tracks:
        length = e - s + 1
        if merged and length < _MIN_TRACK_LENGTH:
            prev_s, prev_e = merged[-1]
            merged[-1] = (prev_s, e)
        else:
            merged.append((s, e))

    tracks = []
    for idx, (s, e) in enumerate(merged):
        vis_vals = [frames[i].mean_visibility for i in range(s, e + 1) if frames[i].landmarks is not None]
        mean_vis = float(np.mean(vis_vals)) if vis_vals else 0.0
        tracks.append(TargetTrack(
            track_id=idx, start_frame=s, end_frame=e, frame_count=e - s + 1,
            mean_visibility=mean_vis, role="pending",
        ))

    if tracks:
        longest = max(tracks, key=lambda t: t.frame_count)
        earliest = tracks[0]
        for t in tracks:
            if t is longest and t is earliest:
                t.role = "longest_and_earliest_track"
            elif t is longest:
                t.role = "longest_track"
            elif t is earliest:
                t.role = "earliest_track"
            else:
                t.role = "other_track"

    return tracks
