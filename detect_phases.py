"""
Milestone 3 (v3) + Milestone 4 prep:
- Realistic rotation math + sharper contact detection (v3).
- Now accepts an input video filename as a CLI argument, derives output
  filenames from it, and saves a portable fingerprint.json with the key
  metrics for cross-swing comparison.

Usage:
  python detect_phases.py                    # defaults to swing.mp4
  python detect_phases.py mookie_swing.mp4   # any other video
  python detect_phases.py mookie_swing.mp4 LEFT   # override handedness
"""

import math
import csv
import json
import os
import sys

import cv2
import mediapipe as mp
from pose_init import init_pose_model
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---- CONFIG ----
INPUT_VIDEO = sys.argv[1] if len(sys.argv) > 1 else "swing.mp4"
# Handedness is auto-detected by default. Pass "LEFT" or "RIGHT" as the second
# arg to override (e.g. if auto-detect is wrong on a low-confidence clip).
HANDEDNESS = sys.argv[2].upper() if len(sys.argv) > 2 else "AUTO"

# Optional player age (years), passed by app.py as "--age N". Written into the
# fingerprint so analyzer.age_bracket() can pick an age-fair band. Absent →
# fingerprint omits `age` → analyzer falls back to the default bracket.
PLAYER_AGE = None
if "--age" in sys.argv:
    try:
        PLAYER_AGE = int(sys.argv[sys.argv.index("--age") + 1])
    except (ValueError, IndexError):
        PLAYER_AGE = None

# Optional swing-center hint (frame index near the swing, typically the
# ground-truth contact frame). When set, the burst detector locks onto the
# swing nearest this frame instead of the loudest burst in the clip — used by
# the reference rebuild on multi-event broadcast clips. Absent → unchanged
# auto-detection (the live user path never passes this).
SWING_CENTER_HINT = None
if "--swing-center" in sys.argv:
    try:
        SWING_CENTER_HINT = int(sys.argv[sys.argv.index("--swing-center") + 1])
    except (ValueError, IndexError):
        SWING_CENTER_HINT = None

# Optional verified phase-frame overrides (foot_plant / contact), used ONLY by
# the reference rebuild to anchor on hand-verified labels — bypasses the
# detector's intra-swing landmark search, which fires early on heavily slowed
# footage. Absent → normal detection (the live user path never passes these).
# All other phases (launch, peak_rotation, load_start, finish) cascade from the
# overridden contact/foot_plant exactly as in normal detection.
FOOT_PLANT_OVERRIDE = None
if "--foot-plant" in sys.argv:
    try:
        FOOT_PLANT_OVERRIDE = int(sys.argv[sys.argv.index("--foot-plant") + 1])
    except (ValueError, IndexError):
        FOOT_PLANT_OVERRIDE = None
CONTACT_OVERRIDE = None
if "--contact" in sys.argv:
    try:
        CONTACT_OVERRIDE = int(sys.argv[sys.argv.index("--contact") + 1])
    except (ValueError, IndexError):
        CONTACT_OVERRIDE = None

_base = os.path.splitext(os.path.basename(INPUT_VIDEO))[0]
OUTPUT_CSV = f"{_base}_metrics.csv"
OUTPUT_CHART = f"{_base}_phases.png"
OUTPUT_FINGERPRINT = f"{_base}_fingerprint.json"
OUTPUT_PHASES_DEBUG = f"{_base}_phases_debug.json"

# Phase 1 instrumentation flag (observability only — does not change any
# existing phase index, metric value, or score). Enable by setting env var
# PHASE_DEBUG_V1 to "1", "true", "yes", or "on". When disabled this module
# is not imported and detect_phases.py runs exactly as before.
PHASE_DEBUG_V1 = str(os.environ.get("PHASE_DEBUG_V1", "")).strip().lower() in {
    "1", "true", "yes", "on",
}

# Phase 2 toe-tap-aware detector flag (shadow mode — v4 runs ALONGSIDE v3
# and emits a parallel phases_v4 dict for comparison; v3 outputs are
# unchanged). Enabling DETECTOR_V4 implicitly enables PHASE_DEBUG_V1 since
# v4 consumes the candidate list produced by phase_debug.
DETECTOR_V4 = str(os.environ.get("DETECTOR_V4", "")).strip().lower() in {
    "1", "true", "yes", "on",
}
if DETECTOR_V4:
    PHASE_DEBUG_V1 = True

OUTPUT_DETECTOR_V4_DEBUG = f"{_base}_detector_v4.json"
# ----------------

NOSE = 0
LEFT_EAR, RIGHT_EAR = 7, 8
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26
LEFT_ANKLE, RIGHT_ANKLE = 27, 28

# FRONT/BACK joint indices are decided AFTER pose extraction, once handedness
# is known. Right-handed batter → left side is front. Left-handed → right.


def joint_angle_deg(a, b, c):
    ba = (a[0] - b[0], a[1] - b[1])
    bc = (c[0] - b[0], c[1] - b[1])
    dot = ba[0]*bc[0] + ba[1]*bc[1]
    mag_ba = math.hypot(*ba)
    mag_bc = math.hypot(*bc)
    if mag_ba == 0 or mag_bc == 0:
        return 0.0
    cos_a = max(-1.0, min(1.0, dot / (mag_ba * mag_bc)))
    return math.degrees(math.acos(cos_a))


def smooth(arr, window=5):
    arr = np.asarray(arr, dtype=float)
    out = np.copy(arr)
    half = window // 2
    for i in range(len(arr)):
        lo = max(0, i - half)
        hi = min(len(arr), i + half + 1)
        out[i] = np.mean(arr[lo:hi])
    return out


def width_to_rotation_deg(widths):
    """Convert 2D pixel widths between two body landmarks (e.g. left/right hip)
    into a rotation angle in degrees, using the geometric fact that
    apparent_width ≈ true_length * sin(rotation_about_vertical_axis).

    We use the 95th percentile of widths in the clip as the "fully open"
    reference (≈90°). Below that, arcsin gives the rotation.
    """
    widths = np.asarray(widths, dtype=float)
    max_width = float(np.percentile(widths, 95))
    if max_width < 1:
        return np.zeros_like(widths)
    ratios = np.clip(widths / max_width, 0, 1)
    return np.degrees(np.arcsin(ratios))


# ---------- POSE EXTRACTION ----------
# model_complexity (BL-003Q): 1, not 2. MediaPipe ships the complexity-0
# ("lite") and complexity-1 ("full") pose models INSIDE the pip wheel, but
# complexity-2 ("heavy") is not bundled — the solutions API downloads it on
# first use into its own install directory (mediapipe/python/solutions/
# download_utils.py). Streamlit Community Cloud's runtime filesystem doesn't
# allow that write, so model_complexity=2 always crashes there with
# PermissionError before a single frame is processed, regardless of video
# quality. Verified directly: a fresh `pip install mediapipe==0.10.14` has
# pose_landmark_full.tflite (complexity 1) present immediately, and
# pose_landmark_heavy.tflite (complexity 2) absent until that risky runtime
# download succeeds. Complexity 1 is MediaPipe's own documented default/
# recommended tier; 2 is an optional accuracy step up most callers don't
# need. Real tradeoff, NOT hidden: side-by-side on the repo's own sample
# clip, phase timing, knee angle, and separation numbers shifted
# materially between complexity 1 and 2 — not a rounding-level difference.
# That clip is a broadcast/crowd shot the app itself tells users not to
# upload (not a clean single-subject phone video), so this isn't
# necessarily representative of real user clips, but it hasn't been
# re-measured against one either. Same landmark schema and indices either
# way, so nothing downstream needed to change — but treat the accuracy
# question as open, not resolved, until checked against a real well-filmed
# clip.
mp_pose = mp.solutions.pose
pose = init_pose_model(
    mp_pose.Pose,
    static_image_mode=False,
    model_complexity=1,
    enable_segmentation=False,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)

