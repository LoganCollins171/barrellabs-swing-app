"""BL-008C.3 Phase B -- contact-independent measurements.

Every measurement here is computed over the WHOLE swing window
(engine_v2.window.SwingWindow), never a contact frame -- these are usable
even when contact detection abstains (which, per BL-008C.1, is most of the
time right now). Each documents the OBSERVABLE quantity, not an implied
physical one: e.g. "normalized head displacement over the window" is a real,
directly-measured 2D quantity; it is explicitly NOT "head drift during the
swing" in any validated biomechanical sense, because we don't know where in
the window the swing-relevant motion actually starts/ends without contact.

None of these claim: ground-reaction force, true weight transfer, 3D
rotation, or physical bat speed. All are 2D, image-space, normalized.
"""
from __future__ import annotations

import numpy as np

from engine_v2.types import LandmarkFrame, Measurement, Quality
from engine_v2.window import SwingWindow, WindowStatus


def _get(f: LandmarkFrame, name: str):
    return f.landmarks.get(name) if f.landmarks else None


def _shoulder_width(f: LandmarkFrame) -> float | None:
    ls, rs = _get(f, "left_shoulder"), _get(f, "right_shoulder")
    if not ls or not rs:
        return None
    return float(np.linalg.norm(np.array(ls[:2]) - np.array(rs[:2]))) or None


_SW_INSTABILITY_RATIO = 4.0  # max/min shoulder width across the window beyond this -> DEGRADED, not OK
_IDENTITY_JUMP_RATIO = 1.0  # single-frame displacement beyond this many shoulder-widths -> suspected L/R swap, not real motion


def _detect_identity_jump(frames: list[LandmarkFrame], s: int, e: int, landmark_name: str, sw: float) -> int | None:
    """BL-008C.5 Phase 1 fix: scans a named landmark's frame-to-frame
    displacement for an implausibly large single-step jump (more than
    _IDENTITY_JUMP_RATIO shoulder-widths in one frame). Confirmed on real
    footage (judge_swing.mp4, frame 533): left_ankle.x held at ~0.518 for
    9+ consecutive frames, then both left_ankle.x and right_ankle.x
    transitioned simultaneously toward each other's prior values over
    frames 533-536, landing at essentially the OTHER foot's pre-swap
    position (post: left~0.33/right~0.51 vs pre: left~0.52/right~0.33 --
    a near-exact cross, not a translation). That is mediapipe's left/right
    ankle identity swapping mid-clip, not the body moving -- no human
    ankle covers one full shoulder-width of lateral image displacement in
    a single 50-60fps frame during a stance hold. Returns the frame index
    of the first such jump, or None if none found."""
    if sw is None or sw <= 0:
        return None
    prev = None
    prev_i = None
    for i in range(s, e + 1):
        f = frames[i]
        lm = _get(f, landmark_name)
        if lm is None:
            prev = None
            continue
        pos = np.array(lm[:2])
        if prev is not None:
            step = float(np.linalg.norm(pos - prev))
            if step > _IDENTITY_JUMP_RATIO * sw:
                return i
        prev = pos
        prev_i = i
    return None


def _normalizer_shoulder_width(frames: list[LandmarkFrame], s: int, e: int):
    """BL-008C.4 Phase 1 fix: the original implementation normalized by
    shoulder width measured at a SINGLE frame (the first one in the window
    where both shoulders were tracked). Diagnosed and confirmed wrong:
    shoulder width is NOT stable across a swing window -- it varies with
    body/trunk rotation toward or away from the camera, independent of any
    tracking error. Measured directly on both accepted pilot clips:
      spencer_torkelson_swing.mp4 window 1579-1697: shoulder width ranges
        0.0072-0.0939 (13x), with the FIRST frame (0.0154) near the low
        end -- using it as the sole normalizer inflated every distance
        measurement in that window by roughly 5x relative to using a
        robust central value.
      judge_swing.mp4 (portrait) window 506-544: shoulder width ranges
        0.0069-0.0858 (12x) -- and here the FIRST frame (0.0825) is near
        the HIGH end, the opposite bias -- confirming this isn't simply
        "first frame is always smallest," it's that single-frame
        normalization is unreliable regardless of which frame is picked.
    Fix: normalize by the MEDIAN shoulder width across the whole window
    (robust to the rotation extremes at either end), and report the
    instability ratio so callers can downgrade quality when it's large
    rather than silently trusting an unstable normalizer.
    Returns (median_width_or_None, instability_ratio_or_None, n_samples).
    """
    vals = []
    for i in range(s, e + 1):
        sw = _shoulder_width(frames[i])
        if sw is not None and sw > 0:
            vals.append(sw)
    if not vals:
        return None, None, 0
    arr = np.array(vals)
    median = float(np.median(arr))
    ratio = float(arr.max() / arr.min()) if arr.min() > 0 else float("inf")
    return median, ratio, len(vals)


