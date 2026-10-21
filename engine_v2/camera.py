"""BL-008C Phase 1 -- explicit camera-angle classification.

Replaces reliance on the legacy, overloaded 'camera_view' field (which in
production actually stores hip_to_torso_ratio_stance, not an angle class --
see BL-008B finding). This is a simple, honest heuristic based on shoulder
separation relative to hip separation at address: in a true side-on view
both shoulders/hips are foreshortened to nearly the same point; in a front
view they're separated by close to the body's actual width. It is NOT a
validated classifier -- confidence is reported explicitly and low-confidence
results should be treated as UNKNOWN, not guessed into PROFILE.
"""
from __future__ import annotations

import numpy as np

from engine_v2.types import LandmarkFrame, SwingSegment, CameraClass


def classify_camera_view(frames: list[LandmarkFrame], segment: SwingSegment) -> tuple[CameraClass, float]:
    idx = segment.start_frame
    f = frames[idx] if 0 <= idx < len(frames) else None
    if f is None or f.landmarks is None:
        return CameraClass.UNKNOWN, 0.0

    ls = f.landmarks.get("left_shoulder")
    rs = f.landmarks.get("right_shoulder")
    lh = f.landmarks.get("left_hip")
    rh = f.landmarks.get("right_hip")
    if not all([ls, rs, lh, rh]):
        return CameraClass.UNKNOWN, 0.0

    shoulder_w = abs(ls[0] - rs[0])
    hip_w = abs(lh[0] - rh[0])
    torso_h = abs((ls[1] + rs[1]) / 2 - (lh[1] + rh[1]) / 2) or 1e-6
    ratio = (shoulder_w + hip_w) / 2 / torso_h  # small = foreshortened = side-on; large = facing camera

    if ratio < 0.35:
        return CameraClass.PROFILE, min(1.0, 0.5 + (0.35 - ratio))
    elif ratio < 0.65:
        return CameraClass.THREE_QUARTER, 0.5
    else:
        return CameraClass.FRONT, min(1.0, 0.5 + (ratio - 0.65))