cap = cv2.VideoCapture(INPUT_VIDEO)
if not cap.isOpened():
    raise FileNotFoundError(f"Could not open '{INPUT_VIDEO}'.")

fps = cap.get(cv2.CAP_PROP_FPS)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

handedness_label = "auto-detect" if HANDEDNESS == "AUTO" else f"{HANDEDNESS}-handed (manual)"
print(f"Processing '{INPUT_VIDEO}' ({width}x{height} at {fps:.1f} fps, {handedness_label})...")

records = []
frame_idx = 0

while True:
    ret, frame = cap.read()
    if not ret:
        break
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = pose.process(rgb)

    if results.pose_landmarks:
        lm = results.pose_landmarks.landmark

        def pt(idx):
            return (lm[idx].x * width, lm[idx].y * height)

        # Head reference = EAR-MIDPOINT, not the nose. The nose projects
        # forward of the skull, so it sweeps a large arc as the batter ROTATES
        # through the swing — the old nose-based head_x counted that rotation as
        # "head drift" and over-penalized hard rotators (lindor read 0.26 torso,
        # mookie 0.15, almost all rotation not lurch). The ear-midpoint sits on
        # the head's rotation axis, so it barely moves during rotation while
        # still capturing genuine head TRANSLATION (the real stability signal).
        # Validated on the 17 MLB refs + controls (scripts/evaluate_head_centers.py):
        # lindor 0.257->0.089, yandy 0.125->0.052; phone controls unchanged.
        _l_ear, _r_ear = pt(LEFT_EAR), pt(RIGHT_EAR)
        head_x = (_l_ear[0] + _r_ear[0]) / 2.0
        head_y = (_l_ear[1] + _r_ear[1]) / 2.0
        left_hip = pt(LEFT_HIP)
        right_hip = pt(RIGHT_HIP)
        left_shoulder = pt(LEFT_SHOULDER)
        right_shoulder = pt(RIGHT_SHOULDER)
        left_knee = pt(LEFT_KNEE)
        right_knee = pt(RIGHT_KNEE)
        left_ankle = pt(LEFT_ANKLE)
        right_ankle = pt(RIGHT_ANKLE)

        hip_w = math.hypot(left_hip[0] - right_hip[0], left_hip[1] - right_hip[1])
        sho_w = math.hypot(left_shoulder[0] - right_shoulder[0],
                           left_shoulder[1] - right_shoulder[1])
        sho_mid_x_f = (left_shoulder[0] + right_shoulder[0]) / 2.0
        sho_mid_y_f = (left_shoulder[1] + right_shoulder[1]) / 2.0
        hip_mid_y_f = (left_hip[1] + right_hip[1]) / 2.0

        # 3D world landmarks (in meters, hip-centered). Camera-invariant
        # estimates derived by MediaPipe's pose model. Used to compute rotation
        # angles that don't depend on the camera viewing angle.
        wlm = results.pose_world_landmarks.landmark if results.pose_world_landmarks else None

        rec = {
            "frame": frame_idx,
            "time_s": frame_idx / fps,
            "hip_width_px": hip_w,
            "shoulder_width_px": sho_w,
            "shoulder_mid_x": sho_mid_x_f,
            "shoulder_mid_y": sho_mid_y_f,
            "hip_mid_y": hip_mid_y_f,
            "head_x": head_x,
            "head_y": head_y,
            "left_hip_x": left_hip[0],   "left_hip_y": left_hip[1],
            "right_hip_x": right_hip[0], "right_hip_y": right_hip[1],
            "left_knee_x": left_knee[0], "left_knee_y": left_knee[1],
            "right_knee_x": right_knee[0], "right_knee_y": right_knee[1],
            "left_ankle_x": left_ankle[0], "left_ankle_y": left_ankle[1],
            "right_ankle_x": right_ankle[0], "right_ankle_y": right_ankle[1],
            # Per-landmark visibility (in [0, 1]) — added in Phase 1 for
            # phase_debug. Existing detector logic ignores these fields.
            "left_ankle_visibility":  float(lm[LEFT_ANKLE].visibility),
            "right_ankle_visibility": float(lm[RIGHT_ANKLE].visibility),
        }
        if wlm is not None:
            rec.update({
                "lhip3d_x": wlm[LEFT_HIP].x,  "lhip3d_y": wlm[LEFT_HIP].y,  "lhip3d_z": wlm[LEFT_HIP].z,
                "rhip3d_x": wlm[RIGHT_HIP].x, "rhip3d_y": wlm[RIGHT_HIP].y, "rhip3d_z": wlm[RIGHT_HIP].z,
                "lsho3d_x": wlm[LEFT_SHOULDER].x,  "lsho3d_y": wlm[LEFT_SHOULDER].y,  "lsho3d_z": wlm[LEFT_SHOULDER].z,
                "rsho3d_x": wlm[RIGHT_SHOULDER].x, "rsho3d_y": wlm[RIGHT_SHOULDER].y, "rsho3d_z": wlm[RIGHT_SHOULDER].z,
            })
        records.append(rec)
    frame_idx += 1

cap.release()
pose.close()

if not records:
    raise RuntimeError("No frames had a detected pose.")


# ---------- HANDEDNESS DETECTION ----------
# Right-handed batter: stands with LEFT side toward the pitcher → LEFT FOOT is
# the front foot → LEFT FOOT lifts during stride.
# Left-handed batter: stands with RIGHT side toward the pitcher → RIGHT FOOT
# strides.
# Detection: measure the vertical range of each ankle in the early portion of
# the clip (load + stride window). The front foot lifts noticeably more.
auto_note = ""
# Handedness auto-detection ratio (bigger/smaller of L and R foot motion in
# the early window). Populated below when HANDEDNESS == "AUTO"; left as None
# for manual overrides. Used by phase_debug for the low_handedness_confidence
# warning.
handedness_auto_ratio = None
if HANDEDNESS == "AUTO":
    n_records = len(records)
    # Tight early window — covers stance + load + stride. Crucially EXCLUDES
    # contact / follow-through where the BACK foot pivots and the heel lifts
    # (which would otherwise look like front-foot motion).
    early_window = max(5, int(n_records * 0.4))

    left_ay  = np.array([r["left_ankle_y"]  for r in records[:early_window]])
    right_ay = np.array([r["right_ankle_y"] for r in records[:early_window]])
    left_ax  = np.array([r["left_ankle_x"]  for r in records[:early_window]])
    right_ax = np.array([r["right_ankle_x"] for r in records[:early_window]])

    # Vertical lift (front foot lifts during stride).
    left_y_lift  = float(np.percentile(left_ay, 90)  - np.percentile(left_ay, 10))
    right_y_lift = float(np.percentile(right_ay, 90) - np.percentile(right_ay, 10))
    # Horizontal stride (front foot moves toward the pitcher; back foot mostly
    # stays planted). This is a strong signal even for no-stride hitters where
    # the foot doesn't lift much but still slides forward.
    left_x_disp  = float(np.percentile(left_ax, 90)  - np.percentile(left_ax, 10))
    right_x_disp = float(np.percentile(right_ax, 90) - np.percentile(right_ax, 10))

    # Combined motion score per ankle.
    left_motion  = left_y_lift  + left_x_disp
    right_motion = right_y_lift + right_x_disp

    if left_motion >= right_motion:
        HANDEDNESS = "RIGHT"
        bigger, smaller = left_motion, right_motion
        striding_foot = "left"
    else:
        HANDEDNESS = "LEFT"
        bigger, smaller = right_motion, left_motion
        striding_foot = "right"

    ratio = bigger / max(smaller, 1.0)
    handedness_auto_ratio = float(ratio)
    detail = (f"L: y={left_y_lift:.0f}px x={left_x_disp:.0f}px | "
              f"R: y={right_y_lift:.0f}px x={right_x_disp:.0f}px")
    if ratio < 1.3:
        auto_note = (f"⚠  Auto-handedness LOW CONFIDENCE — both feet moved similarly "
                     f"({detail}). Defaulted to {HANDEDNESS}. "
                     f"Re-run with LEFT or RIGHT as 2nd arg if wrong.")
    else:
        auto_note = (f"Auto-detected handedness: {HANDEDNESS}-handed "
                     f"({striding_foot} foot moved {ratio:.1f}× more — {detail}).")

