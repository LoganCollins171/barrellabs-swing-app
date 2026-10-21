"""BL-008C.15 Deliverable 2 -- default-display policy for the 7
contact-independent measurements, backed by the BL-008C.14 12-event
sensitivity study (validation/private_dev/bl008c14_sensitivity_results.json;
narrative: validation/BL008C14_SENSITIVITY_STUDY.md).

A metric produces a NUMBER does not make it safe to show a non-expert
user by default -- that was last session's gap, named explicitly in the
BL-008C.15 task. This module implements the actual gate, not just a
written policy.

Method (stated plainly, including a correction made while building
this): for each metric, take the mean ABSOLUTE change in its value
between the independently annotated interval and each of the two
PREDECLARED +/-6-frame simulated user-selection perturbations
("tight"/"loose" from BL-008C.14), then normalize by that metric's own
TYPICAL observed scale across the 12 annotated events. "Typical scale"
was first tried as the MAX observed |annotated value| -- this made
every metric look safe (ratios 0.02-0.17), because a single
larger-than-usual swing in the 12-clip set inflated the denominator for
every metric. That is not a representative "typical" scale, so it was
replaced with the MEDIAN observed |annotated value| instead, which is
far less sensitive to one outlier clip. This switch happened BEFORE any
per-metric pass/fail threshold was chosen, not after seeing which
normalizer gave a more convenient answer -- disclosed here rather than
silently redone.

THRESHOLD = 0.25 (a realistic +/-6-frame user click moving the metric by
more than 25% of its own typical scale is too unstable to show by
default) was picked once, before computing final verdicts, and was not
adjusted afterward.
"""
from __future__ import annotations

import dataclasses
import json
import os
import statistics

SENSITIVITY_THRESHOLD = 0.25  # predeclared -- see module docstring

_RESULTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "validation", "private_dev", "bl008c14_sensitivity_results.json",
)

ALL_METRICS = [
    "head_displacement_normalized_window",
    "trunk_inclination_range_degrees_window",
    "hand_to_torso_distance_range_normalized_window",
    "left_knee_flexion_range_degrees_window",
    "left_ankle_displacement_normalized_window",
    "right_knee_flexion_range_degrees_window",
    "right_ankle_displacement_normalized_window",
]

# Computed once from the BL-008C.14 sensitivity data (see
# scripts/bl008c15_compute_display_policy.py for the exact derivation) and
# frozen here as a plain data table -- this module does not require the
# sensitivity JSON to be present at runtime for normal operation, only for
# regenerating this table.
_SENSITIVITY_RATIOS = {
    "head_displacement_normalized_window": 0.41,
    "trunk_inclination_range_degrees_window": 0.50,
    "hand_to_torso_distance_range_normalized_window": 0.13,
    "left_knee_flexion_range_degrees_window": 0.15,
    "left_ankle_displacement_normalized_window": 0.95,
    "right_knee_flexion_range_degrees_window": 0.69,
    "right_ankle_displacement_normalized_window": 0.65,
}

DISPLAYABLE_BY_DEFAULT = frozenset(
    name for name, ratio in _SENSITIVITY_RATIOS.items() if ratio <= SENSITIVITY_THRESHOLD
)
INTERNAL_EXPERIMENTAL = frozenset(ALL_METRICS) - DISPLAYABLE_BY_DEFAULT


@dataclasses.dataclass
class MetricPolicy:
    name: str
    tier: str              # "displayable_by_default" | "internal_experimental"
    sensitivity_ratio: float | None  # mean |perturbed - annotated| / median |annotated|, worse of tight/loose
    reason: str


def classify_metric(name: str) -> MetricPolicy:
    ratio = _SENSITIVITY_RATIOS.get(name)
    if ratio is None:
        return MetricPolicy(name=name, tier="internal_experimental", sensitivity_ratio=None,
                             reason="no sensitivity data on file for this metric -- treated as unvalidated")
    if ratio <= SENSITIVITY_THRESHOLD:
        return MetricPolicy(name=name, tier="displayable_by_default", sensitivity_ratio=ratio,
                             reason=f"a realistic +/-6-frame selection error moves this metric by only "
                                    f"~{ratio*100:.0f}% of its typical scale (BL-008C.14 sensitivity study)")
    return MetricPolicy(name=name, tier="internal_experimental", sensitivity_ratio=ratio,
                         reason=f"a realistic +/-6-frame selection error moves this metric by "
                                f"~{ratio*100:.0f}% of its typical scale (BL-008C.14 sensitivity study) -- "
                                f"too unstable to show a non-expert user by default")


def recompute_from_sensitivity_json(path: str = _RESULTS_PATH) -> dict:
    """Regenerates the _SENSITIVITY_RATIOS table from the raw BL-008C.14
    JSON -- a diagnostic/regeneration helper, not required at runtime.
    Returns {metric_name: ratio}."""
    rows = json.load(open(path))
    eligible = [r for r in rows if r.get("status") == "ELIGIBLE"]
    out = {}
    for metric in ALL_METRICS:
        annotated_vals, abs_tight, abs_loose = [], [], []
        for r in eligible:
            a = r["measurements"]["annotated"].get(metric, {}).get("value")
            t = r["measurements"]["user_tight"].get(metric, {}).get("value")
            l = r["measurements"]["user_loose"].get(metric, {}).get("value")
            if a is not None:
                annotated_vals.append(abs(a))
                if t is not None:
                    abs_tight.append(abs(t - a))
                if l is not None:
                    abs_loose.append(abs(l - a))
        scale = statistics.median(annotated_vals) if annotated_vals else 0
        n_tight = (sum(abs_tight) / len(abs_tight) / scale) if abs_tight and scale else None
        n_loose = (sum(abs_loose) / len(abs_loose) / scale) if abs_loose and scale else None
        ratios = [x for x in (n_tight, n_loose) if x is not None]
        out[metric] = round(max(ratios), 2) if ratios else None
    return out
