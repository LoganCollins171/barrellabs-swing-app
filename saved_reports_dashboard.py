"""Saved Reports — dashboard-style edition.

The Sessions tab in the Edge masthead lands here. This module replaces
the older `saved_reports.render_saved_reports` look-and-feel with a
layout that matches the dashboard-style Premium Swing Report
(`swing_report_dashboard_preview`).

What stays the same
-------------------
- Data source: `player_storage.load_swing_history` (real saved swings).
- Open Report wiring: sets `view_swing_record` + `page = "swing_report"`
  exactly as the legacy page did, so the production report route
  rendered by `swing_report_page.render_swing_report_page` opens.
- PDF download: receives `build_pdf_fn=build_swing_report_pdf` from
  `app.py` and renders one button per card when allowed.
- Filtering helpers: re-uses `_filter_history` / `_parse_date` /
  `_fmt_short_date` / `_top_focus` from the legacy module so behavior
  is identical.
- Delete-with-confirmation flow: unchanged.

What's new
----------
- Visual language: tokens, typography, spacing match the new dashboard.
  Bone/red/gold palette, Instrument Serif italic titles, Geist Mono
  eyebrows, glassy cards with hairline borders.
- Cards now surface: swing number + score ring color + MLB comp +
  date + top priority + filename in a tightened single-card layout.
- Sessions banner ("Archive") replaced with a serif italic eyebrow +
  title + record count.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import streamlit as st

from bl_edge_chrome import (
    render_edge_masthead,
    render_edge_page_wrapper_open,
    render_edge_page_wrapper_close,
)
from bl_theme import inject_global_theme
from entitlements import can_export_pdf
from player_storage import delete_swing_record, load_swing_history
from saved_reports import (  # reuse — identical filter/parse logic
    _filter_history,
    _fmt_short_date,
    _top_focus,
)
from subscription_storage import load_my_plan


_PAGE_CSS = """
<style>
/* ====================================================================
   TOKENS + LAYOUT scoped to the REAL content container.
   Streamlit auto-closes a bare `<div class="srl-wrap">` into an empty
   phantom node, so design tokens / gutter / button overrides keyed only
   to `.srl-wrap` never reach the actual page content (it renders as
   SIBLINGS of the phantom inside [data-testid="stMainBlockContainer"]).
   We therefore define everything on the block container itself — on a
   Sessions render that container holds nothing but this page. The
   `.srl-wrap` fallback keeps the tokens valid for the empty-state path
   too. The masthead is full-bleed + self-styled, so this is safe. */
[data-testid="stMainBlockContainer"]:has(.srl-wrap),
.srl-wrap {
  --srl-bg:        #0A0B0E;
  --srl-bg-2:      #0F1115;
  --srl-bone:      #F4EFE6;
  --srl-bone-80:   rgba(244,239,230,0.82);
  --srl-bone-60:   rgba(244,239,230,0.58);
  --srl-bone-40:   rgba(244,239,230,0.36);
  --srl-line:      rgba(244,239,230,0.08);
  --srl-line-hi:   rgba(244,239,230,0.16);
  --srl-glass-1:   rgba(255,255,255,0.025);
  --srl-glass-2:   rgba(255,255,255,0.045);
  --srl-red:       #E64530;
  --srl-red-soft:  rgba(230,69,48,0.12);
  --srl-gold:      #E8C170;
  --srl-gold-soft: rgba(232,193,112,0.14);
  --srl-green:     #4AE38C;
  --srl-green-soft:rgba(74,227,140,0.12);
  --srl-radius:    14px;
  --srl-radius-lg: 20px;
  --srl-serif: 'Instrument Serif', 'Fraunces', Georgia, serif;
  --srl-sans:  'Geist', -apple-system, BlinkMacSystemFont, 'Inter', system-ui, sans-serif;
  --srl-mono:  'Geist Mono', 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;

  font-family: var(--srl-sans);
  color: var(--srl-bone);
}
/* Real content frame (1560 / 40px) so Sessions aligns with the nav and
   every other page. Applied to the block container — the element that
   actually wraps the cards. */