# Now assign FRONT and BACK sides based on handedness.
if HANDEDNESS == "RIGHT":
    front_side, back_side = "left", "right"
else:
    front_side, back_side = "right", "left"

# Compute the handedness-dependent fields in each record.
for r in records:
    fh = (r[f"{front_side}_hip_x"],  r[f"{front_side}_hip_y"])
    fk = (r[f"{front_side}_knee_x"], r[f"{front_side}_knee_y"])
    fa = (r[f"{front_side}_ankle_x"], r[f"{front_side}_ankle_y"])
    ba = (r[f"{back_side}_ankle_x"],  r[f"{back_side}_ankle_y"])
    r["front_knee_angle_deg"] = joint_angle_deg(fh, fk, fa)
    r["stride_px"] = abs(fa[0] - ba[0])
    r["front_ankle_x"] = fa[0]
    r["front_ankle_y"] = fa[1]
    # Phase 1 instrumentation: propagate front-ankle visibility once
    # handedness is known. Existing detector logic ignores this field.
    r["front_ankle_visibility"] = float(
        r.get(f"{front_side}_ankle_visibility", 1.0)
    )

if auto_note:
    print(auto_note)

n = len(records)
times = np.array([r["time_s"] for r in records])
stride = smooth([r["stride_px"] for r in records], window=5)
knee = smooth([r["front_knee_angle_deg"] for r in records], window=5)
head_x_raw = smooth([r["head_x"] for r in records], window=5)
head_y_raw = smooth([r["head_y"] for r in records], window=5)
fa_y = smooth([r["front_ankle_y"] for r in records], window=5)

# Shoulder midpoint as the head reference. Camera translation cancels out
# (both head and shoulders shift together when the camera pans/shakes), and
# unlike hip midpoint, the shoulder midpoint stays roughly stationary
# vertically during a swing — shoulders ROTATE around the spine, while hips
# RISE significantly as the legs extend. Using hip_mid as the reference made
# leg extension look like 20 inches of head drift; shoulder_mid avoids that
# while still giving us camera invariance.
sho_mid_x_arr_head = smooth([r["shoulder_mid_x"] for r in records], window=5)
sho_mid_x_arr_head = np.asarray(sho_mid_x_arr_head)
sho_mid_y_arr_head = smooth([r["shoulder_mid_y"] for r in records], window=5)
sho_mid_y_arr_head = np.asarray(sho_mid_y_arr_head)
head_x = np.asarray(head_x_raw) - sho_mid_x_arr_head
head_y = np.asarray(head_y_raw) - sho_mid_y_arr_head

hip_w = smooth([r["hip_width_px"] for r in records], window=5)
sho_w = smooth([r["shoulder_width_px"] for r in records], window=5)
sho_mid_y_arr = smooth([r["shoulder_mid_y"] for r in records], window=5)
hip_mid_y_arr = smooth([r["hip_mid_y"] for r in records], window=5)
torso_length_px = np.abs(hip_mid_y_arr - sho_mid_y_arr)

# ---------- 3D ROTATION FROM WORLD LANDMARKS ----------
# Project hip and shoulder vectors onto the horizontal (XZ) plane and measure
# rotation from stance. Camera-invariant: same swing filmed from any angle
# gives (approximately) the same numbers.
#
# Rotation is computed as the SIGNED ANGLE between the current hip/shoulder
# vector and a stance-baseline vector, using dot-product (magnitude) and
# cross-product Z-component (sign). This avoids the atan2 ±180° wraparound
# entirely — the angle is always in [-180°, +180°] regardless of where the
# stance happens to point in MediaPipe's world frame.
#
# To handle long clips (e.g. 20s of pre-swing wandering, then a 200ms swing),
# we first locate the SWING BURST — the contiguous frames of high rotational
# velocity — and then anchor the stance baseline to the frames immediately
# before it. This makes the algorithm robust to videos that aren't trimmed
# tightly to the swing.

def _horizontal_angle_arr(records_, left_key, right_key):
    angles = []
    for r in records_:
        if f"{left_key}_x" not in r:
            angles.append(np.nan)
            continue
        dx = r[f"{right_key}_x"] - r[f"{left_key}_x"]
        dz = r[f"{right_key}_z"] - r[f"{left_key}_z"]
        angles.append(math.degrees(math.atan2(dz, dx)))
    return np.array(angles, dtype=float)

def _vec_arr_3d(records_, left_key, right_key):
    """Return Nx2 array of horizontal-plane (dx, dz) vectors per frame."""
    out = np.full((len(records_), 2), np.nan)
    for i, r in enumerate(records_):
        if f"{left_key}_x" not in r:
            continue
        out[i, 0] = r[f"{right_key}_x"] - r[f"{left_key}_x"]
        out[i, 1] = r[f"{right_key}_z"] - r[f"{left_key}_z"]
    return out

def _fill_nans(arr):
    if not np.any(np.isnan(arr)):
        return arr
    valid = ~np.isnan(arr)
    if not valid.any():
        return np.zeros_like(arr)
    out = np.copy(arr)
    out[~valid] = np.interp(np.flatnonzero(~valid), np.flatnonzero(valid), arr[valid])
    return out

def _angular_diff_deg(a1, a2):
    """Signed difference (a2 - a1) wrapped to [-180, 180]."""
    return ((a2 - a1 + 180.0) % 360.0) - 180.0

def _signed_angle_to_baseline(vecs, base):
    """For each (dx, dz) in vecs, return the signed angle (deg) relative to
    the baseline vector `base`. Uses dot-product for magnitude and the 2D
    cross-product z-component for sign. Result is in [-180, +180]."""
    if base is None:
        return np.zeros(len(vecs))
    bx, bz = float(base[0]), float(base[1])
    bn = math.hypot(bx, bz)
    if bn < 1e-9:
        return np.zeros(len(vecs))
    bxn, bzn = bx / bn, bz / bn
    out = np.zeros(len(vecs))
    last = 0.0
    for i, v in enumerate(vecs):
        if np.isnan(v[0]) or np.isnan(v[1]):
            out[i] = last
            continue
        n = math.hypot(v[0], v[1])
        if n < 1e-9:
            out[i] = last
            continue
        cxn, czn = v[0] / n, v[1] / n
        dot = max(-1.0, min(1.0, cxn * bxn + czn * bzn))
        ang = math.degrees(math.acos(dot))
        cross = bxn * czn - bzn * cxn  # 2D cross product z-component
        if cross < 0:
            ang = -ang
        out[i] = ang
        last = ang
    return out

have_3d = "lhip3d_x" in records[0]

# ---------- VIEW CLASSIFICATION ----------
# Decide between 3D and 2D rotation based on camera viewing angle.
# Single-camera 3D depth from MediaPipe is unreliable for profile views
# (body axis aligned with camera Z), where the 2D width-ratio method works
# better. Three-quarter views resolve depth well and 3D is more accurate.
ref_torso_len_pre = float(np.percentile(torso_length_px, 95))
peak_hip_w_pre = float(np.percentile(hip_w, 95))
hip_to_torso_pre = (peak_hip_w_pre / ref_torso_len_pre) if ref_torso_len_pre > 1.0 else 0.0
VIEW_3D_THRESHOLD = 0.6

