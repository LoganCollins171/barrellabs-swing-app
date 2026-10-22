"""BL-008C Phase 1 -- video decoding and presentation-timestamp analysis.

Reads actual per-frame presentation timestamps (PTS) via OpenCV's
CAP_PROP_POS_MSEC at each read() -- not an assumed nominal frame rate.
Detects duplicate/near-duplicate frames and reports frame-rate
disagreement as a DESCRIPTIVE signal, not proof of anything by itself
(per BL-008C instruction: "Do not treat average-versus-declared frame-rate
disagreement alone as proof of variable-frame-rate capture").
"""
from __future__ import annotations

import json as _json

import cv2
import numpy as np

from engine_v2.types import FrameTimingReport
from proc_utils import run_subprocess

_DUP_FRAME_DIFF_THRESHOLD = 2.0   # mean abs pixel diff (0-255 scale, downscaled gray) below which frames count as "near-duplicate"

# BL-008C.32: bounds the ffprobe subprocess the same way proc_utils already
# bounds the pose-detection subprocess (run_subprocess's own DEFAULT_TIMEOUT_S
# is 180s, sized for pose detection on a slow clip -- ffprobe reading one
# stream's frame rate needs nowhere near that long on any real clip; a short,
# separate bound keeps a stuck/hung ffprobe invocation from blocking decode
# indefinitely without over-bounding the (much heavier) pose step elsewhere).
_FFPROBE_TIMEOUT_S = 10


def _ffprobe_declared_fps(path: str, cap: cv2.VideoCapture | None = None) -> float:
    """Container-declared frame rate, for the DESCRIPTIVE disagreement signal
    only (see this module's docstring). Never raises.

    BL-008C.28: this was an unconditional, no-fallback, no-timeout
    `subprocess.run(["ffprobe", ...])`; with no ffprobe on PATH it surfaced as
    `FileNotFoundError: ... 'ffprobe'` (the owner's production report, before
    9cae39c). This is the ONLY ffprobe/ffmpeg invocation in the app, and no
    installed Python package calls ffprobe (BL-008C.32 audit), so the
    experimental path needs no external binary at all -- verified end to end
    through the real review page with ffprobe absent
    (tests/test_experimental_path_integration.py), which also reproduces the
    owner's exact error message when the pre-9cae39c version of this function
    is swapped back in.

    BL-008C.32: routed through proc_utils.run_subprocess (the same
    timeout-bounded helper the legacy pipeline uses) with a short bound, so a
    hung ffprobe can't block decode; a non-zero exit, timeout, malformed
    output or non-positive rate all fall back to OpenCV's CAP_PROP_FPS, then
    to 0.0 (the existing "unknown" sentinel disagreement_pct handles)."""
    try:
        rc, out, _err = run_subprocess(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "json", path],
            cwd=".", timeout=_FFPROBE_TIMEOUT_S,
        )
        if rc != 0:
            raise RuntimeError(f"ffprobe exited {rc}")
        data = _json.loads(out)
        num, den = data["streams"][0]["r_frame_rate"].split("/")
        parsed = float(num) / float(den)
        if parsed <= 0:
            raise ValueError("ffprobe reported a non-positive frame rate")
        return parsed
    except Exception:
        try:
            opencv_fps = (cap or cv2.VideoCapture(path)).get(cv2.CAP_PROP_FPS)
            return float(opencv_fps) if opencv_fps and opencv_fps > 0 else 0.0
        except Exception:
            return 0.0


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
        if is_near_duplicate(small, prev_gray_small):
            dup_count += 1
        prev_gray_small = small

        i += 1
        if max_frames and i >= max_frames:
            notes.append(f"decode truncated at max_frames={max_frames}")
            break
    cap.release()

    report = build_timing_report(declared_fps, pts, dup_count, notes)
    return frames, pts, report


def is_near_duplicate(small_gray: np.ndarray, prev_small_gray: np.ndarray | None) -> bool:
    """Near-duplicate test on 160x90 grayscale thumbnails (shared by every
    decoder in engine_v2 so they all count duplicates the same way)."""
    if prev_small_gray is None:
        return False
    diff = float(np.mean(np.abs(small_gray.astype(np.int16) - prev_small_gray.astype(np.int16))))
    return diff < _DUP_FRAME_DIFF_THRESHOLD


def build_timing_report(declared_fps: float, pts: list[float], dup_count: int,
                        notes: list[str] | None = None) -> FrameTimingReport:
    """BL-008C.36: the FrameTimingReport math, shared by decode_with_timing
    and engine_v2.review_prep so both describe a clip's timing identically."""
    notes = list(notes or [])
    frame_count = len(pts)
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
    return report
