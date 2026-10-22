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

BL-008C.22: this page is now gated by the SAME shared entitlement check
and usage counter as the legacy automatic pipeline (see app.py's
"Analyze swing" button) -- entitlements.can_analyze_swing_for_account
and subscription_storage.record_successful_swing_analysis, not a second,
separate system. The gate runs inside the "Analyze this selection"
button's own click-triggered run (same pattern app.py uses for its
"Analyze swing" button), so a plain rerun -- a slider move, an unrelated
button elsewhere on this page -- never re-checks or re-consumes quota;
only an actual new click does. This closes the gap BL-008C.21 flagged
(this page previously called no gate at all).

BL-008C.36: rebuilt as a visual workflow. The user WATCHES their clip
(swing_trimmer: frames decoded by the server, so what they see is exactly
what gets analyzed, HEVC included), drags start/end markers with live frame
previews, checks the selection, confirms, then analyzes. Preparation runs in
a child process with live progress (engine_v2.review_prep) instead of
blocking the server inside the click that opened this page. The charge
boundary below is unchanged: only the "Analyze this selection" click can
charge, never playback, scrubbing, marker moves or checks.
"""
from __future__ import annotations

import html
import logging
import os
import uuid

import streamlit as st

from engine_v2.review_prep import PrepError, display_fps, prepare_review
from engine_v2.user_guided_flow import analyze_user_selection
from engine_v2.measurement_policy import classify_metric
from entitlements import can_analyze_swing_for_account
from subscription_storage import (
    load_my_plan, record_successful_swing_analysis, charge_nonce_for, clear_charge_nonce,
)

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


_log = logging.getLogger("barrellabs.manual_review")

_PAGE_CSS = """
<style>
.blx-head{margin:6px 0 18px}
.blx-eyebrow{font:600 11px/1.2 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.22em;
  text-transform:uppercase;color:#E8C170}
.blx-title{font-size:2.1rem;font-weight:800;letter-spacing:-.02em;color:#F4EFE6;margin:6px 0 6px}
.blx-sub{color:#9A968E;max-width:760px;font-size:.98rem;line-height:1.5}
.blx-steps{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 4px}
.blx-step{font:600 11.5px/1 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.08em;padding:7px 11px;
  border-radius:999px;border:1px solid rgba(255,255,255,.10);color:#9A968E;background:#121419}
.blx-step.on{color:#0A0B0E;background:#E8C170;border-color:#E8C170}
.blx-step.done{color:#E8C170;border-color:rgba(232,193,112,.45)}
.blx-sec{font:700 12px/1 'JetBrains Mono',ui-monospace,monospace;letter-spacing:.16em;text-transform:uppercase;
  color:#9A968E;margin:22px 0 10px}
.blx-pick{display:flex;flex-wrap:wrap;gap:18px;align-items:baseline;background:#121419;
  border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:12px 14px;color:#9A968E;margin:4px 0 10px}
.blx-pick b{color:#F4EFE6;font-size:1.05rem}
</style>
"""

_STEPS = ("1 · Prepare", "2 · Mark the swing", "3 · Check", "4 · Confirm & analyze", "5 · Results")


def _header_html(active: int) -> str:
    chips = "".join(
        f'<span class="blx-step {"on" if i == active else "done" if i < active else ""}">{html.escape(s)}</span>'
        for i, s in enumerate(_STEPS))
    return (
        _PAGE_CSS
        + '<div class="blx-head"><div class="blx-eyebrow">Experimental · manual swing select</div>'
        + '<div class="blx-title">Mark your swing</div>'
        + '<div class="blx-sub">Watch your clip, mark where the swing starts and ends, then confirm. '
        + 'Results here are rough, experimental 2D movement measurements -- not a validated biomechanical '
        + 'analysis, not an Edge Score, and not compared to any MLB player.</div>'
        + f'<div class="blx-steps">{chips}</div></div>'
    )


def _clear_review_state() -> None:
    for k in list(st.session_state.keys()):
        if str(k).startswith("manual_review_"):
            st.session_state.pop(k, None)


def _nav_buttons(prefix: str) -> None:
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("Upload a different clip", key=f"{prefix}_upload", width="stretch"):
        _clear_review_state()
        st.session_state["page"] = "upload"
        st.rerun()
    if c2.button("Back to dashboard", key=f"{prefix}_dash", width="stretch"):
        _clear_review_state()
        st.session_state["page"] = "dashboard"
        st.rerun()


def _secs_label(pts: list[float], a: int, b: int) -> str:
    return f" (~{pts[a]:.2f}s to ~{pts[b]:.2f}s)" if pts else ""


def render_manual_swing_review(user: dict | None) -> None:
    """Entry point, called from app.py exactly like every other
    render_<page> function (see render_pricing_page,
    render_saved_reports_dashboard, etc. for the established pattern)."""
    try:
        from bl_edge_chrome import render_edge_masthead
        render_edge_masthead(user, active_page="upload")
    except Exception:
        pass
    try:
        _render_body(user)
    except Exception as exc:
        # Never leave a blank page: say what happened and offer a way out.
        # (st.rerun/st.stop are BaseExceptions and pass straight through.)
        _log.exception("manual review page failed")
        st.error("Something went wrong on this page. Your account hasn't been charged. "
                 "Try again, or upload the clip again.")
        with st.expander("Details for support"):
            st.code(f"{type(exc).__name__}: {exc}"[:500])
        _nav_buttons("mr_err")


def _prepare_clip() -> bool:
    """Run preparation with live progress. True once the clip is ready."""
    video_path = st.session_state.get("manual_review_video_path")
    if not video_path or not os.path.isfile(video_path):
        st.warning("No video is ready for manual review. Go back to the upload page, upload your clip, "
                   "then choose \"Select & confirm manually\" there.")
        _nav_buttons("mr_empty")
        return False

    box = st.empty()
    with box.container():
        st.markdown('<div class="blx-sec">Preparing your video</div>', unsafe_allow_html=True)
        bar = st.progress(0.0, text="Starting…")
    labels = {"starting": "Starting…", "tracking": "Reading frames and tracking your body",
              "done": "Almost ready…"}

    def on_progress(stage: str, done: int, total: int) -> None:
        frac = (done / total) if total else 0.0
        text = labels.get(stage, "Working…")
        if stage == "tracking" and total:
            text = f"{text} · frame {done} of {total}"
        bar.progress(min(max(frac, 0.0), 1.0), text=text)

    import time as _time
    t0 = _time.monotonic()

    def _perf(**f):
        try:
            import auth_diag
            auth_diag.perf("exp_prepare", ms=int((_time.monotonic() - t0) * 1000), **f)
        except Exception:
            pass

    try:
        prep = prepare_review(video_path, on_progress=on_progress)
    except PrepError as exc:
        _perf(ok=False)
        box.empty()
        st.session_state.pop("manual_review_video_path", None)
        st.error("Could not process this video. " + exc.user_message)
        with st.expander("Details for support"):
            st.code(f"{exc.code}: {exc.detail}"[:500])
        st.caption("Nothing was charged.")
        _nav_buttons("mr_fail")
        return False
    _perf(ok=True, frames=prep.frame_count, previews=len(prep.previews))
    box.empty()
    st.session_state.pop("manual_review_video_path", None)
    n = prep.frame_count
    st.session_state["manual_review_frames"] = prep.landmarks
    st.session_state["manual_review_pts"] = prep.pts
    st.session_state["manual_review_previews"] = prep.previews
    st.session_state["manual_review_fps"] = display_fps(prep.timing)
    st.session_state["manual_review_clip"] = {
        "id": uuid.uuid4().hex, "preview_size": prep.preview_size, "n": n,
        "duration": prep.duration_s,
    }
    st.session_state["manual_review_selection"] = (0, max(n - 1, 1))
    # BL-008C.23: a stable per-decoded-video id for the usage-charge
    # idempotency key below -- generated once here (not per click), so
    # re-clicking "Analyze this selection" for the SAME selection after a
    # failed charge reuses the same key and cannot double-charge, even
    # though the uploaded file is already gone by the time any charge
    # attempt happens.
    st.session_state["manual_review_analysis_session_id"] = uuid.uuid4().hex
    return True


def _on_slider_change() -> None:
    v = st.session_state.get("manual_review_slider")
    if isinstance(v, (tuple, list)) and len(v) == 2:
        st.session_state["manual_review_selection"] = (int(v[0]), int(v[1]))


def _render_body(user: dict | None) -> None:
    # The header (with the step indicator) is filled in at the END of the
    # run, so it reflects whatever this run decided -- e.g. a marker commit.
    head = st.empty()
    head.markdown(_header_html(0 if "manual_review_frames" not in st.session_state else 1),
                  unsafe_allow_html=True)
    if "manual_review_frames" not in st.session_state:
        if not _prepare_clip():
            return

    frames = st.session_state["manual_review_frames"]
    pts = st.session_state.get("manual_review_pts") or []
    previews = st.session_state.get("manual_review_previews") or []
    clip = st.session_state.get("manual_review_clip") or {}
    clip_id = clip.get("id", "legacy")
    n = len(frames)
    # BL-008C.32: the clip's real frame rate (from presentation timestamps),
    # or None when it can't be determined -- never an assumed 30 fps.
    fps = st.session_state.get("manual_review_fps")
    if len(pts) != n:  # defensive: never label frames with times that don't belong to them
        pts = []

    def _secs(a: int, b: int) -> str:
        return _secs_label(pts, a, b)

    # Apply the player's latest COMMITTED selection BEFORE rendering it, so
    # the arguments it receives in this run are already current. (Reading
    # it from the component's return value after rendering sent the player
    # its previous selection first, which overwrote the user's edit --
    # reproduced in the browser E2E.)
    picked = st.session_state.get("manual_review_trimmer")
    if (isinstance(picked, dict) and picked.get("clip_id") == clip_id
            and picked.get("seq") != st.session_state.get("manual_review_trim_seq")):
        st.session_state["manual_review_trim_seq"] = picked.get("seq")
        s_, e_ = picked.get("start"), picked.get("end")
        if isinstance(s_, int) and isinstance(e_, int) and 0 <= s_ < e_ < n:
            st.session_state["manual_review_selection"] = (s_, e_)
    start_frame, end_frame = st.session_state.get("manual_review_selection", (0, max(n - 1, 1)))

    # ---- 1. watch + mark --------------------------------------------------
    st.markdown('<div class="blx-sec">Watch your clip and mark the swing</div>', unsafe_allow_html=True)
    if previews and pts:
        from swing_trimmer import swing_trimmer
        swing_trimmer(
            clip_id=clip_id, previews=previews, pts=pts,
            total_frames=n, start=start_frame, end=end_frame,
            preview_size=tuple(clip.get("preview_size") or (480, 270)),
            fps_label=f"{fps:.1f}" if fps else None, key="manual_review_trimmer",
            ack_seq=st.session_state.get("manual_review_trim_seq"),
        )

    with st.expander("Trouble with the player? Use simple controls", expanded=not (previews and pts)):
        if st.session_state.get("manual_review_slider") != (start_frame, end_frame):
            st.session_state["manual_review_slider"] = (start_frame, end_frame)
        st.slider("Swing start and end (frame numbers)", min_value=0, max_value=max(n - 1, 1),
                  key="manual_review_slider", on_change=_on_slider_change)
        if previews:
            def _nearest(idx: int) -> bytes:
                best = previews[0][1]
                for i, data in previews:
                    if i <= idx:
                        best = data
                    else:
                        break
                return best
            c1, c2 = st.columns(2)
            c1.image(_nearest(start_frame), caption=f"Start · frame {start_frame}")
            c2.image(_nearest(end_frame), caption=f"End · frame {end_frame}")

    length = f" · <b>{pts[end_frame] - pts[start_frame]:.2f} s</b> long" if pts else ""
    st.markdown(
        f'<div class="blx-pick"><span>Your selection: frames <b>{start_frame}–{end_frame}</b> of {n}'
        f'{html.escape(_secs(start_frame, end_frame))}{length}</span></div>',
        unsafe_allow_html=True,
    )

    current_selection = (clip_id, start_frame, end_frame)
    if st.session_state.get("manual_review_checked_selection") != current_selection:
        st.session_state.pop("manual_review_check_result", None)
        st.session_state.pop("manual_review_final_result", None)
        st.session_state.pop("manual_review_paywall_reason", None)

    # ---- 2. check ----------------------------------------------------------
    if st.button("Check this selection", type="primary"):
        with st.spinner("Checking your selection…"):
            st.session_state["manual_review_check_result"] = analyze_user_selection(
                frames, source_file="manual_review_upload", approx_start=start_frame, approx_end=end_frame,
                selected_by=f"human_reviewer:{(user or {}).get('slug', 'unknown')}", user_confirmed_swing=False,
            )
        st.session_state["manual_review_checked_selection"] = current_selection
        st.session_state.pop("manual_review_final_result", None)

    check_result = st.session_state.get("manual_review_check_result")
    if check_result is not None and st.session_state.get("manual_review_checked_selection") == current_selection:
        if check_result.status in BLOCKED_STATUS_MESSAGES:
            st.markdown('<div class="blx-sec">Check result</div>', unsafe_allow_html=True)
            st.error(BLOCKED_STATUS_MESSAGES[check_result.status].format(reason=check_result.reason))
            st.caption("Move the S and E markers above to bracket just your swing, then check again. "
                       "Nothing was charged.")
        else:
            st.markdown('<div class="blx-sec">Confirm</div>', unsafe_allow_html=True)
            kind, msg = EVIDENCE_MESSAGES.get(check_result.validation_state, ("info", ""))
            getattr(st, kind)(msg)
            confirmed = st.checkbox(
                "I've reviewed the clip above and confirm this selection contains my intended swing.",
                value=False, key="manual_review_confirm_checkbox",
            )
            st.caption("This is your own statement, not something BarrelLabs verifies automatically. "
                       "Analyzing uses one swing analysis from your plan.")
            if st.button("Analyze this selection", disabled=not confirmed):
                # BL-008C.22: the SAME gate the automatic pipeline uses,
                # checked fresh (force_refresh=True, same reasoning as
                # app.py's identical call -- a stale cached plan must
                # never under- or over-grant quota) inside this
                # click-triggered run only. A blocked account sees the
                # paywall and analyze_user_selection is never called --
                # no compute spent, no quota touched, matching "failed
                # analyses do not incorrectly consume usage."
                _plan_snapshot = load_my_plan(force_refresh=True)
                _gate = can_analyze_swing_for_account(_plan_snapshot, (user or {}).get("email"))
                st.session_state.pop("manual_review_final_result", None)
                if not _gate.allowed:
                    st.session_state["manual_review_paywall_reason"] = (
                        _gate.reason or "You've reached your swing-analysis limit."
                    )
                else:
                    st.session_state.pop("manual_review_paywall_reason", None)
                    with st.spinner("Analyzing..."):
                        _result = analyze_user_selection(
                            frames, source_file="manual_review_upload", approx_start=start_frame, approx_end=end_frame,
                            selected_by=f"human_reviewer:{(user or {}).get('slug', 'unknown')}",
                            user_confirmed_swing=True,
                        )
                    # Usage is recorded only for an actual successful
                    # measurement -- "poor_quality" and any other
                    # non-"experimental_result" status consumes nothing,
                    # exactly like the automatic pipeline only counts a
                    # completed, saved analysis.
                    #
                    # BL-008C.23: a failed charge now BLOCKS the result
                    # from being revealed (st.session_state is never set
                    # to it) instead of showing it anyway -- the prior
                    # soft-fail-and-continue behavior was exactly the gap
                    # this milestone closes.
                    #
                    # BL-008C.24: the idempotency key is now a
                    # session_state-held NONCE per (decoded video,
                    # selection), not just the raw (session_id, start,
                    # end) tuple -- that tuple alone is stable FOREVER,
                    # so a user who deliberately re-runs the exact same
                    # already-charged selection a second time would
                    # otherwise reuse the same key and get a free,
                    # uncounted repeat (the same class of bug found and
                    # fixed in app.py's content-hash key this session).
                    # The nonce is popped on success (below) so a later,
                    # genuinely new "Analyze" click for the same
                    # selection mints a fresh one and is correctly
                    # charged again; a retry of a still-failing attempt
                    # reuses it and stays double-charge-safe.
                    if _result.status == "experimental_result":
                        _session_id = st.session_state.get("manual_review_analysis_session_id", "unknown")
                        _content_key = f"{_session_id}:{start_frame}:{end_frame}"
                        _key = (
                            f"{(user or {}).get('slug', 'anon')}:{_content_key}:"
                            f"{charge_nonce_for(st.session_state, _content_key)}"
                        )
                        if record_successful_swing_analysis(_plan_snapshot, idempotency_key=_key):
                            clear_charge_nonce(st.session_state, _content_key)
                            st.session_state["manual_review_final_result"] = _result
                        else:
                            st.session_state["manual_review_paywall_reason"] = (
                                "Your selection was analyzed, but we couldn't record it against your "
                                "account (a temporary connection issue on our end, not something you did "
                                "wrong). Click “Analyze this selection” again to retry -- you "
                                "will not be double-charged for this attempt."
                            )
                    else:
                        st.session_state["manual_review_final_result"] = _result

            _paywall_reason = st.session_state.get("manual_review_paywall_reason")
            if _paywall_reason:
                st.error(_paywall_reason)
                if st.button("See plans & upgrade", key="manual_review_paywall_upgrade"):
                    st.session_state.pop("manual_review_paywall_reason", None)
                    st.session_state["page"] = "pricing"
                    st.rerun()

    final_result = st.session_state.get("manual_review_final_result")
    if final_result is not None and st.session_state.get("manual_review_checked_selection") == current_selection:
        st.markdown('<div class="blx-sec">Results</div>', unsafe_allow_html=True)
        if final_result.status == "poor_quality":
            st.warning("The player wasn't tracked clearly enough in that exact selection to measure "
                       "anything reliably, even though you confirmed it.")
        elif final_result.status == "experimental_result":
            s, e = final_result.resolved_interval
            st.error(f"EXPERIMENTAL, user-confirmed -- not automatically verified: "
                     f"{final_result.experimental_disclaimer}")
            st.success(f"Analyzed exactly what you selected: frames {s}-{e}{_secs(s, e)}.")

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

    st.markdown('<div class="blx-sec">&nbsp;</div>', unsafe_allow_html=True)
    _nav_buttons("mr_bottom")

    check_now = st.session_state.get("manual_review_check_result")
    checked_now = st.session_state.get("manual_review_checked_selection") == current_selection
    if final_result is not None and checked_now:
        step = 4
    elif check_now is not None and checked_now:
        step = 2 if check_now.status in BLOCKED_STATUS_MESSAGES else 3
    else:
        step = 1
    head.markdown(_header_html(step), unsafe_allow_html=True)
