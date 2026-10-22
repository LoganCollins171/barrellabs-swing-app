"""BL-008C.36 -- prepare an uploaded clip for the visual swing-selection page.

ONE decode pass produces everything the page needs:
  * pose landmarks for every frame (what the experimental analysis measures),
  * the real presentation timestamp of every frame (never an assumed fps),
  * small JPEG previews of the frames, indexed by the SAME frame numbers, so
    the picture a user scrubs to is exactly the frame their selection uses.

It runs in a CHILD PROCESS (same pattern the legacy pipeline uses for
detect_phases.py). The old in-process decode held every full-resolution frame
in the web server's memory (911 MB peak for a 3.4 s 1080x1920 iPhone clip)
and ran the CPU-heavy decode + pose tracking on the server's own cores while
the browser waited on an unchanged page -- the likely cause of the reported
production white screen. Here:
  * the server process stays responsive (it only polls a progress file),
  * frames are streamed one at a time (only small previews are kept), so
    memory is bounded -- while pose tracking still sees the exact
    full-resolution frames it always did (identical analysis results),
  * thread use is capped, so the child can't starve the server on a small
    Streamlit Cloud container,
  * if the child dies (out of memory, crash, timeout) the page gets a clear,
    actionable message instead of a dead or white screen.

Needs no ffmpeg/ffprobe binaries (OpenCV's own decoder reads MOV/HEVC/MP4;
the declared-fps probe in decode.py falls back to OpenCV when ffprobe is
absent, which is how production runs).

Run as a child:  python -m engine_v2.review_prep <video_path> <out_dir>
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PREVIEW_LONG_SIDE = 480     # what the browser shows
PREVIEW_JPEG_QUALITY = 68
PREVIEW_MAX_FRAMES = 240    # longer clips get every k-th frame as a preview (selected frame numbers stay exact)
MAX_FRAMES = int(os.environ.get("BL_REVIEW_MAX_FRAMES", "900"))   # ~15 s at 60 fps; longer clips are refused clearly
CHILD_TIMEOUT_S = 240
CHILD_THREADS = 2

# Friendly, user-facing messages keyed by the child's error code.
USER_MESSAGES = {
    "cannot_open": "We couldn't open this video file. Try exporting it again from your phone as MP4 or MOV, "
                   "then upload it again.",
    "no_frames": "We couldn't read any frames from this video. It may be damaged or in an unusual format. "
                 "Try exporting it again from your phone as MP4 or MOV.",
    "too_long": "This clip is longer than we can review here (about 15 seconds max). Trim it to just your "
                "swing -- 3 to 8 seconds is ideal -- and upload it again.",
    "timeout": "Preparing this video took too long. Try a shorter clip (3 to 8 seconds), or try again in a "
               "minute.",
    "killed": "Preparing this video ran out of resources on our server. Try a shorter or lower-resolution "
              "clip (3 to 8 seconds, 1080p or less).",
    "crashed": "Something went wrong while preparing this video. Try again, or upload a different clip.",
}


@dataclasses.dataclass
class ReviewPrep:
    """Everything the review page needs. `pts` are seconds since the first
    frame, one per decoded frame; `previews` is a list of (frame_index,
    jpeg_bytes) in frame order (every frame, or every k-th for long clips)."""
    landmarks: list
    pts: list
    timing: object
    previews: list
    preview_size: tuple
    source_size: tuple
    tracking: object = None

    @property
    def frame_count(self) -> int:
        return len(self.pts)

    @property
    def duration_s(self) -> float:
        return self.pts[-1] if self.pts else 0.0


class PrepError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def user_message(self) -> str:
        return USER_MESSAGES.get(self.code, USER_MESSAGES["crashed"])


def _scaled_size(w: int, h: int, long_side: int) -> tuple[int, int]:
    scale = min(1.0, long_side / float(max(w, h)))
    return max(2, int(round(w * scale)) // 2 * 2), max(2, int(round(h * scale)) // 2 * 2)


def display_fps(timing) -> float | None:
    """Frame rate for labeling only: measured from real timestamps first,
    then the container's declared rate; None when neither is usable."""
    actual = getattr(timing, "actual_avg_fps", 0.0) or 0.0
    if getattr(timing, "pts_available", False) and 1.0 <= actual <= 1000.0:
        return float(actual)
    declared = getattr(timing, "container_declared_fps", 0.0) or 0.0
    if 1.0 <= declared <= 1000.0:
        return float(declared)
    return None


