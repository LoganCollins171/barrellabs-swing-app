"""BL-008C.1 -- swing segmentation and event detection, REVISED.

ROOT-CAUSE DIAGNOSIS (BL-008C.1, see validation/BL008C1_EVENT_DETECTION.md
for the full write-up and raw signal evidence): the BL-008C peak-wrist-SPEED
detector was diagnosed, not just replaced on a hunch. Printing the full
wrist-speed time series for the failed judge_swing-copy.mp4 pilot clip
found TWO compounding root causes, both traced to specific frame numbers,
not guessed:

1. The segmentation walk-back/walk-forward (find_primary_swing_segment)
   stopped as soon as it hit a SINGLE frame below its 15%-of-peak threshold.
   On judge_swing copy.mp4, the true burst spans roughly frames 128-183, but
   a one-frame noise dip at frame 168 (smoothed value 0.025 vs threshold
   0.038) tripped the stop condition immediately, truncating the walk and
   losing the entire 128-167 region -- exactly where the real load/plant/
   launch happen. This is fixed below with a _GAP_TOLERANCE hysteresis: the
   walk now requires several consecutive below-threshold frames before it
   actually stops.
2. Even after fixing (1), within the correct region, 2D image-space wrist
   LINEAR SPEED does not have one sharp, isolated peak at contact. It forms
   a broad, sustained plateau spanning the downswing through the
   follow-through (comparable speed at frames 132, 162, AND 171 in the
   Judge clip), because a hand tracing a big circular arc moves at roughly
   constant linear speed through much of that arc in 2D projection. Picking
   "the single highest point in a plateau" is close to arbitrary and
   noise-sensitive -- which explains why it landed near the END of the
   plateau (171, deep in follow-through) even once the window was right.

TESTABLE HYPOTHESIS (not just a new guess): validation/MLB_REF_RELABEL.md,
written by the product owner BEFORE this engine existed, already specifies
the correct definition: "Contact = the frame where the bat is at maximum
forward extension in the hitting zone, with the arms extended toward the
pitcher." That is a POSITION signal (extension reaching its own local
maximum), not a speed signal. This module now implements that definition
directly and tests it against the same 3 pilot clips.

Foot-plant is similarly moved to a POSITION-based leg signal (front ankle's
horizontal position stabilizing after the stride) rather than a wrist-speed
local minimum, since foot-plant is a leg event, not a hand event -- using
the hand to infer it was never well-motivated to begin with.
"""
from __future__ import annotations

import numpy as np

from engine_v2.types import LandmarkFrame, SwingSegment, SwingEvents

_MIN_SEGMENT_FRAMES = 10
_STABLE_VELOCITY_THRESHOLD = 0.004  # normalized units/frame, below which an ankle counts as "landed"
_STABLE_WINDOW = 3                   # consecutive frames below threshold required


def _wrist_speed_series(frames: list[LandmarkFrame]) -> np.ndarray:
    speeds = np.zeros(len(frames))
    prev = None
    for i, f in enumerate(frames):
        if f.landmarks is None:
            prev = None
            continue
        lw = f.landmarks.get("left_wrist")
        rw = f.landmarks.get("right_wrist")
        cand = lw if (lw and (not rw or lw[2] >= rw[2])) else rw
        if cand is None:
            prev = None
            continue
        pos = np.array([cand[0], cand[1]])
        if prev is not None:
            speeds[i] = float(np.linalg.norm(pos - prev))
        prev = pos
    return speeds


