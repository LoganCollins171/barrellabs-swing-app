"""BL-008C.3 Phase A -- contact-independent swing-window segmentation.

Unlike engine_v2/segment.py's contact detector (BL-008C.1: still not
trustworthy), THIS module never uses or needs a contact frame. It answers a
narrower, more defensible question: "where in this clip is the primary
swing motion, as distinct from pre-swing stillness and any secondary
motion events (camera pan, a second practice swing, a bystander)?"

Combines THREE independent motion signals (not just wrist speed, which
BL-008C.1 showed has a plateau shape, not a clean single peak) so a burst
has to show up in more than one body-part's motion to count:
  - wrist speed (hand/bat-arm motion)
  - ankle speed, both feet summed (stride/footwork motion)
  - shoulder-hip separation-angle rate of change (trunk rotation motion)

Explicitly tests the "largest burst = the swing" assumption the task warns
against: if two bursts are comparably strong, this returns a REJECTED
window with both candidates listed, rather than silently picking the
larger one (the same AMBIGUOUS-vs-ESTIMATED_INTERVAL pattern BL-008C.1
established for contact).
"""
from __future__ import annotations

import numpy as np

from engine_v2.types import LandmarkFrame

_GAP_TOLERANCE = 4
_BURST_MIN_SEPARATION = 15   # frames; two bursts closer than this are treated as one
_WINDOW_AMBIGUITY_RATIO = 0.7  # PROVISIONAL, not empirically validated (see BL-008C3 findings doc):
                                 # chosen as a conservative starting point, not tuned against pilot
                                 # results. On re-test it flags the clean judge_swing-copy.mp4 pilot
                                 # clip as ambiguous too (rival burst energy ratio 0.74) -- this may
                                 # be over-conservative, or that clip may genuinely have comparable
                                 # pre-swing motion (a stance adjustment or waggle); not resolved.


class WindowStatus:
    OBSERVED = "observed"
    AMBIGUOUS = "ambiguous"
    NOT_OBSERVABLE = "not_observable"


class SwingWindow:
    def __init__(self, status, start_frame=None, end_frame=None,
                 initial_movement_end=None, primary_motion_end=None,
                 candidate_bursts=None, rejection_reason=None, evidence_note=""):
        self.status = status
        self.start_frame = start_frame
        self.end_frame = end_frame
        # best-effort sub-phase split WITHIN the accepted window -- explicitly
        # a heuristic, not a validated phase detector (see docstring note below)
        self.initial_movement_end = initial_movement_end   # last frame of "initial movement" sub-phase
        self.primary_motion_end = primary_motion_end        # last frame of "primary motion" sub-phase (rest is follow-through)
        self.candidate_bursts = candidate_bursts or []
        self.rejection_reason = rejection_reason
        self.evidence_note = evidence_note

    def to_dict(self):
        return {
            "status": self.status, "start_frame": self.start_frame, "end_frame": self.end_frame,
            "initial_movement_end": self.initial_movement_end, "primary_motion_end": self.primary_motion_end,
            "candidate_bursts": self.candidate_bursts, "rejection_reason": self.rejection_reason,
            "evidence_note": self.evidence_note,
        }


def _series(frames: list[LandmarkFrame], names: list[str]) -> np.ndarray:
    """Summed frame-to-frame displacement across multiple landmarks (e.g.
    both ankles, or both wrists) -- NaN-safe, returns 0 for untracked pairs
    rather than propagating NaN (a missing single landmark shouldn't zero
    out an otherwise-valid combined signal)."""
    out = np.zeros(len(frames))
    prev = {n: None for n in names}
    for i, f in enumerate(frames):
        total = 0.0
        for n in names:
            lm = f.landmarks.get(n) if f.landmarks else None
            if lm is None:
                prev[n] = None
                continue
            pos = np.array(lm[:2])
            if prev[n] is not None:
                total += float(np.linalg.norm(pos - prev[n]))
            prev[n] = pos
        out[i] = total
    return out


