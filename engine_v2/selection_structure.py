"""BL-008C.37 -- movement structure INSIDE the user's selected interval.

Replaces engine_v2.user_selection_validation as the "Check this selection"
evidence (question B in engine_v2.user_guided_flow).

WHY THE OLD CHECK WAS REPLACED (root cause, reproduced on the owner's own
production clip, MarioTSwing.mov, selection 6-60 -- full write-up in
validation/BL008C37_REPORT.md):

  The old check never looked at the selection itself. It ran a
  whole-clip burst detector (engine_v2.event_candidates), grouped the
  clip-wide bursts, and hard-blocked whenever two groups had a peak inside
  (or up to max(5, width//4) frames OUTSIDE) the selection. On the owner's
  clip the real swing (frames 40-49, the strongest motion in the clip) was
  split by the internal-dip rule into two 4-frame bursts, and BOTH were
  then discarded as "shorter than 5 frames -- likely noise". What was
  left was a faint stance sway (frames 0-23, ~18% of the swing's motion)
  and the follow-through fused with the batter walking away (51-115).
  Those two leftovers were reported as "more than one distinct movement".
  The same pattern falsely rejected a padded-but-single-swing selection on
  5 of the 6 phone clips in the BL-008C.16 decision matrix.

WHAT THIS MODULE MEASURES INSTEAD (deliberately simple and explainable):

  * HAND MOTION RELATIVE TO THE BODY: the midpoint of the two wrists
    (both hands are on the bat during a swing), measured relative to the
    hip midpoint and divided by the batter's own torso length. Relative
    to the hips removes camera panning and walking translation; the wrist
    MIDPOINT largely cancels the opposite arm swing of walking; torso
    lengths make it independent of distance to the camera and resolution.
  * PER SECOND, using each frame's real timestamp -- so 30, 54 and 60 fps
    clips (and uneven phone timestamps) are handled the same way, and every
    window below is in seconds, never a frame count.
  * The selection's motion is split into PHASES of activity. Short pauses
    (load -> stride -> swing -> follow-through) are bridged, so one swing's
    several motion peaks form ONE phase. Two movements are only reported
    as separate when each is comparably fast AND they are separated by a
    real, well-tracked pause long enough to reset between them.

It never classifies the movement as a baseball swing, never detects
contact, and never scores swing quality. It answers only: does the
selected interval look like ONE continuous fast movement, and is the body
tracked well enough to measure it? The user confirms that it is their
swing (question C, unchanged).

All thresholds are stated in physical units (seconds, torso lengths per
second, fractions of the selection's own peak) with the reason for each,
and were checked against the labelled real-footage set in
tests/test_selection_structure_bl008c37.py and the report -- they were not
tuned on the owner's clip alone.
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np

SCHEMA_VERSION = "selection_structure_v1"

# Decisions (see engine_v2.user_guided_flow for what each one allows).
DECISION_ACCEPTED = "accepted"          # one clear movement, tracked well
DECISION_UNCERTAIN = "uncertain"        # usable, but the evidence is weak or mixed -- warn, user may continue
DECISION_MULTIPLE = "multiple"          # high-confidence: two separate fast movements -- ask for a narrower selection
DECISION_UNUSABLE = "unusable"          # body not tracked well enough to measure anything meaningful

# --- constants (physical units; why each value) -----------------------------

# Central-difference half window for speed. 0.05 s spans ~1.5 frames at 30
# fps and ~3 at 60 fps: enough to average out phone duplicate frames
# (identical image, then a double-size jump) without blurring a swing,
# whose fast part lasts ~0.15-0.25 s.
SPEED_HALF_WINDOW_S = 0.05

# A frame whose hand position jumps more than this many torso lengths away
# from the median of its +/-2 neighbours is a pose glitch (a wrist
# landmark briefly snapping to the wrong place), not motion. Hands
# genuinely move < 0.5 torso lengths between neighbouring frames even at
# swing speed and 30 fps.
GLITCH_JUMP_TORSO = 1.0

# Wrists below this mediapipe visibility are treated as not tracked.
MIN_WRIST_VISIBILITY = 0.3

# "Active" = hand speed at least this fraction of the selection's own peak.
ACTIVE_FRACTION = 0.25

# Pauses shorter than this inside activity are bridged into one phase.
# A hitter cannot reload the bat and start a SECOND swing within half a
# second, while one swing's own pieces (load -> stride -> launch, and
# follow-through -> bringing the bat back down) are separated by less:
# IMG_8601's bat recovery resumes 0.37 s after the follow-through slows.
BRIDGE_GAP_S = 0.5

# A phase counts as a major movement when its peak is at least this
# fraction of the selection's fastest phase and it lasts at least
# MIN_MAJOR_S (a swing's fast part lasts ~0.15-0.25 s; a 1-2 frame spike
# is a tracking glitch or a scene cut, not a movement).
MAJOR_FRACTION = 0.6
MIN_MAJOR_S = 0.1

# Two major phases are called separate with HIGH confidence only when the
# quiet, well-tracked pause between them is at least this long. Pauses
# between BRIDGE_GAP_S and this are reported as uncertain, never as
# "multiple". Real repeated swings in this dataset are seconds apart
# (My_swing.mov: ~3.5 s); 0.8 s leaves a wide margin on both sides.
DISTINCT_GAP_S = 0.8
# Without usable timestamps, frame gaps are converted at this rate for the
# split decision only (worst case for a phone's high-frame-rate mode).
WORST_CASE_FPS = 120.0

# Minimum share of frames in a pause that must be tracked for the pause to
# count as evidence (an untracked gap is not evidence of stillness).
GAP_TRACKED_FRACTION = 0.8

# Peak hand speed (torso lengths per second) below which no clear fast
# movement is present. A swing's hands move several torso lengths in
# ~0.2 s; standing in a stance or a bat waggle stays near 1. Used only
# to WARN (never to block): slow-motion exports make a real swing look
# slow, so this cannot be a hard rule.
CLEAR_MOVEMENT_SPEED = 3.0
# Every full-speed phone swing in the BL-008C.37 labelled set peaked at
# 6.2-10.6 torso lengths/s, while walking away and bat resets peaked at
# 3.3-4.2 and slow-motion broadcast swings at 3.4-4.1. Between 3 and 5
# we can't tell those apart, so we say so (a warning, never a block).
FULL_SPEED_SWING_SPEED = 5.0

# Below this core-landmark coverage nothing can be measured (same 50%
# floor as engine_v2.user_guided_flow / event_candidates).
MIN_COVERAGE = 0.5
# Below this, or with a long tracking gap inside the movement, warn.
GOOD_COVERAGE = 0.85
MAX_GAP_IN_MOVEMENT_S = 0.25

# Partial-swing check. The selection's fastest moment sitting within
# EDGE_WINDOW_S of START or END means the real peak may lie outside it;
# so does the movement still being active at a marker while hands keep
# moving at >= EDGE_CONTINUES_FRACTION of the peak within CONTEXT_S just
# outside it. That outside look is the ONLY use of frames beyond the
# markers.
EDGE_WINDOW_S = 0.1
EDGE_CONTINUES_FRACTION = 0.5
CONTEXT_S = 0.25

_CORE = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")


@dataclasses.dataclass
class Phase:
    start_frame: int
    end_frame: int
    start_s: float
    end_s: float
    peak_frame: int
    peak_speed: float          # torso lengths / second
    major: bool


@dataclasses.dataclass
class SelectionStructure:
    schema_version: str
    decision: str
    reasons: list              # short machine codes, e.g. ["one_movement"], ["low_tracking", "edge_cut_end"]
    summary: str               # one plain-language sentence for the user
    phases: list               # list[Phase], in time order
    peak_speed: float | None   # torso lengths / second
    coverage: float            # fraction of frames with core landmarks
    max_tracking_gap_s: float  # longest untracked run inside the selection
    timing_source: str         # "timestamps" | "assumed_30fps"
    selection: tuple           # (start_frame, end_frame)

    def as_evidence(self) -> dict:
        d = dataclasses.asdict(self)
        d["phases"] = [dataclasses.asdict(p) for p in self.phases]
        return d


def _times(frames, fallback_fps: float = 30.0) -> tuple[np.ndarray, str]:
    t = [f.pts_s for f in frames]
    if all(isinstance(x, (int, float)) and math.isfinite(x) for x in t) and len(t) > 1:
        arr = np.asarray(t, dtype=float)
        if np.all(np.diff(arr) >= 0) and arr[-1] > arr[0]:
            return arr, "timestamps"
    return np.arange(len(frames), dtype=float) / fallback_fps, "assumed_30fps"


def _point(lms, name, aspect):
    p = lms.get(name) if lms else None
    if not p:
        return None
    return np.array([p[0] * aspect, p[1]]), (p[2] if len(p) > 2 else 1.0)


def _body_samples(frames, s, e, aspect):
    """Per frame: hand midpoint relative to hip midpoint (aspect-corrected
    units), torso length, and whether the core body is tracked."""
    n = e - s + 1
    rel = np.full((n, 2), np.nan)
    torso = np.full(n, np.nan)
    core_ok = np.zeros(n, dtype=bool)
    for k in range(n):
        lms = frames[s + k].landmarks
        if not lms:
            continue
        pts = {c: _point(lms, c, aspect) for c in _CORE}
        if any(v is None for v in pts.values()):
            continue
        core_ok[k] = True
        sh = (pts["left_shoulder"][0] + pts["right_shoulder"][0]) / 2
        hp = (pts["left_hip"][0] + pts["right_hip"][0]) / 2
        torso[k] = float(np.linalg.norm(sh - hp))
        wrists = [w for w in (_point(lms, "left_wrist", aspect), _point(lms, "right_wrist", aspect))
                  if w is not None and w[1] >= MIN_WRIST_VISIBILITY]
        if wrists:
            hand = np.mean([w[0] for w in wrists], axis=0)
            rel[k] = hand - hp
    return rel, torso, core_ok


def _drop_glitches(rel: np.ndarray, torso_len: float) -> np.ndarray:
    out = rel.copy()
    n = len(rel)
    for k in range(n):
        if np.isnan(rel[k, 0]):
            continue
        nb = [rel[j] for j in range(max(0, k - 2), min(n, k + 3)) if j != k and not np.isnan(rel[j, 0])]
        if len(nb) >= 2:
            med = np.median(np.asarray(nb), axis=0)
            if np.linalg.norm(rel[k] - med) > GLITCH_JUMP_TORSO * torso_len:
                out[k] = np.nan
    return out


def _speeds(rel: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Central-difference speed over +/-SPEED_HALF_WINDOW_S using real
    timestamps; NaN where either end is untracked."""
    n = len(rel)
    sp = np.full(n, np.nan)
    for k in range(n):
        if np.isnan(rel[k, 0]):
            continue
        lo = k
        while lo > 0 and t[k] - t[lo] < SPEED_HALF_WINDOW_S:
            lo -= 1
        hi = k
        while hi < n - 1 and t[hi] - t[k] < SPEED_HALF_WINDOW_S:
            hi += 1
        while lo < k and np.isnan(rel[lo, 0]):
            lo += 1
        while hi > k and np.isnan(rel[hi, 0]):
            hi -= 1
        if hi == lo or t[hi] <= t[lo]:
            continue
        sp[k] = float(np.linalg.norm(rel[hi] - rel[lo]) / (t[hi] - t[lo]))
    return sp