def _coverage_pct(frames: list[LandmarkFrame], s: int, e: int, required: list[str]) -> float:
    total = e - s + 1
    if total <= 0:
        return 0.0
    present = 0
    for i in range(s, e + 1):
        f = frames[i]
        if f.landmarks and all(f.landmarks.get(r) for r in required):
            present += 1
    return present / total * 100.0


def _base(name, definition, units, coord, required, s, e):
    return dict(name=name, definition=definition, units=units, coordinate_frame=coord,
                required_observations=required, source_frame_interval=(s, e))


def head_displacement_over_window(frames: list[LandmarkFrame], window: SwingWindow) -> Measurement:
    name = "head_displacement_normalized_window"
    definition = ("Total path length traced by the ear-midpoint landmark across the full "
                  "swing window, normalized by the MEDIAN shoulder width across the window "
                  "(fixed in BL-008C.4 -- see _normalizer_shoulder_width). An observable 2D "
                  "head-stability proxy, not a validated balance metric.")
    base = _base(name, definition, "ratio (shoulder-widths)", "image-space 2D, normalized",
                 ["left_ear", "right_ear", "left_shoulder", "right_shoulder"],
                 window.start_frame, window.end_frame)
    if window.status != WindowStatus.OBSERVED:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"swing window not observed: {window.rejection_reason}", **base)
    s, e = window.start_frame, window.end_frame
    sw, sw_ratio, sw_n = _normalizer_shoulder_width(frames, s, e)
    path = 0.0
    prev = None
    missing = 0
    for i in range(s, e + 1):
        f = frames[i]
        le, re = _get(f, "left_ear"), _get(f, "right_ear")
        if not le or not re:
            missing += 1
            prev = None
            continue
        pos = np.array([(le[0] + re[0]) / 2, (le[1] + re[1]) / 2])
        if prev is not None:
            path += float(np.linalg.norm(pos - prev))
        prev = pos
    total = e - s + 1
    missing_pct = missing / total * 100.0 if total else 100.0
    if sw is None or sw <= 0:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason="shoulder width unavailable for normalization", **base)
    if missing_pct > 40.0:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"ears untracked in {missing_pct:.0f}% of the window", **base)
    notes = []
    quality = Quality.OK
    if missing_pct >= 10.0:
        quality = Quality.DEGRADED
        notes.append(f"ears untracked in {missing_pct:.0f}% of frames")
    if sw_ratio is not None and sw_ratio > _SW_INSTABILITY_RATIO:
        quality = Quality.DEGRADED
        notes.append(f"shoulder-width normalizer varied {sw_ratio:.1f}x across the window (n={sw_n}) -- "
                     f"used the median as a robust reference, but this much variation (likely trunk "
                     f"rotation toward/away from camera) means the normalization itself is less reliable")
    return Measurement(value=path / sw, quality=quality, uncertainty_note="; ".join(notes), **base)


def trunk_inclination_range(frames: list[LandmarkFrame], window: SwingWindow) -> Measurement:
    name = "trunk_inclination_range_degrees_window"
    definition = ("Range (max - min) of the trunk-lean angle over the window, where trunk-lean "
                  "is the angle between the hip-midpoint-to-shoulder-midpoint vector and vertical "
                  "(image-space). An observable apparent-posture-change proxy -- NOT validated "
                  "3D spine angle; camera roll or tilt directly biases this measurement.")
    base = _base(name, definition, "degrees", "image-space 2D angle from vertical",
                 ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
                 window.start_frame, window.end_frame)
    if window.status != WindowStatus.OBSERVED:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"swing window not observed: {window.rejection_reason}", **base)
    s, e = window.start_frame, window.end_frame
    angles = []
    for i in range(s, e + 1):
        f = frames[i]
        ls, rs, lh, rh = _get(f, "left_shoulder"), _get(f, "right_shoulder"), _get(f, "left_hip"), _get(f, "right_hip")
        if not all([ls, rs, lh, rh]):
            continue
        sx, sy = (ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2
        hx, hy = (lh[0] + rh[0]) / 2, (lh[1] + rh[1]) / 2
        angle = np.degrees(np.arctan2(sx - hx, hy - sy))  # 0 = perfectly vertical
        angles.append(angle)
    coverage = _coverage_pct(frames, s, e, ["left_shoulder", "right_shoulder", "left_hip", "right_hip"])
    if not angles or coverage < 60.0:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"shoulders/hips tracked in only {coverage:.0f}% of the window", **base)
    quality = Quality.OK if coverage >= 90.0 else Quality.DEGRADED
    note = "" if quality == Quality.OK else f"shoulders/hips tracked in {coverage:.0f}% of the window"
    return Measurement(value=float(max(angles) - min(angles)), quality=quality, uncertainty_note=note, **base)