[data-testid="stMainBlockContainer"]:has(.srl-wrap) {
  max-width: 1560px !important;
  margin: 0 auto !important;
  padding: 0.6rem 40px 3rem !important;
  box-sizing: border-box !important;
}
.srl-wrap { display: contents; }
.srl-eyebrow {
  font-family: var(--srl-mono);
  font-size: 10.5px;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: var(--srl-red);
  font-weight: 600;
  display:flex; align-items:center; gap:8px;
}
.srl-eyebrow::before {
  content:""; width:6px; height:6px; border-radius:50%;
  background: var(--srl-red); box-shadow: 0 0 8px var(--srl-red);
}
.srl-pagehead {
  display:flex; align-items:flex-end; justify-content:space-between;
  padding: 0.9rem 0 0.7rem;
  margin-bottom: 0.7rem;
  border-bottom: 1px solid var(--srl-line);
  gap: 2rem;
}
.srl-pagehead-title {
  font-family: var(--srl-serif);
  font-size: 2.1rem;
  font-style: italic;
  line-height: 0.95;
  letter-spacing: -0.02em;
  color: var(--srl-bone);
  margin: 0.35rem 0 0;
}
.srl-pagehead-meta {
  text-align: right;
  font-family: var(--srl-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--srl-bone-60);
}
.srl-pagehead-meta strong {
  color: var(--srl-bone); font-weight: 500;
  display:block; margin-top: 4px;
  font-family: var(--srl-serif); font-style: italic;
  font-size: 16px; letter-spacing: 0; text-transform: none;
}

/* FILTER BAR — a real keyed st.container so the card frame actually wraps
   the search + selects (a bare markdown div would collapse to a phantom). */
.st-key-srl_filter_card {
  background: var(--srl-glass-1) !important;
  border: 1px solid var(--srl-line) !important;
  border-radius: var(--srl-radius) !important;
  padding: 0.85rem 1.15rem 1rem !important;
  margin-bottom: 0.55rem !important;
}
.srl-filter-eyebrow {
  font-family: var(--srl-mono);
  font-size: 10px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--srl-bone-60);
  margin-bottom: 0.6rem;
}
/* Streamlit widget polish — scoped to the keyed filter card. */
.st-key-srl_filter_card [data-testid="stTextInput"] input,
.st-key-srl_filter_card [data-baseweb="select"] > div,
.st-key-srl_filter_card [data-baseweb="input"] {
  background: var(--srl-bg-2) !important;
  border-color: var(--srl-line-hi) !important;
  color: var(--srl-bone) !important;
  border-radius: var(--srl-radius) !important;
  font-family: var(--srl-sans) !important;
}
.st-key-srl_filter_card [data-testid="stTextInput"] input::placeholder {
  color: var(--srl-bone-40) !important;
}
.st-key-srl_filter_card [data-baseweb="select"] svg { fill: var(--srl-bone-60) !important; }
.st-key-srl_filter_card label,
.st-key-srl_filter_card label p {
  font-family: var(--srl-mono) !important;
  font-size: 10px !important;
  letter-spacing: 0.18em !important;
  text-transform: uppercase !important;
  color: var(--srl-bone-60) !important;
}

.srl-results-strip {
  display:flex; align-items:center; justify-content:space-between;
  margin: 0.15rem 0 0.4rem;
  font-family: var(--srl-mono);
  font-size: 10.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--srl-bone-60);
}
.srl-results-strip strong { color: var(--srl-bone); }

/* CARD — four columns; the redundant "Trend" column was removed (PB
   already lives on the swing-# label, score delta already shows trend
   via the delta pill, so a 5th cell saying "Saved" was dead weight). */
