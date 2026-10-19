"""BL-003Q: pose-model initialization, split out so the failure-tagging and
message-classification logic around it is actually unit-testable.

detect_phases.py is a procedural CLI script (run only as a subprocess by
app.py), so nothing inside it can be imported and called directly in a test.
The two small, pure pieces that matter here — "tag an init failure
distinctly" and "decide which user-facing message that tag implies" — live
here instead, where they can be exercised without ever touching mediapipe.
"""
from __future__ import annotations

import sys

MODEL_INIT_ERROR_MARKER = "MODEL_INIT_ERROR"


def init_pose_model(pose_cls, **kwargs):
    """Construct a MediaPipe Pose instance, tagging any failure distinctly
    from a genuine "no pose found in this video" result (which only occurs
    later, after frames are actually processed).

    `pose_cls` is passed in (rather than imported here) so this stays
    testable without mediapipe installed — detect_phases.py calls it as
    `init_pose_model(mp_pose.Pose, model_complexity=1, ...)`.
    """
    try:
        return pose_cls(**kwargs)
    except Exception as exc:
        print(f"{MODEL_INIT_ERROR_MARKER}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        raise


def classify_detect_phases_failure(err: str | None) -> str:
    """Given detect_phases.py's captured stderr/stdout from a failed run,
    return "model_init" if the failure happened before any frame was
    processed (a technical problem, not a filming one), else "filming" —
    the existing, genuine "couldn't find the hitter" case.
    """
    if err and MODEL_INIT_ERROR_MARKER in err:
        return "model_init"
    return "filming"