def _rotation_rate_series(frames: list[LandmarkFrame]) -> np.ndarray:
    """Frame-to-frame change in shoulder-hip separation angle -- a 2D proxy
    for trunk rotation speed. angle = atan2 of the vector from hip-midpoint
    to shoulder-midpoint, relative to horizontal."""
    angles = np.full(len(frames), np.nan)
    for i, f in enumerate(frames):
        if f.landmarks is None:
            continue
        ls, rs = f.landmarks.get("left_shoulder"), f.landmarks.get("right_shoulder")
        lh, rh = f.landmarks.get("left_hip"), f.landmarks.get("right_hip")
        if not all([ls, rs, lh, rh]):
            continue
        sx, sy = (ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2
        hx, hy = (lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2
        angles[i] = np.arctan2(sy - hy, sx - hx)
    rate = np.zeros(len(frames))
    for i in range(1, len(frames)):
        if not (np.isnan(angles[i]) or np.isnan(angles[i - 1])):
            d = angles[i] - angles[i - 1]
            d = (d + np.pi) % (2 * np.pi) - np.pi  # wrap to [-pi, pi]
            rate[i] = abs(d)
    return rate


def _normalize(x: np.ndarray) -> np.ndarray:
    m = np.nanmax(x)
    return x / m if m > 0 else x


_DIP_SPLIT_RATIO = 0.30   # internal dip below this fraction of the SMALLER flanking peak -> split into two bursts
_DIP_MIN_WIDTH = 2        # dip must span at least this many frames to count (avoid single-frame noise)


def _split_on_internal_dip(smoothed: np.ndarray, start: int, end: int, peak_idx: int, peak_val: float):
    """BL-008C.8 root-cause fix for the IMG_8608 oversized-window defect.

    Diagnosed with real signal data (see validation/BL008C8_WINDOW_EVALUATION.md):
    IMG_8608.mov's combined-motion signal has two genuinely separate high-
    energy regions -- the real swing (~frames 42-60, peak 0.53) and a
    LATER, unrelated body-turn motion (~frames 70-78, peak 0.59) -- with an
    incomplete dip between them (frame 68 drops to 0.044, but neighboring
    frames 64/66 are still 0.12-0.19, so the existing gap-tolerance
    hysteresis never sees _GAP_TOLERANCE consecutive below-threshold
    frames and bridges straight across, merging two real events into one
    oversized window).

    This is a DIFFERENT failure from the one _GAP_TOLERANCE itself fixed
    in BL-008C.1 (a single noise dip wrongly truncating ONE real burst) --
    here the burst genuinely contains two separate higher-energy regions
    that should not have been merged in the first place. Fix: after the
    hysteresis walk finds a burst, look for the single lowest point inside
    it; if that point drops below _DIP_SPLIT_RATIO of the SMALLER of the
    two flanking local maxima (not just below the original peak's 15%
    threshold), treat it as a genuine separating trough and split the
    burst into two independent candidates there, each re-centered on its
    own peak. The two resulting bursts then go through the existing
    multi-candidate ambiguity check (_WINDOW_AMBIGUITY_RATIO) like any
    other pair of candidates -- this does not add a new accept/reject
    rule, it just stops prematurely merging what should be two candidates
    into one.
    """
    if end - start < 2 * _DIP_MIN_WIDTH + 2:
        return [(start, end, peak_idx, peak_val)]  # too narrow to contain a meaningful split

    span = smoothed[start:end + 1]
    dip_rel = int(np.argmin(span))
    dip_idx = start + dip_rel
    # don't split right at the edges -- need real left/right sub-regions
    if dip_rel < _DIP_MIN_WIDTH or (end - start - dip_rel) < _DIP_MIN_WIDTH:
        return [(start, end, peak_idx, peak_val)]

    left_max = float(np.max(span[:dip_rel])) if dip_rel > 0 else 0.0
    right_max = float(np.max(span[dip_rel + 1:])) if dip_rel < len(span) - 1 else 0.0
    dip_val = float(span[dip_rel])
    smaller_side = min(left_max, right_max)
    if smaller_side <= 0 or dip_val > _DIP_SPLIT_RATIO * smaller_side:
        return [(start, end, peak_idx, peak_val)]  # no genuine separating trough -- keep as one burst

    left_peak_rel = int(np.argmax(span[:dip_rel]))
    right_peak_rel = dip_rel + 1 + int(np.argmax(span[dip_rel + 1:]))
    left_burst = (start, start + dip_rel - 1, start + left_peak_rel, left_max)
    right_burst = (start + dip_rel + 1, end, start + right_peak_rel, right_max)
    # BL-008C.8: a single pass picks the DEEPEST dip in the whole span,
    # which on IMG_8608.mov was a small, not-very-meaningful 2-frame glitch
    # near the start of the range rather than the real separating trough
    # further in -- confirmed by direct debugging (the first split peeled
    # off frames 35-37 and left 39-79, the real swing-vs-later-motion
    # boundary at frame 68, still merged). Recurse into each resulting
    # sub-burst so a real second (or third) separating trough further in
    # still gets found, not just the single deepest one.
    return _split_on_internal_dip(smoothed, *left_burst) + _split_on_internal_dip(smoothed, *right_burst)


def _find_bursts(combined: np.ndarray) -> list[tuple[int, int, int, float]]:
    """Returns a list of (start, end, peak_idx, peak_val) bursts, using the
    same hysteresis-walk logic as engine_v2/segment.py's segmentation fix,
    factored out here so both modules share the validated approach. Each
    raw burst is then checked for a genuine internal separating trough
    (BL-008C.8 fix, see _split_on_internal_dip) before being added."""
    n = len(combined)
    if n < 10 or np.all(combined == 0):
        return []
    k = max(1, n // 100)
    smoothed = np.convolve(combined, np.ones(k) / k, mode="same") if k > 1 else combined.copy()

    bursts = []
    remaining = smoothed.copy()
    for _ in range(4):  # consider up to 4 candidate bursts
        peak_idx = int(np.argmax(remaining))
        peak_val = float(remaining[peak_idx])
        if peak_val <= 0:
            break
        threshold = peak_val * 0.15
        start, gap = peak_idx, 0
        while start > 0:
            if remaining[start - 1] <= threshold:
                gap += 1
                if gap > _GAP_TOLERANCE:
                    start += gap - 1
                    break
            else:
                gap = 0
            start -= 1
        else:
            start = 0
        end, gap = peak_idx, 0
        while end < n - 1:
            if remaining[end + 1] <= threshold:
                gap += 1
                if gap > _GAP_TOLERANCE:
                    end -= gap - 1
                    break
            else:
                gap = 0
            end += 1
        else:
            end = n - 1
        bursts.extend(_split_on_internal_dip(smoothed, start, end, peak_idx, peak_val))
        remaining[max(0, start - _BURST_MIN_SEPARATION):min(n, end + _BURST_MIN_SEPARATION)] = 0
    return bursts


def find_swing_window(frames: list[LandmarkFrame]) -> SwingWindow:
    if len(frames) < 10:
        return SwingWindow(WindowStatus.NOT_OBSERVABLE, rejection_reason="clip too short to analyze (<10 frames)")

    wrist = _normalize(_series(frames, ["left_wrist", "right_wrist"]))
    ankle = _normalize(_series(frames, ["left_ankle", "right_ankle"]))
    rotation = _normalize(_rotation_rate_series(frames))
    combined = wrist * 0.5 + ankle * 0.2 + rotation * 0.3

    bursts = _find_bursts(combined)
    if not bursts:
        return SwingWindow(WindowStatus.NOT_OBSERVABLE,
                            rejection_reason="no usable combined motion signal (wrist+ankle+rotation all flat or untracked)")

    bursts.sort(key=lambda b: b[3], reverse=True)
    best = bursts[0]
    candidate_list = [{"start": b[0], "end": b[1], "peak": b[2], "energy": round(b[3], 4)} for b in bursts]

    rivals = [b for b in bursts[1:] if b[3] >= best[3] * _WINDOW_AMBIGUITY_RATIO]
    if rivals:
        return SwingWindow(
            WindowStatus.AMBIGUOUS, candidate_bursts=candidate_list,
            rejection_reason=(
                f"{len(rivals) + 1} comparably-strong motion bursts found (within "
                f"{_WINDOW_AMBIGUITY_RATIO:.0%} of the strongest) -- cannot determine which "
                f"is the primary swing without additional context (e.g. a user-confirmed "
                f"timestamp); refusing to assume the largest is correct"
            ),
            evidence_note=f"bursts: {candidate_list}",
        )

    s, e, peak, val = best
    pad = max(5, (e - s) // 4)
    s = max(0, s - pad)
    e_padded = min(len(frames) - 1, e + pad)

    # BL-008C.9 end-boundary fix. Root cause, confirmed by direct visual
    # inspection on IMG_9005.MOV (frames 70 and 90 pulled and compared):
    # genuine follow-through motion settles into a largely STATIC HOLD
    # well before the window previously reported as ending -- frame 90
    # shows the same held pose as frame 70, not continued motion. The
    # residual frame-to-frame landmark jitter during that static hold
    # still exceeds the burst's own 15%-of-peak relative threshold often
    # enough that the gap-tolerance hysteresis never cuts it off, and the
    # unconditional end-padding above then adds MORE frames on top
    # regardless of whether real motion justifies it.
    #
    # Fix: calibrate a genuine "not moving" noise floor from the clip's
    # own quiet pre-burst region (frames before the swing starts -- known
    # to be a stance hold, not swing motion), then only keep padded
    # end-frames whose signal is clearly above that floor (not just above
    # the burst's own low relative threshold, which doesn't distinguish
    # jitter from motion once the burst's peak itself is only moderate).
    baseline_region = combined[: max(1, s)]
    noise_floor = float(np.median(baseline_region)) if len(baseline_region) > 0 else 0.0
    settle_threshold = max(noise_floor * 3.0, val * 0.05)
    e = e_padded
    while e > e_padded - pad and combined[e] <= settle_threshold:
        e -= 1
    e = max(e, best[1])  # never trim back past the burst's own un-padded end

    # best-effort sub-phase split: initial movement = first third of the
    # pre-peak region, primary motion = rest of pre-peak through a short
    # tail past peak, follow-through = remainder. This is NOT a validated
    # phase detector -- it's a coarse, explicitly-labeled heuristic split
    # for coaching-evidence presentation (Phase G), not for any
    # measurement that needs frame-accurate boundaries.
    pre_peak_span = max(1, peak - s)
    initial_end = s + pre_peak_span // 3
    primary_end = min(e, peak + max(3, (e - peak) // 3))

    return SwingWindow(
        WindowStatus.OBSERVED, start_frame=s, end_frame=e,
        initial_movement_end=initial_end, primary_motion_end=primary_end,
        candidate_bursts=candidate_list,
        evidence_note=(
            f"single dominant combined-motion burst, peak energy {val:.3f} at frame {peak}"
            + (f"; next-best candidate at {bursts[1][2]} (energy {bursts[1][3]:.3f})" if len(bursts) > 1 else "")
        ),
    )