def find_primary_swing_segment(frames: list[LandmarkFrame]) -> SwingSegment:
    """Unchanged from BL-008C: diagnosis showed this stage locates the
    correct general region. Only the event-detection stage within it (below)
    was replaced."""
    speeds = _wrist_speed_series(frames)
    if len(speeds) < _MIN_SEGMENT_FRAMES or np.all(speeds == 0):
        return SwingSegment(
            start_frame=0, end_frame=max(len(frames) - 1, 0),
            peak_motion_frame=0, motion_energy_score=0.0,
        )

    k = max(1, len(speeds) // 100)
    smoothed = np.convolve(speeds, np.ones(k) / k, mode="same") if k > 1 else speeds

    peak_idx = int(np.argmax(smoothed))
    peak_val = float(smoothed[peak_idx])
    threshold = peak_val * 0.15
    # BL-008C.1 root-cause fix: a single-frame dip below threshold (noise)
    # used to stop the walk immediately, truncating a genuinely sustained
    # burst that has brief dips in it (confirmed on judge_swing copy.mp4 --
    # see validation/BL008C1_EVENT_DETECTION.md). Require _GAP_TOLERANCE
    # consecutive below-threshold frames before actually stopping.
    _GAP_TOLERANCE = 4
    start = peak_idx
    gap = 0
    while start > 0:
        if smoothed[start - 1] <= threshold:
            gap += 1
            if gap > _GAP_TOLERANCE:
                start += gap - 1  # back off to the last above-threshold frame
                break
        else:
            gap = 0
        start -= 1
    else:
        start = 0
    end = peak_idx
    gap = 0
    while end < len(smoothed) - 1:
        if smoothed[end + 1] <= threshold:
            gap += 1
            if gap > _GAP_TOLERANCE:
                end -= gap - 1
                break
        else:
            gap = 0
        end += 1
    else:
        end = len(smoothed) - 1

    pad = max(5, (end - start) // 4)
    start = max(0, start - pad)
    end = min(len(frames) - 1, end + pad)

    return SwingSegment(start_frame=start, end_frame=end, peak_motion_frame=peak_idx, motion_energy_score=peak_val)


def _bat_hand_wrist(f: LandmarkFrame):
    if f.landmarks is None:
        return None
    lw, rw = f.landmarks.get("left_wrist"), f.landmarks.get("right_wrist")
    return lw if (lw and (not rw or lw[2] >= rw[2])) else rw


def _choose_fixed_bat_hand_side(frames: list[LandmarkFrame], s: int, e: int) -> str:
    """BL-008C.1 root-cause fix: per-frame wrist selection (whichever wrist
    is more visible THIS frame) caused a spurious hand flip mid-segment on
    judge_swing copy.mp4 -- exactly at frame 173, where the extension signal
    falsely spiked because tracking switched to the OTHER wrist (farther
    from the shoulder midpoint for anatomical reasons, not because of new
    extension). Confirmed by printing the per-frame hand-side selection
    alongside the extension value. Fix: choose ONE side for the whole
    segment, by majority vote of mean visibility across all frames in it,
    so the signal can't jump sides mid-swing."""
    left_vis, right_vis, n = 0.0, 0.0, 0
    for i in range(s, e + 1):
        f = frames[i]
        if f.landmarks is None:
            continue
        lw, rw = f.landmarks.get("left_wrist"), f.landmarks.get("right_wrist")
        if lw:
            left_vis += lw[2]
        if rw:
            right_vis += rw[2]
        n += 1
    return "left" if left_vis >= right_vis else "right"


def _lead_shoulder_candidates(f: LandmarkFrame):
    """Both shoulders -- caller picks whichever side is farther from the
    body center in the direction of swing extension, per frame, since we
    don't reliably know handedness-to-camera-side mapping for every source."""
    if f.landmarks is None:
        return None, None
    return f.landmarks.get("left_shoulder"), f.landmarks.get("right_shoulder")


def _forward_extension_series(frames: list[LandmarkFrame], s: int, e: int) -> np.ndarray:
    """Distance from the bat-hand wrist to the shoulder MIDPOINT, in the
    horizontal (x) axis only -- a 2D proxy for "arms extended toward the
    pitcher" per the owner's own contact definition. Horizontal-only
    because vertical wrist position is dominated by swing-plane tilt, not
    extension, and we want a signal that peaks distinctly at extension
    regardless of swing plane."""
    side = _choose_fixed_bat_hand_side(frames, s, e)
    out = np.zeros(e - s + 1)
    for i in range(s, e + 1):
        f = frames[i]
        w = f.landmarks.get(f"{side}_wrist") if f.landmarks else None
        ls, rs = _lead_shoulder_candidates(f)
        if w is None or ls is None or rs is None:
            out[i - s] = np.nan
            continue
        shoulder_mid_x = (ls[0] + rs[0]) / 2
        out[i - s] = abs(w[0] - shoulder_mid_x)
    return out


_PEAK_MIN_SEPARATION = 5     # frames; peaks closer than this are treated as one candidate
_AMBIGUITY_RATIO = 0.85      # 2nd-best peak within this fraction of the best -> AMBIGUOUS, don't guess


def _find_local_maxima(signal: np.ndarray, min_separation: int) -> list[int]:
    """All local maxima in `signal` (ignoring NaN), merged so no two
    reported peaks are within `min_separation` frames of each other (keeps
    the taller one). Used to detect when a signal has MULTIPLE comparably
    strong candidates for an event -- the direct, measured basis for
    AMBIGUOUS status, instead of silently picking the global max and
    hoping it's right (which is what produced the Torkelson regression --
    see validation/BL008C1_EVENT_DETECTION.md)."""
    n = len(signal)
    raw_peaks = []
    for i in range(n):
        if np.isnan(signal[i]):
            continue
        left_ok = i == 0 or np.isnan(signal[i - 1]) or signal[i] >= signal[i - 1]
        right_ok = i == n - 1 or np.isnan(signal[i + 1]) or signal[i] >= signal[i + 1]
        if left_ok and right_ok:
            raw_peaks.append(i)
    raw_peaks.sort(key=lambda i: signal[i], reverse=True)
    kept: list[int] = []
    for p in raw_peaks:
        if all(abs(p - k) >= min_separation for k in kept):
            kept.append(p)
    return kept


def _classify_contact(ext: np.ndarray, s: int) -> "EventCandidate":
    from engine_v2.types import EventCandidate, EventStatus

    valid_count = int((~np.isnan(ext)).sum())
    if valid_count == 0:
        return EventCandidate(
            status=EventStatus.NOT_OBSERVABLE, best_estimate_frame=None,
            earliest_plausible_frame=None, latest_plausible_frame=None,
            supporting_signal="forward_extension", candidate_frames=[], rejected_candidates=[],
            evidence_note="bat-hand wrist and/or both shoulders not tracked in this interval",
            failure_reason="no valid forward-extension samples in the segment",
        )

    peaks = _find_local_maxima(ext, _PEAK_MIN_SEPARATION)
    if not peaks:
        return EventCandidate(
            status=EventStatus.NOT_OBSERVABLE, best_estimate_frame=None,
            earliest_plausible_frame=None, latest_plausible_frame=None,
            supporting_signal="forward_extension", candidate_frames=[], rejected_candidates=[],
            evidence_note="no local maxima found in forward-extension signal",
            failure_reason="signal too flat or too sparse to identify a candidate",
        )

    candidate_frames = [s + p for p in peaks[:5]]
    best_rel = peaks[0]
    best_val = ext[best_rel]
    close_rivals = [p for p in peaks[1:] if not np.isnan(ext[p]) and ext[p] >= best_val * _AMBIGUITY_RATIO]

    if close_rivals:
        rival_frames = [s + p for p in close_rivals]
        all_cands = sorted([s + best_rel] + rival_frames)
        return EventCandidate(
            status=EventStatus.AMBIGUOUS,
            best_estimate_frame=None,
            earliest_plausible_frame=min(all_cands),
            latest_plausible_frame=max(all_cands),
            supporting_signal="forward_extension",
            candidate_frames=candidate_frames,
            rejected_candidates=[],
            evidence_note=(
                f"{len(close_rivals) + 1} comparably-strong extension peaks found "
                f"(within {_AMBIGUITY_RATIO:.0%} of the best) -- no principled way to "
                f"pick one over the others from this signal alone"
            ),
            failure_reason="multiple comparable candidate frames, not a single dominant peak",
        )

    # Single dominant peak -- still reported as an interval (one frame of
    # smoothing slack on each side) rather than claiming frame-exact
    # certainty, since we have no independent accuracy measurement for
    # this signal yet (see validation/BL008C1_EVENT_DETECTION.md: the
    # Judge-copy clip improved but did not pass manual review even with a
    # single dominant peak -- so "dominant peak" is necessary, not
    # sufficient, for real confidence; ESTIMATED_INTERVAL, not OBSERVED,
    # reflects that honestly until independently validated further).
    return EventCandidate(
        status=EventStatus.ESTIMATED_INTERVAL,
        best_estimate_frame=s + best_rel,
        earliest_plausible_frame=max(s, s + best_rel - 2),
        latest_plausible_frame=s + best_rel + 2,
        supporting_signal="forward_extension",
        candidate_frames=candidate_frames,
        rejected_candidates=[s + p for p in peaks[1:]],
        evidence_note=(
            f"single dominant forward-extension peak at frame {s + best_rel} "
            f"(value {best_val:.3f}, next-best {ext[peaks[1]]:.3f} if present)"
            if len(peaks) > 1 else
            f"single forward-extension peak at frame {s + best_rel} (value {best_val:.3f})"
        ),
    )


def _ankle_velocity_series(frames: list[LandmarkFrame], s: int, e: int, side: str) -> np.ndarray:
    out = np.zeros(e - s + 1)
    prev = None
    for i in range(s, e + 1):
        f = frames[i]
        ank = f.landmarks.get(f"{side}_ankle") if f.landmarks else None
        if ank is None:
            out[i - s] = np.nan
            prev = None
            continue
        pos = np.array(ank[:2])
        out[i - s] = float(np.linalg.norm(pos - prev)) if prev is not None else 0.0
        prev = pos
    return out


def detect_events(frames: list[LandmarkFrame], segment: SwingSegment) -> SwingEvents:
    """Position-based event detection, replacing the BL-008C speed-peak
    approach. See module docstring for the root-cause diagnosis and the
    testable hypothesis this implements.

    contact: frame of MAXIMUM forward wrist-to-shoulder-midpoint extension
      within the segment (owner's own documented definition).
    foot_plant: the earlier of the two ankles' "landed" frame (sustained
      low velocity after the segment's lead-in), searched BEFORE the
      detected contact frame -- a leg signal for a leg event.
    launch: first frame after foot_plant where forward extension starts
      increasing monotonically toward the contact value (downswing onset
      proxy) -- still a heuristic, explicitly lower-confidence than contact
      or foot_plant, and marked as such via detector_confidence components
      we don't yet expose per-event (documented limitation, see Phase 7).
    """
    s, e = segment.start_frame, segment.end_frame
    if e <= s or segment.motion_energy_score <= 0:
        return SwingEvents(
            load_start_frame=s if e > s else None, foot_plant_frame=None, launch_frame=None,
            contact_frame=None, finish_frame=e if e > s else None, detector_confidence=0.0,
        )

    ext = _forward_extension_series(frames, s, e)
    contact_candidate = _classify_contact(ext, s)
    from engine_v2.types import EventStatus
    if contact_candidate.status == EventStatus.OBSERVED or contact_candidate.status == EventStatus.ESTIMATED_INTERVAL:
        contact = contact_candidate.best_estimate_frame
    else:
        # AMBIGUOUS or NOT_OBSERVABLE: do not assert a specific contact
        # frame. foot_plant/launch below still run (they're independent
        # leg/extension-onset signals) but are bounded by the segment, not
        # by an unreliable contact frame, when contact isn't established.
        contact = None

    # foot_plant: scan both ankles for a "landed" window (STABLE_WINDOW
    # consecutive low-velocity frames) before `contact`; take whichever
    # ankle lands EARLIEST and call that the plant foot (the striding foot
    # is the one that moves then stops; the pivot foot barely moves at all,
    # which would falsely win a "lowest velocity" contest without the
    # stride-then-stop requirement below).
    foot_plant_search_end = contact if contact is not None else e
    foot_plant = None
    best_rel = None
    for side in ("left", "right"):
        vel = _ankle_velocity_series(frames, s, min(foot_plant_search_end, e), side)
        if np.all(np.isnan(vel)):
            continue
        # require the ankle to have MOVED earlier in the window (stride)
        # before it counts as "landed" -- otherwise a never-moving pivot
        # foot trivially satisfies "low velocity" from frame 0.
        moved = np.nanmax(vel[: max(1, len(vel) // 2)]) > _STABLE_VELOCITY_THRESHOLD * 2
        if not moved:
            continue
        for i in range(len(vel) - _STABLE_WINDOW):
            window = vel[i:i + _STABLE_WINDOW]
            if np.all(~np.isnan(window)) and np.all(window < _STABLE_VELOCITY_THRESHOLD):
                cand_rel = i
                if best_rel is None or cand_rel < best_rel:
                    best_rel = cand_rel
                break
    if best_rel is not None:
        foot_plant = s + best_rel

    # launch: first frame after foot_plant (or segment start, if no
    # foot_plant found) where extension exceeds 50% of its contact value --
    # a rough "downswing visibly underway" marker. Explicitly the
    # lowest-confidence event this module produces.
    launch = None
    if contact is not None:
        search_start = foot_plant if foot_plant is not None else s
        contact_rel = contact - s
        half_ext = ext[contact_rel] * 0.5 if 0 <= contact_rel < len(ext) and not np.isnan(ext[contact_rel]) else None
        if half_ext is not None:
            for i in range(search_start, contact + 1):
                rel = i - s
                if rel < len(ext) and not np.isnan(ext[rel]) and ext[rel] > half_ext:
                    launch = i
                    break

    # confidence is a self-reported heuristic signal for triage, NOT a
    # calibrated probability (explicit Section 4 requirement) -- it exists
    # only to flag "this run had less to go on" to a human reviewer.
    confidence = 0.0
    if contact is not None:
        confidence += 0.4
    if foot_plant is not None:
        confidence += 0.3

    return SwingEvents(
        load_start_frame=s,
        foot_plant_frame=foot_plant,
        launch_frame=launch,
        contact_frame=contact,
        finish_frame=e,
        detector_confidence=confidence,
        contact_candidate=contact_candidate,
    )
