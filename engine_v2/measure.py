"""BL-008C Phase 1 -- biomechanical measurement, with explicit suppression.

Every function here returns a Measurement, never a bare float. If the
required landmarks are missing, or real-world time is required but not
independently verified, the Measurement is returned with
quality=SUPPRESSED and a stated reason -- never a silently-substituted
zero (explicit Phase-1 requirement).

Only 2D, phase-relative or normalized-pixel measurements are attempted
here. No 3D angles, no bat speed, no exit velocity -- none of those are
recoverable from ordinary single-camera 2D video without an independently
validated method, which this engine does not have.
"""
from __future__ import annotations

import numpy as np

from engine_v2.types import LandmarkFrame, SwingEvents, Measurement, Quality, FrameTimingReport


def _lm(frames: list[LandmarkFrame], idx: int, name: str):
    if idx is None or idx < 0 or idx >= len(frames):
        return None
    f = frames[idx]
    if f.landmarks is None:
        return None
    return f.landmarks.get(name)


def head_displacement_normalized(frames, events: SwingEvents) -> Measurement:
    name = "head_displacement_normalized_plant_to_contact"
    definition = (
        "Euclidean displacement of the ear-midpoint landmark between "
        "foot-plant and contact, normalized by shoulder width at foot-plant "
        "(so it's comparable across different camera distances)."
    )
    base = dict(
        name=name, definition=definition, units="ratio (shoulder-widths)",
        coordinate_frame="image-space 2D, normalized [0,1]x[0,1]",
        required_observations=["left_ear", "right_ear", "left_shoulder", "right_shoulder"],
        source_frame_interval=(events.foot_plant_frame, events.contact_frame)
        if events.foot_plant_frame is not None and events.contact_frame is not None else None,
    )
    if events.foot_plant_frame is None or events.contact_frame is None:
        return Measurement(value=None, quality=Quality.SUPPRESSED,
                            uncertainty_note="", suppression_reason="foot_plant or contact event not detected",
                            **base)

    le0 = _lm(frames, events.foot_plant_frame, "left_ear")
    re0 = _lm(frames, events.foot_plant_frame, "right_ear")
    ls0 = _lm(frames, events.foot_plant_frame, "left_shoulder")
    rs0 = _lm(frames, events.foot_plant_frame, "right_shoulder")
    le1 = _lm(frames, events.contact_frame, "left_ear")
    re1 = _lm(frames, events.contact_frame, "right_ear")
    if not all([le0, re0, ls0, rs0, le1, re1]):
        return Measurement(value=None, quality=Quality.SUPPRESSED,
                            uncertainty_note="", suppression_reason="required landmarks not visible at plant and/or contact",
                            **base)

    ear0 = np.array([(le0[0] + re0[0]) / 2, (le0[1] + re0[1]) / 2])
    ear1 = np.array([(le1[0] + re1[0]) / 2, (le1[1] + re1[1]) / 2])
    shoulder_width = float(np.linalg.norm(np.array(ls0[:2]) - np.array(rs0[:2]))) or 1e-6
    disp = float(np.linalg.norm(ear1 - ear0)) / shoulder_width

    vis_min = min(le0[2], re0[2], ls0[2], rs0[2], le1[2], re1[2])
    quality = Quality.OK if vis_min >= 0.5 else Quality.DEGRADED
    note = "" if quality == Quality.OK else f"low landmark visibility ({vis_min:.2f}) at one or both endpoints"
    return Measurement(value=disp, quality=quality, uncertainty_note=note, **base)


