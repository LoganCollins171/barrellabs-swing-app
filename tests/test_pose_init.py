"""BL-003Q regression coverage.

1. detect_phases.py must request model_complexity=1, not 2 — complexity 2
   ("heavy") isn't bundled in the mediapipe wheel and triggers a runtime
   download that fails with PermissionError on Streamlit Community Cloud's
   restricted filesystem, before a single frame is processed. Complexity 1
   ("full") ships inside the wheel (verified directly: a fresh
   `pip install mediapipe==0.10.14` has pose_landmark_full.tflite present
   immediately and pose_landmark_heavy.tflite absent).
2. A Pose-construction failure must be tagged distinctly (MODEL_INIT_ERROR)
   from a genuine "no pose found in this video" failure, so the app can
   show an accurate message instead of blaming the user's filming for an
   infrastructure problem.
"""
import re
from pathlib import Path

import pytest

from pose_init import (
    MODEL_INIT_ERROR_MARKER,
    classify_detect_phases_failure,
    init_pose_model,
)

DETECT_PHASES_SRC = (
    Path(__file__).resolve().parent.parent / "detect_phases.py"
).read_text()


def test_detect_phases_uses_model_complexity_1_not_2():
    """Guards the actual bug: regressing to model_complexity=2 here silently
    reintroduces the PermissionError on Streamlit Cloud. Matches the real
    call site, not just any occurrence of the string, so a comment or an
    unrelated '=2' elsewhere in the file can't hide a real regression."""
    call_site = re.search(
        r"init_pose_model\(\s*mp_pose\.Pose,(.*?)\)",
        DETECT_PHASES_SRC,
        re.DOTALL,
    )
    assert call_site, "could not find the init_pose_model(mp_pose.Pose, ...) call"
    assert "model_complexity=1" in call_site.group(1)
    assert "model_complexity=2" not in call_site.group(1)


def test_init_pose_model_success_passes_through():
    sentinel = object()

    def fake_pose_cls(**kwargs):
        assert kwargs["model_complexity"] == 1
        return sentinel

    assert init_pose_model(fake_pose_cls, model_complexity=1) is sentinel


def test_init_pose_model_reproduces_and_tags_permission_error(capsys):
    """Reproduces the exact failure mode seen live on Streamlit Cloud: the
    constructor raises PermissionError (from MediaPipe's own model-download
    attempt) before returning anything."""

    def failing_pose_cls(**kwargs):
        raise PermissionError(
            "[Errno 13] Permission denied: '.../pose_landmark_heavy.tflite'"
        )

    with pytest.raises(PermissionError):
        init_pose_model(failing_pose_cls, model_complexity=1)

    captured = capsys.readouterr()
    assert MODEL_INIT_ERROR_MARKER in captured.err
    assert "PermissionError" in captured.err


def test_init_pose_model_tags_any_init_exception_type():
    """Not just PermissionError — any failure during construction (e.g. a
    ConnectionError from the same download attempt) must be tagged the
    same way."""

    def failing_pose_cls(**kwargs):
        raise ConnectionError("Cannot download pose_landmark_heavy.tflite")

    with pytest.raises(ConnectionError):
        init_pose_model(failing_pose_cls)


def test_classify_model_init_failure():
    err = (
        "Traceback (most recent call last):\n"
        "  ...\n"
        f"{MODEL_INIT_ERROR_MARKER}: PermissionError: [Errno 13] Permission denied\n"
        "PermissionError: [Errno 13] Permission denied: '...'\n"
    )
    assert classify_detect_phases_failure(err) == "model_init"


def test_classify_filming_failure_unchanged():
    """The genuine, pre-existing failure mode (pose detection ran but found
    nobody in frame) must still classify as a filming issue, not a
    technical one — this is the one case where the original guidance
    (film from the side, whole body in frame, etc.) is still correct."""
    err = (
        "Traceback (most recent call last):\n"
        "  ...\n"
        "RuntimeError: No frames had a detected pose.\n"
    )
    assert classify_detect_phases_failure(err) == "filming"


def test_classify_handles_empty_or_none():
    assert classify_detect_phases_failure(None) == "filming"
    assert classify_detect_phases_failure("") == "filming"