.srl-card {
  display:grid;
  grid-template-columns: 84px minmax(0, 1.4fr) minmax(0, 1.3fr) minmax(0, 0.9fr);
  gap: 1.1rem;
  align-items: center;
  background: var(--srl-glass-1);
  border: 1px solid var(--srl-line);
  border-radius: var(--srl-radius-lg);
  padding: 1.1rem 1.3rem;
  transition: border-color .2s ease, background .2s ease;
}
.srl-card:hover {
  border-color: var(--srl-line-hi);
  background: var(--srl-glass-2);
}
.srl-card-score {
  width: 76px; height: 76px; border-radius: 18px;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  font-family: var(--srl-serif); font-style: italic;
  font-size: 2.2rem; line-height: 1;
  border: 1px solid;
}
.srl-card-score-foot {
  font-family: var(--srl-mono); font-size: 8.5px;
  letter-spacing: 0.16em; text-transform: uppercase;
  margin-top: 4px; opacity: 0.7;
}
.srl-card-score.green { background: var(--srl-green-soft); color: var(--srl-green); border-color: rgba(74,227,140,0.25); }
.srl-card-score.amber { background: var(--srl-gold-soft);  color: var(--srl-gold);  border-color: rgba(232,193,112,0.25); }
.srl-card-score.red   { background: var(--srl-red-soft);   color: var(--srl-red);   border-color: rgba(230,69,48,0.28); }

