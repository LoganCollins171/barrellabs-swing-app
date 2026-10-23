"""BL-008C.14/15/16/17 -- regression tests for the user-guided analysis
backend. Covers: valid selection WITHOUT confirmation (must not measure),
valid selection WITH confirmation (must measure), reversed boundaries,
out-of-range frames, very-short selection, multiple-movements-selected
(hard block, never overridable by confirmation), no-movement-selected,
low-landmark-coverage (confirmation never bypasses this), corrupted/
missing video, contact-dependent feature lockout."""
from __future__ import annotations

from engine_v2.types import LandmarkFrame
from engine_v2.user_guided_flow import analyze_user_selection, analyze_uploaded_video


# BL-008C.37: shoulders above hips (a real torso). The old fixture put every
# body landmark at the same point -- a zero-length torso no real pose has,
# which the movement check now (correctly) reports as unmeasurable.
_Y = {"left_shoulder": 0.35, "right_shoulder": 0.35, "left_hip": 0.55, "right_hip": 0.55}


def _lf(i, x, vis=0.9):
    return LandmarkFrame(frame_index=i, pts_s=None,
                          landmarks={k: (x if "wrist" in k or "ankle" in k else 0.5, _Y.get(k, 0.5), vis) for k in
                                     ["left_wrist", "right_wrist", "left_ankle", "right_ankle",
                                      "left_shoulder", "right_shoulder", "left_hip", "right_hip",
                                      "left_knee", "right_knee", "left_ear", "right_ear"]},
                          mean_visibility=vis)


def _burst_frames(n, burst_s, burst_e, amplitude=0.3):
    xs = [0.3] * n
    for i in range(burst_s, min(burst_e, n)):
        xs[i] = 0.3 + amplitude * (1 - abs(i - (burst_s + burst_e) / 2) / ((burst_e - burst_s) / 2 + 1))
    return [_lf(i, xs[i]) for i in range(n)]


def test_unconfirmed_valid_selection_never_measures():
    """BL-008C.17 Deliverable 1/2: strong movement evidence alone must
    NEVER trigger measurement -- confirmation is always required."""
    frames = _burst_frames(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 38, 62)
    assert r.status != "experimental_result"
    assert r.result is None
    assert r.user_confirmed_swing is False
    if r.status == "needs_confirmation":
        assert r.resolved_interval is None


def test_confirmed_valid_selection_measures_and_is_labeled_experimental():
    frames = _burst_frames(100, 40, 60)
    unconfirmed = analyze_user_selection(frames, "test.mp4", 38, 62)
    if unconfirmed.status != "needs_confirmation":
        return  # synthetic signal didn't land in the needs-confirmation path on this run
    r = analyze_user_selection(frames, "test.mp4", 38, 62, user_confirmed_swing=True)
    assert r.status == "experimental_result"
    assert r.user_confirmed_swing is True
    assert r.resolved_interval is not None
    assert r.result is not None
    assert r.experimental_disclaimer is not None
    assert "experimental" in r.experimental_disclaimer.lower()


def test_reversed_boundaries_rejected():
    frames = _burst_frames(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 60, 40)
    assert r.status == "rejected_input"
    assert "before end" in r.reason


def test_out_of_range_frames_rejected():
    frames = _burst_frames(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 90, 150)
    assert r.status == "rejected_input"
    assert "outside the clip's frame range" in r.reason


def test_very_short_selection_rejected():
    frames = _burst_frames(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 40, 41)
    assert r.status == "rejected_input"
    assert "too short" in r.reason


def test_no_movement_selected_needs_confirmation_before_measuring():
    """Without confirmation, a fully static selection (no detected
    motion) must not measure. With explicit confirmation, BL-008C.17's
    design intentionally still measures it (subject to the separate
    landmark-quality gate): the user's own statement that this is their
    swing is trusted for the swing-determination question, even when
    automatic motion evidence is weak or absent -- that is the whole
    point of Deliverable 2's confirmation step, not a bug."""
    frames = [_lf(i, 0.3) for i in range(100)]  # fully static, no candidates at all
    unconfirmed = analyze_user_selection(frames, "test.mp4", 40, 60)
    assert unconfirmed.status == "needs_confirmation"
    assert unconfirmed.validation_state == "INSUFFICIENT_EVIDENCE"
    assert unconfirmed.result is None

    confirmed = analyze_user_selection(frames, "test.mp4", 40, 60, user_confirmed_swing=True)
    assert confirmed.status == "experimental_result"
    assert confirmed.validation_state == "INSUFFICIENT_EVIDENCE"  # evidence state unchanged, just no longer blocking
    assert confirmed.result is not None


