"""BL-008C.28 -- regression test for a real production failure: a real,
authenticated test of the Experimental Manual Review pathway on
app.barrellabsai.com's actual deployed app crashed decode entirely with
`FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`,
because Streamlit Cloud's container (packages.txt) never installed
ffmpeg. engine_v2.decode._ffprobe_declared_fps previously had no
fallback at all for this. These tests prove the fix without needing a
real video file or a real ffprobe binary -- they simulate "ffprobe is
missing" directly."""
from __future__ import annotations

import subprocess

import pytest

from engine_v2.decode import _ffprobe_declared_fps


def test_missing_ffprobe_binary_falls_back_instead_of_raising(monkeypatch):
    """The exact real-world failure: subprocess.run raises
    FileNotFoundError because the 'ffprobe' executable doesn't exist on
    PATH. Must not propagate -- must fall back."""
    def _boom(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'ffprobe'")
    monkeypatch.setattr(subprocess, "run", _boom)

    class _FakeCap:
        def get(self, prop):
            return 29.97

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap())
    assert result == pytest.approx(29.97), "must fall back to the OpenCV-reported fps"


def test_ffprobe_nonzero_exit_also_falls_back(monkeypatch):
    """A different real-world failure mode: ffprobe exists but errors on
    this specific file (corrupt/unsupported) -- check=True raises
    CalledProcessError, which must also be caught."""
    def _boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["ffprobe"])
    monkeypatch.setattr(subprocess, "run", _boom)

    class _FakeCap:
        def get(self, prop):
            return 60.0

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap())
    assert result == pytest.approx(60.0)


def test_opencv_fallback_also_failing_returns_zero_not_a_crash(monkeypatch):
    """If BOTH ffprobe and the OpenCV fallback fail, the function must
    still return a plain float (0.0, the pre-existing 'unknown declared
    fps' sentinel the caller already handles) rather than raising."""
    def _boom(*a, **k):
        raise FileNotFoundError("ffprobe")
    monkeypatch.setattr(subprocess, "run", _boom)

    class _BrokenCap:
        def get(self, prop):
            raise RuntimeError("camera backend unavailable")

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_BrokenCap())
    assert result == 0.0


def test_successful_ffprobe_call_is_unaffected(monkeypatch):
    """The happy path (ffprobe present and working, the normal case on a
    real dev machine) must still return the real parsed value, not the
    fallback -- the fix must not change existing correct behavior."""
    class _FakeCompletedProcess:
        stdout = '{"streams": [{"r_frame_rate": "30000/1001"}]}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    result = _ffprobe_declared_fps("fake_path.mp4")
    assert result == pytest.approx(30000 / 1001)


def test_call_site_still_works_without_passing_cap():
    """The real call site in decode_with_timing calls this with just
    `path` -- confirms the new `cap` parameter is optional and the
    function doesn't require updating that call site."""
    import inspect
    sig = inspect.signature(_ffprobe_declared_fps)
    assert sig.parameters["cap"].default is None