prefer_3d = have_3d and hip_to_torso_pre >= VIEW_3D_THRESHOLD
ROTATION_METHOD = "3d_world" if prefer_3d else "2d_width_ratio"
CAMERA_VIEW = "three_quarter" if hip_to_torso_pre >= VIEW_3D_THRESHOLD else "profile"

# ---------- HELPER: BURST DETECTION FROM A 1D RATE SIGNAL ----------
# Used by both the 3D and 2D paths. Given a per-frame rate-of-change array,
# returns (burst_lo, burst_hi, burst_peak_idx, peak_rate, base_start, base_end).
# Burst detection — extracted to phase_burst.py so it can be unit-tested
# without pulling in mediapipe / opencv. detect_phases.py keeps the legacy
# `_find_burst_and_baseline` name as an alias for the imported function.
from phase_burst import (
    find_burst_and_baseline as _find_burst_and_baseline,
    _find_distinct_burst_peaks,
    MULTI_SWING_MIN_DURATION_S,
    MULTI_SWING_MIN_DISTANCE_S,
    MULTI_SWING_HEIGHT_RATIO,
)
import biomech

if prefer_3d:
    # ===================== 3D PATH (three-quarter views) =====================
    # ---- (1) SWING BURST from raw 3D atan2 frame-to-frame change ----
    hip_raw_atan = _fill_nans(_horizontal_angle_arr(records, "lhip3d", "rhip3d"))
    n_frames_3d = len(hip_raw_atan)
    rate = np.zeros(n_frames_3d)
    for i in range(1, n_frames_3d):
        rate[i] = abs(_angular_diff_deg(hip_raw_atan[i-1], hip_raw_atan[i]))
    burst_lo, burst_hi, burst_peak_idx, peak_rate, base_start, base_end = (
        _find_burst_and_baseline(rate, fps, n_frames_3d, min_rate=1.0,
                                 center_hint=SWING_CENTER_HINT)
    )

    # ---- (2) STANCE BASELINE VECTOR — frames just before the burst ----
    hip_vec = _vec_arr_3d(records, "lhip3d", "rhip3d")
    sho_vec = _vec_arr_3d(records, "lsho3d", "rsho3d")

    def _baseline_vec(vec_arr, lo_i, hi_i):
        seg = vec_arr[lo_i:hi_i]
        valid = ~np.isnan(seg[:, 0])
        if not valid.any():
            return None
        return np.array([float(np.nanmean(seg[valid, 0])),
                         float(np.nanmean(seg[valid, 1]))])

    hip_base = _baseline_vec(hip_vec, base_start, base_end)
    sho_base = _baseline_vec(sho_vec, base_start, base_end)

    # ---- (3) SIGNED ANGLE relative to stance baseline (dot/cross) ----
    hip_signed = _signed_angle_to_baseline(hip_vec, hip_base)
    sho_signed = _signed_angle_to_baseline(sho_vec, sho_base)

    # Auto-determine swing-direction sign from the burst window.
    burst_window = hip_signed[burst_lo:burst_hi + 1]
    if len(burst_window) > 0 and np.any(np.isfinite(burst_window)):
        peak_burst_i = int(np.argmax(np.abs(burst_window)))
        rotation_sign = 1.0 if burst_window[peak_burst_i] >= 0 else -1.0
    else:
        rotation_sign = 1.0

    # Unwrap to handle rotation past ±180° (follow-through over-rotation).
    hip_signed = np.degrees(np.unwrap(np.radians(hip_signed)))
    sho_signed = np.degrees(np.unwrap(np.radians(sho_signed)))

    hip_rotation = smooth(hip_signed * rotation_sign, window=5)
    shoulder_rotation = smooth(sho_signed * rotation_sign, window=5)

    SWING_BURST = (burst_lo, burst_hi, burst_peak_idx)
    print(f"  Camera view: three-quarter (hip/torso = {hip_to_torso_pre:.2f}) → using 3D rotation")
    print(f"  Swing burst: frames {burst_lo}-{burst_hi} "
          f"(peak {peak_rate:.1f}°/frame at frame {burst_peak_idx}, "
          f"baseline frames {base_start}-{base_end})")
else:
    # ===================== 2D PATH (profile views, or no 3D) =================
    # 2D width-ratio rotation: hip width shrinks as hips rotate away from
    # camera-perpendicular. Calibrated against the 95th percentile of
    # observed widths. Camera-angle sensitive but stable for profile views.
    hip_rotation_unsigned = width_to_rotation_deg(hip_w)
    shoulder_rotation_unsigned = width_to_rotation_deg(sho_w)

    # Find the swing burst from frame-to-frame change in 2D rotation.
    n_frames_2d = len(hip_rotation_unsigned)
    rate2d = np.abs(np.gradient(hip_rotation_unsigned))
    burst_lo, burst_hi, burst_peak_idx, peak_rate, base_start, base_end = (
        _find_burst_and_baseline(rate2d, fps, n_frames_2d, min_rate=0.3,
                                 center_hint=SWING_CENTER_HINT)
    )

    # 2D width-ratio is unsigned (always 0-90°). Subtract a stance baseline
    # so the value at stance is ~0, and the value at peak swing is positive.
    # Note: this isn't camera-invariant (a profile camera sees different
    # widths than a three-quarter one), so direct comparison between
    # different camera angles is flagged later in compare.py.
    hip_baseline_2d = float(np.median(hip_rotation_unsigned[base_start:base_end])) \
        if base_end > base_start else 0.0
    sho_baseline_2d = float(np.median(shoulder_rotation_unsigned[base_start:base_end])) \
        if base_end > base_start else 0.0

    hip_rel_2d = hip_rotation_unsigned - hip_baseline_2d
    sho_rel_2d = shoulder_rotation_unsigned - sho_baseline_2d

    # Sign convention: positive = swing direction. Determine from the burst
    # peak — width changes happen monotonically during the swing, so the
    # sign of the rate at the burst peak gives us the rotation direction.
    burst_signed_rate = (np.gradient(hip_rotation_unsigned)[burst_lo:burst_hi + 1]
                         if burst_hi > burst_lo else np.array([0.0]))
    if len(burst_signed_rate) > 0 and np.any(np.isfinite(burst_signed_rate)):
        # Use the median of the rate during the burst — robust to single-
        # frame noise. If positive, hips are widening (front-camera) or
        # narrowing (profile) — either way, tag this as the swing direction.
        rotation_sign = 1.0 if float(np.median(burst_signed_rate)) >= 0 else -1.0
    else:
        rotation_sign = 1.0

    hip_rotation = smooth(hip_rel_2d * rotation_sign, window=5)
    shoulder_rotation = smooth(sho_rel_2d * rotation_sign, window=5)

    SWING_BURST = (burst_lo, burst_hi, burst_peak_idx)
    view_label = "profile" if hip_to_torso_pre < VIEW_3D_THRESHOLD else "three-quarter (no 3D available)"
    print(f"  Camera view: {view_label} (hip/torso = {hip_to_torso_pre:.2f}) → using 2D width-ratio rotation")
    print(f"  Swing burst: frames {burst_lo}-{burst_hi} "
          f"(peak rate {peak_rate:.2f}°/frame at frame {burst_peak_idx}, "
          f"baseline frames {base_start}-{base_end})")

hip_sep = smooth(hip_rotation - shoulder_rotation, window=5)
hip_vel = smooth(np.gradient(hip_rotation), window=5)
hip_accel = smooth(np.gradient(hip_vel), window=5)

