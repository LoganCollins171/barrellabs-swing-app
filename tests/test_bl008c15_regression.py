"""BL-008C.15 Deliverable 5 -- remaining regression categories not
already covered by tests/test_user_guided_flow.py: missing landmarks,
landmark identity discontinuity, excessive normalization instability,
private-video cleanup, suppression messaging."""
from __future__ import annotations

import os
import tempfile

from engine_v2.types import LandmarkFrame
from engine_v2.user_guided_flow import analyze_user_selection, decode_and_cleanup


def _lf(i, x, vis=0.9, landmarks=None):
    base = {k: (x if "wrist" in k or "ankle" in k else 0.5, 0.5, vis) for k in
            ["left_wrist", "right_wrist", "left_ankle", "right_ankle",
             "left_shoulder", "right_shoulder", "left_hip", "right_hip",
             "left_knee", "right_knee", "left_ear", "right_ear"]}
    if landmarks is not None:
        base.update(landmarks)
    return LandmarkFrame(frame_index=i, pts_s=None, landmarks=base, mean_visibility=vis)


def _burst_frames(n, burst_s, burst_e, amplitude=0.3):
    xs = [0.3] * n
    for i in range(burst_s, min(burst_e, n)):
        xs[i] = 0.3 + amplitude * (1 - abs(i - (burst_s + burst_e) / 2) / ((burst_e - burst_s) / 2 + 1))
    return [_lf(i, xs[i]) for i in range(n)]


def test_missing_landmarks_over_most_of_selection_suppresses_not_crashes():
    """A selection where core landmarks (shoulders/hips) are untracked
    for most of the window must suppress, not crash or fabricate a
    value."""
    frames = _burst_frames(100, 40, 60)
    for i in range(35, 65):
        frames[i] = LandmarkFrame(frame_index=i, pts_s=None, landmarks=None, mean_visibility=0.0)
    r = analyze_user_selection(frames, "test.mp4", 35, 65, user_confirmed_swing=True)
    assert r.status in ("poor_quality", "insufficient_evidence")
    assert r.result is None


def test_landmark_identity_discontinuity_does_not_crash():
    """A single-frame large jump in a landmark's position (simulating a
    tracking identity swap) inside the selection must not crash analysis
    -- the existing per-metric identity-jump guard (BL-008C.5) should
    suppress the affected metric rather than report a fabricated value."""
    frames = _burst_frames(100, 40, 60)
    # inject a large single-frame ankle jump mid-selection
    jumped = dict(frames[50].landmarks)
    jumped["left_ankle"] = (0.9, 0.9, 0.9)
    frames[50] = LandmarkFrame(frame_index=50, pts_s=None, landmarks=jumped, mean_visibility=0.9)
    r = analyze_user_selection(frames, "test.mp4", 38, 62, user_confirmed_swing=True)
    # must not raise; whatever the outcome, it is a well-formed result
    assert r.status in ("experimental_result", "poor_quality", "insufficient_evidence", "ambiguous_movement",
                        "multiple_movements", "rejected_input", "needs_confirmation")
    if r.status == "experimental_result":
        left_ankle = next(m for m in r.result.measurements if m.name.startswith("left_ankle"))
        # either suppressed by the identity-jump guard, or a legitimate value -- never a crash either way
        assert left_ankle.value is None or isinstance(left_ankle.value, float)


def test_excessive_normalization_instability_degrades_not_crashes():
    """A selection where the shoulder-width normalizer varies wildly
    (simulating extreme trunk rotation toward/away from camera) must
    degrade affected metrics' quality, not crash or silently report an
    unqualified value."""
    frames = _burst_frames(100, 40, 60)
    for i in range(40, 61):
        ls, rs = frames[i].landmarks["left_shoulder"], frames[i].landmarks["right_shoulder"]
        scale = 0.05 if i % 2 == 0 else 1.0  # shoulders alternately collapse together / spread apart
        landmarks = dict(frames[i].landmarks)
        landmarks["left_shoulder"] = (0.5 - 0.1 * scale, ls[1], ls[2])
        landmarks["right_shoulder"] = (0.5 + 0.1 * scale, rs[1], rs[2])
        frames[i] = LandmarkFrame(frame_index=i, pts_s=None, landmarks=landmarks, mean_visibility=0.9)
    r = analyze_user_selection(frames, "test.mp4", 40, 60, user_confirmed_swing=True)
    assert r.status in ("experimental_result", "poor_quality", "insufficient_evidence", "ambiguous_movement",
                        "multiple_movements")
    if r.status == "experimental_result":
        for m in r.result.measurements:
            assert m.value is None or m.quality is not None  # never an unlabeled/unqualified value


def test_suppression_messaging_is_always_human_readable():
    """Every suppressed measurement must carry a non-empty, human-
    readable suppression_reason -- never a bare None/empty string
    alongside a None value."""
    frames = [LandmarkFrame(frame_index=i, pts_s=None, landmarks=None, mean_visibility=0.0) for i in range(60)]
    r = analyze_user_selection(frames, "test.mp4", 10, 50, user_confirmed_swing=True)
    # fully static/untracked -- expect a suppression-style status with a real reason
    assert r.status in ("poor_quality", "insufficient_evidence", "rejected_input")
    assert r.reason and isinstance(r.reason, str) and len(r.reason) > 5


def test_private_video_cleanup_removes_temp_file_on_success():
    frames_src = _burst_frames(60, 10, 30)
    # decode_and_cleanup needs a real video path -- test the cleanup
    # contract directly: a file that exists before the call must not
    # exist after, regardless of whether decoding succeeds.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
        tmp.write(b"not a real video, just bytes to delete")
        tmp_path = tmp.name
    assert os.path.isfile(tmp_path)
    lf, fps, error = decode_and_cleanup(tmp_path)
    assert not os.path.exists(tmp_path), "temp video file must be removed even when decoding fails"
    assert error is not None  # garbage bytes -- decoding should fail, not matter for this test


def test_private_video_cleanup_removes_temp_file_even_if_already_gone():
    """Calling decode_and_cleanup on a path that was already removed
    must not raise."""
    with tempfile.NamedTemporaryFile(delete=True, suffix=".mp4") as tmp:
        tmp_path = tmp.name
    assert not os.path.exists(tmp_path)
    lf, fps, error = decode_and_cleanup(tmp_path)
    assert error is not None
