"""BL-008C Phase 1 -- pose extraction.

Reuses mediapipe's Pose model (the same validated extractor the production
pipeline already uses -- see pose_init.py, which this calls directly rather
than reimplementing landmark detection). What's new here is the typed,
per-frame LandmarkFrame output and the explicit TrackingQualityReport,
neither of which the old detect_phases.py produced as a reusable object.
"""
from __future__ import annotations

import numpy as np

from engine_v2.types import LandmarkFrame, TrackingQualityReport
from pose_init import init_pose_model

_VISIBILITY_OK_THRESHOLD = 0.5

# The subset of mediapipe pose landmarks relevant to a side-view swing.
_LANDMARK_NAMES = [
    "nose", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder",
    "left_elbow", "right_elbow",
    "left_wrist", "right_wrist",
    "left_hip", "right_hip",
    "left_knee", "right_knee",
    "left_ankle", "right_ankle",
]


def extract_landmarks(frames: list[np.ndarray], on_progress=None) -> tuple[list[LandmarkFrame], TrackingQualityReport]:
    """`on_progress(done, total)`, if given, is called after each frame
    (BL-008C.36: lets the review page show real progress)."""
    import cv2
    import mediapipe as mp

    pose = init_pose_model(
        mp.solutions.pose.Pose,
        model_complexity=1,
        static_image_mode=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    out: list[LandmarkFrame] = []
    had_pose_prev = None
    continuity_breaks = 0
    frames_with_pose = 0
    visibilities: list[float] = []

    try:
        for idx, frame in enumerate(frames):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = pose.process(rgb)
            if result.pose_landmarks is None:
                out.append(LandmarkFrame(frame_index=idx, pts_s=None, landmarks=None, mean_visibility=0.0))
                has_pose = False
            else:
                lm = result.pose_landmarks.landmark
                by_idx = {n.name.lower(): i for i, n in enumerate(mp.solutions.pose.PoseLandmark)}
                landmarks = {}
                vis_vals = []
                for name in _LANDMARK_NAMES:
                    if name.upper() not in [n.name for n in mp.solutions.pose.PoseLandmark]:
                        continue
                    i = by_idx[name]
                    p = lm[i]
                    landmarks[name] = (float(p.x), float(p.y), float(p.visibility))
                    vis_vals.append(p.visibility)
                mean_vis = float(np.mean(vis_vals)) if vis_vals else 0.0
                visibilities.append(mean_vis)
                out.append(LandmarkFrame(frame_index=idx, pts_s=None, landmarks=landmarks, mean_visibility=mean_vis))
                frames_with_pose += 1
                has_pose = True

            if had_pose_prev is True and has_pose is False:
                continuity_breaks += 1
            had_pose_prev = has_pose
            if on_progress is not None:
                on_progress(idx + 1, len(frames))
    finally:
        pose.close()

    total = len(frames)
    missing = total - frames_with_pose
    mean_visibility = float(np.mean(visibilities)) if visibilities else 0.0
    low_vis_pct = (
        (sum(1 for v in visibilities if v < _VISIBILITY_OK_THRESHOLD) / len(visibilities) * 100.0)
        if visibilities else 100.0
    )

    report = TrackingQualityReport(
        total_frames=total,
        frames_with_pose=frames_with_pose,
        frames_missing_pose=missing,
        mean_visibility=mean_visibility,
        low_visibility_frame_pct=low_vis_pct,
        continuity_breaks=continuity_breaks,
    )
    return out, report
