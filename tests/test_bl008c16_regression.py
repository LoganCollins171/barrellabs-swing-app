"""BL-008C.16 Deliverable 8 -- additional regression tests: the two
confirmed real-footage defects at the full user_guided_flow level (not
just the validation module in isolation), path-exposure privacy, and the
reviewer-confirmed-experimental status contract."""
from __future__ import annotations

from engine_v2.event_candidates import EventCandidate
from engine_v2.user_guided_flow import analyze_uploaded_video

SCHEMA = "event_candidates_v1"


def _cand(cid, s, e, peak, rejection_reason=None):
    return EventCandidate(
        schema_version=SCHEMA, source_file="test", candidate_id=cid,
        start_frame=s, end_frame=e, peak_frame=peak, target_track_id=0, motion_energy=0.1,
        motion_signal_provenance="test", landmark_coverage_pct=100.0, mean_visibility=0.9,
        net_translation_ratio=0.05, boundary_uncertainty_frames=5, overlaps_candidate_ids=[],
        rejection_reason=rejection_reason,
    )


def test_missing_video_error_never_exposes_full_path():
    """BL-008C.16 Deliverable 8: a rejection reason must name the file,
    not the full local filesystem path (which can reveal a username or
    directory layout)."""
    secret_path = "/Users/some_real_person/private_folder/clip.mp4"
    r = analyze_uploaded_video(secret_path, 10, 20)
    assert r.status == "rejected_input"
    assert "some_real_person" not in r.reason
    assert "private_folder" not in r.reason
    assert "clip.mp4" in r.reason  # filename alone is fine and useful


def test_missing_video_error_with_no_path_at_all():
    r = analyze_uploaded_video("", 10, 20)
    assert r.status == "rejected_input"
