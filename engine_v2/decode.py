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
    """BL-008C.28/.32: this used to be an unconditional, no-fallback,
    no-timeout `subprocess.run(["ffprobe", ...])` call -- confirmed in a
    real production test (the owner's own authenticated session,
    Experimental Manual Review pathway) to surface as
    `FileNotFoundError: ... 'ffprobe'` when the binary isn't on PATH.

    BL-008C.32 root-cause note: this function's own try/except already
    catches that exact FileNotFoundError and falls back cleanly -- a
    real local reproduction (ffprobe removed from PATH, same repro as
    BL-008C.28) confirms decode completes successfully through this
    exact code. The owner's post-deploy retry showing the identical
    error is therefore most consistent with that retry having reached a
    container/session that predated the redeploy fully taking effect,
    not a residual code defect -- no second, unprotected ffprobe/ffmpeg
    call site exists anywhere else in this module, manual_swing_review.py,
    or engine_v2/ (verified by a full-repo search). packages.txt is also
    fixed in this same change (see that file) so ffmpeg actually
    installs, removing the need for this fallback to activate at all in
    the common case.

    declared_fps is explicitly a DESCRIPTIVE-only value (see this
    module's docstring: "not proof of anything by itself") -- it is
    safe to fall back to an approximate value rather than fail the
    whole decode over it. Now routed through proc_utils.run_subprocess
    (the same shared, timeout-bounded subprocess helper the legacy
    pipeline already uses for its own child process -- BL-008C.32's
    "fix the shared contract" guidance) instead of a second, separate,
    unbounded subprocess.run call -- so a hung/stuck ffprobe process
    can no longer block decode indefinitely either. Falls back to
    OpenCV's own CAP_PROP_FPS (needs no external binary at all) if
    ffprobe is missing, errors, times out, or returns unparseable/
    non-positive data, and to 0.0 (the pre-existing "unknown declared
    fps" sentinel `disagreement_pct` already handles) only if that also
    fails to produce a usable value. Never raises."""
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