/* Score delta pill + Personal Best badge + sparkline */
.srl-delta {
  display:inline-flex; align-items:center; gap:4px;
  font-family: var(--srl-mono); font-size: 10px; font-weight: 600;
  letter-spacing: 0.08em; padding: 3px 8px; border-radius: 999px;
  border: 1px solid var(--srl-line); margin-top: 7px;
}
.srl-delta.up   { color: var(--srl-green); border-color: rgba(74,227,140,0.3);  background: var(--srl-green-soft); }
.srl-delta.down { color: var(--srl-red);   border-color: rgba(230,69,48,0.3);   background: var(--srl-red-soft); }
.srl-delta.flat { color: var(--srl-bone-60); }
.srl-delta.new  { color: var(--srl-gold);  border-color: rgba(232,193,112,0.3); background: var(--srl-gold-soft); }
.srl-pb {
  display:inline-flex; align-items:center; gap:5px;
  font-family: var(--srl-mono); font-size: 9px; font-weight: 700;
  letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--srl-gold); background: var(--srl-gold-soft);
  border: 1px solid rgba(232,193,112,0.35);
  padding: 3px 8px; border-radius: 999px; margin-left: 8px;
}
.srl-mlb-line { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
.srl-mlb-sim {
  font-family: var(--srl-mono); font-size: 11px; font-weight: 600;
  color: var(--srl-gold); letter-spacing: 0.04em;
}
.srl-spark { margin-top: 6px; opacity: 0.85; }
.srl-spark svg { display:block; width: 120px; height: 30px; }

.srl-col-label {
  font-family: var(--srl-mono);
  font-size: 9.5px; letter-spacing: 0.2em;
  text-transform: uppercase; color: var(--srl-bone-60);
  margin-bottom: 5px;
}
.srl-col-val {
  font-family: var(--srl-serif); font-style: italic;
  font-size: 1.15rem; color: var(--srl-bone);
  letter-spacing: -0.005em; line-height: 1.2;
}
.srl-col-sub {
  font-family: var(--srl-mono); font-size: 9.5px;
  letter-spacing: 0.12em; color: var(--srl-bone-60);
  margin-top: 4px; text-transform: uppercase;
}

/* ACTION ROW */
/* Tighten the inter-element gap so each card + its action row read as ONE
   premium unit and cards sit close together. */
[data-testid="stMainBlockContainer"]:has(.srl-wrap)
  div[data-testid="stVerticalBlock"] { gap: 0.5rem !important; }
.srl-card { padding: 0.95rem 1.2rem; }
.srl-actions {
  margin-top: -2px;
  margin-bottom: 0.55rem;
  padding: 0 0.4rem;
}
/* The action buttons can't be wrapped (Streamlit widgets can't live inside
   raw markdown), so they're targeted by their keyed element containers
   (`st-key-srl_*`) — the only reliable hook once the .srl-actions wrapper
   collapses into a phantom node. Each card's keys carry the record id, so
   the prefix match catches every row. */
/* OPEN REPORT — primary red pill */
[class*="st-key-srl_open_"] button {
  background: var(--srl-red) !important;
  color: #fff !important;
  border: 1px solid rgba(255,255,255,0.10) !important;
  border-radius: 999px !important;
  font-family: var(--srl-sans) !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  padding: 0.6rem 1.2rem !important;
  letter-spacing: -0.005em !important;
  box-shadow: 0 10px 24px -12px rgba(230,69,48,0.55),
              inset 0 1px 0 rgba(255,255,255,0.16) !important;
  transition: transform .15s ease, box-shadow .2s ease !important;
}
[class*="st-key-srl_open_"] button:hover {
  transform: translateY(-1px);
  box-shadow: 0 14px 28px -12px rgba(230,69,48,0.7),
              inset 0 1px 0 rgba(255,255,255,0.22) !important;
}
/* DOWNLOAD / PDF gate — glass secondary pill */
[data-testid="stMainBlockContainer"]:has(.srl-wrap)
  [data-testid="stDownloadButton"] button,
[class*="st-key-srl_pdfgate_"] button {
  background: var(--srl-glass-2) !important;
  color: var(--srl-bone) !important;
  border: 1px solid var(--srl-line-hi) !important;
  border-radius: 999px !important;
  font-family: var(--srl-sans) !important;
  font-weight: 500 !important;
  font-size: 13px !important;
  padding: 0.6rem 1.2rem !important;
  box-shadow: none !important;
  transition: border-color .2s ease, background .2s ease !important;
}
[data-testid="stMainBlockContainer"]:has(.srl-wrap)
  [data-testid="stDownloadButton"] button:hover,
[class*="st-key-srl_pdfgate_"] button:hover {
  border-color: var(--srl-bone-40) !important;
  background: rgba(255,255,255,0.07) !important;
}
/* DELETE — quiet ghost pill; CONFIRM DELETE — red-tinted ghost */
[class*="st-key-srl_del_"] button {
  background: transparent !important;
  color: var(--srl-bone-60) !important;
  border: 1px solid var(--srl-line) !important;
  border-radius: 999px !important;
  font-family: var(--srl-sans) !important;
  font-weight: 500 !important;
  font-size: 13px !important;
  padding: 0.6rem 1.2rem !important;
  box-shadow: none !important;
  transition: color .2s ease, border-color .2s ease !important;
}
[class*="st-key-srl_del_"] button:hover {
  color: var(--srl-bone) !important;
  border-color: var(--srl-line-hi) !important;
}
[class*="st-key-srl_del_yes_"] button {
  color: var(--srl-red) !important;
  border-color: rgba(230,69,48,0.45) !important;
  background: var(--srl-red-soft) !important;
}

/* EMPTY STATE */
.srl-empty {
  text-align: center;
  padding: 2rem 1.5rem;
  background: var(--srl-glass-1);
  border: 1px dashed var(--srl-line-hi);
  border-radius: var(--srl-radius-lg);
}
.srl-empty-icon {
  font-size: 2rem; color: var(--srl-bone-40);
  margin-bottom: 0.7rem;
}
.srl-empty-title {
  font-family: var(--srl-serif); font-style: italic;
  font-size: 1.6rem; color: var(--srl-bone);
  margin-bottom: 0.5rem;
}
.srl-empty-sub {
  color: var(--srl-bone-60);
  font-size: 14px; line-height: 1.55;
  max-width: 480px; margin: 0 auto;
}

/* RESPONSIVE — placed AFTER all base rules so source order can't let the
   base padding/grid override these (known gotcha in this codebase). The
   gutter now lives on the block container, so the media queries target it
   to stay aligned with the masthead's responsive gutter. */
@media (max-width: 1100px) {
  [data-testid="stMainBlockContainer"]:has(.srl-wrap) {
    padding: 0.6rem 22px 3rem !important;
  }
}
@media (max-width: 960px) {
  .srl-card { grid-template-columns: 80px 1fr 1fr; gap: 0.9rem; }
  .srl-card-file-col { display: none; }
}
@media (max-width: 560px) {
  [data-testid="stMainBlockContainer"]:has(.srl-wrap) {
    padding: 0.6rem 16px 2.5rem !important;
  }
  .srl-pagehead { flex-direction: column; align-items: flex-start; gap: 0.7rem; }
  .srl-pagehead-meta { text-align: left; }
  .srl-pagehead-title { font-size: 2.3rem; }
  .srl-card {
    grid-template-columns: 64px 1fr;
    padding: 0.9rem 1rem;
  }
  .srl-card-score { width: 60px; height: 60px; font-size: 1.7rem; border-radius: 14px; }
  .srl-card-cell-hide-mobile { display: none; }
}

/* Match the app's button language — the rest of this page (chips, eyebrows,
   labels, the masthead) is all Geist Mono UPPERCASE; the action buttons were
   Geist Sans title-case, which is why they read as "not part of the app".
   Appended last so it wins. */
[class*="st-key-srl_open_"] button,
[class*="st-key-srl_pdfgate_"] button,
[class*="st-key-srl_del_"] button,
[class*="st-key-srl_del_yes_"] button,
[data-testid="stMainBlockContainer"]:has(.srl-wrap) [data-testid="stDownloadButton"] button {
  font-family: var(--srl-mono) !important;
  text-transform: uppercase !important;
  letter-spacing: 0.13em !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  padding: 0.58rem 1.05rem !important;
}
/* De-airy the action area: pull the buttons up under their card and tighten the
   stacked gap between Open / Delete on mobile. */
[class*="st-key-srl_open_"], [class*="st-key-srl_pdfgate_"],
[class*="st-key-srl_del_"], [class*="st-key-srl_del_yes_"] {
  margin-top: -4px !important;
}
[data-testid="stMainBlockContainer"]:has(.srl-wrap) [data-testid="stHorizontalBlock"] {
  gap: 8px !important; row-gap: 6px !important; margin-bottom: 4px !important;
}
</style>
"""


def _band_class(score: Optional[float]) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "amber"
    if s >= 85: return "green"
    if s < 60:  return "red"
    return "amber"


def _score_of(rec: Dict[str, Any]) -> Optional[float]:
    try:
        return float(rec.get("score"))
    except (TypeError, ValueError):
        return None


def _mini_sparkline(points: List[float], width: int = 120,
                     height: int = 30) -> str:
    """Tiny score-trend sparkline (gold). Empty string if <2 points."""
    pts = [p for p in points if p is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = max(hi - lo, 1.0)
    pad = 3
    step = (width - 2 * pad) / max(len(pts) - 1, 1)
    coords = []
    for i, v in enumerate(pts):
        x = pad + i * step
        y = (height - pad) - ((v - lo) / span) * (height - 2 * pad)
        coords.append((x, y))
    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    lx, ly = coords[-1]
    return (
        f'<div class="srl-spark"><svg viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none">'
        f'<path d="{path}" fill="none" stroke="#E8C170" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="#E8C170"/>'
        f'</svg></div>'
    )


def _empty(title: str, sub: str, icon: str = "◇") -> None:
    st.markdown(
        f'<div class="srl-empty"><div class="srl-empty-icon">{icon}</div>'
        f'<div class="srl-empty-title">{html.escape(title)}</div>'
        f'<div class="srl-empty-sub">{html.escape(sub)}</div></div>',
        unsafe_allow_html=True,
    )


def render_saved_reports_dashboard(user: Dict[str, Any],
                                    build_pdf_fn=None) -> None:
    """Dashboard-style Sessions / Saved Reports page.

    Drop-in replacement for `saved_reports.render_saved_reports` —
    identical signature, identical data flow, identical Open Report
    wiring. Only the visual layer changes.
    """
    inject_global_theme()
    render_edge_masthead(user, active_page="saved_reports")
    render_edge_page_wrapper_open()

    st.markdown(_PAGE_CSS, unsafe_allow_html=True)
    st.markdown('<div class="srl-wrap">', unsafe_allow_html=True)

    if not user:
        st.markdown(
            '<div class="srl-pagehead">'
            '<div><div class="srl-eyebrow">Sessions</div>'
            '<h1 class="srl-pagehead-title">Saved Reports</h1></div>'
            '</div>', unsafe_allow_html=True,
        )
        _empty("Please sign in.",
                "Your saved reports are tied to your BarrelLabs account.")
        st.markdown('</div>', unsafe_allow_html=True)
        render_edge_page_wrapper_close()
        return

    player_id = user.get("slug") or user.get("id")
    history = load_swing_history(player_id) or []
    history_sorted = list(reversed(history))  # newest first

    # ---- Header ----
    total = len(history_sorted)
    most_recent = _fmt_short_date(history_sorted[0]) if history_sorted else "—"
    st.markdown(
        f'<div class="srl-pagehead">'
        f'  <div>'
        f'    <div class="srl-eyebrow">Sessions</div>'
        f'    <h1 class="srl-pagehead-title">Saved Reports</h1>'
        f'  </div>'
        f'  <div class="srl-pagehead-meta">'
        f'    On file<strong>{total} {"swing" if total == 1 else "swings"}</strong>'
        f'    <div style="margin-top:14px;">Most recent<strong>{html.escape(most_recent)}</strong></div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not history_sorted:
        _empty("No saved reports yet.",
                "Analyze your first swing on the Upload page — every "
                "analysis will live here once you do.", icon="↗")
        st.markdown('</div>', unsafe_allow_html=True)
        render_edge_page_wrapper_close()
        return

    # ---- Filters ----
    # A real keyed container so the editorial card frame + widget polish
    # actually wrap the controls (a bare markdown <div> collapses into a
    # phantom sibling and the frame/polish never reach the widgets).
    with st.container(key="srl_filter_card"):
        st.markdown('<div class="srl-filter-eyebrow">Search &amp; Filter</div>',
                    unsafe_allow_html=True)
        f1, f2, f3 = st.columns([2.2, 1, 1])
        with f1:
            search_q = st.text_input(
                "Search",
                key="srl_search",
                placeholder="Search by MLB comp, focus, or file name…",
                label_visibility="visible",
            )
        with f2:
            score_filter = st.selectbox(
                "Score range",
                ["All scores", "80+ (Elite)", "60–79 (Strong)", "Below 60 (Building)"],
                key="srl_score_filter",
            )
        with f3:
            time_filter = st.selectbox(
                "Time range",
                ["All time", "Last 7 days", "Last 30 days", "Last 90 days"],
                key="srl_time_filter",
            )

    filtered = _filter_history(history_sorted, search_q, score_filter, time_filter)

    st.markdown(
        f'<div class="srl-results-strip">'
        f'<div>Showing <strong>{len(filtered)}</strong> of <strong>{len(history_sorted)}</strong></div>'
        f'<div>Newest first</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not filtered:
        _empty("No reports match those filters.",
                "Try widening the score range, choosing a longer time window, "
                "or clearing the search box.", icon="≡")
        st.markdown('</div>', unsafe_allow_html=True)
        render_edge_page_wrapper_close()
        return

    pdf_allowed = bool(can_export_pdf(load_my_plan()))

    pending_delete_key = "srl_pending_delete_id"

    # ---- Chronological context for per-card deltas / PB / trend ----
    # `history` from load_swing_history is oldest-first. Build:
    #   * prev_by_id : record-id -> immediately-prior swing's score
    #   * pb_score   : best score across the player's whole history
    #   * trend_pts  : score series (oldest->newest) for the sparkline
    chrono = [r for r in history if _score_of(r) is not None]
    trend_pts = [_score_of(r) for r in chrono]
    pb_score = max(trend_pts) if trend_pts else None
    prev_by_id: Dict[Any, Optional[float]] = {}
    for _i, _r in enumerate(chrono):
        _rid = _r.get("id") or _r.get("timestamp") or f"_c{_i}"
        prev_by_id[_rid] = _score_of(chrono[_i - 1]) if _i > 0 else None

    # MLB-similarity helper (reused from the report renderer). Lazy
    # import keeps the Sessions page from pulling heavy report deps
    # unless this page is actually rendered.
    try:
        from swing_report_dashboard_preview import _radar_sim_pct
    except Exception:
        _radar_sim_pct = lambda _rec: None  # noqa: E731

    # ---- Cards ----
    for idx, rec in enumerate(filtered):
        rec_id = rec.get("id") or rec.get("timestamp") or f"row{idx}"
        swing_num = rec.get("swing_number", "—")
        try:
            num_disp = f"Swing #{int(swing_num):02d}"
        except Exception:
            num_disp = f"Swing #{swing_num}"
        score = rec.get("score")
        try:
            score_int = int(round(float(score)))
            score_disp = str(score_int)
        except (TypeError, ValueError):
            score_int, score_disp = None, "—"
        band = _band_class(score)
        ref = str(rec.get("reference_name") or "—")
        focus = _top_focus(rec)
        date_disp = _fmt_short_date(rec)
        filename = str(rec.get("filename") or "—")
        if len(filename) > 24:
            filename = filename[:21] + "…"

        # Score delta vs the immediately-prior swing (chronological).
        cur_score = _score_of(rec)
        prev_score = prev_by_id.get(
            rec.get("id") or rec.get("timestamp") or f"row{idx}"
        )
        if prev_score is None or cur_score is None:
            delta_html = '<span class="srl-delta new">★ NEW</span>'
        else:
            d = cur_score - prev_score
            if d > 0:
                delta_html = f'<span class="srl-delta up">▲ +{int(round(d))}</span>'
            elif d < 0:
                delta_html = f'<span class="srl-delta down">▼ {int(round(d))}</span>'
            else:
                delta_html = '<span class="srl-delta flat">± 0</span>'

        # Personal Best badge — only when this swing equals the
        # all-time best AND the player has more than one swing.
        is_pb = (
            cur_score is not None and pb_score is not None
            and cur_score >= pb_score and len(trend_pts) > 1
        )
        pb_html = ('<span class="srl-pb">★ Personal Best</span>'
                   if is_pb else "")

        # MLB similarity % (real biomech radar avg; hidden if unknown).
        # BL-004C (4): label + tooltip caveat come from mlb_match.py's
        # single source of truth — same wording as the full swing report
        # and every other page that shows this number. The number itself
        # is unchanged; a dense per-row list has no room for a full
        # footnote, so the caveat lives in the title= tooltip here
        # instead of an inline caption.
        try:
            sim = _radar_sim_pct(rec)
        except Exception:
            sim = None
        if isinstance(sim, (int, float)):
            from mlb_match import MATCH_LABEL, MATCH_CAVEAT_SHORT
            sim_html = (
                f'<span class="srl-mlb-sim" title="{html.escape(MATCH_CAVEAT_SHORT)}">'
                f'{int(sim)}% {html.escape(MATCH_LABEL)}</span>'
            )
        else:
            sim_html = ""

        # Score-trend sparkline for swings up to & including this one.
        try:
            _upto = trend_pts[:trend_pts.index(cur_score) + 1] \
                if cur_score in trend_pts else trend_pts
        except ValueError:
            _upto = trend_pts
        spark_html = _mini_sparkline(_upto[-8:]) if len(_upto) >= 2 else ""

        st.markdown(
            f'<div class="srl-card">'
            f'  <div class="srl-card-score {band}">'
            f'    <div>{html.escape(score_disp)}</div>'
            f'    <div class="srl-card-score-foot">/ 100</div>'
            f'  </div>'
            f'  <div>'
            f'    <div class="srl-col-label">{html.escape(num_disp)}{pb_html}</div>'
            f'    <div class="srl-mlb-line">'
            f'      <span class="srl-col-val">{html.escape(ref)}</span>'
            f'      {sim_html}'
            f'    </div>'
            f'    <div class="srl-col-sub">{html.escape(date_disp)}</div>'
            f'    {delta_html}'
            f'  </div>'
            f'  <div class="srl-card-cell-hide-mobile">'
            f'    <div class="srl-col-label">Top Priority</div>'
            f'    <div class="srl-col-val" style="font-size:1rem;">{html.escape(focus)}</div>'
            f'    {spark_html}'
            f'  </div>'
            f'  <div class="srl-card-file-col srl-card-cell-hide-mobile">'
            f'    <div class="srl-col-label">Source</div>'
            f'    <div class="srl-col-val" style="font-size:1rem;font-family:var(--srl-mono);font-style:normal;letter-spacing:0;">{html.escape(filename)}</div>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Action row (real Streamlit widgets — they have to live outside
        # the card HTML because Streamlit doesn't allow widgets inside
        # raw markdown). Tight 1.8-unit trailing spacer (was 4) so action
        # buttons cluster on the left rather than floating in dead space.
        st.markdown('<div class="srl-actions">', unsafe_allow_html=True)
        a_open, a_dl, a_del, _spacer = st.columns([1.3, 1.5, 1.1, 1.8])
        with a_open:
            if st.button("Open Report →", key=f"srl_open_{rec_id}"):
                st.session_state["view_swing_record"] = rec
                st.session_state["view_swing_report_id"] = rec_id
                rp = rec.get("_record_path")
                if rp:
                    st.session_state["view_swing_path"] = rp
                else:
                    st.session_state.pop("view_swing_path", None)
                st.session_state["page"] = "swing_report"
                st.session_state.pop("view", None)
                st.rerun()
        with a_dl:
            # PDF export is free for everyone now. Generate on tap (not for every
            # card on page load — that rebuilt every PDF on every rerun and was
            # slow). First tap builds it; then it becomes a one-tap download.
            if build_pdf_fn is not None and pdf_allowed:
                _pk = f"_srl_pdfbytes_{rec_id}"
                if st.session_state.get(_pk):
                    st.download_button(
                        "⬇  Save PDF",
                        data=st.session_state[_pk],
                        file_name=f"swing_{rec_id}.pdf",
                        mime="application/pdf",
                        key=f"srl_pdf_{rec_id}",
                    )
                elif st.button("⬇  Download PDF", key=f"srl_pdfgate_{rec_id}"):
                    try:
                        st.session_state[_pk] = build_pdf_fn(rec)
                    except Exception:
                        st.toast("Couldn't build that PDF — try again in a moment.")
                    st.rerun()
        with a_del:
            st.markdown('<div class="srl-actions-ghost">',
                         unsafe_allow_html=True)
            if st.session_state.get(pending_delete_key) == rec_id:
                if st.button("Confirm delete", key=f"srl_del_yes_{rec_id}"):
                    try:
                        target_id = rec.get("id")
                        if target_id:
                            delete_swing_record(target_id)
                        st.session_state.pop(pending_delete_key, None)
                        st.toast("Report deleted.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Delete failed: {e}")
            else:
                if st.button("Delete", key=f"srl_del_{rec_id}"):
                    st.session_state[pending_delete_key] = rec_id
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)  # /.srl-wrap
    render_edge_page_wrapper_close()
