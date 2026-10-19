"""Scale-invariant movement match — pure functions. Spec System 2."""
from __future__ import annotations
import json, math, os
from typing import Optional

_EPS = 1e-6

# BL-004C (4): shared presentation strings, single source of truth so every
# page that shows this number (the full swing report, saved-reports/session
# history, the compare-swings page, the dashboard, and the development
# tracker) says the same honest thing, worded the same way.
#
# `movement_match_pct` (match_pro(), below) is a real, useful similarity
# heuristic — exp(-distance/_MATCH_SCALE) against a 17-player reference
# set, with z-scores clamped to ±3 — but its 1%-granularity presentation
# ("75% match") implies a validated statistical probability that nothing
# in this computation actually provides: _MATCH_SCALE is a design choice,
# not a fit parameter, and 17 exemplars is a small set to imply fine-
# grained distinguishability between e.g. 74% and 76%. The number itself
# (the "diagnostic value") is NOT changed by this — only how it's labeled
# and captioned, per BL-004C (4)'s explicit instruction not to touch the
# underlying calculation.
MATCH_LABEL = "style match"
MATCH_CAVEAT = (
    "Experimental movement-style comparison against a small reference "
    "set — not a validated statistical probability."
)
MATCH_CAVEAT_SHORT = "Experimental style comparison, not a validated probability."


def _g(d, *path, default=0.0):
    for k in path:
        d = (d or {}).get(k)
        if d is None:
            return default
    return d


def movement_vector(fp: dict) -> list:
    t = fp.get("timing_ms") or {}
    total = (t.get("total_swing") or 0) or _EPS
    load = float(t.get("load_duration") or 0)
    rot = fp.get("rotation_deg") or {}
    ph = fp.get("phases_t") or {}
    kn = fp.get("knee_deg") or {}
    plant_to_contact = ((ph.get("contact") or 0) - (ph.get("foot_plant") or 0)) or _EPS
    return [
        # 0: load:swing tempo as a true fraction in [0,1). (Was load/downswing,
        #    which exceeded 1 and let one short-swing outlier dominate.)
        float(load / (load + total)),
        # 1: downswing split. The companion foot_plant_to_launch ratio was
        #    DROPPED — it summed to 1.0 with this one (collinear, double-counted).
        float((t.get("launch_to_contact") or 0) / total),
        # 2: X-factor timing — when peak separation lands within plant→contact.
        float(((rot.get("peak_separation_t") or 0) - (ph.get("foot_plant") or 0)) / plant_to_contact),
        # 3: separation retention into contact.
        float((rot.get("separation_at_contact") or 0) / ((rot.get("peak_separation") or 0) or _EPS)),
        # 4: rotational vs linear lean.
        float((rot.get("peak_hip") or 0) / (abs(rot.get("peak_separation") or 0) + _EPS)),
        # 5: knee re-extension (brace).
        float((kn.get("re_extension") or 0) / (((kn.get("at_foot_plant") or 0) - (kn.get("min_during_load") or 0)) + _EPS)),
        # 6: head stability.
        float(_g(fp, "head_movement_normalized_foot_plant_to_contact", "total_drift_torso")),
    ]


# Winsorize each standardized dim so a single outlier feature (e.g. one pro's
# mis-measured short swing) can't dominate the Euclidean match distance over a
# small 17-exemplar reference set.
_Z_CLAMP = 3.0


def zscore(vec, stats):
    out = []
    for v, m, s in zip(vec, stats["means"], stats["stds"]):
        z = (v - m) / (s or _EPS)
        out.append(max(-_Z_CLAMP, min(_Z_CLAMP, z)))
    return out


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


_MATCH_SCALE = 3.0


def match_pro(z_vector: list, stats: dict) -> dict:
    ci = min(range(len(stats["centroids"])), key=lambda i: _dist(z_vector, stats["centroids"][i]))
    in_cluster = [p for p in stats["pros"] if p["cluster"] == ci] or stats["pros"]
    best = min(in_cluster, key=lambda p: _dist(z_vector, p["z"]))
    pct = round(100.0 * math.exp(-_dist(z_vector, best["z"]) / _MATCH_SCALE))
    return {"slug": best["slug"], "name": best["name"],
            "movement_match_pct": max(0, min(100, pct)), "cluster": ci}
