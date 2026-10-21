"""BL-008C.12 Deliverable 4 -- EXPERIMENTAL safe measurement pathway for an
explicitly selected event candidate.

Explicit scope, read before use: this module NEVER decides which
candidate is a swing. It computes the existing 7 contact-independent
measurements (engine_v2.measure_window) on a candidate interval that
something ELSE -- a human reviewer today, a future validated automatic
selector later -- has explicitly chosen. There is no code path here that
auto-selects a "best" candidate and treats it as a confirmed swing.

Contact-dependent measurements are NEVER computed here -- this module
imports nothing from engine_v2.segment or engine_v2.measure, and does not
read or touch _CONTACT_DETECTION_VALIDATED. That flag stays exactly where
BL-008C.7 left it.
"""
from __future__ import annotations

import dataclasses

from engine_v2.types import LandmarkFrame, Measurement
from engine_v2.window import SwingWindow, WindowStatus
from engine_v2.event_candidates import EventCandidate
from engine_v2 import measure_window


@dataclasses.dataclass
class SelectedCandidateResult:
    schema_version: str
    source_file: str
    candidate_id: int
    selected_by: str          # "human_reviewer" | "automatic_selector_<name>_v<version>" -- never left implicit
    target_identity: str      # explicit identity note or "unknown" -- never silently assumed
    selection_frame_interval: tuple
    candidate_quality: dict   # carried-through metadata: landmark_coverage_pct, mean_visibility, net_translation_ratio, rejection_reason
    movement_label_at_selection: str   # whatever label (or "unclassified") the candidate had when selected -- NOT asserted as "swing" by this module
    measurements: list


def compute_measurements_for_selected_candidate(
    frames: list[LandmarkFrame],
    candidate: EventCandidate,
    selected_by: str,
    target_identity: str = "unknown",
) -> SelectedCandidateResult:
    """`selected_by` is REQUIRED and must name who/what made the selection
    (e.g. "human_reviewer:logan" or "automatic_selector_v0" once one
    exists and is validated) -- there is no default that implies automatic,
    unreviewed acceptance. `target_identity` defaults to "unknown" rather
    than guessing from the candidate's target_track_id, since a track id
    is an internal tracking label (BL-008C.10's target_track module),
    never a verified person identity.
    """
    if candidate.rejection_reason is not None:
        return SelectedCandidateResult(
            schema_version="selected_candidate_measurement_v1",
            source_file=candidate.source_file,
            candidate_id=candidate.candidate_id,
            selected_by=selected_by,
            target_identity=target_identity,
            selection_frame_interval=(candidate.start_frame, candidate.end_frame),
            candidate_quality={
                "landmark_coverage_pct": candidate.landmark_coverage_pct,
                "mean_visibility": candidate.mean_visibility,
                "net_translation_ratio": candidate.net_translation_ratio,
                "rejection_reason": candidate.rejection_reason,
            },
            movement_label_at_selection=candidate.movement_label,
            measurements=[],  # no measurements computed -- the candidate itself was already flagged as unreliable
        )

    window = SwingWindow(
        status=WindowStatus.OBSERVED,
        start_frame=candidate.start_frame,
        end_frame=candidate.end_frame,
        initial_movement_end=candidate.start_frame,
        primary_motion_end=candidate.end_frame,
    )
    # Reuses the EXISTING, already-gated measurement functions unchanged --
    # every quality/suppression/normalization rule from BL-008C.3-C.5 still
    # applies exactly as it does for the legacy window.py path. This module
    # adds provenance and explicit-selection enforcement; it does not
    # relax or duplicate any measurement logic.
    measurements: list[Measurement] = measure_window.all_window_measurements(frames, window)

    return SelectedCandidateResult(
        schema_version="selected_candidate_measurement_v1",
        source_file=candidate.source_file,
        candidate_id=candidate.candidate_id,
        selected_by=selected_by,
        target_identity=target_identity,
        selection_frame_interval=(candidate.start_frame, candidate.end_frame),
        candidate_quality={
            "landmark_coverage_pct": candidate.landmark_coverage_pct,
            "mean_visibility": candidate.mean_visibility,
            "net_translation_ratio": candidate.net_translation_ratio,
            "rejection_reason": None,
        },
        movement_label_at_selection=candidate.movement_label,
        measurements=measurements,
    )
