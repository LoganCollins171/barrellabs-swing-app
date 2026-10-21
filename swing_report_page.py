"""
Individual Swing Report Page — dedicated route for opening one saved swing.

Why this exists
---------------
Before this module, clicking "Open Report" on the Saved Reports page
called render_dashboard_v3(force_record=record) — re-skinning the *entire
dashboard template* with that swing's data. From the user's perspective
that looked like "the dashboard came back" because they saw the same
dashboard layout. There was no dedicated report page.

This module renders a focused single-swing experience with:
  - Unified Edge masthead (Sessions tab active)
  - Header context strip (swing #, date, MLB comp)
  - Back-to-Sessions link
  - The full premium swing report (delegated to swing_report.render_swing_report)
  - Practice log (drill check-off)
  - Redesigned Swing Comparison section AT THE BOTTOM, using real data only

The page reads `st.session_state["view_swing_record"]` (or loads from
`view_swing_path` for legacy on-disk records). It NEVER falls back to the
dashboard if the record is missing — instead shows a clear error state.
"""

from __future__ import annotations

from typing import Dict, Any, List, Optional
import textwrap
import streamlit as st

from bl_edge_chrome import (
    render_edge_masthead,
    render_edge_page_wrapper_open,
    render_edge_page_wrapper_close,
)
from bl_theme import inject_global_theme