def _longest_false_run_s(mask: np.ndarray, t: np.ndarray) -> float:
    best, k, n = 0.0, 0, len(mask)
    while k < n:
        if not mask[k]:
            j = k
            while j + 1 < n and not mask[j + 1]:
                j += 1
            # an untracked run lasts from the last tracked frame before it
            # to the first tracked frame after it
            a = t[k - 1] if k > 0 else t[k]
            b = t[j + 1] if j + 1 < n else t[j]
            best = max(best, float(b - a))
            k = j + 1
        else:
            k += 1
    return best


def _phases(speed: np.ndarray, t: np.ndarray, s: int, peak: float) -> list[Phase]:
    active = np.nan_to_num(speed, nan=0.0) >= ACTIVE_FRACTION * peak
    runs, k, n = [], 0, len(active)
    while k < n:
        if active[k]:
            j = k
            while j + 1 < n and active[j + 1]:
                j += 1
            runs.append([k, j])
            k = j + 1
        else:
            k += 1
    merged: list[list[int]] = []
    for r in runs:
        if merged and t[r[0]] - t[merged[-1][1]] < BRIDGE_GAP_S:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = []
    for a, b in merged:
        seg = np.nan_to_num(speed[a:b + 1], nan=0.0)
        pk = a + int(np.argmax(seg))
        out.append(Phase(start_frame=s + a, end_frame=s + b, start_s=round(float(t[a]), 3),
                         end_s=round(float(t[b]), 3), peak_frame=s + pk,
                         peak_speed=round(float(speed[pk]), 2), major=False))
    top = max((p.peak_speed for p in out), default=0.0)
    for p in out:
        p.major = p.peak_speed >= MAJOR_FRACTION * top and (p.end_s - p.start_s) >= MIN_MAJOR_S
    return out


