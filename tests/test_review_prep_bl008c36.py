"""BL-008C.36 -- engine_v2.review_prep: the child-process clip preparation
behind the visual swing-selection page. Real decode + real pose tracking on
disposable copies of the repo's sample clip; ffprobe genuinely absent."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "assets" / "sample_swing.mp4"
UPLOADS = ROOT / "uploads_streamlit"


@pytest.fixture(autouse=True)
def _no_ffprobe_and_original_unchanged(monkeypatch):
    real_ffmpeg = shutil.which("ffmpeg")
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    before = hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()
    yield real_ffmpeg
    assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == before


def _copy(suffix=".mp4", data: bytes | None = None) -> Path:
    UPLOADS.mkdir(exist_ok=True)
    p = UPLOADS / f"bl008c36-test_{uuid.uuid4().hex}{suffix}"
    if data is None:
        shutil.copyfile(ORIGINAL, p)
    else:
        p.write_bytes(data)
    return p


def _work_dirs() -> set:
    return {d.name for d in UPLOADS.glob(".review_*")}


def test_prepared_landmarks_are_identical_to_the_old_in_process_decode():
    """Streaming full-resolution frames must not change a single landmark --
    tracking on downscaled frames measurably changed analysis results."""
    from engine_v2.review_prep import prepare_review
    from engine_v2.user_guided_flow import decode_and_cleanup
    old, _, err = decode_and_cleanup(str(_copy()))
    assert err is None
    new = prepare_review(str(_copy()))
    assert len(new.landmarks) == len(old) == 323
    assert all(a.landmarks == b.landmarks and a.mean_visibility == b.mean_visibility
               for a, b in zip(old, new.landmarks))


def test_timestamps_previews_and_fps_are_honest():
    from engine_v2.review_prep import PREVIEW_MAX_FRAMES, display_fps, prepare_review
    before = _work_dirs()
    src = _copy()
    stages = []
    prep = prepare_review(str(src), on_progress=lambda *a: stages.append(a))
    n = prep.frame_count
    assert n == len(prep.landmarks) == 323
    assert prep.pts[0] == 0.0 and all(b > a for a, b in zip(prep.pts, prep.pts[1:])), "monotonic real timestamps"
    assert all(lf.pts_s == t for lf, t in zip(prep.landmarks, prep.pts))
    assert 50 < display_fps(prep.timing) < 61 and abs(display_fps(prep.timing) - 30.0) > 5
    idx = [i for i, _ in prep.previews]
    assert idx[0] == 0 and idx == sorted(idx) and idx[-1] < n
    assert len(idx) <= PREVIEW_MAX_FRAMES and idx[1] - idx[0] == 2, "323 frames -> every 2nd frame previewed"
    assert all(data[:2] == b"\xff\xd8" for _, data in prep.previews), "JPEG previews"
    assert max(prep.preview_size) <= 480
    assert stages[0][0] == "starting" and any(s[0] == "tracking" and s[1] > 0 for s in stages)
    assert not src.exists(), "the temp upload copy is removed after preparation"
    assert _work_dirs() == before, "the child's work dir is removed"


def test_mov_container_is_prepared(_no_ffprobe_and_original_unchanged):
    ffmpeg = _no_ffprobe_and_original_unchanged
    if not ffmpeg:
        pytest.skip("ffmpeg not available to build a .mov copy")
    from engine_v2.review_prep import prepare_review
    mov = UPLOADS / f"bl008c36-test_{uuid.uuid4().hex}.mov"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(ORIGINAL), "-c", "copy", str(mov)], check=True)
    prep = prepare_review(str(mov))
    assert prep.frame_count == 323 and prep.previews


def test_corrupt_file_fails_with_a_friendly_message_and_cleans_up():
    from engine_v2.review_prep import PrepError, prepare_review
    before = _work_dirs()
    bad = _copy(".mov", b"\x00\x00\x00\x18ftypqt  " + os.urandom(4096))
    with pytest.raises(PrepError) as ei:
        prepare_review(str(bad))
    assert ei.value.code in ("cannot_open", "no_frames")
    assert "ffprobe" not in ei.value.user_message and "Try exporting it again" in ei.value.user_message
    assert not bad.exists() and _work_dirs() == before


def test_too_long_clip_is_refused_clearly(monkeypatch):
    from engine_v2.review_prep import PrepError, prepare_review
    monkeypatch.setenv("BL_REVIEW_MAX_FRAMES", "100")   # read by the child process
    with pytest.raises(PrepError) as ei:
        prepare_review(str(_copy()))
    assert ei.value.code == "too_long" and "3 to 8 seconds" in ei.value.user_message


def test_timeout_kills_the_child_and_reports_it():
    from engine_v2.review_prep import PrepError, prepare_review
    before = _work_dirs()
    with pytest.raises(PrepError) as ei:
        prepare_review(str(_copy()), timeout_s=0.3)
    assert ei.value.code == "timeout"
    assert _work_dirs() == before


def test_interrupted_preparation_kills_the_child_and_keeps_the_upload():
    """A Streamlit rerun (user navigated away) arrives as a BaseException
    from the progress callback: the child must die, and the upload must be
    kept so the page can prepare it again later."""
    from engine_v2.review_prep import prepare_review

    class _Rerun(BaseException):
        pass

    def _interrupt(stage, done, total):
        if stage == "tracking" and done > 5:
            raise _Rerun()

    src = _copy()
    before = _work_dirs()
    with pytest.raises(_Rerun):
        prepare_review(str(src), on_progress=_interrupt)
    assert src.exists(), "interrupted -> upload kept for a retry"
    assert _work_dirs() == before
    src.unlink()


def test_child_output_is_json_not_pickle():
    source = (ROOT / "engine_v2" / "review_prep.py").read_text()
    assert "pickle.load" not in source and "import pickle" not in source