# =====================================================================
#                       PAGE-LOCAL STYLES
# =====================================================================
_SRP_CSS = """
<style>
.srp-context-strip {
  display: flex; align-items: center; justify-content: space-between;
  gap: 24px;
  padding: 18px 0 22px;
  border-bottom: 1px solid rgba(244,239,230,0.08);
  margin-bottom: 28px;
  font-family: 'Geist', -apple-system, sans-serif;
}
.srp-context-strip .left,
.srp-context-strip .right { display: flex; align-items: center; gap: 14px; }
.srp-context-eyebrow {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.22em;
  text-transform: uppercase; color: #8B8E94;
}
.srp-context-title {
  font-family: 'Instrument Serif', 'Fraunces', Georgia, serif;
  font-style: italic;
  font-size: 26px; line-height: 1.1;
  color: #F4EFE6;
  white-space: nowrap;
}
.srp-context-meta {
  font-family: 'Geist Mono', monospace;
  font-size: 11px; letter-spacing: 0.05em;
  color: #C8C4BB;
}
.srp-context-pill {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.18em;
  text-transform: uppercase;
  color: #E64530;
  padding: 5px 11px;
  border: 1px solid rgba(230,69,48,0.36);
  border-radius: 100px;
  background: rgba(230,69,48,0.08);
}
.srp-back-link a, .srp-back-link button {
  font-family: 'Geist Mono', monospace !important;
  font-size: 11px !important; letter-spacing: 0.12em !important;
  text-transform: uppercase !important;
  color: #C8C4BB !important;
  background: transparent !important;
  border: 1px solid rgba(244,239,230,0.10) !important;
  border-radius: 100px !important;
  padding: 7px 14px !important;
  cursor: pointer; text-decoration: none !important;
  transition: color 0.18s, border-color 0.18s, background 0.18s;
}
.srp-back-link button:hover {
  color: #F4EFE6 !important;
  border-color: rgba(244,239,230,0.22) !important;
  background: rgba(244,239,230,0.04) !important;
}
.srp-section-divider {
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(244,239,230,0.12), transparent);
  margin: 48px 0 28px;
}

/* ---------- Redesigned Swing Comparison ---------- */
.srp-compare-wrap {
  margin-top: 36px;
  padding: 36px 36px 32px;
  border-radius: 24px;
  border: 1px solid rgba(244,239,230,0.08);
  background:
    radial-gradient(ellipse at 95% -10%, rgba(230,69,48,0.06) 0%, transparent 55%),
    linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.012));
}
.srp-compare-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 20px; margin-bottom: 24px; flex-wrap: wrap;
}
.srp-compare-eyebrow {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.24em;
  text-transform: uppercase; color: #E64530;
  margin-bottom: 8px;
}
.srp-compare-title {
  font-family: 'Instrument Serif', Georgia, serif; font-style: italic;
  font-size: 32px; line-height: 1.05;
  color: #F4EFE6;
}
.srp-compare-sub {
  font-family: 'Geist', sans-serif;
  font-size: 13.5px; color: #C8C4BB;
  max-width: 520px; line-height: 1.55;
}
.srp-compare-pill {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.18em;
  text-transform: uppercase; color: #C8C4BB;
  padding: 6px 12px;
  border: 1px solid rgba(244,239,230,0.10);
  border-radius: 100px;
  background: rgba(255,255,255,0.02);
  white-space: nowrap;
}
.srp-compare-grid {
  display: grid;
  grid-template-columns: 1fr 88px 1fr;
  gap: 16px;
  align-items: stretch;
}
.srp-compare-col {
  padding: 24px 26px;
  border: 1px solid rgba(244,239,230,0.08);
  border-radius: 18px;
  background: rgba(255,255,255,0.02);
}
.srp-compare-col.is-current {
  border-color: rgba(230,69,48,0.38);
  background:
    radial-gradient(ellipse at 100% 0%, rgba(230,69,48,0.10) 0%, transparent 60%),
    rgba(255,255,255,0.03);
}
.srp-compare-col-eyebrow {
  font-family: 'Geist Mono', monospace;
  font-size: 10px; letter-spacing: 0.24em;
  text-transform: uppercase; color: #8B8E94;
  margin-bottom: 10px;
}
.srp-compare-col-eyebrow.is-current { color: #E64530; }
.srp-compare-col-title {
  font-family: 'Geist', sans-serif;
  font-size: 14px; font-weight: 600;
  color: #F4EFE6; margin-bottom: 14px;
  letter-spacing: -0.01em;
}
.srp-compare-col-score {
  font-family: 'Instrument Serif', Georgia, serif;
  font-size: 52px; line-height: 1; font-weight: 400;
  color: #F4EFE6; margin-bottom: 4px;
}
.srp-compare-col-score .of {
  font-size: 18px; color: #8B8E94; margin-left: 2px;
}
.srp-compare-col-mlb {
  font-family: 'Geist', sans-serif;
  font-size: 12.5px; letter-spacing: 0.02em;
  color: #C8C4BB; margin-top: 12px;
}
.srp-compare-col-date {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.05em;
  color: #565A62; margin-top: 14px;
}
.srp-compare-delta {
  display: flex; align-items: center; justify-content: center;
}
.srp-compare-delta-inner {
  width: 88px; height: 88px;
  border-radius: 50%;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  font-family: 'Geist Mono', monospace;
  font-weight: 600; letter-spacing: 0.02em;
  border: 1px solid rgba(244,239,230,0.12);
  background: rgba(10,11,14,0.65);
}
.srp-compare-delta-arrow {
  font-size: 18px; line-height: 1;
}
.srp-compare-delta-value {
  font-size: 16px; line-height: 1.1;
  margin-top: 2px;
}
.srp-compare-delta-label {
  font-size: 9px;
  letter-spacing: 0.18em; text-transform: uppercase;
  color: #8B8E94; margin-top: 4px;
}
.srp-delta-up { color: #4AE38C; border-color: rgba(74,227,140,0.4); }
.srp-delta-down { color: #E64530; border-color: rgba(230,69,48,0.4); }
.srp-delta-flat { color: #C8C4BB; }

.srp-metric-rows {
  margin-top: 24px;
  display: grid;
  grid-template-columns: 1fr;
  gap: 6px;
}
.srp-metric-row {
  display: grid;
  grid-template-columns: 220px 1fr 80px 1fr 90px;
  align-items: center;
  gap: 14px;
  padding: 12px 16px;
  border-radius: 12px;
  background: rgba(255,255,255,0.015);
  border: 1px solid rgba(244,239,230,0.05);
}
.srp-metric-row:hover { background: rgba(255,255,255,0.03); }
.srp-metric-label {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.16em;
  text-transform: uppercase; color: #8B8E94;
}
.srp-metric-prev,
.srp-metric-curr {
  font-family: 'Geist', sans-serif; font-weight: 500;
  font-size: 14px; color: #C8C4BB;
}
.srp-metric-curr { color: #F4EFE6; font-weight: 600; }
.srp-metric-arrow {
  font-family: 'Geist Mono', monospace;
  font-size: 10.5px; letter-spacing: 0.12em;
  color: #565A62; text-align: center;
}
.srp-metric-delta {
  font-family: 'Geist Mono', monospace;
  font-size: 12px; font-weight: 600;
  text-align: right;
  letter-spacing: 0.02em;
}
.srp-metric-delta.up { color: #4AE38C; }
.srp-metric-delta.down { color: #E64530; }
.srp-metric-delta.flat { color: #8B8E94; }

.srp-compare-empty {
  padding: 48px 36px;
  text-align: center;
  border: 1px dashed rgba(244,239,230,0.12);
  border-radius: 18px;
  background: rgba(255,255,255,0.015);
}
.srp-compare-empty-icon {
  font-size: 28px; color: #565A62; margin-bottom: 14px;
}
.srp-compare-empty-title {
  font-family: 'Instrument Serif', Georgia, serif; font-style: italic;
  font-size: 22px; color: #F4EFE6; margin-bottom: 10px;
}
.srp-compare-empty-body {
  font-family: 'Geist', sans-serif; font-size: 13.5px;
  color: #C8C4BB; line-height: 1.55; max-width: 480px;
  margin: 0 auto;
}

@media (max-width: 900px) {
  .srp-compare-grid { grid-template-columns: 1fr; gap: 14px; }
  .srp-compare-delta { order: 2; height: 56px; }
  .srp-compare-delta-inner { width: 56px; height: 56px; }
  .srp-compare-delta-arrow { font-size: 14px; }
  .srp-compare-delta-value { font-size: 13px; }
  .srp-metric-row {
    grid-template-columns: 1fr 1fr; gap: 8px;
    padding: 12px;
  }
  .srp-metric-label { grid-column: 1 / -1; }
  .srp-metric-arrow { display: none; }
  .srp-context-strip { flex-direction: column; align-items: flex-start; }
}
</style>
"""