# ---------------------------------------------------------------------- child
def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    os.replace(tmp, path)          # atomic: the parent never reads half a file


class _StreamingFrames:
    """Feeds extract_landmarks FULL-RESOLUTION frames one at a time, straight
    from the decoder, so pose tracking sees exactly the frames the old
    in-process path gave it (identical landmarks -> identical analysis;
    tracking on downscaled frames measurably changed results), while only
    one full frame is ever in memory. Timestamps, duplicate counting and
    preview JPEGs are produced in the same pass. extract_landmarks reads
    len(frames) only after iterating, when it equals the decoded count."""

    def __init__(self, cap, hint: int, progress):
        self.cap, self.hint, self.progress = cap, hint, progress
        self.stride = max(1, -(-max(hint, 1) // PREVIEW_MAX_FRAMES))   # ceil division
        self.raw_pts, self.previews = [], []
        self.dup_count, self.count = 0, 0
        self.src_size = self.preview_size = None

    def __len__(self) -> int:
        return self.count

    def __iter__(self):
        import cv2
        from engine_v2.decode import is_near_duplicate
        prev_small = None
        while True:
            ok, frame = self.cap.read()
            if not ok:
                break
            if self.count >= MAX_FRAMES:
                raise PrepError("too_long", f">{MAX_FRAMES} frames")
            self.raw_pts.append(self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            h, w = frame.shape[:2]
            if self.src_size is None:
                self.src_size = (w, h)
                self.preview_size = _scaled_size(w, h, PREVIEW_LONG_SIDE)
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
            if is_near_duplicate(small, prev_small):
                self.dup_count += 1
            prev_small = small
            if self.count % self.stride == 0:
                img = cv2.resize(frame, self.preview_size, interpolation=cv2.INTER_AREA)
                ok_enc, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY])
                if ok_enc:
                    self.previews.append((self.count, buf.tobytes()))
            self.count += 1
            yield frame
            self.progress("tracking", self.count, max(self.hint, self.count))


def _prepare(video_path: str, out_dir: Path) -> ReviewPrep:
    import cv2
    from engine_v2.decode import _ffprobe_declared_fps, build_timing_report
    from engine_v2.pose import extract_landmarks

    cv2.setNumThreads(CHILD_THREADS)
    progress_path = out_dir / "progress.json"
    last_write = [0.0]

    def progress(stage: str, done: int, total: int, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - last_write[0] > 0.15:
            last_write[0] = now
            _write_json(progress_path, {"stage": stage, "done": done, "total": total})

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise PrepError("cannot_open")
    try:
        declared_fps = _ffprobe_declared_fps(video_path, cap)
        hint = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if hint > MAX_FRAMES:
            raise PrepError("too_long", f"{hint} frames")
        progress("tracking", 0, hint, force=True)
        stream = _StreamingFrames(cap, hint, progress)
        landmarks, tracking = extract_landmarks(stream)
    finally:
        cap.release()
    if not stream.raw_pts:
        raise PrepError("no_frames")

    # Some containers report a last-frame position of 0 or repeat a value;
    # keep the sequence monotonic so frame order and time order always agree.
    pts = []
    for t in stream.raw_pts:
        pts.append(t if not pts or t > pts[-1] else pts[-1] + 1e-6)
    t0 = pts[0]
    rel_pts = [round(t - t0, 6) for t in pts]
    timing = build_timing_report(declared_fps, pts, stream.dup_count)
    for lf, t in zip(landmarks, rel_pts):
        lf.pts_s = t
    progress("done", len(rel_pts), len(rel_pts), force=True)
    return ReviewPrep(landmarks=landmarks, pts=rel_pts, timing=timing, previews=stream.previews,
                      preview_size=stream.preview_size, source_size=stream.src_size, tracking=tracking)


def _write_result(prep: ReviewPrep, out: Path) -> None:
    """Plain JSON + JPEG files (no pickle: nothing executable crosses the
    process boundary, and the parent rebuilds only the declared types)."""
    prev_dir = out / "previews"
    prev_dir.mkdir()
    for idx, data in prep.previews:
        (prev_dir / f"{idx:06d}.jpg").write_bytes(data)
    _write_json(out / "result.json", {
        "pts": prep.pts,
        "preview_indices": [idx for idx, _ in prep.previews],
        "preview_size": list(prep.preview_size),
        "source_size": list(prep.source_size),
        "timing": dataclasses.asdict(prep.timing),
        "tracking": dataclasses.asdict(prep.tracking),
        "landmarks": [[lf.frame_index, lf.pts_s, lf.landmarks, lf.mean_visibility] for lf in prep.landmarks],
    })


def _read_result(out: Path) -> ReviewPrep:
    from engine_v2.types import FrameTimingReport, LandmarkFrame, TrackingQualityReport
    data = json.loads((out / "result.json").read_text())
    landmarks = []
    for idx, pts_s, lms, vis in data["landmarks"]:
        if lms is not None:
            lms = {str(k): (float(v[0]), float(v[1]), float(v[2])) for k, v in lms.items()}
        landmarks.append(LandmarkFrame(frame_index=int(idx), pts_s=None if pts_s is None else float(pts_s),
                                       landmarks=lms, mean_visibility=float(vis)))
    previews = [(int(i), (out / "previews" / f"{int(i):06d}.jpg").read_bytes())
                for i in data["preview_indices"]]
    return ReviewPrep(
        landmarks=landmarks, pts=[float(t) for t in data["pts"]],
        timing=FrameTimingReport(**data["timing"]), previews=previews,
        preview_size=tuple(int(v) for v in data["preview_size"]),
        source_size=tuple(int(v) for v in data["source_size"]),
        tracking=TrackingQualityReport(**data["tracking"]),
    )


def _child_main(video_path: str, out_dir: str) -> int:
    out = Path(out_dir)
    try:
        prep = _prepare(video_path, out)
        _write_result(prep, out)
        _write_json(out / "done.json", {"ok": True})
        return 0
    except PrepError as exc:
        _write_json(out / "error.json", {"code": exc.code, "detail": exc.detail})
        return 3
    except Exception as exc:  # never let a traceback be the only signal
        _write_json(out / "error.json", {"code": "crashed",
                                         "detail": f"{type(exc).__name__}: {exc}"[:500]})
        traceback.print_exc()
        return 4


# --------------------------------------------------------------------- parent
def _owned_work_dir() -> Path:
    base = PROJECT_ROOT / "uploads_streamlit"
    base.mkdir(exist_ok=True)
    d = base / f".review_{uuid.uuid4().hex}"
    d.mkdir()
    return d


def prepare_review(video_path: str, *, on_progress=None, timeout_s: float = CHILD_TIMEOUT_S,
                   poll_s: float = 0.2) -> ReviewPrep:
    """Run the preparation in a child process and return its ReviewPrep.

    Raises PrepError (with a user-facing `.user_message`) on any failure.
    `on_progress(stage, done, total)` is called from THIS process while
    polling; stage is "starting", "reading", "tracking" or "done".

    The uploaded temp copy at `video_path` is deleted (via safe_remove,
    which refuses anything outside the app's own upload dir) once
    preparation has finished -- success or failure. If this run is
    interrupted (the user navigated away), the child is killed and the
    upload is kept so the page can prepare it again."""
    from engine_v2.safe_delete import safe_remove

    work = _owned_work_dir()
    proc = None
    finished = False
    try:
        env = dict(os.environ)
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS"):
            env[var] = str(CHILD_THREADS)
        with open(work / "child.log", "wb") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "engine_v2.review_prep", str(video_path), str(work)],
                cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT,
            )
        if on_progress:
            on_progress("starting", 0, 0)
        deadline = time.monotonic() + timeout_s
        last = None
        while True:
            rc = proc.poll()
            try:
                p = json.loads((work / "progress.json").read_text())
                key = (p.get("stage"), p.get("done"), p.get("total"))
                if on_progress and key != last:
                    last = key
                    on_progress(*key)
            except (OSError, ValueError):
                pass
            if rc is not None:
                break
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait(timeout=10)
                finished = True
                raise PrepError("timeout", f"> {timeout_s:.0f}s")
            time.sleep(poll_s)
        finished = True
        if rc == 0 and (work / "done.json").exists():
            return _read_result(work)
        try:
            err = json.loads((work / "error.json").read_text())
            raise PrepError(err.get("code", "crashed"), err.get("detail", ""))
        except (OSError, ValueError):
            pass
        if rc is not None and rc < 0:
            raise PrepError("killed", f"signal {-rc}")
        raise PrepError("crashed", f"exit code {rc}")
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        shutil.rmtree(work, ignore_errors=True)
        if finished:
            try:
                safe_remove(video_path)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv[1], sys.argv[2]))
