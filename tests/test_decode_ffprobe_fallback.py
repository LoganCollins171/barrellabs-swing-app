"""BL-008C.28/.32 -- regression tests for a real production failure: a real,
authenticated test of the Experimental Manual Review pathway on the live
deployed app crashed decode entirely with
`FileNotFoundError: [Errno 2] No such file or directory: 'ffprobe'`,
because Streamlit Cloud's container (packages.txt) never installed ffmpeg.
engine_v2.decode._ffprobe_declared_fps previously had no fallback at all
for this. BL-008C.32 additionally routed the call through
proc_utils.run_subprocess (the same timeout-bounded helper the legacy
pipeline already uses) and hardened malformed/non-positive-output
handling. These tests simulate every failure mode directly -- no real
video file or real ffprobe binary needed."""
from __future__ import annotations

import subprocess

import pytest

import proc_utils
from engine_v2.decode import _ffprobe_declared_fps


class _FakeCap:
    def __init__(self, fps):
        self._fps = fps
    def get(self, prop):
        return self._fps


def test_missing_ffprobe_binary_falls_back_instead_of_raising(monkeypatch):
    """The exact real-world failure: the ffprobe executable doesn't exist
    on PATH, so the underlying subprocess.run inside run_subprocess
    raises FileNotFoundError. Must not propagate -- must fall back."""
    def _boom(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'ffprobe'")
    monkeypatch.setattr(subprocess, "run", _boom)

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap(29.97))
    assert result == pytest.approx(29.97), "must fall back to the OpenCV-reported fps"


def test_ffprobe_nonzero_exit_also_falls_back(monkeypatch):
    """ffprobe exists but errors on this specific file (corrupt/unsupported) --
    run_subprocess returns a non-zero returncode (it does not raise for
    this case), which must also trigger the fallback."""
    monkeypatch.setattr(proc_utils, "run_subprocess", lambda *a, **k: (1, "", "Invalid data found"))
    # decode.py imported run_subprocess by name -- patch it there too.
    import engine_v2.decode as decode_mod
    monkeypatch.setattr(decode_mod, "run_subprocess", lambda *a, **k: (1, "", "Invalid data found"))

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap(60.0))
    assert result == pytest.approx(60.0)


def test_ffprobe_timeout_falls_back(monkeypatch):
    """A hung/stuck ffprobe process -- proc_utils.run_subprocess itself
    converts subprocess.TimeoutExpired into a non-zero returncode (124)
    rather than raising, so this exercises the same rc!=0 path, proving
    the timeout bound (BL-008C.32) doesn't hang decode."""
    import engine_v2.decode as decode_mod
    monkeypatch.setattr(decode_mod, "run_subprocess", lambda *a, **k: (124, "", "Timed out after 10s."))

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap(24.0))
    assert result == pytest.approx(24.0)


def test_malformed_json_output_falls_back(monkeypatch):
    """ffprobe exits 0 but produces unparseable output -- must not raise
    a json.JSONDecodeError out of this function."""
    import engine_v2.decode as decode_mod
    monkeypatch.setattr(decode_mod, "run_subprocess", lambda *a, **k: (0, "not json at all", ""))

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap(30.0))
    assert result == pytest.approx(30.0)


def test_zero_denominator_frame_rate_falls_back(monkeypatch):
    """A genuinely malformed/zero r_frame_rate ('0/0') must not raise
    ZeroDivisionError, and must not be trusted as a real 0 fps either --
    falls back to the OpenCV value."""
    import engine_v2.decode as decode_mod
    monkeypatch.setattr(
        decode_mod, "run_subprocess",
        lambda *a, **k: (0, '{"streams": [{"r_frame_rate": "0/0"}]}', ""),
    )

    result = _ffprobe_declared_fps("fake_path.mp4", cap=_FakeCap(25.0))
    assert result == pytest.approx(25.0)


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
    import engine_v2.decode as decode_mod
    monkeypatch.setattr(
        decode_mod, "run_subprocess",
        lambda *a, **k: (0, '{"streams": [{"r_frame_rate": "30000/1001"}]}', ""),
    )

    result = _ffprobe_declared_fps("fake_path.mp4")
    assert result == pytest.approx(30000 / 1001)


def test_call_site_still_works_without_passing_cap():
    """The real call site in decode_with_timing calls this with just
    `path` -- confirms the `cap` parameter is optional."""
    import inspect
    sig = inspect.signature(_ffprobe_declared_fps)
    assert sig.parameters["cap"].default is None


def test_ffprobe_call_is_routed_through_the_shared_timeout_bounded_helper(monkeypatch):
    """BL-008C.32: confirms the call goes through proc_utils.run_subprocess
    (the same helper the legacy pipeline's pose-detection subprocess
    uses) rather than a second, separate, unbounded subprocess.run --
    proven by patching run_subprocess specifically and confirming it is
    actually invoked with a timeout."""
    import engine_v2.decode as decode_mod
    calls = []
    def _fake(args, cwd, timeout=None):
        calls.append({"args": args, "timeout": timeout})
        return (0, '{"streams": [{"r_frame_rate": "30/1"}]}', "")
    monkeypatch.setattr(decode_mod, "run_subprocess", _fake)

    result = _ffprobe_declared_fps("fake_path.mp4")
    assert result == pytest.approx(30.0)
    assert len(calls) == 1
    assert calls[0]["timeout"] is not None and calls[0]["timeout"] > 0
    assert "ffprobe" in calls[0]["args"]