# ---- HIP-SHOULDER SEPARATION OVERRIDE FOR 3D PATH ----
# pose_world_landmarks is inferred from a single camera with a learned
# torso prior, so 3D-derived shoulder rotation tends to track 3D-derived
# hip rotation (collapsing the rotational lag we want to measure).
# The 2D widths of each joint pair shrink/expand independently, so they
# preserve the lag even on three-quarter views. Override hip_sep with a
# 2D-widths-based calculation whenever we're using the 3D path.
if prefer_3d:
    _hip_rot_2d  = width_to_rotation_deg(hip_w)
    _sho_rot_2d  = width_to_rotation_deg(sho_w)
    _hip_base_2d = (float(np.median(_hip_rot_2d[base_start:base_end]))
                    if base_end > base_start else 0.0)
    _sho_base_2d = (float(np.median(_sho_rot_2d[base_start:base_end]))
                    if base_end > base_start else 0.0)
    # Sign convention: align with the same swing direction the 3D path picked,
    # using the median sign of the 2D hip excursion across the burst.
    _burst_hip = (_hip_rot_2d[burst_lo:burst_hi + 1] - _hip_base_2d)
    if len(_burst_hip) > 0 and np.any(np.isfinite(_burst_hip)):
        _sign_2d = 1.0 if float(np.median(_burst_hip)) >= 0 else -1.0
    else:
        _sign_2d = 1.0
    _sep_2d = ((_hip_rot_2d - _hip_base_2d) - (_sho_rot_2d - _sho_base_2d)) * _sign_2d
    hip_sep = smooth(_sep_2d, window=5)


# ---------- PHASE DETECTION (v3) ----------
# When we have a swing burst from the 3D rotation analysis, anchor the
# entire phase search to it. Otherwise (2D fallback) search the whole clip
# starting after a small lead-in. The burst-based search is robust to any
# clip length and to clips with lots of pre-swing or post-swing footage.
if SWING_BURST is not None:
    burst_lo, burst_hi, _bpk = SWING_BURST
    # Slight padding around the burst for the velocity search.
    search_start = max(int(fps * 0.05), burst_lo - int(fps * 0.5))
    search_end = min(n, burst_hi + int(fps * 0.4))
