"""BL-008C.15 Deliverable 2 -- tests for the default-display policy."""
from __future__ import annotations

from engine_v2.measurement_policy import (
    classify_metric, ALL_METRICS, DISPLAYABLE_BY_DEFAULT, INTERNAL_EXPERIMENTAL,
    SENSITIVITY_THRESHOLD,
)


def test_every_metric_is_classified():
    for name in ALL_METRICS:
        policy = classify_metric(name)
        assert policy.tier in ("displayable_by_default", "internal_experimental")


def test_displayable_and_internal_partition_all_metrics_with_no_overlap():
    assert DISPLAYABLE_BY_DEFAULT | INTERNAL_EXPERIMENTAL == frozenset(ALL_METRICS)
    assert DISPLAYABLE_BY_DEFAULT & INTERNAL_EXPERIMENTAL == frozenset()


def test_unknown_metric_defaults_to_internal_experimental():
    policy = classify_metric("some_future_metric_not_yet_studied")
    assert policy.tier == "internal_experimental"
    assert "no sensitivity data" in policy.reason


def test_classification_respects_the_declared_threshold():
    for name in ALL_METRICS:
        policy = classify_metric(name)
        if policy.sensitivity_ratio is not None:
            if policy.sensitivity_ratio <= SENSITIVITY_THRESHOLD:
                assert policy.tier == "displayable_by_default"
            else:
                assert policy.tier == "internal_experimental"


def test_majority_of_metrics_are_internal_experimental_given_real_data():
    """Documents the actual, real finding from this session: most metrics
    do not currently clear the bar for default display. This is not an
    assertion of what SHOULD be true in the abstract -- it pins down the
    real BL-008C.14 data-derived result so a future change to the policy
    table is a deliberate, visible diff, not a silent drift."""
    assert len(INTERNAL_EXPERIMENTAL) >= len(DISPLAYABLE_BY_DEFAULT)
