"""BL-008C.19 -- production integration of the user-guided, confirmation-
required swing review workflow (engine_v2.user_guided_flow).

This is a NEW, separate page, reached from the existing upload page via
an explicit "select & confirm manually (experimental)" button -- it does
NOT replace or modify the existing automatic analyze() / Edge Score /
MLB-match pipeline in any way. The two pipelines are completely
independent; nothing here is imported by, or can affect, analyzer.py,
swing_score.py, or mlb_match.py.

Honesty rules carried over unchanged from the dev-branch work
(BL-008C.14-18), enforced here exactly the same way:
  - The exact interval the user selects and confirms is what gets
    measured -- never silently substituted.
  - Nothing is measured without an explicit confirmation click.
  - Results are always labeled experimental, never implied "verified."
  - Only the 2 metrics engine_v2.measurement_policy currently classifies
    "displayable_by_default" are shown as primary results; the other 5
    are available under a collapsed "internal/experimental diagnostics"
    expander, same split as the dev tool.
  - No Edge Score, no MLB-match percentage, no biomechanical conclusion
    is computed or shown on this page.

Does NOT yet persist results to Supabase (see BL008C19_FINAL_REPORT.md
"Production-versus-development gap analysis" -- this is an explicitly
flagged, not-yet-made decision: what table/shape to store this new
result type in). Results live only in st.session_state for the current
session, same as the dev tool.

BL-008C.21, noted honestly rather than silently: this page does NOT call
entitlements.can_analyze_swing_for_account (or any gate) -- it is not
currently metered by FREE_SWING_LIMIT, PILOT_SWING_LIMIT, or plan at
all. That's a real, flagged gap, not a hidden bypass: results here are
session-only and unsaved (see above), so an unmetered swing here has no
lasting cost the way a persisted automatic-pipeline analysis does. Left
unchanged this milestone rather than adding a gate under time pressure --
see the BL-008C.21 final report's "five-video pilot access" section for
why that's an acceptable, explicitly-stated tradeoff for this pilot
rather than an oversight.
"""
from __future__ import annotations

import os

import streamlit as st

from engine_v2.user_guided_flow import decode_and_cleanup, analyze_user_selection
from engine_v2.measurement_policy import classify_metric

EVIDENCE_MESSAGES = {
    "SINGLE_MOVEMENT_DETECTED": ("info", "We found one clear, well-bounded movement in this selection."),
    "AMBIGUOUS_MOVEMENT": ("warning", "We're not confident there's a single clear movement in this exact "
                                       "selection -- it may include extra footage before or after the real "
                                       "movement. Double-check your selection before confirming."),
    "INSUFFICIENT_EVIDENCE": ("warning", "We didn't detect any clear movement in this selection. Make sure "
                                          "your swing is inside the selected range before confirming."),
}
BLOCKED_STATUS_MESSAGES = {
    "multiple_movements": "This selection appears to contain more than one distinct movement. Please narrow "
                           "it down to just your swing.",
    "rejected_input": "That selection isn't usable: {reason}",
}
PLAIN_LANGUAGE_METRIC_INFO = {
    "hand_to_torso_distance_range_normalized_window": {
        "label": "Hand-to-body distance (bat hand)",
        "explanation": ("How far your bat hand moved away from your body during the selected clip, "
                         "measured in multiples of your own shoulder width (not inches or cm) -- a rough "
                         "shape-of-motion number, not a measurement of bat speed or swing quality."),
    },
    "left_knee_flexion_range_degrees_window": {
        "label": "Left knee bend range",
        "explanation": ("How much your left knee angle changed during the selected clip, in degrees -- a "
                         "rough 2D estimate from video, not a medical or biomechanical measurement, and it "
                         "can look different depending on which way you're facing the camera."),
    },
}