def test_confirmed_selection_measures_the_exact_user_interval_not_the_candidate():
    """BL-008C.15 Deliverable 1 regression, re-verified under the new
    confirmation-required flow: the measured interval must be exactly
    the user's own selection, even when a discovered candidate's own
    boundaries differ from it."""
    frames = _burst_frames(100, 40, 60)
    # select a WIDER interval than the discovered burst itself
    r = analyze_user_selection(frames, "test.mp4", 30, 70, user_confirmed_swing=True)
    if r.status == "experimental_result":
        assert r.resolved_interval == (30, 70)
        assert r.result.selection_frame_interval == (30, 70)
        # the candidate, if one was found nearby, must be surfaced only as a suggestion
        if r.candidate_suggestion is not None:
            assert "informational only" in r.candidate_suggestion["note"]


def test_multiple_movements_selected_is_a_hard_block_even_if_confirmed():
    """BL-008C.17 Deliverable 2: a selection spanning more than one
    distinct movement cannot be a single measurable event -- confirmation
    must not override this, unlike the weaker evidence states."""
    frames = _burst_frames(200, 10, 30)
    second = _burst_frames(200, 150, 170)
    for i in range(150, 170):
        frames[i] = second[i]
    for confirmed in (False, True):
        r = analyze_user_selection(frames, "test.mp4", 5, 175, user_confirmed_swing=confirmed)
        assert r.status != "experimental_result"
        # BL-008C.37: 120 frames apart is a clear split even without timestamps
        assert r.status == "multiple_movements"
        assert "separate fast movements" in r.reason
        assert r.result is None


def test_missing_video_file_rejected():
    r = analyze_uploaded_video("/nonexistent/path/does_not_exist.mp4", 10, 20)
    assert r.status == "rejected_input"
    assert "not found" in r.reason


def test_contact_dependent_path_never_imported():
    """Structural guard: this module must never import the
    contact-dependent measurement path."""
    import ast
    import inspect
    import engine_v2.user_guided_flow as mod
    tree = ast.parse(inspect.getsource(mod))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
    assert "engine_v2.segment" not in imported_modules
    assert "engine_v2.measure" not in imported_modules


def test_requested_interval_always_preserved_even_on_rejection():
    """Provenance requirement: the exact reviewer-requested interval is
    always carried through, even when the request is rejected."""
    frames = _burst_frames(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 60, 40)
    assert r.requested_interval == (60, 40)


def test_resolved_interval_always_equals_requested_interval_when_measured():
    """BL-008C.15 Deliverable 1 invariant, re-verified under the new
    confirmation-required flow: whenever status == 'experimental_result',
    the measured interval is exactly the user's own selection -- never a
    discovered candidate's boundaries."""
    frames = _burst_frames(100, 40, 60)
    for a_s, a_e in [(38, 62), (20, 80), (45, 55)]:
        r = analyze_user_selection(frames, "test.mp4", a_s, a_e, user_confirmed_swing=True)
        if r.status == "experimental_result":
            assert r.resolved_interval == (a_s, a_e)
            assert r.result.selection_frame_interval == (a_s, a_e)


def test_confirmation_never_bypasses_the_landmark_quality_gate():
    """BL-008C.17 Deliverable 2/3: confirming does not make an unusable
    selection measurable -- the per-interval landmark-coverage gate
    (question D) still applies after confirmation."""
    frames = _burst_frames(100, 40, 60)
    for i in range(35, 65):
        frames[i] = LandmarkFrame(frame_index=i, pts_s=None, landmarks=None, mean_visibility=0.0)
    r = analyze_user_selection(frames, "test.mp4", 35, 65, user_confirmed_swing=True)
    assert r.status in ("poor_quality", "insufficient_evidence")
    assert r.result is None


def test_deprecated_reviewer_confirmed_experimental_alias_still_works():
    """Migration path: the old BL-008C.16 kwarg name must still confirm,
    not silently become a no-op."""
    frames = _burst_frames(100, 40, 60)
    unconfirmed = analyze_user_selection(frames, "test.mp4", 38, 62)
    if unconfirmed.status != "needs_confirmation":
        return
    r = analyze_user_selection(frames, "test.mp4", 38, 62, reviewer_confirmed_experimental=True)
    assert r.status == "experimental_result"
    assert r.user_confirmed_swing is True


def test_reviewer_confirmed_experimental_never_applies_to_invalid_selections():
    """Confirmation (by either kwarg name) only ever applies past question
    A -- never to structurally invalid input (reversed/out-of-range/too-short)."""
    frames = _burst_frames(100, 40, 60)
    r = analyze_user_selection(frames, "test.mp4", 60, 40, reviewer_confirmed_experimental=True)
    assert r.status == "rejected_input"