else:
    search_start = min(int(fps * 0.5), n // 4)
    search_end = n
search_end = max(search_start + 3, search_end)

# Find swing window via peak rotational velocity (within search window).
vel_segment = np.abs(hip_vel[search_start:search_end])
peak_vel_idx = search_start + int(np.argmax(vel_segment))

# CONTACT — moment of maximum hip rotational VELOCITY.
# Biomechanically this is the closest single-pose proxy for bat-ball contact:
# the hips reach peak angular speed at or just before the bat meets the ball.
# (Earlier versions used max hip ACCELERATION which actually marks the start
# of the swing burst, well before contact — that produced the cascade bug
# where launch got clamped to `contact - 1`.)
contact = peak_vel_idx
if CONTACT_OVERRIDE is not None:
    contact = max(1, min(int(CONTACT_OVERRIDE), n - 1))

# PEAK_ROTATION — max hip rotation after contact, but constrained to burst.
# Using the burst's tail prevents picking up post-swing wandering on long
# clips. Old behavior (0.5s window) as the upper bound when no burst known.
if SWING_BURST is not None:
    post_contact_end = min(n, max(contact + int(fps * 0.05), burst_hi + 1))
else:
    post_contact_end = min(n, contact + int(fps * 0.5))
peak_rotation = contact + int(np.argmax(hip_rotation[contact:post_contact_end]))

# FOOT_PLANT — walk back from contact via front ankle Y
search_back_start = max(search_start, contact - int(fps * 0.6))
search_back_end = contact

if search_back_end > search_back_start + 2:
    fa_y_window = fa_y[search_back_start:search_back_end]
    foot_plant = search_back_start + int(np.argmax(fa_y_window))
    min_gap = int(fps * 0.08)
    if contact - foot_plant < min_gap:
        foot_plant = max(search_back_start, contact - int(fps * 0.18))
else:
    foot_plant = max(0, contact - int(fps * 0.18))
if FOOT_PLANT_OVERRIDE is not None:
    foot_plant = max(0, min(int(FOOT_PLANT_OVERRIDE), contact - 1))

# LAUNCH — start of the high-velocity rotation burst (when hips fire).
# We already detected the burst window during rotation analysis, so use its
# leading edge directly. Earlier versions used "moment of deepest knee bend"
# (knee_min_idx) which was unreliable: the search window extended to
# peak_rotation, so knee_min often landed near the end of the swing and the
# launch < contact clamp collapsed it to a single-frame launch_to_contact.
if SWING_BURST is not None:
    launch_candidate = SWING_BURST[0]  # burst_lo
else:
    launch_candidate = foot_plant + 1
launch = max(foot_plant + 1, launch_candidate)
if contact > foot_plant + 1:
    launch = min(launch, contact - 1)

# LOAD_START — walk back from foot_plant (bounded so long pre-swing clips
# don't yield 20-second "load" durations).
# Stride/knee baseline must come from BEFORE the load (the pre-burst window
# used for rotation captures the load motion itself, which is exactly what
# we want stride/knee to be different from). Use frames at least 0.5s before
# foot_plant. Use 25th percentile for stride (smallest = most still) and
# 75th percentile for knee (largest = most extended) to be robust to small
# pre-swing fidgeting.
sk_end = max(5, foot_plant - int(fps * 0.5))
sk_start = 0
if sk_end - sk_start >= 5:
    stride_baseline = float(np.percentile(stride[sk_start:sk_end], 25))
    knee_baseline = float(np.percentile(knee[sk_start:sk_end], 75))
else:
    # Very short or weird clip — fall back to whatever we can measure.
    fallback_hi = max(5, min(int(fps * 0.5), max(1, foot_plant - 2)))
    stride_baseline = float(np.median(stride[:fallback_hi])) if fallback_hi > 0 else 0.0
    knee_baseline = float(np.median(knee[:fallback_hi])) if fallback_hi > 0 else 180.0

LOAD_STRIDE_DELTA = 5
LOAD_KNEE_DELTA = 3

# Don't search further back than ~1.0s before foot_plant — anything earlier
# is pre-swing wandering, not loading.
load_search_floor = max(0, foot_plant - int(fps * 1.0))
load_start = None
for i in range(foot_plant, load_search_floor - 1, -1):
    if (stride[i] - stride_baseline < LOAD_STRIDE_DELTA
            and knee_baseline - knee[i] < LOAD_KNEE_DELTA):
        load_start = i + 1
        break
if load_start is None or load_start >= foot_plant:
    load_start = max(load_search_floor, foot_plant - int(fps * 0.5))

# KNEE_MIN — moment of deepest front-knee bend (used only for the
# "knee_at_min" report value, NOT for launch detection anymore).
knee_search_start = max(load_start, foot_plant - int(fps * 0.2))
knee_search_end = min(n, peak_rotation + 1)
if knee_search_end > knee_search_start:
    knee_min_idx = knee_search_start + int(np.argmin(knee[knee_search_start:knee_search_end]))
else:
    knee_min_idx = foot_plant
# (launch was already set above to burst_lo, with foot_plant/contact clamps.)

# FINISH
finish = min(n - 1, peak_rotation + int(fps * 0.4))

phases = {
    "load_start":     load_start,
    "foot_plant":     foot_plant,
    "launch":         launch,
    "contact":        contact,
    "peak_rotation":  peak_rotation,
    "finish":         finish,
}

edge_warnings = []
for name, idx in phases.items():
    if idx <= 1 or idx >= n - 2:
        edge_warnings.append(f"  ⚠  {name} at frame {idx} (edge of video)")


# ---------- PHASE-RELATIVE METRICS ----------
def head_drift(i, j):
    dx = float(head_x[j] - head_x[i])
    dy = float(head_y[j] - head_y[i])
    return dx, dy, math.hypot(dx, dy)

head_dx_swing, head_dy_swing, head_total_swing = head_drift(foot_plant, contact)

# ---- NORMALIZED HEAD DRIFT (resolution-/camera-distance-invariant) ----
# Express head movement in "torso lengths" (shoulder-mid to hip-mid).
# Use the 95th-percentile torso across the entire video as the reference —
# this represents the "most upright" body and avoids posture-induced
# shrinkage from bent-over batting stances.
ref_torso_len = float(np.percentile(torso_length_px, 95))
if ref_torso_len > 1.0:
    head_dx_norm = head_dx_swing / ref_torso_len
    head_dy_norm = head_dy_swing / ref_torso_len
    head_total_norm = head_total_swing / ref_torso_len
else:
    head_dx_norm = head_dy_norm = head_total_norm = 0.0

# ---- CAMERA-VIEW SANITY METRIC ----
# Hip-width / reference-torso-length, using max hip width across the swing.
# Profile/side cameras → small ratio (hips look narrow). Front/three-quarter
# cameras → larger ratio. Both numerator and denominator are now posture-
# robust so the ratio actually reflects camera viewpoint.
peak_hip_w = float(np.percentile(hip_w, 95))
hip_to_torso_ratio = peak_hip_w / ref_torso_len if ref_torso_len > 1.0 else 0.0

sep_at_foot_plant = float(hip_sep[foot_plant])
sep_at_launch = float(hip_sep[launch])
sep_at_contact = float(hip_sep[contact])
sep_peak = float(np.max(hip_sep[foot_plant:peak_rotation + 1]))
sep_peak_idx = foot_plant + int(np.argmax(hip_sep[foot_plant:peak_rotation + 1]))

hip_rot_at_foot_plant = float(hip_rotation[foot_plant])
hip_rot_at_contact = float(hip_rotation[contact])
hip_rot_peak = float(hip_rotation[peak_rotation])

knee_at_foot_plant = float(knee[foot_plant])
knee_at_min = float(knee[knee_min_idx])
knee_at_contact = float(knee[contact])

t_load = times[foot_plant] - times[load_start]
t_foot_plant_to_launch = times[launch] - times[foot_plant]
t_launch_to_contact = times[contact] - times[launch]
t_swing = times[contact] - times[foot_plant]


# ---------- PHASE_DEBUG_V1 INSTRUMENTATION (observability only) ----------
# This block runs only when PHASE_DEBUG_V1 is enabled. It computes a parallel
# set of observations (candidate stable contacts, per-phase confidence,
# stride-style classification, alternatives, warnings) WITHOUT modifying any
# of the phase indices, metric values, or score outputs above. The output is
# written into the fingerprint as `analysis_debug` and to a separate
# `<base>_phases_debug.json` file. See phase_debug.py for the algorithm.
#
# Wrapped in try/except: instrumentation is observability-only and MUST NEVER
# break the legacy detector. If any exception escapes, log it and continue
# with a fingerprint that simply omits the analysis_debug field.
analysis_debug = None
if PHASE_DEBUG_V1:
    try:
        import phase_debug  # local import so disabled runs pay no cost
        fa_visibility_arr = np.asarray(
            [r.get("front_ankle_visibility", 1.0) for r in records], dtype=float
        )
        burst_lo_dbg, burst_hi_dbg, burst_peak_dbg = SWING_BURST
        ref_torso_for_debug = (
            float(np.percentile(torso_length_px, 95))
            if len(torso_length_px) > 0 else 0.0
        )
        analysis_debug = phase_debug.build_debug_payload(
            times=times,
            fa_y=fa_y,
            vis_fa=fa_visibility_arr,
            hip_vel=hip_vel,
            stride=stride,
            knee=knee,
            phases=phases,
            burst_lo=int(burst_lo_dbg),
            burst_hi=int(burst_hi_dbg),
            burst_peak=int(burst_peak_dbg),
            fps=float(fps),
            torso_length_px=ref_torso_for_debug,
            handedness=HANDEDNESS,
            handedness_ratio=handedness_auto_ratio,
            edge_warnings=edge_warnings,
        )
        # Also emit a standalone debug JSON next to the fingerprint
        with open(OUTPUT_PHASES_DEBUG, "w") as f:
            json.dump(analysis_debug, f, indent=2)
        print(phase_debug.format_debug_summary(analysis_debug))
        print(f"Saved phase debug  → {OUTPUT_PHASES_DEBUG}")
    except Exception as _dbg_exc:
        # Instrumentation failure must not block the legacy pipeline.
        import traceback
        print(f"⚠  PHASE_DEBUG_V1 instrumentation failed: {_dbg_exc!r}")
        print("   Fingerprint will be written without analysis_debug.")
        traceback.print_exc()
        analysis_debug = None


# ---------- DETECTOR_V4 SHADOW MODE (observability + parallel detection) ----------
# v4 runs ALONGSIDE the legacy v3 detector. It selects foot_plant by ranking
# the stable-contact periods from analysis_debug against rotation_onset and
# contact timing — fixing the toe-tap bug where v3's argmax(fa_y) picks the
# tap instead of the final plant. v4 outputs are written to a new
# `detector_v4` field on the fingerprint and to a standalone debug JSON.
# v3 outputs are unchanged.
#
# Wrapped in try/except: a v4 failure must never break v3 output.
detector_v4_result = None
if DETECTOR_V4:
    try:
        import phase_detector_v4
        if analysis_debug is None:
            raise RuntimeError(
                "DETECTOR_V4 requires PHASE_DEBUG_V1 instrumentation, but "
                "analysis_debug was not produced (instrumentation may have failed)."
            )
        detector_v4_result = phase_detector_v4.detect_phases_v4(
            times=times,
            stride=stride,
            knee=knee,
            analysis_debug=analysis_debug,
            phases_v3=phases,
            burst_lo=int(SWING_BURST[0]),
            burst_hi=int(SWING_BURST[1]),
            fps=float(fps),
        )
        with open(OUTPUT_DETECTOR_V4_DEBUG, "w") as f:
            json.dump(detector_v4_result, f, indent=2)
        print(phase_detector_v4.format_v4_summary(detector_v4_result))
        print(f"Saved v4 detector → {OUTPUT_DETECTOR_V4_DEBUG}")
    except Exception as _v4_exc:
        import traceback
        print(f"⚠  DETECTOR_V4 shadow run failed: {_v4_exc!r}")
        print("   Fingerprint will be written without detector_v4 block.")
        traceback.print_exc()
        detector_v4_result = None


# ---------- SAVE CSV ----------
records_out = []
for i, r in enumerate(records):
    rec = dict(r)
    rec["hip_rotation_deg"] = float(hip_rotation[i])
    rec["shoulder_rotation_deg"] = float(shoulder_rotation[i])
    rec["hip_shoulder_sep_deg"] = float(hip_sep[i])
    records_out.append(rec)

with open(OUTPUT_CSV, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(records_out[0].keys()))
    writer.writeheader()
    writer.writerows(records_out)


# ---------- PRINT REPORT ----------
print()
print("=" * 60)
print("              SWING ANALYSIS  (v3)")
print("=" * 60)
print()
print("DETECTED PHASES")
print("-" * 60)
for name, idx in phases.items():
    print(f"  {name:<14}  t = {times[idx]:5.2f}s   (frame {idx})")
if edge_warnings:
    print()
    print("WARNINGS:")
    for w in edge_warnings:
        print(w)
print()
print("TIMING")
print("-" * 60)
print(f"  Load duration            (load_start → foot_plant) : {t_load*1000:5.0f} ms")
print(f"  Foot plant → launch                                : {t_foot_plant_to_launch*1000:5.0f} ms")
print(f"  Launch → contact                                   : {t_launch_to_contact*1000:5.0f} ms")
print(f"  Total swing duration     (foot_plant → contact)    : {t_swing*1000:5.0f} ms")
print()
_rot_header_note = ("3D world landmarks — camera-invariant"
                    if ROTATION_METHOD == "3d_world"
                    else "2D width-ratio fallback — camera-angle sensitive")
print(f"ROTATION ({_rot_header_note})")
print("-" * 60)
print(f"  Hip rotation at foot plant         : {hip_rot_at_foot_plant:5.1f}°")
print(f"  Hip rotation at contact            : {hip_rot_at_contact:5.1f}°")
print(f"  Peak hip rotation                  : {hip_rot_peak:5.1f}°")
print()
print(f"  Hip-shoulder separation at foot plant : {sep_at_foot_plant:+6.1f}°")
print(f"  Hip-shoulder separation at launch     : {sep_at_launch:+6.1f}°")
print(f"  Hip-shoulder separation at contact    : {sep_at_contact:+6.1f}°")
print(f"  Peak hip-shoulder separation          : {sep_peak:+6.1f}°  (at t={times[sep_peak_idx]:.2f}s)")
print()
print("FRONT KNEE")
print("-" * 60)
print(f"  Angle at foot plant : {knee_at_foot_plant:5.1f}°")
print(f"  Most bent (load)    : {knee_at_min:5.1f}°  at t = {times[knee_min_idx]:.2f}s")
print(f"  Angle at contact    : {knee_at_contact:5.1f}°")
print(f"  Re-extension        : {knee_at_contact - knee_at_min:+.1f}°")
print()
print("HEAD MOVEMENT (foot plant → contact)")
print("-" * 60)
print(f"  Δx (toward pitcher) : {head_dx_swing:+6.0f} px   ({head_dx_norm:+.2f} torso-lengths)")
print(f"  Δy (vertical)       : {head_dy_swing:+6.0f} px   ({head_dy_norm:+.2f} torso-lengths, +=down)")
print(f"  Total drift         : {head_total_swing:6.0f} px   ({head_total_norm:.2f} torso-lengths)")
print()
print("CAMERA VIEW (sanity check)")
print("-" * 60)
print(f"  Hip-width / torso-length (stance avg) : {hip_to_torso_ratio:.2f}")
print(f"  Lower = more profile/side view; higher = more front/three-quarter view.")
print()
print(f"Saved per-frame data → {OUTPUT_CSV}")


# ---------- CHART ----------
fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)