def analyze_selection_structure(frames, start: int, end: int, *, frame_aspect: float | None = None
                                ) -> SelectionStructure:
    """`frames`: the clip's LandmarkFrames (already pose-extracted -- nothing
    is decoded or re-tracked here). `start`/`end`: the user's inclusive
    selection, exactly as shown on the markers. `frame_aspect`: source
    width / height, so horizontal and vertical motion are compared in the
    same units (1.0 if unknown)."""
    aspect = float(frame_aspect) if frame_aspect and frame_aspect > 0 else 1.0
    t_all, timing_source = _times(frames)
    # A little context on each side, used ONLY to tell whether the movement
    # continues past a marker (partial swing). Every decision about what is
    # inside the selection uses the selected frames alone.
    lo = int(np.searchsorted(t_all, t_all[start] - CONTEXT_S, side="left"))
    hi = int(np.searchsorted(t_all, t_all[end] + CONTEXT_S, side="right")) - 1
    lo, hi = max(0, min(lo, start)), min(len(frames) - 1, max(hi, end))
    i0, i1 = start - lo, end - lo
    t_ctx = t_all[lo:hi + 1]
    t = t_ctx[i0:i1 + 1]
    rel_ctx, torso_ctx, core_ctx = _body_samples(frames, lo, hi, aspect)
    rel, torso, core_ok = rel_ctx[i0:i1 + 1], torso_ctx[i0:i1 + 1], core_ctx[i0:i1 + 1]
    coverage = float(core_ok.mean()) if len(core_ok) else 0.0
    tracked = core_ok & ~np.isnan(rel[:, 0])
    max_gap = _longest_false_run_s(tracked, t)

    def result(decision, reasons, summary, phases=(), peak=None):
        return SelectionStructure(
            schema_version=SCHEMA_VERSION, decision=decision, reasons=list(reasons), summary=summary,
            phases=list(phases), peak_speed=None if peak is None else round(float(peak), 2),
            coverage=round(coverage, 3), max_tracking_gap_s=round(max_gap, 3),
            timing_source=timing_source, selection=(start, end))

    torso_len = float(np.nanmedian(torso)) if np.any(~np.isnan(torso)) else float("nan")
    if coverage < MIN_COVERAGE or not math.isfinite(torso_len):
        return result(DECISION_UNUSABLE, ["low_tracking"],
                      f"The body was only tracked in {coverage:.0%} of this selection, which is too little to "
                      f"measure anything reliably.")
    if torso_len < 1e-3:
        return result(DECISION_UNUSABLE, ["degenerate_pose"],
                      "The tracked shoulders and hips sit on top of each other in this selection, so the "
                      "body's size and movement can't be measured.")

    speed_ctx = _speeds(_drop_glitches(rel_ctx, torso_len) / torso_len, t_ctx)
    speed = speed_ctx[i0:i1 + 1]
    if not np.any(~np.isnan(speed)):
        return result(DECISION_UNUSABLE, ["no_hand_tracking"],
                      "Your hands weren't tracked in this selection, so the movement can't be measured.")
    peak = float(np.nanmax(speed))
    if peak <= 0:
        return result(DECISION_UNCERTAIN, ["no_clear_movement"],
                      "We couldn't see your hands move in this selection.", peak=0.0)

    phases = _phases(speed, t, start, peak)
    # A blip (shorter than MIN_MAJOR_S) must not set the scale for
    # everything else: if the fastest phase is a blip, measure the rest
    # against the fastest real phase instead.
    real = [p for p in phases if (p.end_s - p.start_s) >= MIN_MAJOR_S]
    if real and max(phases, key=lambda p: p.peak_speed) not in real:
        peak = max(p.peak_speed for p in real)
        phases = _phases(np.minimum(np.nan_to_num(speed, nan=0.0), peak), t, start, peak)
    major = [p for p in phases if p.major]
    reasons: list[str] = []

    # Separate fast movements: consecutive MAJOR phases, split by a pause.
    # A phase touching START or END is only the visible part of something
    # mostly outside the selection (e.g. the tail of a bat reset), so it
    # can raise a warning but never decide a block on its own.
    inner = [p for p in major if p.start_frame > start and p.end_frame < end]
    separate_gaps = []
    for a, b in zip(major, major[1:]):
        gap_s = b.start_s - a.end_s
        ga, gb = a.end_frame - start + 1, b.start_frame - start
        gap_tracked = float(tracked[ga:gb].mean()) if gb > ga else 1.0
        separate_gaps.append((a, b, gap_s, gap_tracked))

    def long_enough(a, b, gap_s):
        if timing_source == "timestamps":
            return gap_s >= DISTINCT_GAP_S
        # No real timestamps: the pause must be long even if the video were
        # recorded at a fast frame rate, so a high-fps clip without timing
        # can't be mistaken for two swings.
        return (b.start_frame - a.end_frame) / WORST_CASE_FPS >= DISTINCT_GAP_S

    confident_split = []
    for a, b in zip(inner, inner[1:]):
        gap_s = b.start_s - a.end_s
        ga, gb = a.end_frame - start + 1, b.start_frame - start
        gap_tracked = float(tracked[ga:gb].mean()) if gb > ga else 1.0
        if long_enough(a, b, gap_s) and gap_tracked >= GAP_TRACKED_FRACTION:
            confident_split.append((a, b, gap_s))
    if confident_split:
        starts = [f"{p.start_s:.2f}s" for p in major]
        listed = ", ".join(starts[:-1]) + f" and {starts[-1]}"
        return result(
            DECISION_MULTIPLE, ["separate_fast_movements"],
            f"This selection has {len(major)} separate fast movements, starting around {listed}, with "
            f"clear breaks between them. Measurements would mix them together.",
            phases, peak)
    if separate_gaps:
        reasons.append("possible_second_movement")

    if peak < CLEAR_MOVEMENT_SPEED:
        reasons.append("no_clear_movement")
    elif peak < FULL_SPEED_SWING_SPEED:
        reasons.append("slower_than_full_speed")
    if coverage < GOOD_COVERAGE:
        reasons.append("partial_tracking")
    main = max(major or phases, key=lambda p: p.peak_speed)
    a, b = main.start_frame - start, main.end_frame - start
    gap_in_main = _longest_false_run_s(tracked[a:b + 1], t[a:b + 1]) if b > a else 0.0
    if gap_in_main > MAX_GAP_IN_MOVEMENT_S:
        reasons.append("tracking_gap_in_movement")
    # Partial swing: the fastest moment sits right at a marker, or
    # comparably fast hand motion just outside a marker is close enough to
    # the main movement that it would have been bridged into it (i.e. the
    # same movement continues past the marker).
    t_peak = float(t[main.peak_frame - start])
    sp_ctx = np.nan_to_num(speed_ctx, nan=0.0)
    goes_on = EDGE_CONTINUES_FRACTION * main.peak_speed
    fast_before = [k for k in range(i0) if sp_ctx[k] >= goes_on]
    fast_after = [k for k in range(i1 + 1, len(sp_ctx)) if sp_ctx[k] >= goes_on]
    if (t_peak - t[0] <= EDGE_WINDOW_S
            or (fast_before and main.start_s - t_ctx[fast_before[-1]] < BRIDGE_GAP_S)):
        reasons.append("edge_cut_start")
    if (t[-1] - t_peak <= EDGE_WINDOW_S
            or (fast_after and t_ctx[fast_after[0]] - main.end_s < BRIDGE_GAP_S)):
        reasons.append("edge_cut_end")
    if timing_source != "timestamps":
        reasons.append("assumed_timing")

    if not reasons:
        return result(DECISION_ACCEPTED, ["one_movement"],
                      f"We found one continuous fast movement ({main.start_s:.2f}-{main.end_s:.2f}s), "
                      f"tracked well.", phases, peak)
    return result(DECISION_UNCERTAIN, reasons, _uncertain_summary(reasons, phases, main), phases, peak)


_UNCERTAIN_TEXT = {
    "possible_second_movement": "there may be a second, separate movement in it",
    "no_clear_movement": "we didn't see a clearly fast movement (normal for slow-motion video)",
    "slower_than_full_speed": ("it's slower than a full-speed swing usually looks (walking, a practice "
                               "swing and slow-motion video all look like this)"),
    "partial_tracking": "your body was only partly tracked",
    "tracking_gap_in_movement": "tracking dropped out during the movement",
    "edge_cut_start": "the movement may already be underway at your START marker",
    "edge_cut_end": "the movement may still be going at your END marker",
    "assumed_timing": "the video has no usable timestamps, so times are approximate",
}


def _uncertain_summary(reasons, phases, main) -> str:
    parts = [_UNCERTAIN_TEXT[r] for r in reasons if r in _UNCERTAIN_TEXT]
    return ("The fastest movement is around " f"{main.start_s:.2f}-{main.end_s:.2f}s, but "
            + "; ".join(parts) + ".")