def hand_path_length_normalized(frames, events: SwingEvents) -> Measurement:
    name = "hand_path_length_normalized_plant_to_contact"
    definition = (
        "Total normalized path length traced by the bat-hand wrist landmark "
        "from foot-plant to contact (sum of per-frame displacement), "
        "normalized by shoulder width at foot-plant. A 2D proxy for swing "
        "path length/efficiency -- NOT bat speed (no real-world time or "
        "bat-length calibration available)."
    )
    base = dict(
        name=name, definition=definition, units="ratio (shoulder-widths)",
        coordinate_frame="image-space 2D, normalized [0,1]x[0,1]",
        required_observations=["left_wrist", "right_wrist", "left_shoulder", "right_shoulder"],
        source_frame_interval=(events.foot_plant_frame, events.contact_frame)
        if events.foot_plant_frame is not None and events.contact_frame is not None else None,
    )
    if events.foot_plant_frame is None or events.contact_frame is None:
        return Measurement(value=None, quality=Quality.SUPPRESSED,
                            uncertainty_note="", suppression_reason="foot_plant or contact event not detected",
                            **base)
    s, e = events.foot_plant_frame, events.contact_frame
    if e <= s:
        return Measurement(value=None, quality=Quality.SUPPRESSED,
                            uncertainty_note="", suppression_reason="contact frame not after foot_plant frame",
                            **base)

    ls0 = _lm(frames, s, "left_shoulder")
    rs0 = _lm(frames, s, "right_shoulder")
    if not ls0 or not rs0:
        return Measurement(value=None, quality=Quality.SUPPRESSED,
                            uncertainty_note="", suppression_reason="shoulders not visible at foot_plant for normalization",
                            **base)
    shoulder_width = float(np.linalg.norm(np.array(ls0[:2]) - np.array(rs0[:2]))) or 1e-6

    path = 0.0
    prev = None
    missing_frames = 0
    for i in range(s, e + 1):
        lw = _lm(frames, i, "left_wrist")
        rw = _lm(frames, i, "right_wrist")
        cand = lw if (lw and (not rw or lw[2] >= rw[2])) else rw
        if cand is None:
            missing_frames += 1
            continue
        pos = np.array(cand[:2])
        if prev is not None:
            path += float(np.linalg.norm(pos - prev))
        prev = pos

    total_frames = e - s + 1
    missing_pct = missing_frames / total_frames * 100.0 if total_frames else 100.0
    if missing_pct > 40.0:
        return Measurement(value=None, quality=Quality.SUPPRESSED,
                            uncertainty_note="",
                            suppression_reason=f"bat-hand wrist missing in {missing_pct:.0f}% of the plant-to-contact interval",
                            **base)

    quality = Quality.OK if missing_pct < 10.0 else Quality.DEGRADED
    note = "" if quality == Quality.OK else f"bat-hand wrist missing in {missing_pct:.0f}% of frames in this interval; path length is an undercount"
    return Measurement(value=path / shoulder_width, quality=quality, uncertainty_note=note, **base)


def phase_relative_timing(events: SwingEvents, timing: FrameTimingReport) -> Measurement:
    """Dimensionless phase-relative timing (fraction of the detected swing
    interval spent in load vs downswing) -- deliberately NOT real
    milliseconds, since original physical capture rate is not independently
    verified for these sources (see FrameTimingReport.original_capture_rate_known)."""
    name = "downswing_fraction_of_segment"
    definition = (
        "(contact_frame - foot_plant_frame) / (finish_frame - load_start_frame), "
        "a dimensionless ratio of detected-event frame counts. Phase-relative "
        "only -- does not require knowing the clip's real-world frame rate."
    )
    base = dict(
        name=name, definition=definition, units="ratio [0,1]",
        coordinate_frame="phase-relative (frame counts, this clip's own timebase)",
        required_observations=["foot_plant_frame", "contact_frame", "load_start_frame", "finish_frame"],
        source_frame_interval=(events.load_start_frame, events.finish_frame)
        if events.load_start_frame is not None and events.finish_frame is not None else None,
    )
    needed = [events.load_start_frame, events.foot_plant_frame, events.contact_frame, events.finish_frame]
    if any(v is None for v in needed):
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason="one or more required swing events not detected", **base)
    total = events.finish_frame - events.load_start_frame
    if total <= 0:
        return Measurement(value=None, quality=Quality.SUPPRESSED, uncertainty_note="",
                            suppression_reason="degenerate segment (finish <= load_start)", **base)
    frac = (events.contact_frame - events.foot_plant_frame) / total
    quality = Quality.OK if events.detector_confidence >= 0.5 else Quality.DEGRADED
    note = "" if quality == Quality.OK else "event-detection confidence below 0.5 for this clip -- treat frame boundaries as approximate"
    return Measurement(value=frac, quality=quality, uncertainty_note=note, **base)


def real_time_ms_measurements(events: SwingEvents, timing: FrameTimingReport) -> list[Measurement]:
    """Per explicit Phase-1 instruction: if real-world time cannot be
    established, physical-millisecond measurements MUST remain unavailable.
    We never independently verify original capture rate for these legacy
    source files (no camera metadata, no user-confirmed slow-mo factor),
    so these are always suppressed for this engine version -- shown
    explicitly rather than silently omitted, so a reviewer sees the gap."""
    out = []
    for nm in ["load_duration_ms", "downswing_duration_ms", "total_swing_duration_ms"]:
        out.append(Measurement(
            name=nm,
            definition="Real-world-time duration of a swing phase, in milliseconds.",
            units="ms (real-time)",
            coordinate_frame="n/a",
            required_observations=["independently verified original capture frame rate"],
            value=None,
            quality=Quality.UNAVAILABLE,
            source_frame_interval=None,
            uncertainty_note="",
            suppression_reason=(
                "original physical capture rate is not independently verified for this "
                "source (declared container fps is known to be unreliable for these clips -- "
                "see BL-008A/B); real-world ms must not be guessed from it."
            ),
        ))
    return out