phase_regions = [
    (0,                    phases["load_start"],  "#dbeaf3"),
    (phases["load_start"], phases["foot_plant"],  "#fde9c4"),
    (phases["foot_plant"], phases["contact"],     "#f9c8c8"),
    (phases["contact"],    phases["finish"],      "#dcdcdc"),
]

for ax in axes:
    for start_i, end_i, color in phase_regions:
        if end_i > start_i:
            ax.axvspan(times[start_i], times[end_i], color=color, alpha=0.55, zorder=0)

axes[0].plot(times, hip_rotation, color="#1f4e79", linewidth=2, label="Hip rotation")
axes[0].plot(times, shoulder_rotation, color="#9d2f2f", linewidth=2, label="Shoulder rotation")
axes[0].plot(times, hip_sep, color="#444444", linewidth=1.5, linestyle="--",
             label="Separation (hip − shoulder)")
axes[0].set_ylabel("Rotation (°)")
axes[0].set_title("Swing Biomechanics — Phase-Aware (v3)", fontsize=14, fontweight="bold")
axes[0].legend(loc="upper left", fontsize=9)

axes[1].plot(times, knee, color="#2ca02c", linewidth=2)
axes[1].set_ylabel("Front knee\nangle (°)")

axes[2].plot(times, stride, color="#ff7f0e", linewidth=2)
axes[2].set_ylabel("Stride\ndistance (px)")

axes[3].plot(times, head_x - head_x[0], color="#9467bd", linewidth=2, label="head Δx (toward pitcher)")
axes[3].plot(times, head_y - head_y[0], color="#d62728", linewidth=2, label="head Δy (vertical, +=down)")
axes[3].set_ylabel("Head drift\nfrom start (px)")
axes[3].set_xlabel("Time (s)")
axes[3].legend(loc="upper left", fontsize=9)

phase_label_map = {
    "load_start":    ("Load",      "top"),
    "foot_plant":    ("Foot plant","bottom"),
    "launch":        ("Launch",    "top"),
    "contact":       ("Contact",   "bottom"),
    "peak_rotation": ("Peak rot.", "top"),
    "finish":        ("Finish",    "bottom"),
}

for name, idx in phases.items():
    for ax in axes:
        ax.axvline(times[idx], color="black", linestyle="--", alpha=0.55, linewidth=1)
    label, position = phase_label_map[name]
    if position == "top":
        axes[0].annotate(
            label, xy=(times[idx], axes[0].get_ylim()[1]),
            xytext=(0, 18), textcoords="offset points",
            ha="center", fontsize=8.5, fontweight="bold",
        )
    else:
        axes[0].annotate(
            label, xy=(times[idx], axes[0].get_ylim()[1]),
            xytext=(0, 4), textcoords="offset points",
            ha="center", fontsize=8.5, fontweight="bold",
        )

for ax in axes:
    ax.grid(True, alpha=0.3, zorder=1)

plt.tight_layout()
plt.savefig(OUTPUT_CHART, dpi=120, bbox_inches="tight")
plt.close()

print(f"Saved chart        → {OUTPUT_CHART}")

# ---------- SLOW-MOTION DETECTION + TIMING CORRECTION ----------
# Real-time MLB swings (foot_plant → contact) cluster around ~150ms. Anything
# meaningfully longer is almost always slow-motion playback (phone slow-mo,
# YouTube breakdown videos, etc.) where a real swing is recorded at a high
# capture FPS but plays back at standard FPS, making the swing look stretched.
#
# We compute a slow_mo_factor = measured_swing_ms / TARGET_REAL_TIME_MS, but
# only when the measured swing exceeds SLOW_MO_THRESHOLD_MS (so we don't
# falsely "correct" a normal-speed swing that happens to be on the slower
# end of MLB variability). The corrected timing values are file_ms / factor,
# giving us real-time-equivalent milliseconds. compare.py uses the corrected
# values so any clip — slow-mo or not — can serve as a valid reference.
TARGET_REAL_TIME_SWING_MS = 150.0
SLOW_MO_THRESHOLD_MS      = 250.0

_t_swing_ms = float(t_swing * 1000)
if _t_swing_ms > SLOW_MO_THRESHOLD_MS:
    slow_mo_factor = _t_swing_ms / TARGET_REAL_TIME_SWING_MS
else:
    slow_mo_factor = 1.0

# ───── Power Sequence biomech block (see biomech.py + spec) ─────
try:
    sequence_block = biomech.compute_sequence(
        hip_vel=hip_vel,
        shoulder_rotation=shoulder_rotation,
        load_start=int(phases["load_start"]),
        launch=int(phases["launch"]),
        contact=int(phases["contact"]),
        fps=float(fps),
        slow_mo_factor=float(slow_mo_factor),
    )