def render_manual_swing_review(user: dict | None) -> None:
    """Entry point, called from app.py exactly like every other
    render_<page> function (see render_pricing_page,
    render_saved_reports_dashboard, etc. for the established pattern)."""
    st.title("Select & confirm your swing — experimental")
    st.caption(
        "This is a separate, experimental path for when automatic analysis doesn't find your swing "
        "reliably. Results here are rough, experimental 2D movement measurements -- not a validated "
        "biomechanical analysis, not an Edge Score, and not compared to any MLB player."
    )

    # BL-008C.19 integration-test fix: decode_and_cleanup removes the temp
    # file (and this function pops its own path reference) once decode
    # succeeds, so the "is there a video to decode" check must never run
    # again after frames are already cached -- otherwise every later
    # rerun (any slider move, any button click) would hit this guard and
    # bail out even though the real work is already done. Caught by
    # testing this page end-to-end before declaring it working, not by
    # code review alone.
    if "manual_review_frames" not in st.session_state:
        video_path = st.session_state.get("manual_review_video_path")
        if not video_path or not os.path.isfile(video_path):
            st.warning("No video is ready for manual review. Go back to the upload page, upload your clip, "
                       "then choose \"select & confirm manually\" there.")
            if st.button("Back to upload"):
                st.session_state["page"] = "dashboard"
                st.rerun()
            return

        with st.spinner("Reading video and tracking the player (one-time, this may take a moment)..."):
            lf, fps, error = decode_and_cleanup(video_path)
            st.session_state.pop("manual_review_video_path", None)
            if error is not None:
                st.error(f"Could not process this video: {error}")
                return
            st.session_state["manual_review_frames"] = lf
            st.session_state["manual_review_fps"] = fps

    frames = st.session_state["manual_review_frames"]
    fps = st.session_state.get("manual_review_fps", 30.0) or 30.0
    n = len(frames)

    st.subheader("Select the approximate start and end of your swing")
    start_frame, end_frame = st.slider(
        "Drag to roughly bracket your swing (you don't need to be exact)",
        min_value=0, max_value=max(n - 1, 1), value=(0, max(n - 1, 1)),
        key="manual_review_slider",
    )
    st.write(f"Selected: frame {start_frame} to {end_frame} "
             f"(~{start_frame / fps:.2f}s to ~{end_frame / fps:.2f}s)")

    current_selection = (start_frame, end_frame)
    if st.session_state.get("manual_review_checked_selection") != current_selection:
        st.session_state.pop("manual_review_check_result", None)
        st.session_state.pop("manual_review_final_result", None)

    if st.button("Check this selection", type="primary"):
        with st.spinner("Checking..."):
            st.session_state["manual_review_check_result"] = analyze_user_selection(
                frames, source_file="manual_review_upload", approx_start=start_frame, approx_end=end_frame,
                selected_by=f"human_reviewer:{(user or {}).get('slug', 'unknown')}", user_confirmed_swing=False,
            )
        st.session_state["manual_review_checked_selection"] = current_selection
        st.session_state.pop("manual_review_final_result", None)

    check_result = st.session_state.get("manual_review_check_result")
    if check_result is not None and st.session_state.get("manual_review_checked_selection") == current_selection:
        if check_result.status in BLOCKED_STATUS_MESSAGES:
            st.subheader("Result")
            st.error(BLOCKED_STATUS_MESSAGES[check_result.status].format(reason=check_result.reason))
        else:
            st.subheader("Confirm")
            kind, msg = EVIDENCE_MESSAGES.get(check_result.validation_state, ("info", ""))
            getattr(st, kind)(msg)
            confirmed = st.checkbox(
                "I've reviewed the clip above and confirm this selection contains my intended swing.",
                value=False, key="manual_review_confirm_checkbox",
            )
            st.caption("This is your own statement, not something BarrelLabs verifies automatically.")
            if st.button("Analyze this selection", disabled=not confirmed):
                with st.spinner("Analyzing..."):
                    st.session_state["manual_review_final_result"] = analyze_user_selection(
                        frames, source_file="manual_review_upload", approx_start=start_frame, approx_end=end_frame,
                        selected_by=f"human_reviewer:{(user or {}).get('slug', 'unknown')}",
                        user_confirmed_swing=True,
                    )

    final_result = st.session_state.get("manual_review_final_result")
    if final_result is not None and st.session_state.get("manual_review_checked_selection") == current_selection:
        st.subheader("Results")
        if final_result.status == "poor_quality":
            st.warning("The player wasn't tracked clearly enough in that exact selection to measure "
                       "anything reliably, even though you confirmed it.")
        elif final_result.status == "experimental_result":
            s, e = final_result.resolved_interval
            st.error(f"EXPERIMENTAL, user-confirmed -- not automatically verified: "
                     f"{final_result.experimental_disclaimer}")
            st.success(f"Analyzed exactly what you selected: frames {s}-{e} "
                       f"(~{s/fps:.2f}s to ~{e/fps:.2f}s).")

            displayable, internal = [], []
            for m in final_result.result.measurements:
                policy = classify_metric(m.name)
                (displayable if policy.tier == "displayable_by_default" else internal).append((m, policy))

            for m, policy in displayable:
                plain = PLAIN_LANGUAGE_METRIC_INFO.get(m.name)
                label = plain["label"] if plain else m.name.replace("_", " ")
                if m.value is None:
                    st.caption(f"— {label}: not available ({m.suppression_reason})")
                elif m.quality == "ok":
                    st.metric(label, f"{m.value:.2f} {m.units}")
                else:
                    st.metric(label, f"{m.value:.2f} {m.units}", help=m.uncertainty_note)
                    st.caption(f"degraded: {m.uncertainty_note}")
                if plain:
                    st.caption(plain["explanation"])

            if internal:
                with st.expander("Internal/experimental diagnostics (not validated for display)"):
                    for m, policy in internal:
                        val = f"{m.value:.2f} {m.units}" if m.value is not None else "not available"
                        st.write(f"**{m.name}**: {val} — {policy.reason}")

    if st.button("Back to upload"):
        for k in list(st.session_state.keys()):
            if k.startswith("manual_review_"):
                st.session_state.pop(k, None)
        st.session_state["page"] = "dashboard"
        st.rerun()
