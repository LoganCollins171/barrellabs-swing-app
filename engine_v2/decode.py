"""BL-008C Phase 1 -- video decoding and presentation-timestamp analysis.

Reads actual per-frame presentation timestamps (PTS) via OpenCV's
CAP_PROP_POS_MSEC at each read() -- not an assumed nominal frame rate.
Detects duplicate/near-duplicate frames and reports frame-rate
disagreement as a DESCRIPTIVE signal, not proof of anything by itself
(per BL-008C instruction: "Do not treat average-versus-declared frame-rate
disagreement alone as proof of variable-frame-rate capture").
"""
from __future__ import annotations

import subprocess
import json as _json

import cv2
import numpy as np

from engine_v2.types import FrameTimingReport

_DUP_FRAME_DIFF_THRESHOLD = 2.0   # mean abs pixel diff (0-255 scale, downscaled gray) below which frames count as "near-duplicate"


def _ffprobe_declared_fps(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    data = _json.loads(out.stdout)
    num, den = data["streams"][0]["r_frame_rate"].split("/")
    return float(num) / float(den)


def decode_with_timing(path: str, max_frames: int | None = None) -> tuple[list[np.ndarray], list[float], FrameTimingReport]:
    """Returns (frames_bgr, pts_seconds, timing_report).

    frames_bgr are full-resolution BGR arrays (caller downsamples for pose
    extraction if desired). pts_seconds[i] is the real presentation
    timestamp OpenCV reports for frame i, in seconds -- this is what every
    downstream module must use for "when did this happen", never
    frame_index / nominal_fps.
    """
    declared_fps = _ffprobe_declared_fps(path)

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {path}")

    frames: list[np.ndarray] = []
    pts: list[float] = []
    prev_gray_small = None
    dup_count = 0
    notes: list[str] = []

    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
        pts.append(t_ms / 1000.0)
        frames.append(frame)

        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
        if prev_gray_small is not None:
            diff = float(np.mean(np.abs(small.astype(np.int16) - prev_gray_small.astype(np.int16))))
            if diff < _DUP_FRAME_DIFF_THRESHOLD:
                dup_count += 1
        prev_gray_small = small

        i += 1
        if max_frames and i >= max_frames:
            notes.append(f"decode truncated at max_frames={max_frames}")
            break
    cap.release()

    frame_count = len(frames)
    duration_s = (pts[-1] - pts[0]) if len(pts) >= 2 else 0.0
    actual_avg_fps = (frame_count - 1) / duration_s if duration_s > 0 else 0.0
    disagreement_pct = (
        abs(declared_fps - actual_avg_fps) / declared_fps * 100.0 if declared_fps > 0 else 0.0
    )
    dup_pct = (dup_count / max(frame_count - 1, 1)) * 100.0

    # Per instruction: disagreement alone is NOT proof of VFR. We report both
    # signals and only flag "likely_edited_playback" when disagreement is
    # large AND duplicate-frame rate is also elevated -- i.e. two independent
    # signals pointing the same way, not one threshold on one number.
    likely_edited = disagreement_pct > 10.0 and dup_pct > 50.0
    if disagreement_pct > 1.5 and not likely_edited:
        notes.append(
            f"declared/actual fps disagree by {disagreement_pct:.1f}% but duplicate-frame rate "
            f"({dup_pct:.1f}%) doesn't independently corroborate edited playback -- "
            "treating as an open question, not a conclusion."
        )

    report = FrameTimingReport(
        container_declared_fps=declared_fps,
        actual_avg_fps=actual_avg_fps,
        fps_disagreement_pct=disagreement_pct,
        frame_count=frame_count,
        duration_s=duration_s,
        pts_available=len(pts) >= 2 and pts[-1] > 0,
        duplicate_frame_pct=dup_pct,
        likely_edited_playback=likely_edited,
        original_capture_rate_known=False,  # never assumed true; set by caller only with independent evidence
        notes=notes,
    )
    return frames, pts, report