except Exception as _seq_exc:
    # Biomech failure must not break the pipeline — fall back to empty block.
    import traceback
    print(f"⚠  Power Sequence biomech compute failed: {_seq_exc!r}")
    traceback.print_exc()
    sequence_block = {
        "sequencing_lag_ms":         None,
        "peak_hip_omega_deg_s":      None,
        "front_side_stability_pct":  None,
        "hip_peak_frame":            None,
        "shoulder_peak_frame":       None,
        "rating": {"sequencing_lag": None,
                   "peak_hip_omega": None,
                   "front_side_stability": None},
    }

# ---------- BIOMECH SIGNAL DUMP (R&D only — gated by env, zero normal cost) ----------
# Writes the per-frame rotation signals + phase frames to a sidecar so we can
# experiment with lag/omega/flyout formulations OFFLINE (no repeated MediaPipe).
if os.environ.get("BIOMECH_DUMP") == "1":
    try:
        _hr2d = width_to_rotation_deg(hip_w)
        _sr2d = width_to_rotation_deg(sho_w)
        _sig_dump = {
            "video": os.path.basename(INPUT_VIDEO),
            "fps": float(fps),
            "prefer_3d": bool(prefer_3d),
            "phases_frame": {k: int(v) for k, v in phases.items()},
            "hip_rotation":      [float(x) for x in hip_rotation],
            "shoulder_rotation": [float(x) for x in shoulder_rotation],
            "hip_rot_2d":        [float(x) for x in _hr2d],
            "sho_rot_2d":        [float(x) for x in _sr2d],
            "hip_w":             [float(x) for x in hip_w],
            "sho_w":             [float(x) for x in sho_w],
        }
        with open(f"{_base}_signals.json", "w") as _sf:
            json.dump(_sig_dump, _sf)
        print(f"  BIOMECH_DUMP → {_base}_signals.json")
    except Exception as _sig_exc:
        print(f"  BIOMECH_DUMP failed: {_sig_exc!r}")

# ---------- STRIDE DIRECTION (front foot toward the pitcher?) ----------
# ref_torso_len is the 95th-pct torso length used elsewhere for
# scale-invariant normalization.
# Use the midpoint of the pre-load window as the stance reference — more
# representative of settled stance than frame 0 on clips that open mid-load.
_stance_ref = max(0, min((sk_start + sk_end) // 2, len(records) - 1))
_front_ax = [r["front_ankle_x"] for r in records]
_back_ax = [r[f"{back_side}_ankle_x"] for r in records]
stride_block = biomech.stride_direction(
    _front_ax, _back_ax, _stance_ref, int(phases["foot_plant"]), ref_torso_len,
)

# Sign head Δx so positive = toward the pitcher (head_x is raw image-x, so the
# "toward pitcher" label was backwards for one handedness). Same convention as
# the stride direction above, so the two always agree.
try:
    _px_sign = biomech.pitcher_sign(_front_ax[_stance_ref], _back_ax[_stance_ref])
except Exception:
    _px_sign = 1.0
head_dx_swing = head_dx_swing * _px_sign
head_dx_norm = head_dx_norm * _px_sign

# ---------- FINGERPRINT JSON (for cross-swing comparison) ----------
fingerprint = {
    "video": os.path.basename(INPUT_VIDEO),
    "handedness": HANDEDNESS,
    "rotation_method": ROTATION_METHOD,
    "fps": float(fps),
    "slow_mo_factor": float(slow_mo_factor),
    "phases_t": {name: float(times[idx]) for name, idx in phases.items()},
    "phases_frame": {name: int(idx) for name, idx in phases.items()},
    "sequence": sequence_block,
    "timing_ms": {
        "load_duration":          float(t_load * 1000),
        "foot_plant_to_launch":   float(t_foot_plant_to_launch * 1000),
        "launch_to_contact":      float(t_launch_to_contact * 1000),
        "total_swing":            float(t_swing * 1000),
    },
    "timing_ms_corrected": {
        "load_duration":          float(t_load * 1000 / slow_mo_factor),
        "foot_plant_to_launch":   float(t_foot_plant_to_launch * 1000 / slow_mo_factor),
        "launch_to_contact":      float(t_launch_to_contact * 1000 / slow_mo_factor),
        "total_swing":            float(t_swing * 1000 / slow_mo_factor),
    },
    "rotation_deg": {
        "hip_at_foot_plant":          float(hip_rot_at_foot_plant),
        "hip_at_contact":             float(hip_rot_at_contact),
        "peak_hip":                   float(hip_rot_peak),
        "separation_at_foot_plant":   float(sep_at_foot_plant),
        "separation_at_launch":       float(sep_at_launch),
        "separation_at_contact":      float(sep_at_contact),
        "peak_separation":            float(sep_peak),
        "peak_separation_t":          float(times[sep_peak_idx]),
    },
    "knee_deg": {
        "at_foot_plant":   float(knee_at_foot_plant),
        "min_during_load": float(knee_at_min),
        "at_contact":      float(knee_at_contact),
        "re_extension":    float(knee_at_contact - knee_at_min),
    },
    "head_movement_px_foot_plant_to_contact": {
        "dx": float(head_dx_swing),
        "dy": float(head_dy_swing),
        "total_drift": float(head_total_swing),
    },
    "head_movement_normalized_foot_plant_to_contact": {
        "dx_torso": float(head_dx_norm),
        "dy_torso": float(head_dy_norm),
        "total_drift_torso": float(head_total_norm),
    },
    "camera_view": {
        "hip_to_torso_ratio_stance": float(hip_to_torso_ratio),
    },
    "stride": stride_block,
}

# Attach Phase 1 observability payload when PHASE_DEBUG_V1 is on. This is
# additive — every other fingerprint field above is unchanged from the
# legacy v3 detector.
if analysis_debug is not None:
    fingerprint["analysis_debug"] = analysis_debug

# Attach Phase 2 v4 shadow result when DETECTOR_V4 is on. Also additive;
# never mutates the v3 phases above. The legacy `phases_t` and `phases_frame`
# fields continue to reflect the v3 detector exactly.
if detector_v4_result is not None:
    fingerprint["detector_v4"] = detector_v4_result
    # Convenience top-level mirrors so consumers can compare without
    # walking into detector_v4.phases — kept symmetrical with the v3
    # phases_t / phases_frame fields above.
    fingerprint["phases_t_v4"] = detector_v4_result["phases_t"]
    fingerprint["phases_frame_v4"] = detector_v4_result["phases"]

# Player age (optional) — additive; absent when not supplied.
if PLAYER_AGE is not None:
    fingerprint["age"] = int(PLAYER_AGE)

# Lower-body tracking quality (mean front-ankle visibility, the key brace /
# stability landmark) so analyzer._pose_visibility can soften pillar confidence
# when the legs/feet aren't well tracked. Additive; defaults to fully-visible.
try:
    _fa_vis = [float(r.get("front_ankle_visibility", 1.0)) for r in records]
    if _fa_vis:
        fingerprint["lower_body_visibility"] = max(
            0.0, min(1.0, sum(_fa_vis) / len(_fa_vis))
        )
except Exception:
    pass

# Pose-detection coverage: fraction of frames where a pose was found. Low
# coverage (poor light / distance / motion blur) is the cheapest global signal
# that every metric is unreliable; analyzer softens all pillars accordingly.
# Additive; absent -> analyzer applies no penalty.
try:
    _total_frames = int(frame_idx)
    if _total_frames > 0:
        fingerprint["pose_coverage"] = max(
            0.0, min(1.0, len(records) / _total_frames)
        )
except Exception:
    pass

with open(OUTPUT_FINGERPRINT, "w") as f:
    json.dump(fingerprint, f, indent=2)

print(f"Saved fingerprint  → {OUTPUT_FINGERPRINT}")
print()
print("Open the chart — colored bands are phases, dashed lines mark moments.")
