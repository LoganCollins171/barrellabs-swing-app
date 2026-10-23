"""BL-008C.37 -- synthetic pose clips with a realistic body and swing-like
hand motion, for tests that must not depend on private footage.

Units: normalized image coords (aspect 1.0). Torso (shoulder-mid to
hip-mid) is 0.2 tall. A swing moves the hand midpoint around a 0.085-radius
half circle in SWING_S seconds -- roughly 9 torso lengths/s at its fastest,
like the real phone swings in the BL-008C.37 set (6-10)."""
from __future__ import annotations

import math

from engine_v2.types import LandmarkFrame

SWING_S = 0.25
_HIP = (0.5, 0.55)
_REST = (-0.12, -0.12)   # hands relative to hip mid, in the stance


def _swing_offset(u: float) -> tuple[float, float]:
    """Hands relative to the hip midpoint, u in [0,1] through the swing."""
    a = math.pi * u
    return (-0.12 + 0.085 * (1 - math.cos(a)) * 1.6, -0.12 - 0.085 * math.sin(a) * 0.5)


def _lerp(p, q, w):
    return (p[0] + (q[0] - p[0]) * w, p[1] + (q[1] - p[1]) * w)


def hand_path(t: float, swings: list[float], sway: float = 0.004):
    """Hand offset at time t. Each swing: 0.4 s slow load, a fast swing
    (with a short mid-swing slow-down so the hand speed has TWO peaks, like
    real swings), a 0.35 s follow-through, then a 0.6 s return to rest."""
    off = (_REST[0] + sway * math.sin(2 * math.pi * 1.3 * t), _REST[1] + sway * math.cos(2 * math.pi * 0.9 * t))
    for s0 in swings:
        load0, sw0 = s0 - 0.4, s0
        sw1 = sw0 + SWING_S
        ft1 = sw1 + 0.35
        back1 = ft1 + 0.6
        end_pos = _swing_offset(1.0)
        finish = (end_pos[0] + 0.05, end_pos[1] - 0.06)
        if load0 <= t < sw0:
            off = _lerp(_REST, (_REST[0] - 0.03, _REST[1] - 0.01), (t - load0) / 0.4)
        elif sw0 <= t < sw1:
            u = (t - sw0) / SWING_S
            # two speed peaks inside ONE swing (launch, then through the zone)
            u = u - 0.06 * math.sin(2 * math.pi * u)
            p = _swing_offset(u)
            off = (p[0] - 0.03 * (1 - u), p[1] - 0.01 * (1 - u))
        elif sw1 <= t < ft1:
            w = (t - sw1) / 0.35
            off = _lerp(end_pos, finish, 1 - (1 - w) ** 2)
        elif ft1 <= t < back1:
            off = _lerp(finish, _REST, (t - ft1) / 0.6)
    return off


def make_clip(duration_s: float, fps: float, swings: list[float], *, timestamps: bool = True,
              walk_from: float | None = None, drop: tuple[float, float] | None = None,
              glitch_at: float | None = None, sway: float = 0.004) -> list[LandmarkFrame]:
    """A clip of `duration_s` at `fps`. `walk_from`: the whole body drifts
    sideways from then on (walking off; arms swing in opposite directions,
    so the hand MIDPOINT barely moves relative to the hips). `drop`: (t0,
    t1) with no pose at all. `glitch_at`: one frame where a wrist snaps
    far away."""
    frames = []
    n = int(round(duration_s * fps))
    for i in range(n):
        t = i / fps
        if drop and drop[0] <= t < drop[1]:
            frames.append(LandmarkFrame(frame_index=i, pts_s=round(t, 6) if timestamps else None,
                                        landmarks=None, mean_visibility=0.0))
            continue
        dx = 0.0
        arm = 0.0
        if walk_from is not None and t >= walk_from:
            dx = 0.12 * (t - walk_from)             # body walks off at ~0.6 torso/s
            arm = 0.06 * math.sin(2 * math.pi * 1.0 * (t - walk_from))
        hip = (_HIP[0] + dx, _HIP[1])
        hx, hy = hand_path(t, swings, sway=sway)
        lw = (hip[0] + hx + arm, hip[1] + hy)
        rw = (hip[0] + hx - arm + 0.02, hip[1] + hy + 0.01)
        if glitch_at is not None and abs(t - glitch_at) < 0.5 / fps:
            lw = (lw[0] + 0.5, lw[1] + 0.4)
        lms = {
            "left_shoulder": (hip[0] - 0.05, 0.35, 0.95), "right_shoulder": (hip[0] + 0.05, 0.35, 0.95),
            "left_hip": (hip[0] - 0.04, 0.55, 0.95), "right_hip": (hip[0] + 0.04, 0.55, 0.95),
            "left_wrist": (lw[0], lw[1], 0.9), "right_wrist": (rw[0], rw[1], 0.9),
            "left_knee": (hip[0] - 0.05, 0.72, 0.9), "right_knee": (hip[0] + 0.05, 0.72, 0.9),
            "left_ankle": (hip[0] - 0.06, 0.9, 0.9), "right_ankle": (hip[0] + 0.06, 0.9, 0.9),
            "left_ear": (hip[0] - 0.02, 0.27, 0.8), "right_ear": (hip[0] + 0.02, 0.27, 0.8),
            "left_elbow": (hip[0] - 0.08, 0.45, 0.9), "right_elbow": (hip[0] + 0.08, 0.45, 0.9),
            "nose": (hip[0], 0.28, 0.9),
        }
        frames.append(LandmarkFrame(frame_index=i, pts_s=round(t, 6) if timestamps else None,
                                    landmarks=lms, mean_visibility=0.9))
    return frames


def frame_at(frames, t: float) -> int:
    return min(range(len(frames)), key=lambda i: abs((frames[i].pts_s if frames[i].pts_s is not None
                                                      else i / 30.0) - t))