def knee_flexion_range(frames: list[LandmarkFrame], window: SwingWindow, side: str) -> Measurement:
    name = f"{side}_knee_flexion_range_degrees_window"
    definition = (f"Range of the {side} knee angle (hip-knee-ankle) over the window, in degrees "
                  "from full extension (180deg). An observable 2D joint-configuration proxy; "
                  "perspective foreshortening biases the true flexion angle in either direction "
                  "depending on camera position relative to that leg.")
    base = _base(name, definition, "degrees", "image-space 2D joint angle",
                 [f"{side}_hip", f"{side}_knee", f"{side}_ankle"], window.start_frame, window.end_frame)
    if window.status != WindowStatus.OBSERVED:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"swing window not observed: {window.rejection_reason}", **base)
    s, e = window.start_frame, window.end_frame
    angles = []
    for i in range(s, e + 1):
        f = frames[i]
        hip, knee, ankle = _get(f, f"{side}_hip"), _get(f, f"{side}_knee"), _get(f, f"{side}_ankle")
        if not all([hip, knee, ankle]):
            continue
        v1 = np.array(hip[:2]) - np.array(knee[:2])
        v2 = np.array(ankle[:2]) - np.array(knee[:2])
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        cos_a = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
        angles.append(np.degrees(np.arccos(cos_a)))
    coverage = _coverage_pct(frames, s, e, [f"{side}_hip", f"{side}_knee", f"{side}_ankle"])
    if not angles or coverage < 60.0:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"{side} leg tracked in only {coverage:.0f}% of the window", **base)
    quality = Quality.OK if coverage >= 90.0 else Quality.DEGRADED
    note = "" if quality == Quality.OK else f"{side} leg tracked in {coverage:.0f}% of the window"
    return Measurement(value=float(max(angles) - min(angles)), quality=quality, uncertainty_note=note, **base)


def stride_foot_displacement(frames: list[LandmarkFrame], window: SwingWindow, side: str) -> Measurement:
    name = f"{side}_ankle_displacement_normalized_window"
    definition = (f"Net displacement of the {side} ankle from the window's first to last tracked "
                  "position, normalized by the MEDIAN shoulder width across the window (fixed in "
                  "BL-008C.4 -- see _normalizer_shoulder_width docstring; a single-frame normalizer "
                  "was confirmed unstable). An observable stride/footwork proxy -- does not "
                  "distinguish stride foot from pivot foot; caller must interpret both sides and "
                  "judge which moved more.")
    base = _base(name, definition, "ratio (shoulder-widths)", "image-space 2D, normalized",
                 [f"{side}_ankle", "left_shoulder", "right_shoulder"], window.start_frame, window.end_frame)
    if window.status != WindowStatus.OBSERVED:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"swing window not observed: {window.rejection_reason}", **base)
    s, e = window.start_frame, window.end_frame
    sw, sw_ratio, sw_n = _normalizer_shoulder_width(frames, s, e)
    if sw is None:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"{side} ankle or shoulder-width normalizer not tracked in the window", **base)
    jump_frame = _detect_identity_jump(frames, s, e, f"{side}_ankle", sw)
    if jump_frame is not None:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=(
                                f"suspected left/right ankle identity swap at frame {jump_frame} "
                                f"(single-frame displacement exceeded {_IDENTITY_JUMP_RATIO:.1f}x shoulder-width "
                                f"-- see BL-008C.5 _detect_identity_jump docstring); refusing to report a "
                                f"net displacement that may span a label swap rather than real motion"
                            ), **base)
    first_ank = last_ank = None
    for i in range(s, e + 1):
        f = frames[i]
        ank = _get(f, f"{side}_ankle")
        if ank is None:
            continue
        if first_ank is None:
            first_ank = ank
        last_ank = ank
    if first_ank is None or last_ank is None:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"{side} ankle not tracked in the window", **base)
    disp = float(np.linalg.norm(np.array(first_ank[:2]) - np.array(last_ank[:2])))
    quality = Quality.OK
    note = ""
    if sw_ratio is not None and sw_ratio > _SW_INSTABILITY_RATIO:
        quality = Quality.DEGRADED
        note = (f"shoulder-width normalizer varied {sw_ratio:.1f}x across the window (n={sw_n}) -- "
                f"used the median as a robust reference, but this much variation means the "
                f"normalization itself is less reliable")
    return Measurement(value=disp / sw, quality=quality, uncertainty_note=note, **base)