# =====================================================================
#                       HELPERS
# =====================================================================
def _fmt_date(rec: Dict[str, Any]) -> str:
    from datetime import datetime
    ts = rec.get("timestamp") or rec.get("created_at") or rec.get("date")
    if not ts:
        return "—"
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%b %-d · %Y")
        except Exception:
            return ts[:10]
    try:
        return ts.strftime("%b %-d · %Y")
    except Exception:
        return str(ts)


def _swing_label(rec: Dict[str, Any]) -> str:
    n = rec.get("swing_number")
    try:
        return f"Swing #{int(n):02d}"
    except Exception:
        return "Swing"


def _score(rec: Dict[str, Any]) -> Optional[float]:
    s = rec.get("score")
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _mlb_ref(rec: Dict[str, Any]) -> str:
    return str(rec.get("reference_name") or rec.get("mlb_comp") or "—")


def _previous_record(
    current: Dict[str, Any], history: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return the swing that came IMMEDIATELY BEFORE the current one.

    Strategy: identify by id/timestamp where possible; fall back to
    index by swing_number. Returns None if current is the very first.
    """
    if not history:
        return None
    cur_id = current.get("id")
    cur_ts = current.get("timestamp") or current.get("created_at")
    cur_num = current.get("swing_number")

    # history is oldest-first (per load_swing_history). Walk it.
    idx = None
    for i, rec in enumerate(history):
        if cur_id and rec.get("id") == cur_id:
            idx = i; break
        if cur_ts and (rec.get("timestamp") or rec.get("created_at")) == cur_ts:
            idx = i; break
        if cur_num is not None and rec.get("swing_number") == cur_num:
            idx = i; break
    if idx is None:
        return None
    if idx == 0:
        return None
    return history[idx - 1]


def _delta_pct(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    if curr is None or prev is None:
        return None
    return curr - prev  # raw absolute delta, not percentage


def _delta_class(d: Optional[float]) -> str:
    if d is None or abs(d) < 0.01:
        return "flat"
    return "up" if d > 0 else "down"


def _delta_arrow(d: Optional[float]) -> str:
    if d is None or abs(d) < 0.01:
        return "→"
    return "↑" if d > 0 else "↓"


def _fmt_delta(d: Optional[float], unit: str = "", precision: int = 1) -> str:
    if d is None:
        return "—"
    if abs(d) < (10 ** -precision) / 2:
        return f"±0{unit}"
    sign = "+" if d > 0 else ""
    return f"{sign}{d:.{precision}f}{unit}"


def _collect_metric_pairs(
    current: Dict[str, Any],
    previous: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build a list of comparable metric rows using ONLY real data
    present in BOTH records. Anything missing is skipped — no
    placeholders. Returns rows like:
        {"label": "...", "prev": float, "curr": float, "unit": "..."}
    """
    rows: List[Dict[str, Any]] = []

    def _push(label, cur_val, prev_val, unit="", precision=1, higher_is_better=True):
        if cur_val is None or prev_val is None:
            return
        try:
            cv, pv = float(cur_val), float(prev_val)
        except (TypeError, ValueError):
            return
        rows.append({
            "label": label,
            "prev": pv,
            "curr": cv,
            "unit": unit,
            "precision": precision,
            "higher_is_better": higher_is_better,
        })

    cur_metrics = current.get("metrics") or {}
    prev_metrics = previous.get("metrics") or {}

    # Composite similarity score (the headline metric — always present)
    _push("Swing Score", current.get("score"), previous.get("score"),
          unit="", precision=0, higher_is_better=True)

    # Common per-axis category match percentages, if both records have them.
    # We accept several common naming conventions used historically in the
    # codebase (category_matches dict, *_match keys at top level, etc.).
    def _axis_value(rec: Dict[str, Any], keys: tuple) -> Optional[float]:
        for k in keys:
            if k in rec and rec[k] is not None:
                return rec[k]
        cat = rec.get("category_matches") or rec.get("axis_matches") or {}
        if isinstance(cat, dict):
            for k in keys:
                if k in cat and cat[k] is not None:
                    return cat[k]
        m = rec.get("metrics") or {}
        if isinstance(m, dict):
            for k in keys:
                if k in m and m[k] is not None:
                    return m[k]
        return None

    axes = [
        ("Head Stability",        ("head_stability_match", "head_stability", "head_match")),
        ("Hip Rotation",          ("hip_rotation_match", "hip_rotation", "hip_match")),
        ("Hip-Shoulder Separation", ("hip_shoulder_separation_match", "hip_shoulder_separation", "separation_match")),
        ("Front-Side Firmness",   ("knee_extension_match", "front_side_firmness_match", "knee_extension")),
        ("Timing & Quickness",    ("timing_match", "tempo_match", "timing")),
    ]
    for label, keys in axes:
        cv = _axis_value(current, keys)
        pv = _axis_value(previous, keys)
        _push(label, cv, pv, unit="%", precision=0, higher_is_better=True)

    return rows


# =====================================================================
#                       REDESIGNED COMPARISON
# =====================================================================
def render_swing_compare_redesigned(
    current: Dict[str, Any],
    history: List[Dict[str, Any]],
) -> None:
    """
    Premium, integrated comparison section for the individual swing report.

    Compares the CURRENTLY OPENED swing (the page's focus) against the
    swing that came immediately before it. Uses only real metrics that
    exist in both records; missing metrics are silently skipped rather
    than back-filled with placeholders.

    Empty state: if no previous swing exists, shows a clean explanation
    that comparison unlocks after the next swing.
    """
    st.markdown('<div class="srp-compare-wrap">', unsafe_allow_html=True)

    # --- Header strip
    st.markdown(
        """
        <div class="srp-compare-head">
          <div>
            <div class="srp-compare-eyebrow">PROGRESS · v.s. PREVIOUS</div>
            <div class="srp-compare-title">Swing Comparison</div>
            <div class="srp-compare-sub">
              How this swing stacks up against your last one. Only
              metrics present in both reports are shown — anything
              unavailable is intentionally omitted.
            </div>
          </div>
          <span class="srp-compare-pill">Real data only</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    previous = _previous_record(current, history or [])

    if previous is None:
        # Empty state
        st.markdown(
            """
            <div class="srp-compare-empty">
              <div class="srp-compare-empty-icon">◇</div>
              <div class="srp-compare-empty-title">
                Comparison unlocks after your next swing
              </div>
              <div class="srp-compare-empty-body">
                This is your first analyzed swing — there's nothing to
                compare against yet. Upload a new clip from the Dashboard
                and we'll show side-by-side progress here automatically.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # --- Side-by-side score cards
    cur_score = _score(current)
    prev_score = _score(previous)
    score_delta = _delta_pct(cur_score, prev_score)

    delta_cls = _delta_class(score_delta)
    delta_arrow = _delta_arrow(score_delta)
    delta_value = _fmt_delta(score_delta, unit="", precision=0)

    def _score_html(rec, score, is_current: bool) -> str:
        col_cls = "srp-compare-col is-current" if is_current else "srp-compare-col"
        eye_cls = "srp-compare-col-eyebrow is-current" if is_current else "srp-compare-col-eyebrow"
        eyebrow = "THIS SWING" if is_current else "PREVIOUS SWING"
        score_txt = f"{int(round(score))}" if score is not None else "—"
        return f"""
        <div class="{col_cls}">
          <div class="{eye_cls}">{eyebrow}</div>
          <div class="srp-compare-col-title">{_swing_label(rec)}</div>
          <div class="srp-compare-col-score">{score_txt}<span class="of">/100</span></div>
          <div class="srp-compare-col-mlb">vs. {_mlb_ref(rec)}</div>
          <div class="srp-compare-col-date">{_fmt_date(rec)}</div>
        </div>
        """

    delta_html = f"""
    <div class="srp-compare-delta">
      <div class="srp-compare-delta-inner srp-delta-{delta_cls}">
        <span class="srp-compare-delta-arrow">{delta_arrow}</span>
        <span class="srp-compare-delta-value">{delta_value}</span>
        <span class="srp-compare-delta-label">SCORE</span>
      </div>
    </div>
    """

    st.markdown(
        f"""
        <div class="srp-compare-grid">
          {_score_html(previous, prev_score, is_current=False)}
          {delta_html}
          {_score_html(current, cur_score, is_current=True)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Per-metric rows (only those present in BOTH records)
    metric_rows = _collect_metric_pairs(current, previous)
    # Drop the duplicated headline "Swing Score" row since it already lives
    # in the big cards above — but keep it if for some reason the score
    # cards rendered as "—" (means scores missing) so the user still sees
    # something useful.
    if cur_score is not None and prev_score is not None:
        metric_rows = [r for r in metric_rows if r["label"] != "Swing Score"]

    if not metric_rows:
        # No comparable per-axis metrics — that's OK, the score cards
        # above already convey the comparison. Nothing more to add.
        st.markdown("</div>", unsafe_allow_html=True)
        return

    st.markdown('<div class="srp-metric-rows">', unsafe_allow_html=True)
    for row in metric_rows:
        d = row["curr"] - row["prev"]
        # Direction-aware coloring: if higher-is-better is False (e.g.
        # head drift would be lower-is-better) we flip.
        is_improvement = (d > 0) if row.get("higher_is_better", True) else (d < 0)
        if abs(d) < (10 ** -row["precision"]) / 2:
            cls = "flat"; arrow = "→"
        else:
            cls = "up" if is_improvement else "down"
            arrow = "↑" if is_improvement else "↓"
        prev_str = f"{row['prev']:.{row['precision']}f}{row['unit']}"
        curr_str = f"{row['curr']:.{row['precision']}f}{row['unit']}"
        delta_str = _fmt_delta(d, unit=row["unit"], precision=row["precision"])

        st.markdown(
            f"""
            <div class="srp-metric-row">
              <div class="srp-metric-label">{row['label']}</div>
              <div class="srp-metric-prev">{prev_str}</div>
              <div class="srp-metric-arrow">→</div>
              <div class="srp-metric-curr">{curr_str}</div>
              <div class="srp-metric-delta {cls}">{arrow} {delta_str}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


# =====================================================================
#                       MAIN PAGE RENDERER
# =====================================================================
def render_swing_report_page(
    user: Dict[str, Any],
    record: Dict[str, Any],
    *,
    history: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Render the standalone individual-swing report page (DASHBOARD STYLE).

    As of the dashboard-style report promotion this delegates to
    `swing_report_dashboard_preview.render_swing_report_dashboard_preview`
    with `is_preview=False`, which renders the full premium report
    using the BarrelLabs Edge design system. The renderer includes its
    own Hero / Top Priorities / Drills / Key Metrics / Mechanical
    Breakdown / Progress / Compare This Swing / Next Session sections.

    Layout (top to bottom):
        1. Unified Edge masthead (Sessions tab active)
        2. Back-to-Sessions link
        3. Full dashboard-style premium swing report
        4. Practice log (drill check-off — preserved from legacy)
    """
    inject_global_theme()

    # 1. Masthead
    render_edge_masthead(user, active_page="swing_report")

    # BL-008C.20 Deliverable 6: a visible (not hover-only) disclaimer at
    # the top of every swing report. The existing MATCH_CAVEAT text
    # (mlb_match.py, from BL-004C) was previously shown only as an HTML
    # `title` tooltip on the MLB-match percentage specifically -- easy to
    # miss, invisible on mobile (no hover), and absent near the Edge
    # Score itself. Same wording, reused rather than replaced, now
    # always visible and covering both numbers. Does not change how
    # either number is computed -- only how it's labeled.
    try:
        from mlb_match import MATCH_CAVEAT
        st.caption(f"⚠️ {MATCH_CAVEAT} Edge Score is likewise an experimental, unvalidated composite, "
                   "not a measured or certified metric.")
    except Exception:
        pass

    # 2. Page wrapper
    render_edge_page_wrapper_open()

    # 3. Back-to-Sessions affordance + export bar (PDF / Print)
    bcol, _spacer = st.columns([2, 8])
    with bcol:
        st.markdown('<div class="srp-back-link">', unsafe_allow_html=True)
        if st.button("← Back to Sessions", key="srp_back_to_sessions"):
            st.session_state["page"] = "saved_reports"
            st.session_state.pop("view_swing_record", None)
            st.session_state.pop("view_swing_path", None)
            st.session_state.pop("view_swing_report_id", None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ---- Export bar — Download PDF + Print (audit follow-up) ----
    # The new editorial Swing Report shipped without export wiring even
    # though the PDF builder exists. Lifting the Pro-gated pattern from
    # app.py:2333-2380 so users can finally download/print their reports
    # from the new path. Build is gated on can_export_pdf so we don't
    # waste cycles building PDF bytes for Free users (just shows a 🔒).
    pdf_bytes = None
    pdf_allowed = False
    try:
        from entitlements import can_export_pdf
        from subscription_storage import load_my_plan
        from swing_report import build_swing_report_pdf
        pdf_allowed = bool(can_export_pdf(load_my_plan()))
        if pdf_allowed:
            pdf_bytes = build_swing_report_pdf(record, history=history)
    except Exception as _pdf_err:
        # Gracefully degrade — the report still renders even if PDF
        # build fails or entitlements lookup goes sideways.
        pdf_bytes = None
        pdf_allowed = False

    _row_key = record.get("id") or record.get("timestamp") or "new"
    exp_col1, exp_col2, _exp_spacer = st.columns([1.4, 1.4, 5])
    with exp_col1:
        if pdf_allowed and pdf_bytes is not None:
            st.download_button(
                "⬇  Download PDF Report",
                data=pdf_bytes,
                file_name=f"swing_report_{record.get('timestamp', 'saved')}.pdf",
                mime="application/pdf",
                width="stretch",
                key=f"srp_pdf_dl_{_row_key}",
            )
        else:
            if st.button(
                "🔒  PDF — Upgrade",
                width="stretch",
                key=f"srp_pdf_locked_{_row_key}",
                help="PDF report export is a Pro feature.",
            ):
                st.toast(
                    "PDF export is a Pro feature. Upgrade to Solo Pro "
                    "(or redeem a beta code) to download reports.",
                    icon="🔒",
                )
    with exp_col2:
        # Print via window.print() — tiny inline HTML button matched to
        # the new editorial palette (bone text on transparent w/ gold
        # hover, not the old red).
        st.markdown(
            """
            <button class="srp-print-btn" onclick="window.print();return false;">
              🖨  Print Report
            </button>
            <style>
            .srp-print-btn {
                width: 100%;
                background: transparent;
                color: #F4EFE6;
                border: 1px solid rgba(232,193,112,0.45);
                border-radius: 999px;
                padding: 0.62rem 1rem;
                font-family: 'Geist', 'Inter', system-ui, sans-serif;
                font-weight: 500;
                font-size: 0.92rem;
                letter-spacing: -0.005em;
                cursor: pointer;
                transition: background .22s ease, transform .22s ease, box-shadow .22s ease;
            }
            .srp-print-btn:hover {
                background: rgba(232,193,112,0.08);
                transform: translateY(-1px);
                box-shadow: 0 6px 20px -10px rgba(232,193,112,0.6);
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    # 4. The full dashboard-style premium swing report.
    try:
        from swing_report_dashboard_preview import (
            render_swing_report_dashboard_preview,
        )
        render_swing_report_dashboard_preview(
            record, history or [], is_sample=False, is_preview=False,
        )
    except Exception as e:
        st.error(f"Couldn't render the swing report: {e}")

    # 5. Practice log — preserved from the legacy chain since it's
    #    orthogonal to the report renderer and the user relies on it
    #    to track drill completion.
    try:
        from app import render_swing_practice_log  # type: ignore
        player_id = user.get("slug") or user.get("id")
        if player_id:
            render_swing_practice_log(record, player_id)
    except Exception:
        # Non-fatal — the practice log is a bonus, not a blocker.
        pass

    render_edge_page_wrapper_close()
