"""BL-008C Phase 1 -- pipeline orchestration.

Ties decode -> pose -> segment -> measure -> serialize together into one
ReferenceRecordV2. This is the only module that decides acceptance: a
processed clip is NOT automatically admitted to the comparison library
(explicit Phase-1/3 requirement) -- accepted=False with a reason is a
normal, expected outcome, not a failure of the pipeline.
"""
from __future__ import annotations

import hashlib
import os
import time

from engine_v2 import decode, pose, segment as segment_mod, camera, measure, measure_window, window as window_mod
from engine_v2.types import ReferenceRecordV2, SwingEvents, Measurement, Quality, SCHEMA_VERSION

_MIN_TRACKING_QUALITY_PCT = 60.0   # frames_with_pose / total below this -> reject

# BL-008C.7: contact-dependent acceptance is disabled until a human
# reviewer independently re-validates the contact signal on real footage
# and flips this deliberately. Do NOT flip this because tests pass or
# because the pipeline runs without error -- it must be flipped on the
# basis of frame-linked evidence that the estimates are actually correct,
# the same standard applied in validation/BL008C7_REALWORLD_VALIDATION.md.
_CONTACT_DETECTION_VALIDATED = False
_MIN_SEGMENT_ENERGY = 1e-4          # degenerate-segment guard


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def process_clip(path: str, player_name: str | None = None, handedness: str | None = None,
                  max_frames: int | None = None) -> ReferenceRecordV2:
    t0 = time.time()
    notes: list[str] = []

    frames_bgr, pts, timing_report = decode.decode_with_timing(path, max_frames=max_frames)
    notes.append(f"decode: {timing_report.frame_count} frames, {timing_report.duration_s:.2f}s, "
                 f"declared {timing_report.container_declared_fps:.1f}fps / actual {timing_report.actual_avg_fps:.1f}fps")

    landmark_frames, tracking_report = pose.extract_landmarks(frames_bgr)
    for i, lf in enumerate(landmark_frames):
        lf.pts_s = pts[i] if i < len(pts) else None
    notes.append(f"pose: {tracking_report.frames_with_pose}/{tracking_report.total_frames} frames tracked, "
                 f"mean visibility {tracking_report.mean_visibility:.2f}")

    seg = segment_mod.find_primary_swing_segment(landmark_frames)
    events = segment_mod.detect_events(landmark_frames, seg)
    cam_class, cam_conf = camera.classify_camera_view(landmark_frames, seg)
    notes.append(f"segment: frames {seg.start_frame}-{seg.end_frame}, peak@{seg.peak_motion_frame}, "
                 f"energy={seg.motion_energy_score:.4f}, camera={cam_class.value} (conf {cam_conf:.2f})")

    measurements: list[Measurement] = [
        measure.head_displacement_normalized(landmark_frames, events),
        measure.hand_path_length_normalized(landmark_frames, events),
        measure.phase_relative_timing(events, timing_report),
        *measure.real_time_ms_measurements(events, timing_report),
    ]

    tracked_pct = (tracking_report.frames_with_pose / tracking_report.total_frames * 100.0) if tracking_report.total_frames else 0.0
    accepted = True
    rejection_reason = None
    if tracked_pct < _MIN_TRACKING_QUALITY_PCT:
        accepted = False
        rejection_reason = f"pose tracking coverage {tracked_pct:.0f}% below {_MIN_TRACKING_QUALITY_PCT:.0f}% minimum"
    elif seg.motion_energy_score < _MIN_SEGMENT_ENERGY:
        accepted = False
        rejection_reason = "no usable motion signal found -- segmentation degenerate"
    elif events.contact_frame is None or events.foot_plant_frame is None:
        accepted = False
        cc = events.contact_candidate
        if cc is not None and cc.status.value in ("ambiguous", "not_observable"):
            rejection_reason = f"contact event {cc.status.value}: {cc.failure_reason or cc.evidence_note}"
        else:
            rejection_reason = "required swing events (foot_plant, contact) could not be located"
    elif cam_class.value == "front":
        accepted = False
        rejection_reason = "camera classified as front-on -- not usable for side-plane 2D measurements"
    elif not _CONTACT_DETECTION_VALIDATED:
        # BL-008C.7 policy revision: independently inspected all 4 of the
        # private-footage clips the engine had marked accepted=True (i.e.
        # events.contact_frame was non-None and passed every check above).
        # Frame-linked review against the actual video found 0 of 4
        # confirmed correct: 3 were deep follow-through (ball already off
        # the tee, body already rotated well past the hitting zone) and 1
        # was PLAUSIBLE but not independently confirmable as the exact
        # contact instant. This matches the same late-estimate bias
        # documented since BL-008C.1 -- it is not limited to the MLB
        # library's compromised footage. "Found a candidate frame" (which
        # is all `events.contact_frame is not None` actually verifies) is
        # not the same claim as "found the correct frame", and this gate
        # was conflating the two. Per explicit instruction ("If the
        # accepted estimates are not supported, revise the contact
        # acceptance policy"): accepted is now FALSE for the
        # contact-dependent path unconditionally, regardless of whether an
        # estimate was found, until a contact signal is independently
        # verified on a real, frame-linked sample and this flag is
        # deliberately flipped back by a human reviewer -- not by a test
        # suite passing. See validation/BL008C7_REALWORLD_VALIDATION.md
        # Phase 4 for the full frame-linked evidence (all 4 clips).
        accepted = False
        rejection_reason = (
            "contact-dependent acceptance disabled pending independent validation (BL-008C.7): "
            "0 of 4 previously-accepted real-footage contact estimates were confirmed correct on "
            "frame-linked review -- see validation/BL008C7_REALWORLD_VALIDATION.md"
        )

    # BL-008C.3 Phase A/B: contact-INDEPENDENT window + measurements,
    # computed regardless of the contact-dependent `accepted` outcome
    # above -- this is the whole point of this milestone: a clip whose
    # contact detection correctly abstains can still produce useful,
    # defensible measurements via this path.
    swing_window = window_mod.find_swing_window(landmark_frames)
    window_measurements = measure_window.all_window_measurements(landmark_frames, swing_window)
    window_accepted = (
        swing_window.status == window_mod.WindowStatus.OBSERVED
        and tracked_pct >= _MIN_TRACKING_QUALITY_PCT
        and cam_class.value != "front"
    )
    window_rejection_reason = None
    if not window_accepted:
        if swing_window.status != window_mod.WindowStatus.OBSERVED:
            window_rejection_reason = f"window {swing_window.status}: {swing_window.rejection_reason}"
        elif tracked_pct < _MIN_TRACKING_QUALITY_PCT:
            window_rejection_reason = f"pose tracking coverage {tracked_pct:.0f}% below {_MIN_TRACKING_QUALITY_PCT:.0f}% minimum"
        elif cam_class.value == "front":
            window_rejection_reason = "camera classified as front-on -- not usable for side-plane 2D measurements"
    notes.append(f"window: status={swing_window.status} "
                 f"{swing_window.start_frame}-{swing_window.end_frame}" if swing_window.status == "observed" else
                 f"window: status={swing_window.status} ({swing_window.rejection_reason})")

    record = ReferenceRecordV2(
        schema_version=SCHEMA_VERSION,
        source_file=os.path.basename(path),
        source_sha256=_sha256(path),
        player_name=player_name,
        handedness=handedness,
        camera_class=cam_class,
        camera_class_confidence=cam_conf,
        timing=timing_report,
        tracking=tracking_report,
        segment=seg,
        events=events,
        measurements=measurements,
        processing_notes=notes + [f"processing_time_s={time.time() - t0:.1f}"],
        accepted=accepted,
        rejection_reason=rejection_reason,
        window=swing_window.to_dict(),
        window_measurements=window_measurements,
        window_accepted=window_accepted,
        window_rejection_reason=window_rejection_reason,
    )
    return record