def hand_to_torso_distance_range(frames: list[LandmarkFrame], window: SwingWindow) -> Measurement:
    name = "hand_to_torso_distance_range_normalized_window"
    definition = ("Range (max - min) of the distance from the bat-hand wrist (majority-vote side, "
                  "same selection as engine_v2.segment) to the torso center (shoulder-hip "
                  "midpoint), normalized by the MEDIAN shoulder width across the window (fixed in "
                  "BL-008C.4). An observable extension-range proxy.")
    base = _base(name, definition, "ratio (shoulder-widths)", "image-space 2D, normalized",
                 ["left_wrist", "right_wrist", "left_shoulder", "right_shoulder", "left_hip", "right_hip"],
                 window.start_frame, window.end_frame)
    if window.status != WindowStatus.OBSERVED:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"swing window not observed: {window.rejection_reason}", **base)
    from engine_v2.segment import _choose_fixed_bat_hand_side
    s, e = window.start_frame, window.end_frame
    side = _choose_fixed_bat_hand_side(frames, s, e)
    sw, sw_ratio, sw_n = _normalizer_shoulder_width(frames, s, e)
    # BL-008C.5: TRIED applying the same single-frame-jump guard used for
    # stride_foot_displacement here (the same identity-swap risk exists in
    # principle). REVERTED after testing against real footage: on
    # spencer_torkelson_swing.mp4 it fired at frame 1626, 4 frames from the
    # visually-confirmed-correct contact-area frame (1630) -- i.e. it
    # caught genuine peak bat-hand speed near contact, not a tracking
    # error, and incorrectly suppressed a measurement that was previously
    # computing fine. The wrist moves far faster than an ankle during a
    # real swing, so the ankle-tuned 1.0x-shoulder-width/frame threshold
    # is wrong for this landmark. Not fixing with a re-tuned threshold
    # this session (would need real wrist-speed-distribution data to pick
    # one responsibly, not a guess) -- documented as a known, deliberately
    # NOT-implemented gap rather than shipped as a guess.
    dists = []
    for i in range(s, e + 1):
        f = frames[i]
        w = _get(f, f"{side}_wrist")
        ls, rs, lh, rh = _get(f, "left_shoulder"), _get(f, "right_shoulder"), _get(f, "left_hip"), _get(f, "right_hip")
        if not w or not all([ls, rs, lh, rh]):
            continue
        cx = (ls[0] + rs[0] + lh[0] + rh[0]) / 4
        cy = (ls[1] + rs[1] + lh[1] + rh[1]) / 4
        dists.append(float(np.linalg.norm(np.array(w[:2]) - np.array([cx, cy]))))
    coverage = _coverage_pct(frames, s, e, [f"{side}_wrist", "left_shoulder", "right_shoulder", "left_hip", "right_hip"])
    if not dists or not sw or coverage < 60.0:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason=f"bat-hand wrist or torso landmarks tracked in only {coverage:.0f}% of the window", **base)
    notes = []
    quality = Quality.OK
    if coverage < 90.0:
        quality = Quality.DEGRADED
        notes.append(f"tracked in {coverage:.0f}% of the window")
    if sw_ratio is not None and sw_ratio > _SW_INSTABILITY_RATIO:
        quality = Quality.DEGRADED
        notes.append(f"shoulder-width normalizer varied {sw_ratio:.1f}x across the window (n={sw_n})")
    return Measurement(value=float((max(dists) - min(dists)) / sw), quality=quality, uncertainty_note="; ".join(notes), **base)


def all_window_measurements(frames: list[LandmarkFrame], window: SwingWindow) -> list[Measurement]:
    """Convenience bundle -- every Phase-B measurement implemented so far,
    for a single clip's window. Repeated-swing consistency (Phase B item 9)
    is NOT a per-clip measurement and would need to operate across multiple
    clips' results -- NOT implemented as of BL-008C.4 despite an earlier
    comment here claiming it lived in a engine_v2/repeatability.py module;
    that module does not exist. Correcting the record rather than leaving
    a reference to code that was never written."""
    out = [
        head_displacement_over_window(frames, window),
        trunk_inclination_range(frames, window),
        hand_to_torso_distance_range(frames, window),
    ]
    for side in ("left", "right"):
        out.append(knee_flexion_range(frames, window, side))
        out.append(stride_foot_displacement(frames, window, side))
    return out
