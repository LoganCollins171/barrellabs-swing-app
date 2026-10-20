"""Dashboard-style Premium Swing Report — PREVIEW ONLY.

Why this file exists
--------------------
The earlier Premium Swing Report renderer shipped a self-contained
`bld2-*` visual language that no longer matched the BarrelLabs Edge dashboard.
This module is the renderer that visually matches the Edge design system.

It is intentionally:
  * A pure presentational layer — it reuses data extraction helpers
    from `swing_report` / `swing_metrics` and never touches the
    analyzer, scoring, billing, or auth pipelines.
  * Self-contained CSS under the `srd-*` namespace (swing-report-dashboard)
    so it cannot leak into `bld2-*` or `bl-*` rules.

Public API
----------
    render_swing_report_dashboard_preview(record, history=None, *, is_sample=False)
        Streamlit entry point. Renders the full preview page including the
        "PREVIEW ONLY" banner.

    build_dashboard_preview_html(record, history=None, *, is_sample=False)
        Pure-HTML builder used by `scripts/visual_qa/render_swing_report_static.py`
        for headless screenshotting.

    SAMPLE_RECORD : Dict
        Synthetic record used when no real swing is selected. Mirrors the
        field schema the renderer reads (score, reference, narratives,
        gaps, metric_table, drill_plan, strengths, score_history).
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Tuple

# Reuse data extractors from the existing report stack — we are a NEW
# presentation layer, not a new data model.
from swing_report import (
    _extract_ref_info,
    _initials,
    coach_summary,
    top_three_fixes,
    swing_progress,
    enrich_fixes_with_history,
)
from swing_metrics import (
    _flatten_metric_table,
    _find_metric_row,
    _compute_key_metrics,
)


# =====================================================================
#                          DESIGN-SYSTEM CSS
# =====================================================================
# Tokens mirror mock_dashboard_template.py / bl_edge_chrome.py so the
# preview shares the Edge masthead/dashboard visual language exactly.
# Namespace: .srd-*
# =====================================================================

_DASHBOARD_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Fraunces:ital,wght@0,400;0,500;0,600;1,400&family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap');

/* Design tokens live on :root, NOT on .srd-wrap. The live report is emitted as
   3 separate st.html() calls (top sections, interactive Compare, Next Session);
   a <div class="srd-wrap"> opened in the first chunk can't span the others
   (Streamlit closes it at each chunk boundary). When these vars lived on
   .srd-wrap, the Compare + Next Session chunks rendered OUTSIDE it and every
   var(--srd-*) failed -> those sections lost all color/font and looked broken.
   On :root they cascade to every chunk. */
:root {
  --srd-bg:        #0A0B0E;
  --srd-bg-2:      #0F1115;
  --srd-bone:      #F4EFE6;
  --srd-bone-80:   rgba(244,239,230,0.82);
  --srd-bone-60:   rgba(244,239,230,0.58);
  --srd-bone-40:   rgba(244,239,230,0.36);
  --srd-bone-20:   rgba(244,239,230,0.16);
  --srd-line:      rgba(244,239,230,0.08);
  --srd-line-hi:   rgba(244,239,230,0.16);
  --srd-glass-1:   rgba(255,255,255,0.025);
  --srd-glass-2:   rgba(255,255,255,0.045);
  --srd-red:       #E64530;
  --srd-red-soft:  rgba(230,69,48,0.12);
  --srd-gold:      #E8C170;
  --srd-gold-soft: rgba(232,193,112,0.14);
  --srd-green:     #4AE38C;
  --srd-green-soft:rgba(74,227,140,0.12);
  --srd-gray:      #8B8E94;
  --srd-radius:    14px;
  --srd-radius-lg: 20px;
  --srd-radius-sm: 10px;
  --srd-serif: 'Instrument Serif', 'Fraunces', Georgia, serif;
  --srd-sans:  'Geist', -apple-system, BlinkMacSystemFont, 'Inter', system-ui, sans-serif;
  --srd-mono:  'Geist Mono', 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
}
.srd-wrap {
  background: var(--srd-bg);
  color: var(--srd-bone);
  font-family: var(--srd-sans);
  /* Match the masthead/dashboard content frame exactly (max-width
     1560, 40px side gutter) so text aligns with the nav on every
     page — one cohesive rhythm, never edge-crammed. */
  max-width: 1560px;
  margin: 0 auto;
  padding: 1.8rem 40px 4rem;
  font-feature-settings: "ss01", "ss02", "cv11";
  -webkit-font-smoothing: antialiased;
}
/* Continuation frame for the live split-off chunks (Compare, Next Session):
   same width + side gutter as .srd-wrap so they align with the top, but no
   doubled vertical padding and no opaque background (avoids a seam). The page
   behind the report is already the dark Edge theme. */
.srd-frame {
  max-width: 1560px;
  margin: 0 auto;
  padding: 0 40px;
  color: var(--srd-bone);
  font-family: var(--srd-sans);
  font-feature-settings: "ss01", "ss02", "cv11";
  -webkit-font-smoothing: antialiased;
}

/* PREVIEW banner */
.srd-banner {
  display:flex; align-items:center; gap:14px;
  padding: 10px 16px;
  margin-bottom: 1.4rem;
  background: rgba(232,193,112,0.06);
  border: 1px solid rgba(232,193,112,0.32);
  border-radius: 12px;
  font-family: var(--srd-mono);
  font-size: 11px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--srd-gold);
}
.srd-banner-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--srd-gold); box-shadow: 0 0 12px var(--srd-gold);
}
.srd-banner-text { flex:1; color: var(--srd-bone-80); letter-spacing: 0.12em; }
.srd-banner-tag { color: var(--srd-gold); font-weight:600; }

/* Page title strip */
.srd-pagehead {
  display:flex; align-items:flex-end; justify-content:space-between;
  padding-bottom: 1.4rem;
  margin-bottom: 1.4rem;
  border-bottom: 1px solid var(--srd-line);
  gap: 2rem;
}
.srd-eyebrow {
  font-family: var(--srd-mono);
  font-size: 10.5px;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: var(--srd-red);
  font-weight: 600;
  display:flex; align-items:center; gap:8px;
}
.srd-eyebrow::before {
  content:""; width:6px; height:6px; border-radius:50%;
  background: var(--srd-red); box-shadow: 0 0 8px var(--srd-red);
}
.srd-pagehead-title {
  font-family: var(--srd-sans);
  font-size: 3.4rem;
  font-style: normal;
  line-height: 0.95;
  letter-spacing: -0.02em;
  color: var(--srd-bone);
  margin: 0.6rem 0 0;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-pagehead-meta {
  text-align: right;
  font-family: var(--srd-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-pagehead-meta strong {
  color: var(--srd-bone); font-weight: 500;
  display:block; margin-top: 4px;
  font-family: var(--srd-sans); font-style: normal;
  font-size: 16px; letter-spacing: 0; text-transform: none;
}

/* Card primitive */
.srd-card {
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius-lg);
  padding: 1.4rem 1.5rem;
  position: relative;
  transition: border-color .2s ease, background .2s ease;
}
.srd-card.is-hover:hover {
  border-color: var(--srd-line-hi);
  background: var(--srd-glass-2);
}
.srd-card-eyebrow {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
  margin-bottom: 1rem;
  display:flex; align-items:center; gap:8px;
}
.srd-card-eyebrow .dot {
  width:5px; height:5px; border-radius:50%;
  background: var(--srd-red);
}

/* ========== HERO ROW: SCORE | MLB COMP ========== */
.srd-hero {
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  gap: 1rem;
  margin-bottom: 1.1rem;
}
.srd-hero-card {
  background:
    radial-gradient(ellipse at 100% 0%, rgba(230,69,48,0.07) 0%, transparent 55%),
    var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius-lg);
  padding: 1.6rem 1.7rem;
  position: relative; overflow: hidden;
}
.srd-hero-card.mlb {
  background:
    radial-gradient(ellipse at 0% 0%, rgba(232,193,112,0.06) 0%, transparent 55%),
    var(--srd-glass-1);
}

.srd-hero-grid {
  display:grid;
  grid-template-columns: 1.4fr 0.9fr;
  gap: 1.6rem;
  align-items: center;
}

/* Score side */
.srd-score-num {
  font-family: var(--srd-mono);
  font-size: 6rem;
  line-height: 0.92;
  letter-spacing: -0.04em;
  color: var(--srd-bone);
  font-style: normal;
  font-variant-numeric: tabular-nums;
}
.srd-score-foot {
  font-family: var(--srd-mono);
  font-size: 14px;
  color: var(--srd-bone-60);
  margin-left: 0.4rem;
  letter-spacing: 0.06em;
}
.srd-score-band {
  display:inline-flex; align-items:center; gap:8px;
  margin-top: 0.8rem;
  padding: 5px 12px;
  border-radius: 999px;
  font-family: var(--srd-mono);
  font-size: 10.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  font-weight: 600;
}
.srd-score-band.green { background: var(--srd-green-soft); color: var(--srd-green); border:1px solid rgba(74,227,140,0.25); }
.srd-score-band.amber { background: var(--srd-gold-soft); color: var(--srd-gold);  border:1px solid rgba(232,193,112,0.25); }
.srd-score-band.red   { background: var(--srd-red-soft);  color: var(--srd-red);   border:1px solid rgba(230,69,48,0.28); }
.srd-score-band .dot {
  width:6px; height:6px; border-radius:50%; background: currentColor;
  box-shadow: 0 0 8px currentColor;
}
.srd-score-blurb {
  margin-top: 1.1rem;
  color: var(--srd-bone-80);
  font-size: 14.5px;
  line-height: 1.6;
  max-width: 38ch;
  font-family: var(--srd-sans);
}
.srd-score-blurb strong { color: var(--srd-bone); font-weight: 600; }

.srd-ring-wrap { display:flex; flex-direction:column; align-items:center; gap:12px; }
/* Score gauge hero — the big ring is the centerpiece, band + delta beside it. */
.srd-score-hero {
  display:flex; align-items:center; gap:32px;
  margin-top: 1.1rem; flex-wrap: wrap;
}
.srd-gauge { flex: 0 0 auto; }
/* CSS conic-gradient score gauge (SVG is stripped by st.html). */
.srd-gauge-ring {
  width: 188px; height: 188px; border-radius: 50%;
  position: relative; display: grid; place-items: center;
  box-shadow: 0 0 34px -14px rgba(232,193,112,0.55);
}
.srd-gauge-ring::before {
  content: ""; position: absolute; inset: 13px; border-radius: 50%;
  background: var(--srd-bg);
}
.srd-gauge-ring-in { position: relative; z-index: 1; text-align: center; }
.srd-gauge-num {
  font-family: var(--srd-mono); font-weight: 600; font-size: 3.7rem;
  line-height: 1; color: var(--srd-bone); letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.srd-gauge-cap {
  font-family: var(--srd-mono); font-size: 0.6rem; letter-spacing: 0.22em;
  color: var(--srd-bone-60); margin-top: 7px;
}
.srd-gauge-meta {
  display:flex; flex-direction:column; align-items:flex-start; gap:14px;
  min-width: 140px;
}
@media (max-width: 560px) {
  .srd-score-hero { gap: 20px; justify-content: center; }
  .srd-gauge-meta { align-items: center; }
}
.srd-ring-delta {
  font-family: var(--srd-mono);
  font-size: 12px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  text-align: center;
  padding: 6px 12px;
  border-radius: 999px;
  border:1px solid var(--srd-line);
}
.srd-ring-delta.up   { color: var(--srd-green); border-color: rgba(74,227,140,0.3); }
.srd-ring-delta.down { color: var(--srd-red);   border-color: rgba(230,69,48,0.3); }
.srd-ring-delta.flat { color: var(--srd-bone-60); }
.srd-ring-delta .sub { display:block; margin-top:3px; font-size:9.5px; opacity:.7; }

/* MLB comp side */
.srd-mlb-grid {
  display:grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.4rem;
  align-items: center;
}
.srd-mlb-name {
  font-family: var(--srd-sans);
  font-style: normal;
  font-size: 2.2rem;
  letter-spacing: -0.015em;
  color: var(--srd-bone);
  line-height: 1.05;
  margin-top: 0.2rem;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-mlb-team {
  font-family: var(--srd-mono);
  font-size: 10.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
  margin-top: 0.55rem;
}
.srd-mlb-sim-row {
  display:flex; align-items:baseline; gap:10px;
  margin-top: 1rem;
}
.srd-mlb-sim {
  font-family: var(--srd-mono);
  font-style: normal;
  font-size: 2.4rem;
  color: var(--srd-gold);
  letter-spacing: -0.02em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.srd-mlb-sim-foot {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-mlb-style {
  margin-top: 1rem;
  color: var(--srd-bone-80);
  font-size: 13.5px;
  line-height: 1.55;
  font-style: italic;
  font-family: var(--srd-serif);
  border-left: 2px solid var(--srd-gold);
  padding-left: 12px;
}
.srd-mlb-avatar-row { display:flex; align-items:center; gap:14px; }
.srd-mlb-avatar {
  width: 56px; height: 56px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(232,193,112,0.18), rgba(232,193,112,0.04));
  border: 1px solid rgba(232,193,112,0.3);
  display:flex; align-items:center; justify-content:center;
  font-family: var(--srd-serif); font-style: italic;
  color: var(--srd-gold);
  font-size: 22px;
  flex-shrink:0;
}
.srd-mlb-radar { display:flex; justify-content:center; align-items:flex-start; }
.srd-mlb-radar svg { max-width: 190px; width: 100%; height:auto; }

/* MLB insight blocks (shared traits / biggest gap / why-matters) */
.srd-mlb-insights {
  margin-top: 1.2rem;
  display:flex; flex-direction:column; gap: 0.95rem;
}
.srd-mlb-insight-eyebrow {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
  margin-bottom: 0.45rem;
  display:flex; align-items:center; gap: 7px;
}
.srd-mlb-insight-eyebrow .dot {
  width:5px; height:5px; border-radius:50%;
}
.srd-mlb-insight-eyebrow.gold .dot  { background: var(--srd-gold); }
.srd-mlb-insight-eyebrow.red  .dot  { background: var(--srd-red); }
.srd-mlb-insight-eyebrow.bone .dot  { background: var(--srd-bone-60); }

.srd-mlb-traits {
  display:flex; flex-wrap:wrap; gap: 6px;
}
.srd-mlb-trait {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  padding: 5px 10px;
  border-radius: 999px;
  background: var(--srd-gold-soft);
  color: var(--srd-gold);
  border: 1px solid rgba(232,193,112,0.28);
  font-weight: 600;
}
.srd-mlb-gap {
  background: var(--srd-red-soft);
  border: 1px solid rgba(230,69,48,0.28);
  border-radius: var(--srd-radius-sm);
  padding: 0.7rem 0.9rem;
  color: var(--srd-bone);
  font-size: 13.5px;
  line-height: 1.5;
}
.srd-mlb-why {
  color: var(--srd-bone-80);
  font-size: 13px;
  line-height: 1.55;
  font-family: var(--srd-serif);
  font-style: italic;
  border-left: 2px solid var(--srd-gold);
  padding-left: 12px;
}

/* Section title strip used between bands */
.srd-section {
  display:flex; align-items:baseline; justify-content:space-between;
  margin: 2.2rem 0 1rem;
  padding-bottom: 0.9rem;
  border-bottom: 1px solid var(--srd-line);
}
.srd-section-title {
  font-family: var(--srd-sans);
  font-style: normal;
  font-size: 1.7rem;
  letter-spacing: -0.015em;
  color: var(--srd-bone);
  margin: 0.4rem 0 0;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-section-sub {
  font-family: var(--srd-mono);
  font-size: 10.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}

/* PRIORITIES + DRILLS GRID */
.srd-pd-grid {
  display:grid;
  grid-template-columns: 1.05fr 1fr;
  gap: 1rem;
}
.srd-pri-list, .srd-drill-list {
  display:flex; flex-direction:column; gap: 0.7rem;
}
.srd-pri {
  display:grid;
  grid-template-columns: 44px 1fr;
  gap: 1rem;
  padding: 1rem 1.1rem;
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
  align-items: start;
  transition: border-color .2s ease, background .2s ease;
}
.srd-pri:hover { border-color: var(--srd-line-hi); background: var(--srd-glass-2); }
/* Severity rail + number color so fixes read as ranked priorities. */
.srd-pri.sev-high { border-left: 3px solid var(--srd-red); }
.srd-pri.sev-med  { border-left: 3px solid var(--srd-gold); }
.srd-pri.sev-low  { border-left: 3px solid var(--srd-green); }
.srd-pri.sev-high .srd-pri-num { color: var(--srd-red); }
.srd-pri.sev-med  .srd-pri-num { color: var(--srd-gold); }
.srd-pri.sev-low  .srd-pri-num { color: var(--srd-green); }
.srd-pri-num {
  font-family: var(--srd-mono);
  font-style: normal;
  font-size: 2rem;
  color: var(--srd-red);
  line-height: 1;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.srd-pri-head {
  display:flex; align-items:center; justify-content:space-between;
  gap: 12px; margin-bottom: 0.4rem;
}
.srd-pri-title {
  font-family: var(--srd-sans);
  font-style: normal;
  font-size: 1.2rem;
  color: var(--srd-bone);
  letter-spacing: -0.01em;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-pri-tag {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid;
  font-weight: 600;
  white-space: nowrap;
}
.srd-pri-tag.high { color: var(--srd-red);   border-color: rgba(230,69,48,0.35); background: var(--srd-red-soft); }
.srd-pri-tag.med  { color: var(--srd-gold);  border-color: rgba(232,193,112,0.3); background: var(--srd-gold-soft); }
.srd-pri-tag.low  { color: var(--srd-green); border-color: rgba(74,227,140,0.3); background: var(--srd-green-soft); }
.srd-pri-desc {
  color: var(--srd-bone-80);
  font-size: 13.5px;
  line-height: 1.55;
}

.srd-drill {
  display:grid;
  grid-template-columns: 56px 1fr auto;
  gap: 1rem;
  padding: 0.95rem 1.1rem;
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
  align-items: center;
  transition: border-color .2s ease, background .2s ease;
}
.srd-drill:hover { border-color: var(--srd-line-hi); background: var(--srd-glass-2); }
.srd-drill-thumb {
  width: 56px; height: 56px;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(230,69,48,0.16), rgba(230,69,48,0.03));
  border: 1px solid rgba(230,69,48,0.22);
  display:flex; align-items:center; justify-content:center;
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.18em;
  color: var(--srd-red);
  font-weight: 700;
  line-height: 1.1; text-align: center;
}
.srd-drill-title {
  font-family: var(--srd-sans);
  font-style: normal;
  font-size: 1.1rem;
  color: var(--srd-bone);
  letter-spacing: -0.005em;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-drill-pills {
  display:flex; flex-wrap:wrap; gap: 6px;
  margin-top: 5px;
}
.srd-drill-pill {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--srd-bone-60);
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--srd-line);
  padding: 3px 9px;
  border-radius: 999px;
  text-transform: uppercase;
}
.srd-drill-check {
  width: 28px; height: 28px;
  border: 1px solid var(--srd-line-hi);
  border-radius: 50%;
  display:flex; align-items:center; justify-content:center;
  color: var(--srd-bone-40);
  font-size: 14px;
}

/* KEY METRICS STRIP */
.srd-km {
  display:grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.85rem;
}
.srd-km-tile {
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
  padding: 1.05rem 1.1rem;
  transition: border-color .2s ease;
}
.srd-km-tile:hover { border-color: var(--srd-line-hi); }
.srd-km-label {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-km-val-row { display:flex; align-items:baseline; gap:6px; margin-top: 0.7rem; }
.srd-km-val {
  font-family: var(--srd-mono);
  font-style: normal;
  font-size: 1.9rem;
  color: var(--srd-bone);
  letter-spacing: -0.015em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.srd-km-unit {
  font-family: var(--srd-mono);
  font-size: 11px;
  color: var(--srd-bone-60);
  letter-spacing: 0.06em;
}
.srd-km-delta {
  margin-left: auto;
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  padding: 3px 7px;
  border-radius: 999px;
  border: 1px solid var(--srd-line);
}
.srd-km-delta.up   { color: var(--srd-green); border-color: rgba(74,227,140,0.3); }
.srd-km-delta.down { color: var(--srd-red);   border-color: rgba(230,69,48,0.3); }
.srd-km-delta.flat { color: var(--srd-bone-60); }
.srd-km-spark { margin-top: 0.7rem; }
.srd-km-spark svg { width: 100%; height: 28px; }

/* Match gauge inside each key-metric tile — % closeness to the pro. */
.srd-km-gauge {
  margin-top: 0.9rem; height: 6px; border-radius: 999px;
  background: rgba(244,239,230,0.08); overflow: hidden;
}
.srd-km-gauge > span {
  display: block; height: 100%; border-radius: 999px;
  background: var(--srd-gold);
  box-shadow: 0 0 10px -2px var(--srd-gold);
}
.srd-km-gauge.good > span { background: var(--srd-green); box-shadow: 0 0 10px -2px var(--srd-green); }
.srd-km-gauge.mid  > span { background: var(--srd-gold);  box-shadow: 0 0 10px -2px var(--srd-gold); }
.srd-km-gauge.low  > span { background: var(--srd-red);   box-shadow: 0 0 10px -2px var(--srd-red); }
.srd-km-gauge-cap {
  margin-top: 0.45rem; font-family: var(--srd-mono);
  font-size: 9.5px; letter-spacing: 0.12em; text-transform: uppercase;
  color: var(--srd-bone-60);
}

/* BREAKDOWN TABLE */
.srd-br-table { width:100%; border-collapse: collapse; margin-top: 0.5rem; }
.srd-br-table th {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
  text-align: left;
  font-weight: 600;
  padding: 10px 12px;
  border-bottom: 1px solid var(--srd-line-hi);
}
.srd-br-table th + th, .srd-br-table td + td { text-align: right; }
.srd-br-table td {
  font-family: var(--srd-sans);
  font-size: 13.5px;
  color: var(--srd-bone-80);
  padding: 11px 12px;
  border-bottom: 1px solid var(--srd-line);
}
.srd-br-table td:first-child { color: var(--srd-bone); font-weight: 500; }
.srd-br-table tbody tr:last-child td { border-bottom: 0; }
.srd-br-status {
  display:inline-flex; align-items:center; justify-content:center;
  width: 22px; height: 22px; border-radius: 50%;
  font-family: var(--srd-mono); font-size: 11px; font-weight: 600;
}
.srd-br-status.ok   { background: var(--srd-green-soft); color: var(--srd-green); }
.srd-br-status.warn { background: var(--srd-gold-soft);  color: var(--srd-gold); }
.srd-br-status.bad  { background: var(--srd-red-soft);   color: var(--srd-red); }

/* PROGRESS / VS PREVIOUS */
.srd-prog-grid {
  display:grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 1rem;
}
.srd-prog-spark { height: 110px; margin-top: 0.8rem; }
.srd-prog-spark svg { width: 100%; height: 100%; }
.srd-prog-stats {
  display:grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.7rem;
  margin-top: 0.4rem;
}
.srd-prog-stat {
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
  padding: 0.85rem 1rem;
}
.srd-prog-stat-label {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-prog-stat-val {
  font-family: var(--srd-mono);
  font-style: normal;
  font-size: 1.5rem;
  color: var(--srd-bone);
  margin-top: 0.4rem;
  letter-spacing: -0.01em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.srd-prog-stat-sub {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  color: var(--srd-bone-60);
  margin-top: 0.4rem;
}

/* NEXT SESSION */
.srd-next-list {
  list-style: none; padding: 0; margin: 0.4rem 0 0;
  display:flex; flex-direction:column; gap: 0.7rem;
}
.srd-next-item {
  display:grid;
  grid-template-columns: 28px 1fr;
  gap: 12px;
  padding: 0.9rem 1rem;
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
}
.srd-next-num {
  width: 26px; height: 26px;
  border-radius: 50%;
  background: var(--srd-red-soft);
  color: var(--srd-red);
  font-family: var(--srd-mono);
  font-size: 11px;
  font-weight: 700;
  display:flex; align-items:center; justify-content:center;
}
.srd-next-title {
  font-family: var(--srd-sans);
  font-style: normal;
  font-size: 1.1rem;
  color: var(--srd-bone);
  letter-spacing: -0.005em;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-next-sub {
  color: var(--srd-bone-80);
  font-size: 13px;
  margin-top: 4px;
  line-height: 1.55;
}

/* STRENGTHS chip row */
.srd-chips {
  display:flex; flex-wrap:wrap; gap: 6px;
  margin-top: 1rem;
}
.srd-chip {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  padding: 5px 10px;
  border-radius: 999px;
  background: var(--srd-green-soft);
  color: var(--srd-green);
  border: 1px solid rgba(74,227,140,0.22);
  font-weight: 600;
}

/* COMPARE THIS SWING */
.srd-cmp-selector {
  margin-bottom: 1rem;
  display:grid;
  grid-template-columns: 140px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 0.85rem 1rem;
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
}
.srd-cmp-selector-label {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-cmp-selector-display {
  display:flex; align-items:center; justify-content:space-between;
  gap: 12px;
  padding: 0.55rem 0.85rem;
  background: var(--srd-bg-2);
  border: 1px solid var(--srd-line-hi);
  border-radius: var(--srd-radius-sm);
  color: var(--srd-bone);
  font-family: var(--srd-sans);
  font-size: 13px;
}
.srd-cmp-selector-caret { color: var(--srd-bone-60); font-size: 10px; }
.srd-cmp-selector-hint {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.14em;
  color: var(--srd-bone-40);
  text-transform: uppercase;
}

.srd-cmp-summary {
  margin-bottom: 1rem;
  background:
    radial-gradient(ellipse at 0% 0%, rgba(74,227,140,0.05) 0%, transparent 55%),
    var(--srd-glass-1);
}
.srd-cmp-summary-row {
  display:flex; align-items:center; gap: 1.4rem;
}
.srd-cmp-summary-badge {
  flex-shrink:0;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  width: 104px; padding: 1rem 0.6rem;
  border-radius: var(--srd-radius);
  border: 1px solid var(--srd-line-hi);
  background: var(--srd-bg-2);
  text-align:center;
}
.srd-cmp-summary-badge .big {
  font-family: var(--srd-mono); font-style: normal;
  font-size: 2.4rem; line-height: 1;
  font-variant-numeric: tabular-nums;
}
.srd-cmp-summary-badge.up .big   { color: var(--srd-green); }
.srd-cmp-summary-badge.down .big { color: var(--srd-red); }
.srd-cmp-summary-badge .of {
  font-family: var(--srd-mono); font-size: 11px;
  color: var(--srd-bone-60); margin-top: 4px;
}
.srd-cmp-summary-badge .lbl {
  font-family: var(--srd-mono); font-size: 9px;
  letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--srd-bone-60); margin-top: 6px;
}
.srd-cmp-summary-text {
  color: var(--srd-bone-80);
  font-size: 14.5px; line-height: 1.65;
}
.srd-cmp-summary-text strong { color: var(--srd-bone); font-weight: 600; }

.srd-cmp-pair {
  display:grid;
  grid-template-columns: 1fr 110px 1fr;
  gap: 0.8rem;
  align-items: stretch;
  margin-bottom: 1rem;
}
.srd-cmp-card {
  background: var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius-lg);
  padding: 1.2rem 1.3rem;
}
.srd-cmp-role {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
  margin-bottom: 0.85rem;
  display:flex; align-items:center; gap: 7px;
}
.srd-cmp-role .dot { width:5px; height:5px; border-radius:50%; }
.srd-cmp-head { display:flex; align-items:center; gap: 14px; }
.srd-cmp-avatar {
  width: 56px; height: 56px; border-radius: 14px;
  display:flex; align-items:center; justify-content:center;
  font-family: var(--srd-sans); font-style: normal;
  font-size: 1.5rem;
  flex-shrink:0;
}
.srd-cmp-avatar-green { background: var(--srd-green-soft); color: var(--srd-green); border: 1px solid rgba(74,227,140,0.25); }
.srd-cmp-avatar-amber { background: var(--srd-gold-soft);  color: var(--srd-gold);  border: 1px solid rgba(232,193,112,0.25); }
.srd-cmp-avatar-red   { background: var(--srd-red-soft);   color: var(--srd-red);   border: 1px solid rgba(230,69,48,0.28); }
.srd-cmp-swing {
  font-family: var(--srd-sans); font-style: normal;
  font-size: 1.25rem; color: var(--srd-bone);
  letter-spacing: -0.005em; line-height: 1.1;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-cmp-meta {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
  margin-top: 4px;
}
.srd-cmp-mlb {
  margin-top: 5px;
  font-size: 12.5px;
  color: var(--srd-bone-80);
}

.srd-cmp-delta-badge {
  align-self: center;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  text-align:center;
  background: var(--srd-bg-2);
  border: 1px solid var(--srd-line-hi);
  border-radius: 999px;
  padding: 1rem 0.5rem;
  min-height: 110px;
}
.srd-cmp-delta-label {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-cmp-delta-val {
  font-family: var(--srd-mono); font-style: normal;
  font-size: 2rem; margin-top: 6px; letter-spacing: -0.02em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.srd-cmp-delta-val.up   { color: var(--srd-green); }
.srd-cmp-delta-val.down { color: var(--srd-red); }
.srd-cmp-delta-val.flat { color: var(--srd-bone-60); }

.srd-cmp-metrics-card { padding: 1.2rem 1.4rem 1rem; }
.srd-cmp-table {
  width:100%; border-collapse: collapse; margin-top: 0.4rem;
}
.srd-cmp-table th {
  font-family: var(--srd-mono); font-size: 9.5px; letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--srd-bone-60);
  text-align: left; font-weight: 600;
  padding: 10px 12px;
  border-bottom: 1px solid var(--srd-line-hi);
}
.srd-cmp-table th.num, .srd-cmp-table td.num { text-align: right; }
.srd-cmp-table th + th, .srd-cmp-table td + td { text-align: right; }
.srd-cmp-table td {
  font-family: var(--srd-sans); font-size: 13.5px;
  color: var(--srd-bone-80); padding: 10px 12px;
  border-bottom: 1px solid var(--srd-line);
}
.srd-cmp-table td:first-child { color: var(--srd-bone); font-weight: 500; }
.srd-cmp-table tbody tr:last-child td { border-bottom: 0; }
.srd-cmp-delta {
  font-family: var(--srd-mono); font-size: 11.5px;
  letter-spacing: 0.06em; padding: 3px 9px;
  border-radius: 999px; border: 1px solid var(--srd-line);
  display:inline-block; min-width: 48px; text-align: center;
  font-weight: 600;
}
.srd-cmp-delta.up   { color: var(--srd-green); border-color: rgba(74,227,140,0.3); background: var(--srd-green-soft); }
.srd-cmp-delta.down { color: var(--srd-red);   border-color: rgba(230,69,48,0.3); background: var(--srd-red-soft); }
.srd-cmp-delta.flat { color: var(--srd-bone-60); }
.srd-cmp-footnote {
  margin-top: 0.7rem;
  font-family: var(--srd-mono); font-size: 9.5px;
  letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--srd-bone-40);
}

.srd-cmp-empty {
  padding: 1.6rem 1.7rem;
  background:
    radial-gradient(ellipse at 50% 0%, rgba(232,193,112,0.05) 0%, transparent 55%),
    var(--srd-glass-1);
}
.srd-cmp-empty-msg {
  font-family: var(--srd-serif); font-style: italic;
  font-size: 1.4rem; color: var(--srd-bone);
  margin: 0.4rem 0 0.5rem;
}
.srd-cmp-empty-sub {
  color: var(--srd-bone-60); font-size: 13.5px; line-height: 1.55;
}

/* GRID GAPS */
.srd-stack { display:flex; flex-direction:column; gap: 1rem; }

/* RESPONSIVE */
@media (max-width: 1100px) {
  /* Track the masthead's responsive gutter so text stays aligned. */
  .srd-wrap { padding: 1.4rem 22px 3.5rem; }
}
@media (max-width: 960px) {
  .srd-hero, .srd-pd-grid, .srd-prog-grid { grid-template-columns: 1fr; }
  .srd-hero-grid, .srd-mlb-grid { grid-template-columns: 1fr; gap: 1.2rem; }
  .srd-km { grid-template-columns: repeat(2, 1fr); }
  .srd-cmp-pair { grid-template-columns: 1fr; }
  .srd-cmp-delta-badge {
    min-height: 0; padding: 0.7rem 1rem; flex-direction: row; gap: 14px;
    border-radius: var(--srd-radius);
  }
  .srd-cmp-selector { grid-template-columns: 1fr; gap: 6px; }
  .srd-cmp-summary-row { flex-direction: column; align-items: flex-start; gap: 1rem; }
  .srd-cmp-summary-badge { flex-direction: row; gap: 10px; width: auto; padding: 0.6rem 1rem; }
}
@media (max-width: 560px) {
  .srd-wrap { padding: 1rem 16px 3rem; }
  .srd-pagehead { flex-direction: column; align-items: flex-start; gap: 0.8rem; }
  .srd-pagehead-title { font-size: 2.4rem; }
  .srd-pagehead-meta { text-align: left; }
  .srd-score-num { font-size: 4.6rem; }
  .srd-mlb-name { font-size: 1.7rem; }
  .srd-km { grid-template-columns: 1fr 1fr; }
  .srd-pri { grid-template-columns: 36px 1fr; gap: 0.7rem; padding: 0.85rem 0.9rem; }
  .srd-drill { grid-template-columns: 44px 1fr; gap: 0.8rem; }
  .srd-drill-check { display: none; }
}

/* ───── Power Sequence section (new) ───── */
.srd-power-section {
    margin: 32px 0 40px 0;
    padding: 28px 32px 32px 32px;
    border: 1px solid var(--srd-line);
    border-radius: 16px;
    background:
      radial-gradient(120% 60% at 50% 0%, rgba(232,193,112,0.06), transparent 70%),
      var(--srd-glass-1);
}
.srd-power-eyebrow {
    font-family: var(--srd-mono); font-size: 11px; font-weight: 600;
    letter-spacing: 0.22em; text-transform: uppercase;
    color: var(--srd-gold);
    margin: 0 0 8px 0;
}
.srd-power-title {
    font-family: var(--srd-sans); font-size: 2.4rem;
    line-height: 1.05; letter-spacing: -0.018em;
    color: var(--srd-bone); font-weight: 400; margin: 0 0 8px 0;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-power-title .ital { font-style: italic; color: var(--srd-gold); }
.srd-power-verdict {
    font-family: var(--srd-sans); font-size: 1.05rem;
    line-height: 1.5; color: var(--srd-bone-60); max-width: 60ch;
    margin: 0 0 22px 0;
}
.srd-power-tiles {
    display: grid; grid-template-columns: 1fr 1fr 1fr;
    gap: 16px; margin-top: 20px;
}
@media (max-width: 760px) {
    .srd-power-tiles { grid-template-columns: 1fr; }
}
/* Two tiles fill the row evenly instead of hugging the left 1/3. Scoped to
   desktop so the mobile 1-col rule above still wins on narrow screens. */
@media (min-width: 761px) {
    .srd-power-tiles--two { grid-template-columns: 1fr 1fr; }
}
.srd-power-tile {
    border: 1px solid var(--srd-line);
    border-radius: 12px;
    padding: 18px 22px;
    background: rgba(244,239,230,0.025);
}
.srd-power-tile.good   { border-color: rgba(232,193,112,0.42); }
.srd-power-tile.marginal { border-color: var(--srd-line-hi); }
.srd-power-tile.poor   { border-color: rgba(230,69,48,0.45); }
.srd-power-tile-label {
    font-family: var(--srd-mono); font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.20em; text-transform: uppercase;
    color: var(--srd-bone-60); margin-bottom: 6px;
}
.srd-power-tile.good     .srd-power-tile-label { color: var(--srd-gold); }
.srd-power-tile.poor     .srd-power-tile-label { color: var(--srd-red); }
.srd-power-tile-value {
    font-family: var(--srd-mono); font-style: normal;
    font-size: 2.2rem; line-height: 1; letter-spacing: -0.02em;
    color: var(--srd-bone); margin: 4px 0 8px 0;
  font-variant-numeric: tabular-nums;
}
.srd-power-tile-unit {
    font-family: var(--srd-mono); font-size: 11px; font-weight: 500;
    color: var(--srd-bone-60); letter-spacing: 0.12em;
    text-transform: lowercase; margin-left: 4px;
}
.srd-power-tile-coach {
    font-family: var(--srd-sans); font-size: 0.92rem;
    line-height: 1.45; color: var(--srd-bone-60); max-width: 32ch;
}

/* ───── Two-System Layout: Match Reveal + Reconciliation + Score Card ───── */

/* Match Reveal Hero (full-width, top of report) */
.srd-match-reveal {
  background:
    radial-gradient(ellipse at 30% 0%, rgba(232,193,112,0.10) 0%, transparent 60%),
    var(--srd-glass-1);
  border: 1px solid rgba(232,193,112,0.20);
  border-radius: var(--srd-radius-lg);
  padding: 2rem 2.2rem;
  margin-bottom: 1rem;
  position: relative;
  overflow: hidden;
}
.srd-match-eyebrow {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.26em;
  text-transform: uppercase;
  color: var(--srd-gold);
  margin-bottom: 1.2rem;
  display: flex;
  align-items: center;
  gap: 8px;
}
.srd-match-eyebrow::before {
  content: "";
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--srd-gold);
  box-shadow: 0 0 10px var(--srd-gold);
}
.srd-match-body {
  display: flex;
  align-items: flex-start;
  gap: 2rem;
}
.srd-match-avatar {
  width: 96px; height: 96px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(232,193,112,0.30), rgba(232,193,112,0.04));
  border: 2px solid rgba(232,193,112,0.55);
  box-shadow: 0 0 30px -6px rgba(232,193,112,0.45),
              inset 0 1px 0 rgba(255,255,255,0.14);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--srd-sans);
  font-style: normal; font-weight: 700;
  color: var(--srd-gold);
  font-size: 34px; letter-spacing: 0.02em;
  flex-shrink: 0;
}
.srd-match-info { flex: 1; }
.srd-match-name {
  font-family: var(--srd-sans);
  font-style: normal;
  font-size: 2.8rem;
  letter-spacing: -0.02em;
  color: var(--srd-bone);
  line-height: 1;
  margin-bottom: 0.5rem;
  text-transform: uppercase; letter-spacing: 0.005em; font-weight: 700;
}
.srd-match-pct-row {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
  margin-top: 0.8rem;
}
.srd-match-pct {
  font-family: var(--srd-mono);
  font-style: normal;
  font-size: 2.2rem;
  color: var(--srd-gold);
  letter-spacing: -0.02em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.srd-match-pct-label {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.20em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-match-nudge {
  margin-top: 0.9rem;
  color: var(--srd-bone-60);
  font-family: var(--srd-mono);
  font-size: 11px;
  letter-spacing: 0.10em;
  font-style: italic;
}
/* BL-004C (4): same treatment as .srd-match-nudge — small, muted, italic
   caption — so the honesty caveat reads as a normal footnote, not an
   alarming warning. */
.srd-match-pct-caveat {
  margin-top: 0.3rem;
  color: var(--srd-bone-60);
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.06em;
  font-style: italic;
}

/* Reconciliation line */
.srd-reconcile {
  margin: 1rem 0;
  padding: 0.9rem 1.2rem;
  background: rgba(244,239,230,0.03);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius);
  text-align: center;
  font-family: var(--srd-serif);
  font-style: italic;
  font-size: 14px;
  color: var(--srd-bone-60);
  line-height: 1.6;
}
.srd-reconcile strong { color: var(--srd-bone-80); font-weight: normal; }

/* Pillar mini-bars */
.srd-pillars { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 1rem; }
.srd-pillar-row {
  display: grid;
  grid-template-columns: 80px 1fr 80px;
  align-items: center;
  gap: 0.7rem;
}
.srd-pillar-label {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--srd-bone-60);
}
.srd-pillar-track {
  height: 5px;
  border-radius: 999px;
  background: rgba(244,239,230,0.08);
  overflow: hidden;
}
.srd-pillar-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--srd-gold);
  transition: width 0.4s ease;
}
.srd-pillar-fill.green { background: var(--srd-green); }
.srd-pillar-fill.red   { background: var(--srd-red); }
.srd-pillar-right {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}

/* Confidence badge */
.srd-conf-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--srd-mono);
  font-size: 9px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  padding: 2px 7px;
  border-radius: 999px;
  font-weight: 600;
}
.srd-conf-badge::before {
  content: ""; width: 5px; height: 5px; border-radius: 50%;
  background: currentColor;
}
.srd-conf-badge.green { color: var(--srd-green); background: var(--srd-green-soft); border: 1px solid rgba(74,227,140,0.25); }
.srd-conf-badge.amber { color: var(--srd-gold);  background: var(--srd-gold-soft);  border: 1px solid rgba(232,193,112,0.25); }
.srd-conf-badge.red   { color: var(--srd-red);   background: var(--srd-red-soft);   border: 1px solid rgba(230,69,48,0.28); }

/* What you did well */
.srd-did-well {
  margin-top: 1rem;
  padding: 0.9rem 1.1rem;
  background: var(--srd-green-soft);
  border: 1px solid rgba(74,227,140,0.22);
  border-radius: var(--srd-radius);
  color: var(--srd-bone-80);
  font-size: 14px;
  line-height: 1.6;
}
.srd-did-well-label {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.20em;
  text-transform: uppercase;
  color: var(--srd-green);
  margin-bottom: 0.4rem;
  font-weight: 600;
}

/* Filming guide */
.srd-filming-guide {
  margin-top: 1rem;
  padding: 0.85rem 1.1rem;
  background: rgba(232,193,112,0.06);
  border: 1px solid rgba(232,193,112,0.22);
  border-radius: var(--srd-radius);
}
.srd-filming-label {
  font-family: var(--srd-mono);
  font-size: 9.5px;
  letter-spacing: 0.20em;
  text-transform: uppercase;
  color: var(--srd-gold);
  margin-bottom: 0.4rem;
  font-weight: 600;
}
.srd-filming-text {
  color: var(--srd-bone-60);
  font-size: 13px;
  line-height: 1.6;
}

/* Age nudge caption */
.srd-age-nudge { margin-top:8px; font-size:12px; line-height:1.4;
  color:rgba(244,239,230,0.62); font-style:italic; }

/* New score card layout for two-system */
.srd-score-card-two {
  background:
    radial-gradient(ellipse at 100% 0%, rgba(230,69,48,0.06) 0%, transparent 55%),
    var(--srd-glass-1);
  border: 1px solid var(--srd-line);
  border-radius: var(--srd-radius-lg);
  padding: 1.6rem 1.7rem;
  margin-bottom: 1rem;
}
.srd-score-card-top {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 1.6rem;
  align-items: start;
}

/* Pillar re-film warning */
.srd-pillar-warn {
  font-family: var(--srd-mono);
  font-size: 10px;
  letter-spacing: 0.10em;
  color: var(--srd-red);
  font-style: italic;
  margin-top: 0.2rem;
}

/* =====================================================================
   REDESIGN v3 — dense dashboard-sibling hero + new sections.
   Namespace: .srd2-*  (additive; old .srd-* kept for Compare/etc.)
   ===================================================================== */

/* ---- Issue line under the masthead ---- */
.srd2-issue {
  display:flex; justify-content:space-between; align-items:center;
  gap:1.5rem; padding: 0.9rem 0 1.6rem;
  font-family: var(--srd-mono); font-size: 10.5px;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--srd-gray);
}
.srd2-issue .mid { color: var(--srd-bone-80); }
.srd2-issue .right { color: var(--srd-gray); }

/* ---- HERO 3-COLUMN BAND (mirrors dashboard .hero) ---- */
.srd2-hero {
  display:grid; grid-template-columns: 0.92fr 1.18fr 0.92fr; gap: 2.4rem;
  padding: 1.6rem 0 2.6rem;
  border-bottom: 1px solid var(--srd-line);
  align-items:center;
}
/* LEFT — score gauge + category rail */
.srd2-gauge-wrap { display:flex; flex-direction:column; align-items:center; }
.srd2-gauge-svg { filter: drop-shadow(0 0 24px rgba(232,193,112,0.06)); max-width:100%; }
.srd2-gauge-num {
  position:absolute; left:50%; top:50%; transform:translate(-50%,-52%);
  text-align:center; pointer-events:none;
}
.srd2-gauge-num .v {
  font-family: var(--srd-mono); font-weight:500; font-size: 3.9rem; line-height:1;
  color: var(--srd-bone); letter-spacing:-0.02em; font-variant-numeric: tabular-nums;
}
.srd2-gauge-num .out {
  font-family: var(--srd-mono); font-size: 9.5px; letter-spacing:0.22em;
  text-transform:uppercase; color: var(--srd-gray); margin-top:6px;
}
.srd2-gauge-num .delta {
  font-family: var(--srd-mono); font-size:10px; letter-spacing:0.1em; margin-top:7px;
}
.srd2-gauge-num .delta.up { color: var(--srd-green); }
.srd2-gauge-num .delta.down { color: var(--srd-red); }
.srd2-gauge-num .delta.flat { color: var(--srd-gray); }
.srd2-gauge-label {
  margin-top: 14px; font-family: var(--srd-mono); font-size: 10px;
  letter-spacing: 0.18em; text-transform:uppercase; color: var(--srd-gray);
  text-align:center;
}
.srd2-gauge-label .band { color: var(--srd-gold); }
.srd2-cats {
  margin-top: 22px; width:100%;
  display:grid; grid-template-columns: 1fr 1fr; gap: 11px 22px;
  font-family: var(--srd-mono); font-size: 10.5px;
  letter-spacing: 0.05em; color: var(--srd-gray); text-transform: uppercase;
}
.srd2-cat { display:flex; justify-content:space-between; gap:12px; }
.srd2-cat .v { color: var(--srd-bone); font-weight:500; }
.srd2-cat .v.peak { color: var(--srd-gold); }
.srd2-cat .v.low { color: var(--srd-red); }

/* CENTER — headline + deck + meta */
.srd2-hero-eyebrow {
  display:inline-flex; align-items:center; gap:9px;
  font-family: var(--srd-mono); font-size: 10.5px;
  letter-spacing: 0.18em; text-transform:uppercase; color: var(--srd-red);
  margin-bottom: 22px;
}
.srd2-hero-eyebrow .swatch { display:inline-block; width:22px; height:1px; background: var(--srd-red); }
.srd2-headline {
  font-family: var(--srd-sans); font-weight:600;
  font-size: 3.6rem; line-height:1.05; letter-spacing:-0.03em;
  color: var(--srd-bone); margin: 0 0 22px; text-transform: uppercase;
}
.srd2-headline .gold { color: var(--srd-gold); display:inline-block; padding:0 0.04em; }
.srd2-headline .red  { color: var(--srd-red);  display:inline-block; }
.srd2-deck {
  font-family: var(--srd-sans); font-weight:300; font-size: 15.5px;
  line-height:1.6; color: var(--srd-bone-80); max-width: 520px; margin: 0 0 26px;
}
.srd2-hero-meta {
  display:flex; gap: 2rem; padding-top: 20px; flex-wrap:wrap;
  border-top: 1px solid var(--srd-line);
}
.srd2-meta-block { display:flex; flex-direction:column; gap:6px; }
.srd2-meta-label {
  font-family: var(--srd-mono); font-size: 9.5px; letter-spacing:0.14em;
  text-transform:uppercase; color: var(--srd-gray);
}
.srd2-meta-value {
  font-family: var(--srd-sans); font-size: 13px; font-weight:500; color: var(--srd-bone);
}

/* RIGHT — Match card (mirrors .tier-card) */
.srd2-mc {
  border:1px solid var(--srd-line); border-radius: var(--srd-radius);
  padding: 24px 26px 22px;
  background:
    radial-gradient(140% 90% at 100% 0%, rgba(232,193,112,0.10), transparent 60%),
    radial-gradient(140% 90% at 0% 100%, rgba(230,69,48,0.06), transparent 60%),
    var(--srd-glass-1);
  position:relative; overflow:hidden;
}
.srd2-mc::after {
  content:""; position:absolute; left:0; right:0; top:-1px; height:2px;
  background: linear-gradient(90deg, transparent, var(--srd-gold), transparent);
}
.srd2-mc-eyebrow {
  display:flex; justify-content:space-between; align-items:center;
  font-family: var(--srd-mono); font-size: 10px;
  letter-spacing:0.16em; text-transform:uppercase; color: var(--srd-gray);
}
.srd2-mc-badge {
  display:inline-flex; align-items:center; gap:6px;
  padding: 3px 9px; border-radius: 100px;
  border:1px solid rgba(232,193,112,0.32); background: rgba(232,193,112,0.08);
  color: var(--srd-gold); font-weight:500;
}
.srd2-mc-badge .dot { width:5px; height:5px; border-radius:50%; background: var(--srd-gold); }
.srd2-mc-id { display:flex; align-items:center; gap:14px; margin-top:16px; }
.srd2-mc-avatar {
  width:48px; height:48px; border-radius:50%; flex:0 0 auto;
  display:grid; place-items:center;
  background: radial-gradient(circle at 30% 30%, rgba(232,193,112,0.22), rgba(232,193,112,0.05));
  border:1px solid rgba(232,193,112,0.5); color: var(--srd-gold);
  font-family: var(--srd-mono); font-weight:600; font-size:16px; letter-spacing:0.04em;
  box-shadow: 0 0 18px -6px rgba(232,193,112,0.6);
}
.srd2-mc-name {
  font-family: var(--srd-sans); font-weight:700; font-size: 1.7rem; line-height:1;
  letter-spacing:-0.01em; text-transform:uppercase; color: var(--srd-bone);
}
.srd2-mc-team {
  font-family: var(--srd-mono); font-size: 10px; letter-spacing:0.1em;
  text-transform:uppercase; color: var(--srd-gray); margin-top:5px;
}
.srd2-mc-track { margin-top: 24px; position:relative; }
.srd2-mc-segs { display:grid; grid-template-columns: repeat(4,1fr); gap:4px; }
.srd2-mc-seg { height:6px; border-radius:3px; background: rgba(244,239,230,0.10); position:relative; overflow:hidden; }
.srd2-mc-seg.on::after {
  content:""; position:absolute; inset:0;
  background: linear-gradient(90deg, var(--srd-gold), rgba(232,193,112,0.65));
  border-radius:3px;
}
.srd2-mc-seg.cur::after {
  content:""; position:absolute; left:0; top:0; bottom:0; width: var(--fill,60%);
  background: linear-gradient(90deg, #C9A350, var(--srd-gold)); border-radius:3px;
}
.srd2-mc-marker {
  position:absolute; top:-7px; width:18px; height:18px; border-radius:50%;
  background: var(--srd-gold); border:3px solid var(--srd-bg);
  box-shadow: 0 0 0 1px rgba(232,193,112,0.55), 0 0 20px rgba(232,193,112,0.6);
  transform: translateX(-50%);
}
.srd2-mc-labels {
  display:grid; grid-template-columns: repeat(4,1fr); gap:4px; margin-top:13px;
  font-family: var(--srd-mono); font-size: 9px; letter-spacing:0.12em; text-transform:uppercase;
}
.srd2-mc-labels span { color: var(--srd-gray); text-align:center; }
.srd2-mc-labels span.now { color: var(--srd-gold); }
.srd2-mc-foot {
  margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--srd-line);
  display:flex; justify-content:space-between; align-items:baseline;
  font-family: var(--srd-mono); font-size: 10.5px; letter-spacing:0.06em;
}
.srd2-mc-foot .lab { color: var(--srd-gray); text-transform:uppercase; }
.srd2-mc-foot .next { font-family: var(--srd-sans); font-size: 12.5px; letter-spacing:0; color: var(--srd-bone); }
.srd2-mc-foot .next .gold { color: var(--srd-gold); font-weight:600; }

/* ---- RADAR SECTION (mirrors .comp-radar-card) ---- */
.srd2-radar-card {
  display:grid; grid-template-columns: 1fr 1.05fr; gap: 3.2rem; align-items:center;
  padding: 2.6rem 2.4rem; margin: 0.5rem 0;
  border:1px solid var(--srd-line); border-radius: var(--srd-radius-lg);
  background:
    radial-gradient(60% 80% at 0% 50%, rgba(232,193,112,0.06), transparent 70%),
    linear-gradient(135deg, #14171d 0%, #0a0b0e 100%);
}
.srd2-radar-vis { display:grid; place-items:center; }
.srd2-radar-vis svg { width:100%; max-width: 420px; height:auto; }
.srd2-radar-narr { display:flex; flex-direction:column; gap: 18px; }
.srd2-radar-line {
  font-family: var(--srd-serif); font-size: 1.9rem; line-height:1.3;
  color: var(--srd-bone); margin:0; max-width:460px; font-weight:400;
}
.srd2-radar-line .em { font-style:italic; color: var(--srd-gold); }
.srd2-radar-deltas {
  font-family: var(--srd-mono); font-size: 11px; letter-spacing:0.14em;
  text-transform:uppercase; color: var(--srd-bone-80); margin:0; line-height:1.7;
}
.srd2-radar-legend {
  display:flex; gap:20px; font-family: var(--srd-mono);
  font-size:10px; letter-spacing:0.1em; text-transform:uppercase; color: var(--srd-gray);
}
.srd2-radar-legend .row { display:flex; align-items:center; gap:9px; }
.srd2-radar-legend .sw.you  { width:14px; height:3px; background: var(--srd-gold); border-radius:2px; }
.srd2-radar-legend .sw.comp { width:14px; height:0; border-top:1px dashed var(--srd-bone-60); }

/* ---- The swing clip — phone-sized player, not a full-page block ---- */
.srd2-video-card {
  display:inline-block;            /* shrink card to the (portrait) video width */
  border:1px solid var(--srd-line); border-radius: var(--srd-radius-lg);
  background:#000; overflow:hidden; padding:0; line-height:0;
  box-shadow: 0 24px 60px -30px rgba(0,0,0,0.8);
}
.srd2-video {
  display:block; height:auto; width:auto;
  max-height: 440px; max-width: 100%;   /* portrait clip ~440px tall, not 1000px */
  background:#000;
}

/* ---- Pose skeletons at key moments ---- */
.srd2-skel-row { display:grid; grid-template-columns: repeat(3,1fr); gap: 1rem; }
@media (max-width: 640px) { .srd2-skel-row { grid-template-columns: 1fr 1fr 1fr; } }
.srd2-skel {
  border:1px solid var(--srd-line); border-radius: var(--srd-radius);
  background:
    radial-gradient(80% 60% at 50% 30%, rgba(232,193,112,0.05), transparent 70%),
    var(--srd-glass-1);
  padding: 1.1rem 1rem 0.9rem; display:flex; flex-direction:column;
  align-items:center; gap: 0.8rem;
}
.srd2-skel-vis { width:100%; height: 250px; display:grid; place-items:center; }
.srd2-skel-svg { width:auto; height:100%; max-width:100%; }
.srd2-skel-svg .skel-lines line {
  stroke: var(--srd-gold); stroke-width: 2.5; stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}
.srd2-skel-svg .skel-dots circle { fill: var(--srd-bone); }
.srd2-skel-cap {
  font-family: var(--srd-mono); font-size: 10px; letter-spacing: 0.18em;
  text-transform: uppercase; color: var(--srd-bone-60);
}

/* ---- Real swing frames with the detected pose overlaid ---- */
.srd2-frame-row { display:grid; grid-template-columns: repeat(3,1fr); gap: 1.1rem; }
@media (max-width: 640px) { .srd2-frame-row { grid-template-columns: 1fr 1fr 1fr; } }
.srd2-frame { display:flex; flex-direction:column; align-items:center; gap: 0.7rem; }
.srd2-frame-inner {
  position:relative; width:100%; border-radius: var(--srd-radius);
  overflow:hidden; border:1px solid var(--srd-line); background:#000;
}
.srd2-frame-img { display:block; width:100%; height:100%; object-fit:cover; }
.srd2-frame-overlay { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
/* dim base skeleton, bright highlighted segment (the part the fix is about) */
.srd2-frame-overlay line.bl {
  stroke: rgba(244,239,230,0.45); stroke-width: 2; stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}
.srd2-frame-overlay line.hl {
  stroke-width: 5; stroke-linecap: round; vector-effect: non-scaling-stroke;
  filter: drop-shadow(0 0 3px rgba(0,0,0,0.6));
}
.srd2-frame-overlay circle { fill: #fff; opacity: 0.7; }
/* phase chip on the frame + the metric caption under it */
.srd2-frame-tag {
  position:absolute; left:10px; top:10px;
  font-family: var(--srd-mono); font-size: 10px; letter-spacing: 0.14em;
  text-transform: uppercase; color: #fff; padding: 4px 9px; border-radius: 999px;
  background: rgba(0,0,0,0.5); border: 1px solid var(--c, var(--srd-gold));
  backdrop-filter: blur(4px);
}
.srd2-frame-cap { text-align:center; display:flex; flex-direction:column; gap:3px; }
.srd2-fc-name {
  font-family: var(--srd-mono); font-size: 10px; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--srd-bone-60);
}
.srd2-fc-val { font-family: var(--srd-sans); font-size: 0.92rem; color: var(--srd-bone); }
.srd2-fc-val.ok { color: var(--srd-green); }

/* ---- STRENGTHS (mirrors stat cards) ---- */
.srd2-str-grid { display:grid; grid-template-columns: repeat(4,1fr); gap: 1rem; }
.srd2-str {
  border:1px solid var(--srd-line); border-radius: var(--srd-radius);
  background: var(--srd-glass-1); padding: 20px 20px 18px;
  display:flex; flex-direction:column;
  transition: transform .2s ease, border-color .2s ease;
}
.srd2-str:hover { transform: translateY(-2px); border-color: rgba(74,227,140,0.3); }
.srd2-str-cat {
  font-family: var(--srd-mono); font-size: 9.5px; letter-spacing:0.14em;
  text-transform:uppercase; color: var(--srd-green); margin-bottom:12px;
}
.srd2-str-vals { display:flex; align-items:baseline; gap:10px; }
.srd2-str-you {
  font-family: var(--srd-mono); font-weight:500; font-size: 1.7rem; line-height:1;
  color: var(--srd-bone); font-variant-numeric:tabular-nums;
}
.srd2-str-vs { font-family: var(--srd-mono); font-size:10px; letter-spacing:0.08em; color: var(--srd-gray); }
.srd2-str-bar {
  margin-top: 16px; height:6px; border-radius:3px;
  background: rgba(244,239,230,0.08); overflow:hidden; position:relative;
}
.srd2-str-bar > span {
  position:absolute; left:0; top:0; bottom:0; border-radius:3px;
  background: linear-gradient(90deg, #2EA866, var(--srd-green));
  box-shadow: 0 0 10px -2px var(--srd-green);
}
.srd2-str-pct {
  margin-top: 10px; font-family: var(--srd-mono); font-size: 10px;
  letter-spacing:0.06em; color: var(--srd-bone-60);
}
.srd2-str-pct .n { color: var(--srd-green); font-weight:500; }

/* ---- PHASE / KINETIC STRIP (mirrors .velocity-ladder) ---- */
.srd2-phase-card {
  border:1px solid var(--srd-line); border-radius: var(--srd-radius-lg);
  background:
    radial-gradient(80% 120% at 0% 0%, rgba(232,193,112,0.05), transparent 60%),
    var(--srd-glass-1);
  padding: 2rem 2rem 1.6rem;
}
.srd2-phase-strip { display:flex; align-items:stretch; gap:0; }
.srd2-phase {
  flex:1; display:flex; flex-direction:column; align-items:center; text-align:center;
  position:relative;
}
.srd2-phase-node {
  width:14px; height:14px; border-radius:50%;
  background: var(--srd-bg); border:2px solid var(--srd-gold);
  box-shadow: 0 0 12px -3px var(--srd-gold); z-index:2;
}
.srd2-phase-name {
  margin-top:14px; font-family: var(--srd-mono); font-size: 10px;
  letter-spacing:0.1em; text-transform:uppercase; color: var(--srd-bone);
}
.srd2-phase-ms {
  margin-top:5px; font-family: var(--srd-mono); font-size:9.5px;
  letter-spacing:0.06em; color: var(--srd-gray);
}
.srd2-phase-gap {
  position:absolute; top:6px; left:50%; width:100%; height:2px;
  background: linear-gradient(90deg, rgba(232,193,112,0.5), rgba(232,193,112,0.18));
  z-index:1;
}
.srd2-phase-gaplabel {
  position:absolute; top:-14px; left:50%; transform:translateX(-50%);
  font-family: var(--srd-mono); font-size:9px; letter-spacing:0.06em;
  color: var(--srd-gold); white-space:nowrap;
}
.srd2-phase-callout {
  margin-top: 1.6rem; padding-top: 1.2rem; border-top: 1px solid var(--srd-line);
  font-family: var(--srd-sans); font-size: 13.5px; line-height:1.6; color: var(--srd-bone-80);
}
.srd2-phase-callout strong { color: var(--srd-gold); font-weight:600; }

/* ---- COLLAPSIBLE FULL BREAKDOWN ---- */
.srd-collapse {
  border:1px solid var(--srd-line); border-radius: var(--srd-radius);
  background: var(--srd-glass-1); overflow:hidden;
}
.srd-collapse > summary {
  list-style:none; cursor:pointer; user-select:none;
  display:flex; align-items:center; justify-content:space-between; gap:1rem;
  padding: 18px 22px;
  font-family: var(--srd-mono); font-size: 11px; letter-spacing:0.14em;
  text-transform:uppercase; color: var(--srd-bone-80);
  transition: background .2s ease;
}
.srd-collapse > summary::-webkit-details-marker { display:none; }
.srd-collapse > summary:hover { background: var(--srd-glass-2); color: var(--srd-bone); }
.srd-collapse > summary .caret {
  font-family: var(--srd-mono); color: var(--srd-gold);
  transition: transform .25s ease; display:inline-block;
}
.srd-collapse[open] > summary .caret { transform: rotate(90deg); }
.srd-collapse[open] > summary { border-bottom: 1px solid var(--srd-line); }
.srd-collapse .srd2-break-body { padding: 6px 22px 18px; }
/* table-layout:fixed so columns respect width:100% and content wraps instead of
   forcing horizontal overflow (mobile fit). First column wraps long metric names. */
.srd2-break-table { width:100%; max-width:100%; border-collapse:collapse; table-layout:fixed; }
.srd2-break-table td:first-child, .srd2-break-table th:first-child { word-break:break-word; }
.srd2-break-table th {
  font-family: var(--srd-mono); font-size:9px; letter-spacing:0.14em;
  text-transform:uppercase; color: var(--srd-gray); text-align:left;
  padding: 12px 8px 10px; border-bottom:1px solid var(--srd-line); font-weight:500;
}
@media (max-width: 560px) {
  .srd2-break-table th, .srd2-break-table td { padding: 8px 4px; font-size: 10.5px; }
}
.srd2-break-table th + th, .srd2-break-table td + td { text-align:right; }
.srd2-break-table td {
  font-family: var(--srd-mono); font-size:12px; color: var(--srd-bone-80);
  padding: 10px 8px; border-bottom:1px solid var(--srd-line);
  font-variant-numeric: tabular-nums;
}
.srd2-break-table td:first-child { color: var(--srd-bone); font-weight:500; }
.srd2-break-table tbody tr:last-child td { border-bottom:0; }
.srd2-break-st {
  display:inline-grid; place-items:center; width:22px; height:22px; border-radius:6px;
  font-size:11px; font-weight:600;
}
.srd2-break-st.ok   { background: var(--srd-green-soft); color: var(--srd-green); }
.srd2-break-st.warn { background: var(--srd-gold-soft);  color: var(--srd-gold); }
.srd2-break-st.bad  { background: var(--srd-red-soft);   color: var(--srd-red); }

@media (max-width: 1000px) {
  .srd2-hero { grid-template-columns: 1fr; gap: 2rem; }
  .srd2-radar-card { grid-template-columns: 1fr; gap: 2rem; padding: 2rem 1.4rem; }
  .srd2-str-grid { grid-template-columns: repeat(2,1fr); }
  .srd2-headline { font-size: 2.8rem; }
}
@media (max-width: 560px) {
  .srd2-str-grid { grid-template-columns: 1fr; }
  .srd2-phase-name { font-size: 8.5px; }
  .srd2-issue { flex-direction:column; align-items:flex-start; gap:6px; }
}
</style>
"""


# =====================================================================
#                           SECTION BUILDERS
# =====================================================================


def _band_class_srd(band_color: str, score: int) -> str:
    bc = (band_color or "").lower()
    if bc in {"green", "elite"}: return "green"
    if bc in {"red", "rebuild"}: return "red"
    if bc in {"amber", "strong", "building", "gold"}: return "amber"
    # Derive from score if no hint
    if score >= 85: return "green"
    if score < 60:  return "red"
    return "amber"


def _ring_svg(score: int, band: str, size: int = 196) -> str:
    color = {"green": "#4AE38C", "amber": "#E8C170", "red": "#E64530"}.get(band, "#E8C170")
    dim   = {"green": "#2EA866", "amber": "#C79A45", "red": "#B8351F"}.get(band, "#C79A45")
    radius = (size / 2) - 14
    cx = cy = size / 2
    circ = 2 * 3.14159 * radius
    pct = max(0, min(100, score)) / 100
    dash = pct * circ
    gid = f"sgrad_{band}"
    sw = max(10, round(size * 0.062))
    return f"""
<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" style="display:block;">
  <defs>
    <linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{color}"/>
      <stop offset="100%" stop-color="{dim}"/>
    </linearGradient>
  </defs>
  <circle cx="{cx}" cy="{cy}" r="{radius}" fill="none"
          stroke="rgba(244,239,230,0.07)" stroke-width="{sw}"/>
  <circle cx="{cx}" cy="{cy}" r="{radius}" fill="none"
          stroke="url(#{gid})" stroke-width="{sw}" stroke-linecap="round"
          stroke-dasharray="{dash:.2f} {circ:.2f}"
          transform="rotate(-90 {cx} {cy})"
          style="filter: drop-shadow(0 0 10px {color}59);"/>
  <text x="{cx}" y="{cy + size * 0.075}" text-anchor="middle"
        font-family="'Geist Mono', ui-monospace, monospace" font-weight="600"
        font-size="{round(size * 0.31)}" letter-spacing="-1"
        fill="#F4EFE6" style="font-variant-numeric:tabular-nums;">{score}</text>
  <text x="{cx}" y="{cy + size * 0.205}" text-anchor="middle"
        font-family="'Geist Mono', ui-monospace, monospace"
        font-size="{round(size * 0.058)}" letter-spacing="2.5"
        fill="rgba(244,239,230,0.45)">/ 100</text>
</svg>
"""


def _ring_html(score: int, band: str) -> str:
    """CSS conic-gradient score gauge. st.html() STRIPS <svg>, so the SVG ring
    never rendered live — this pure-CSS donut does. The inner circle is drawn
    with ::before (in CSS) using the page bg to punch the hole."""
    color = {"green": "#4AE38C", "amber": "#E8C170", "red": "#E64530"}.get(band, "#E8C170")
    try:
        s = max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        s = 0
    deg = round(s * 3.6, 1)
    return (
        f'<div class="srd-gauge-ring" style="background:conic-gradient(from -90deg, '
        f'{color} {deg}deg, rgba(244,239,230,0.08) {deg}deg);">'
        f'<div class="srd-gauge-ring-in">'
        f'<div class="srd-gauge-num">{s}</div>'
        f'<div class="srd-gauge-cap">/ 100</div>'
        f'</div></div>'
    )


def _radar_svg(axes: List[Tuple[str, float]], size: int = 220) -> str:
    n = len(axes)
    if n == 0:
        return ""
    import math
    cx = cy = size / 2
    R = size / 2 - 22
    # Grid rings
    rings = []
    for r_pct in (0.25, 0.5, 0.75, 1.0):
        pts = []
        for i in range(n):
            ang = -math.pi / 2 + 2 * math.pi * i / n
            x = cx + R * r_pct * math.cos(ang)
            y = cy + R * r_pct * math.sin(ang)
            pts.append(f"{x:.1f},{y:.1f}")
        rings.append(
            f'<polygon points="{" ".join(pts)}" fill="none" '
            f'stroke="rgba(244,239,230,0.06)" stroke-width="1"/>'
        )
    # Axis lines + labels
    axis_lines, labels = [], []
    for i, (lbl, _pct) in enumerate(axes):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        x = cx + R * math.cos(ang)
        y = cy + R * math.sin(ang)
        axis_lines.append(
            f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" '
            f'stroke="rgba(244,239,230,0.06)" stroke-width="1"/>'
        )
        lx = cx + (R + 12) * math.cos(ang)
        ly = cy + (R + 12) * math.sin(ang) + 3
        anchor = "middle"
        if math.cos(ang) > 0.3: anchor = "start"
        elif math.cos(ang) < -0.3: anchor = "end"
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-family="Geist Mono, monospace" font-size="8.5" '
            f'fill="rgba(244,239,230,0.5)" letter-spacing="1.6">{html.escape(lbl)}</text>'
        )
    # Player polygon
    pts = []
    for i, (_lbl, pct) in enumerate(axes):
        p = max(0, min(100, float(pct or 0))) / 100
        ang = -math.pi / 2 + 2 * math.pi * i / n
        x = cx + R * p * math.cos(ang)
        y = cy + R * p * math.sin(ang)
        pts.append(f"{x:.1f},{y:.1f}")
    poly = (
        f'<polygon points="{" ".join(pts)}" fill="rgba(232,193,112,0.18)" '
        f'stroke="#E8C170" stroke-width="1.6" stroke-linejoin="round"/>'
    )
    return (
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" style="display:block;">'
        + "".join(rings) + "".join(axis_lines) + poly + "".join(labels)
        + '</svg>'
    )


def _sparkline_svg(points: List[float], width: int = 280, height: int = 90,
                    color: str = "#E8C170") -> str:
    if not points:
        return ""
    pad_x, pad_y = 6, 8
    lo, hi = min(points), max(points)
    span = max(hi - lo, 1)
    step = (width - 2 * pad_x) / max(len(points) - 1, 1)
    coords = []
    for i, v in enumerate(points):
        x = pad_x + i * step
        y = (height - pad_y) - ((v - lo) / span) * (height - 2 * pad_y)
        coords.append((x, y))
    path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = path + f" L {coords[-1][0]:.1f},{height} L {coords[0][0]:.1f},{height} Z"
    last_x, last_y = coords[-1]
    return f"""
<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none">
  <defs>
    <linearGradient id="srd-spark-grad" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="{color}" stop-opacity="0.28"/>
      <stop offset="100%" stop-color="{color}" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <path d="{area}" fill="url(#srd-spark-grad)"/>
  <path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.2" fill="{color}"/>
</svg>
"""


# Phrase library used to turn raw metric labels into player-traits.
# Keys are matched as case-insensitive substrings against metric labels
# so "Hip rotation at contact" → "Explosive hip rotation through contact".
_TRAIT_PHRASES: List[Tuple[str, str]] = [
    ("hip-shoulder separation", "Efficient hip-shoulder sequencing"),
    ("hip-shoulder",            "Efficient hip-shoulder sequencing"),
    ("hip rotation",            "Explosive hip rotation through contact"),
    ("bat path",                "Clean, repeatable bat path"),
    ("bat speed",               "High-end bat speed"),
    ("swing duration",          "Compact, repeatable swing tempo"),
    ("foot plant",              "Sharp, on-time foot plant"),
    ("launch",                  "Snappy launch-to-contact window"),
    ("re-extension",            "Powerful lower-body re-extension"),
    ("most bent",               "Athletic, balanced load position"),
    ("load",                    "Stable, athletic load position"),
    ("head drift",              "Stable head and locked visual line"),
    ("stride",                  "Balanced stride length"),
    ("shoulder",                "Strong shoulder load and turn"),
    ("rotation",                "Explosive rotational acceleration"),
    ("contact",                 "Consistent contact mechanics"),
]


def _trait_phrase_for(label: str) -> str:
    """Map a raw metric/category label to a player-trait phrase."""
    ll = (label or "").lower()
    for key, phrase in _TRAIT_PHRASES:
        if key in ll:
            return phrase
    # Fallback: title-case the label so we never emit raw snake_case.
    return (label or "").strip().title() or "Sound fundamentals"


def _gap_sentence(row: Dict[str, Any], ref_name: str) -> str:
    """One-sentence diagnosis of the worst metric, tied to the reference."""
    raw_label = (row.get("label") or "this mechanic").strip()
    label = raw_label[0].upper() + raw_label[1:] if raw_label else "This mechanic"
    you = row.get("player_str") or "—"
    mlb = row.get("ref_str") or "—"
    if you != "—" and mlb != "—":
        return (
            f"{label} sits at {you} versus {ref_name}'s {mlb} — "
            f"the largest mechanical gap between your swings."
        )
    return (
        f"{label} is the biggest mechanical gap between your swing and {ref_name}'s."
    )


# Templated "why this comparison matters" lines, keyed by the category of
# the top narrative. Used as a fallback when the analyzer's own copy
# isn't available.
_WHY_BY_TOPIC: List[Tuple[str, str]] = [
    ("hip",        "Closing this gap restores the elastic torque MLB hitters use to drive bat speed and exit velocity."),
    ("separation", "Tighter hip-shoulder sequencing is the single biggest unlock for bat speed at this level."),
    ("head",       "Stabilising the head improves visual tracking, contact quality, and pitch recognition under stress."),
    ("lower",      "Re-sequencing the lower body lets you keep stored energy until contact instead of bleeding it early."),
    ("rotation",   "Sharper rotation gives you more barrel time in the zone without losing balance."),
    ("contact",    "More consistent contact mechanics is what separates good high-school timing from pro-level repeatability."),
    ("timing",     "Better timing keeps your swing on-plane through more pitch locations."),
    ("stride",     "A cleaner stride sets up everything downstream — sequencing, balance, and bat path."),
]

_WHY_DEFAULT = (
    "Closing this gap is the fastest path to more bat speed, cleaner contact, "
    "and a more repeatable swing."
)


def _why_matters_sentence(record: Dict[str, Any]) -> str:
    """Single sentence explaining why the top gap matters.
    Prefer the analyzer's own narrative; fall back to a topic-templated line.
    """
    narratives = record.get("narratives") or []
    if narratives:
        paras = narratives[0].get("paragraphs") or []
        if len(paras) > 1:
            txt = (paras[1] or "").replace("Why it costs you: ", "").strip()
            if txt:
                # Already a complete sentence — trust it.
                return txt
        title = (narratives[0].get("title") or "").lower()
        for key, sentence in _WHY_BY_TOPIC:
            if key in title:
                return sentence
    return _WHY_DEFAULT


def _build_mlb_insights(record: Dict[str, Any],
                        ref_name: str) -> Tuple[List[str], str, str]:
    """Return (shared_traits, biggest_gap_sentence, why_matters_sentence).

    `shared_traits` is 2-4 deduped phrases derived from the highest sim_pct
    rows in metric_table (≥75%). If fewer than 2 qualify, we top up from
    the precomputed `strengths` list. Every output is plain text — caller
    is responsible for HTML escaping.
    """
    rows = _flatten_metric_table(record)
    scored = [r for r in rows if r.get("sim_pct") is not None]
    high = sorted(scored, key=lambda r: r.get("sim_pct", 0), reverse=True)
    low = sorted(scored, key=lambda r: r.get("sim_pct", 0))

    traits: List[str] = []
    seen = set()
    for r in high:
        if (r.get("sim_pct") or 0) < 75:
            break
        phrase = _trait_phrase_for(r.get("label", ""))
        if phrase in seen:
            continue
        seen.add(phrase)
        traits.append(phrase)
        if len(traits) >= 4:
            break

    if len(traits) < 2:
        for s in (record.get("strengths") or []):
            phrase = _trait_phrase_for(s.get("category_label") or "")
            if phrase and phrase not in seen:
                seen.add(phrase)
                traits.append(phrase)
            if len(traits) >= 3:
                break

    if not traits:
        traits = ["Shared athletic foundation"]

    biggest_gap = ""
    if low:
        biggest_gap = _gap_sentence(low[0], ref_name)

    why = _why_matters_sentence(record)
    return traits, biggest_gap, why


# =====================================================================
#                   TWO-SYSTEM SECTION BUILDERS
# =====================================================================


def _confidence_class(confidence: Optional[float]) -> str:
    """Return 'green', 'amber', or 'red' based on pillar confidence value."""
    if confidence is None:
        return "red"
    if confidence >= 0.8:
        return "green"
    if confidence >= 0.4:
        return "amber"
    return "red"


def _build_confidence_badge(confidence: Optional[float]) -> str:
    """Return an HTML confidence badge for the given confidence value."""
    cls = _confidence_class(confidence)
    labels = {"green": "High", "amber": "Medium", "red": "Low"}
    lbl = labels[cls]
    return f'<span class="srd-conf-badge {cls}">{lbl}</span>'


def _filming_guide_needed(pillars: Optional[Dict[str, Any]]) -> bool:
    """Return True if any pillar has yellow or red confidence."""
    if not pillars:
        return False
    for pillar_data in pillars.values():
        conf = pillar_data.get("confidence")
        if conf is None or conf < 0.8:
            return True
    return False


def _build_filming_guide() -> str:
    """Return the 'Film it like this' inline guide block."""
    return """
<div class="srd-filming-guide">
  <div class="srd-filming-label">Film it like this</div>
  <div class="srd-filming-text">
    Film side-on, perpendicular to the pitcher &nbsp;&middot;&nbsp; full body in
    frame &nbsp;&middot;&nbsp; good light &nbsp;&middot;&nbsp; slow-mo
    (120/240fps) if your phone supports it
  </div>
</div>
"""


def _build_match_reveal(record: Dict[str, Any]) -> str:
    """Card 1 — MLB Match reveal: full-width hero at the very top.

    Shows the pro name always. Shows the movement-match % ONLY when
    mlb_match.confident is True, labeled as 'movement match'. Never
    '/100', never red-banded. When not confident, shows a side-angle nudge.

    Falls back gracefully to the legacy `reference` block.
    """
    mlb = record.get("mlb_match") or {}
    reference = record.get("reference") or {}

    # Resolve pro name: live result first, then the reference dict, then the
    # saved-record's flat reference_name (saved reports only carry this — without
    # it the hero showed a generic "Your Pro Match" / "YM" instead of the pro).
    pro_name = (
        mlb.get("pro_name")
        or reference.get("name")
        or record.get("reference_name")
        or "Your Pro Match"
    )
    initials = _initials(pro_name)

    confident = mlb.get("confident", False)
    movement_pct = mlb.get("movement_match_pct")
    locked = mlb.get("locked", False)

    # Style identity / team from reference
    team_pos = (reference.get("team") or "")
    if reference.get("position"):
        sep = " · " if team_pos else ""
        team_pos = f"{team_pos}{sep}{reference['position']}"
    style_text = (reference.get("style") or "")

    # Movement match % — only when confident and present; never /100
    # BL-004C (4): label + caveat now come from mlb_match.py's single
    # source of truth, so this reads consistently with every other page
    # that shows this number. The number itself is unchanged.
    if confident and movement_pct is not None and mlb:
        from mlb_match import MATCH_LABEL, MATCH_CAVEAT_SHORT
        pct_html = f"""
<div class="srd-match-pct-row">
  <span class="srd-match-pct">{int(movement_pct)}%</span>
  <span class="srd-match-pct-label" title="{html.escape(MATCH_CAVEAT_SHORT)}">{html.escape(MATCH_LABEL)}</span>
</div>
<div class="srd-match-pct-caveat">{html.escape(MATCH_CAVEAT_SHORT)}</div>
"""
        nudge_html = ""
    else:
        pct_html = ""
        if mlb:
            # New record but low confidence — show nudge
            nudge_html = (
                '<div class="srd-match-nudge">'
                'Film a cleaner side angle to confirm your match.'
                '</div>'
            )
        else:
            # Legacy record — no nudge, no %
            nudge_html = ""

    style_html = (
        f'<div class="srd-mlb-style">{html.escape(style_text)}</div>'
        if style_text else ""
    )

    team_html = (
        f'<div class="srd-mlb-team">{html.escape(team_pos)}</div>'
        if team_pos else
        '<div class="srd-mlb-team">Your closest pro swing — the hitter you move like.</div>'
    )

    locked_tag = ""
    if locked:
        locked_tag = (
            '<span class="srd-conf-badge green" style="margin-left:10px;">LOCKED</span>'
        )

    return f"""
<div class="srd-match-reveal">
  <div class="srd-match-eyebrow">MLB Match{locked_tag}</div>
  <div class="srd-match-body">
    <div class="srd-match-avatar">{initials}</div>
    <div class="srd-match-info">
      <div class="srd-match-name">{html.escape(pro_name)}</div>
      {team_html}
      {pct_html}
      {nudge_html}
      {style_html}
    </div>
  </div>
</div>
"""


def _build_reconciliation() -> str:
    """The exact reconciliation line between Match and Score."""
    return """
<div class="srd-reconcile">
  Your Match is who you move like; your Swing Score is how well you&#39;re
  executing it &#8212; <strong>you grow your Score, not your Match.</strong>
</div>
"""


def _build_pillar_bars(pillars: Dict[str, Any]) -> str:
    """Render the 4 pillar mini-bars with compliance + confidence badges."""
    PILLAR_ORDER = [
        ("sequence",  "Sequence"),
        ("stability", "Stability"),
        ("timing",    "Timing"),
        ("stride",    "Stride"),
    ]
    rows_html = []
    for key, display in PILLAR_ORDER:
        p = pillars.get(key) or {}
        compliance = p.get("compliance")
        confidence = p.get("confidence")
        label = p.get("label") or display

        fill_pct = int(round((compliance or 0) * 100))
        conf_cls = _confidence_class(confidence)
        fill_color_cls = conf_cls  # green/amber/red maps to same bar color

        # Low-confidence warning
        if conf_cls == "red":
            warn_html = (
                '<div class="srd-pillar-warn">'
                'Couldn&#39;t read this cleanly — re-film for a better read.'
                '</div>'
            )
        else:
            warn_html = ""

        badge_html = _build_confidence_badge(confidence)
        rows_html.append(f"""
<div>
  <div class="srd-pillar-row">
    <span class="srd-pillar-label">{html.escape(display)}</span>
    <div class="srd-pillar-track">
      <div class="srd-pillar-fill {fill_color_cls}" style="width:{fill_pct}%;"></div>
    </div>
    <div class="srd-pillar-right">{badge_html}</div>
  </div>
  {warn_html}
</div>
""")
    return f'<div class="srd-pillars">{"".join(rows_html)}</div>'


def _build_score_card(record: Dict[str, Any],
                      history: Optional[List[Dict[str, Any]]]) -> str:
    """Card 2 — Swing Score: ring/number + band + pillar bars + what-you-did-well.

    Falls back gracefully when new fields are absent (legacy records).
    """
    # Headline score — new field first, legacy fallback. A swing_score of 0
    # is a legitimate value (all pillars zero), so test for None rather than
    # falsiness — an `or` chain would wrongly fall through to the legacy
    # pro-similarity score for a real zero-scoring swing.
    raw_score = record.get("swing_score")
    if raw_score is None:
        raw_score = record.get("score") or 0
    try:
        score = int(round(float(raw_score)))
    except (TypeError, ValueError):
        score = 0

    band_class = _band_class_srd(record.get("score_band_color") or "", score)
    band_label = (record.get("score_band_label") or {
        "green": "Elite", "amber": "Strong Foundation", "red": "Rebuild Zone"
    }[band_class])

    ring = _ring_html(score, band_class)

    # Δ vs prior
    prog = swing_progress(record, history) or {}
    delta = prog.get("score_delta")
    if delta is None or not prog.get("has_prior"):
        delta_html = (
            '<div class="srd-ring-delta flat">'
            'Baseline<span class="sub">First measured swing</span></div>'
        )
    else:
        d = int(round(float(delta)))
        prev_int = int(round(float(prog.get("prev_score") or 0)))
        if d > 0:
            cls, arrow, txt = "up", "↑", f"+{d}"
        elif d < 0:
            cls, arrow, txt = "down", "↓", f"{d}"
        else:
            cls, arrow, txt = "flat", "→", "±0"
        delta_html = (
            f'<div class="srd-ring-delta {cls}">{arrow}&nbsp;{txt}'
            f'<span class="sub">vs previous · {prev_int}</span></div>'
        )

    # Overall confidence badge (mean pillar confidence or None)
    pillars = record.get("pillars")
    overall_conf_html = ""
    pillar_html = ""
    filming_guide_html = ""

    if pillars:
        confs = [p.get("confidence") for p in pillars.values()
                 if p.get("confidence") is not None]
        mean_conf = (sum(confs) / len(confs)) if confs else None
        overall_conf_html = _build_confidence_badge(mean_conf)
        pillar_html = _build_pillar_bars(pillars)
        if _filming_guide_needed(pillars):
            filming_guide_html = _build_filming_guide()
    else:
        # Legacy: show aggregate badge from band only (no exact confidence)
        overall_conf_html = ""

    # "What you did well" — renders before any fix
    well_text = record.get("what_you_did_well") or ""
    did_well_html = ""
    if well_text:
        did_well_html = f"""
<div class="srd-did-well">
  <div class="srd-did-well-label">What you did well</div>
  {html.escape(well_text)}
</div>
"""

    # Honest age nudge (#134): only on new-engine reports (those with a
    # swing_score) where age was unknown, so the score used the default band.
    _is_new_engine = record.get("swing_score") is not None
    _age_unknown = not record.get("age_known", False)
    age_nudge_html = ""
    if _is_new_engine and _age_unknown:
        age_nudge_html = (
            '<div class="srd-age-nudge">Scored on the 13–14 standard — '
            'add your birth year in Settings for an age-accurate score.</div>'
        )

    return f"""
<div class="srd-score-card-two">
  <div class="srd-card-eyebrow">
    <span class="dot"></span>Swing Score {overall_conf_html}
  </div>
  <div class="srd-score-hero">
    <div class="srd-gauge">{ring}</div>
    <div class="srd-gauge-meta">
      <div class="srd-score-band {band_class}">
        <span class="dot"></span>{html.escape(band_label.upper())}
      </div>
      {delta_html}
      {age_nudge_html}
    </div>
  </div>
  {pillar_html}
  {did_well_html}
  {filming_guide_html}
</div>
"""


def _fmt_date(record: Dict[str, Any]) -> str:
    return (
        record.get("date_str")
        or record.get("date")
        or (record.get("timestamp", "")[:10] if record.get("timestamp") else "")
        or "—"
    )


def _swing_label(record: Dict[str, Any]) -> str:
    num = record.get("swing_number")
    if num: return f"Swing No. {num}"
    return "Latest Swing"


# =====================================================================
#                      POWER SEQUENCE SECTION
# =====================================================================

# Plain-language coach lines per rating (spec § Tile copy).
_POWER_COPY = {
    "sequencing_lag": {
        "good":     "Pelvis-then-torso, the way pros do it.",
        "marginal": "Hips and shoulders firing close together — small power leak.",
        "poor":     "Shoulders fired before the hips. Top fix.",
        None:       "Need a cleaner side angle to read this.",
    },
    "peak_hip_omega": {
        "good":     "Solid rotational power. Good HS / college-prep range.",
        "marginal": "Build hip speed — med-ball rotational throws.",
        "poor":     "Hips aren't yet generating elite rotational power.",
        None:       "Could not measure.",
    },
    "front_side_stability": {
        "good":     "Front side stayed shut through plant.",
        "marginal": "Front side opening earlier than ideal.",
        "poor":     "Front shoulder flew open early. #1 amateur fault.",
        None:       "Not enough shoulder rotation to characterize.",
    },
    "tempo_ratio": {
        "good":     "A real gather into a crisp fire — pro tempo.",
        "marginal": "Lengthen the gather (or sharpen the fire) and the barrel jumps.",
        "poor":     "Rushed — not enough gather before you fire.",
        None:       "Gather-to-fire ratio — grade needs a cleaner read.",
    },
    "xfactor_timing": {
        "good":     "Stretch holds, then unwinds into the ball — elite.",
        "marginal": "Separation peaks right around contact — a hair earlier adds power.",
        "poor":     "Separation peaks after contact — you're unwinding late.",
        None:       "Need a cleaner side angle to read this.",
    },
}


def _format_pwr_value(metric: str, value):
    """Format the tile value + unit per metric."""
    if value is None:
        return ("—", "")
    if metric == "sequencing_lag":
        return (f"{value:.0f}", "ms")
    if metric == "peak_hip_omega":
        return (f"{value:.0f}", "°/s")
    if metric == "front_side_stability":
        return (f"{value:.0f}", "%")
    return (f"{value}", "")


def _xfactor_rating(ms):
    """Good/marginal/poor for X-Factor timing (peak hip-shoulder separation
    relative to contact, ms; negative = before contact). "Balanced" band:
    good <= -20 (unwinds into the ball), marginal -20 < ms <= 10 (~at contact),
    poor > 10 (peaks after contact). None when unmeasured."""
    if ms is None:
        return None
    if ms <= -20:
        return "good"
    if ms <= 10:
        return "marginal"
    return "poor"


def _xfactor_value(ms):
    """(value, unit) for the X-Factor tile. Sign mapped to early/late so the
    number stays coach-legible (no raw negatives). None -> ("—", "")."""
    if ms is None:
        return ("—", "")
    n = f"{abs(ms):.0f}"
    if ms < 0:
        return (n, "ms early")
    if ms > 0:
        return (n, "ms late")
    return ("0", "ms")


def _tempo_rating(record):
    """Good/marginal/poor for the Tempo (gather:fire) tile, derived from the
    Timing PILLAR's compliance so the tile can never contradict the Timing bar
    shown elsewhere. None when the pillar is unmeasured (no compliance, or zero
    confidence)."""
    p = ((record.get("pillars") or {}).get("timing")) or {}
    comp = p.get("compliance")
    if comp is None or (p.get("confidence", 0) or 0) <= 0:
        return None
    if comp >= 0.66:
        return "good"
    if comp >= 0.33:
        return "marginal"
    return "poor"


def _render_power_sequence(record) -> str:
    """Return the HTML for the Kinetic-Chain section, or empty string.

    Surfaces up to three phone-reliable reads, each as its own tile:
      1. SEQUENCING — the hips-vs-shoulders firing ORDER (categorical, never a
         false-precise ms).
      2. TEMPO — the gather:fire ratio (`tempo_ratio`); its grade REUSES the
         Timing pillar so the tile can never contradict the Timing bar.
      3. X-FACTOR — when peak hip-shoulder SEPARATION lands vs contact
         (`xfactor_timing_ms`); negative = unwinds into the ball.

    Each tile is independent: the section shows if ANY has data, and a tile
    with no data is omitted (never faked). Peak hip speed + front-side
    stability stay hidden — unreliable from a single phone video, and we never
    display a number we can't stand behind. One tile -> spans full width
    (preserves the legacy sequencing-only look); two or more -> the 3-col grid.
    The bottom verdict line is driven by sequencing only."""
    # Each tile: (rating_class, label, value, unit, coach)
    tiles = []
    verdict = ""

    # 1. Sequencing — categorical firing order.
    seq = (record.get("sequence") or {})
    lag_rating = (seq.get("rating") or {}).get("sequencing_lag")
    if lag_rating is not None and seq.get("sequencing_lag_ms") is not None:
        category = {
            "good":     "Hips lead",
            "marginal": "Nearly synced",
            "poor":     "Shoulders fire early",
        }.get(lag_rating, "—")
        tiles.append((
            lag_rating, "Sequencing — hips vs. shoulders", category, "",
            _POWER_COPY["sequencing_lag"].get(lag_rating, ""),
        ))
        verdict = {
            "good":     "Your chain fired in the right order — pelvis first, then torso. "
                        "That sequence is where real bat speed comes from.",
            "marginal": "Your chain is close. Get the hips to clearly lead the shoulders "
                        "and the barrel will jump.",
            "poor":     "Your shoulders are firing before your hips — that's casting, and "
                        "it's the single biggest power leak available to fix.",
        }.get(lag_rating, "")

    # 2. Tempo (gather:fire) — grade tracks the Timing pillar.
    tempo = record.get("tempo_ratio")
    if tempo is not None:
        t_rating = _tempo_rating(record)
        tiles.append((
            t_rating or "marginal",          # neutral border when grade uncertain
            "Tempo — gather : fire", f"{float(tempo):.1f} : 1", "",
            _POWER_COPY["tempo_ratio"].get(t_rating, _POWER_COPY["tempo_ratio"][None]),
        ))

    # 3. X-Factor timing — separation peak vs contact.
    xms = record.get("xfactor_timing_ms")
    if xms is not None:
        x_rating = _xfactor_rating(xms)
        x_val, x_unit = _xfactor_value(xms)
        tiles.append((
            x_rating or "marginal",
            "X-Factor — separation timing", x_val, x_unit,
            _POWER_COPY["xfactor_timing"].get(x_rating, ""),
        ))

    if not tiles:
        return ""

    # 1 tile -> the tile itself spans full width; 2 tiles -> fill the row 50/50
    # via a modifier class (not inline grid-template-columns, which would beat
    # the mobile 1-col media query); 3 -> the default responsive 3-col grid.
    span = ' style="grid-column: 1 / -1;"' if len(tiles) == 1 else ""
    tiles_cls = "srd-power-tiles" + (" srd-power-tiles--two" if len(tiles) == 2 else "")
    tiles_html = ""
    for cls, label, value, unit, coach in tiles:
        unit_html = f'<span class="srd-power-tile-unit">{unit}</span>' if unit else ""
        tiles_html += f"""
        <div class="srd-power-tile {cls}"{span}>
          <div class="srd-power-tile-label">{label}</div>
          <div class="srd-power-tile-value">{value}{unit_html}</div>
          <div class="srd-power-tile-coach">{coach}</div>
        </div>"""
    verdict_html = f'\n      <p class="srd-power-verdict">{verdict}</p>' if verdict else ""

    return f"""
    <div class="srd-power-section">
      <div class="srd-power-eyebrow">§ 01 · Kinetic Chain</div>
      <h2 class="srd-power-title">How your body <span class="ital">fired.</span></h2>
      <div class="{tiles_cls}">{tiles_html}
      </div>{verdict_html}
    </div>
    """


def _build_issue_line(record: Dict[str, Any]) -> str:
    """Thin issue line under the masthead (mirrors dashboard .issue-line)."""
    ref = _extract_ref_info(record)
    score = _score_int(record)
    band_label = (record.get("score_band_label") or "").split("—")[0].strip()
    hand = (record.get("player_handedness") or "").strip().upper()
    hand_txt = {"RIGHT": "RHH", "LEFT": "LHH"}.get(hand, hand or "")
    fname = (record.get("filename") or "").strip()
    date = _fmt_date(record)
    left_bits = [b for b in ["Swing Report", hand_txt, fname] if b]
    mid = band_label or f"Score {score}"
    return f"""
<div class="srd2-issue">
  <span class="left">{html.escape(' · '.join(left_bits))}</span>
  <span class="mid">{html.escape(mid)} · vs {html.escape(ref.get('name') or 'reference')}</span>
  <span class="right">{html.escape(date)}</span>
</div>
"""


def _build_header(record: Dict[str, Any], is_sample: bool) -> str:
    swing = _swing_label(record)
    date = _fmt_date(record)
    ref = _extract_ref_info(record)
    sample_tag = '<span class="srd-banner-tag">SAMPLE DATA</span>' if is_sample else \
        '<span class="srd-banner-tag" style="color:var(--srd-bone-80);">REAL DATA</span>'
    return f"""
<div class="srd-banner">
  <span class="srd-banner-dot"></span>
  <span class="srd-banner-text">Preview only — not the live report</span>
  {sample_tag}
</div>

<div class="srd-pagehead">
  <div>
    <div class="srd-eyebrow">Premium Swing Report</div>
    <h1 class="srd-pagehead-title">{html.escape(swing)}</h1>
  </div>
  <div class="srd-pagehead-meta">
    Captured<strong>{html.escape(date)}</strong>
    <div style="margin-top:14px;">Comparison<strong>{html.escape(ref.get('name') or 'Unknown')}</strong></div>
  </div>
</div>
"""


# =====================================================================
#         REDESIGN v3 — dense dashboard-sibling builders
# =====================================================================

def _score_int(record: Dict[str, Any]) -> int:
    try:
        return int(round(float(record.get("score") or 0)))
    except (TypeError, ValueError):
        return 0


def _strength_axes(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-area match rows straight from `strengths` (the cleanest source of
    category_label + sim_pct). Returns dicts: label, pct, you, ref."""
    out: List[Dict[str, Any]] = []
    for s in (record.get("strengths") or []):
        lbl = (s.get("category_label") or s.get("label") or "").strip()
        if not lbl:
            continue
        try:
            pct = int(round(float(s.get("sim_pct") or 0)))
        except (TypeError, ValueError):
            pct = 0
        out.append({
            "label": lbl, "pct": pct,
            "you": (s.get("player_str") or "").strip(),
            "ref": (s.get("ref_str") or "").strip(),
            "category": (s.get("category") or "").lower(),
        })
    return out


def _gauge_ring_svg2(score: int, band: str, size: int = 230) -> str:
    """Dashboard-style score ring (SVG, renders inside the iframe)."""
    color = {"green": "#4AE38C", "amber": "#E8C170", "red": "#E64530"}.get(band, "#E8C170")
    dim   = {"green": "#2EA866", "amber": "#C9A350", "red": "#B8351F"}.get(band, "#C9A350")
    pct = max(0, min(100, score)) / 100
    r = 92
    circ = 2 * 3.14159265 * r
    dash = pct * circ
    off = circ - dash
    # tick marks every 36deg
    ticks = "".join(
        f'<g transform="rotate({a})"><line x1="0" y1="-118" x2="0" y2="-110" '
        f'stroke="rgba(244,239,230,0.18)" stroke-width="1"/></g>'
        for a in range(0, 360, 36)
    )
    return f"""
<svg class="srd2-gauge-svg" width="{size}" height="{size}" viewBox="-128 -128 256 256" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="srd2grad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{color}"/><stop offset="100%" stop-color="{dim}"/>
    </linearGradient>
  </defs>
  <circle cx="0" cy="0" r="{r}" fill="none" stroke="rgba(244,239,230,0.07)" stroke-width="13"/>
  <circle cx="0" cy="0" r="110" fill="none" stroke="rgba(244,239,230,0.06)" stroke-width="1"/>
  <circle cx="0" cy="0" r="78" fill="rgba(232,193,112,0.02)" stroke="rgba(232,193,112,0.10)"/>
  <circle cx="0" cy="0" r="{r}" fill="none" stroke="url(#srd2grad)" stroke-width="13"
          stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}"
          stroke-dashoffset="0" transform="rotate(-90)"
          style="filter: drop-shadow(0 0 9px {color}55);"/>
  <g>{ticks}</g>
</svg>
"""


def _build_hero_band(record: Dict[str, Any],
                     history: Optional[List[Dict[str, Any]]]) -> str:
    """The dense 3-column hero band. Left: score ring + category rail.
    Center: eyebrow + auto headline + deck + meta. Right: MLB match card."""
    score = _score_int(record)
    band = _band_class_srd(record.get("score_band_color") or "", score)
    band_label = (record.get("score_band_label") or "").split("—")[0].strip() or {
        "green": "Elite", "amber": "Strong foundation", "red": "Rebuild zone"
    }[band]

    # delta vs prior
    prog = swing_progress(record, history) or {}
    delta = prog.get("score_delta")
    if delta is None or not prog.get("has_prior"):
        delta_html = '<div class="delta flat">baseline swing</div>'
    else:
        d = int(round(float(delta)))
        if d > 0:   delta_html = f'<div class="delta up">▲ +{d} vs last</div>'
        elif d < 0: delta_html = f'<div class="delta down">▼ {d} vs last</div>'
        else:       delta_html = '<div class="delta flat">± 0 vs last</div>'

    ring = _gauge_ring_svg2(score, band)

    # ---- category rail (MLB match + per-area %) ----
    axes = _strength_axes(record)
    # sort so the peak (highest) shows gold, lowest shows red
    cat_rows = [{"label": "MLB match", "pct": score, "always": True}]
    cat_rows += sorted(axes, key=lambda a: -a["pct"])
    # add re-extension / lower-body row from narratives gap if present (it's the
    # #1 fix area and conveys the weak link) — pull from metric_table Front Knee.
    mt = record.get("metric_table") or {}
    knee = (mt.get("Front Knee") or []) if isinstance(mt, dict) else []
    reext = next((r for r in knee if "re-ext" in str(r.get("label", "")).lower()
                  or "re extension" in str(r.get("label", "")).lower()), None)
    if reext and reext.get("sim_pct") is not None:
        cat_rows.append({"label": "Re-extension", "pct": int(round(float(reext["sim_pct"])))})
    cat_rows = cat_rows[:6]
    pcts = [r["pct"] for r in cat_rows]
    hi, lo = (max(pcts) if pcts else 0), (min(pcts) if pcts else 0)
    cat_html = ""
    for r in cat_rows:
        cls = ""
        if r["pct"] == hi and hi != lo: cls = "peak"
        elif r["pct"] == lo and hi != lo: cls = "low"
        cat_html += (f'<div class="srd2-cat"><span>{html.escape(r["label"])}</span>'
                     f'<span class="v {cls}">{r["pct"]}</span></div>')

    # ---- center: headline + deck from the #1 narrative ----
    narratives = record.get("narratives") or []
    ref_name = (record.get("reference_name") or "the pro")
    n0 = narratives[0] if narratives else {}
    paras = n0.get("paragraphs") or []
    why = (paras[1].replace("Why it costs you: ", "").strip()
           if len(paras) > 1 else (paras[0] if paras else "")).strip()
    title = (n0.get("title") or "").strip()
    headline = _hero_headline_for(title, ref_name)
    deck = why or coach_summary(record)

    swing_dur = record.get("swing_duration_ms")
    dur_txt = f"{int(round(float(swing_dur)))}ms" if isinstance(swing_dur, (int, float)) else "—"
    hand = (record.get("player_handedness") or "").strip().upper()
    hand_txt = {"RIGHT": "RHH", "LEFT": "LHH"}.get(hand, hand or "—")
    date = _fmt_date(record)

    # ---- right: match card / band ladder ----
    mc = _match_card_html(record, score, band)

    return f"""
<section class="srd2-hero">
  <div class="srd2-gauge-wrap">
    <div style="position:relative;display:inline-block;">
      {ring}
      <div class="srd2-gauge-num">
        <div class="v">{score}</div>
        <div class="out">Swing Score · / 100</div>
        {delta_html}
      </div>
    </div>
    <div class="srd2-gauge-label">pose-derived · <span class="band">{html.escape(band_label.upper())}</span></div>
    <div class="srd2-cats">{cat_html}</div>
  </div>

  <div>
    <div class="srd2-hero-eyebrow"><span class="swatch"></span>§ 01 · This swing's headline</div>
    <h1 class="srd2-headline">{headline}</h1>
    <p class="srd2-deck">{html.escape(deck)}</p>
    <div class="srd2-hero-meta">
      <div class="srd2-meta-block"><span class="srd2-meta-label">Swing length</span><span class="srd2-meta-value">{dur_txt}</span></div>
      <div class="srd2-meta-block"><span class="srd2-meta-label">Match</span><span class="srd2-meta-value">{html.escape(ref_name)}</span></div>
      <div class="srd2-meta-block"><span class="srd2-meta-label">Captured</span><span class="srd2-meta-value">{html.escape(date)}</span></div>
      <div class="srd2-meta-block"><span class="srd2-meta-label">Hitter</span><span class="srd2-meta-value">{html.escape(hand_txt)}</span></div>
    </div>
  </div>

  {mc}
</section>
"""


_HEADLINE_MAP = {
    "knee_extension": ("Your <span class=\"gold\">front leg</span> is the unlock.", None),
    "lower-body drive": ("Your <span class=\"gold\">front leg</span> is the unlock.", None),
    "timing": ("Your <span class=\"gold\">tempo</span> is the next gear.", None),
    "timing & tempo": ("Your <span class=\"gold\">tempo</span> is the next gear.", None),
    "sequencing": ("Sequence the <span class=\"gold\">chain</span>, find the power.", None),
    "hip_rotation": ("Free up the <span class=\"gold\">hips</span> to add rotation.", None),
    "rotation": ("Free up the <span class=\"gold\">hips</span> to add rotation.", None),
}


def _hero_headline_for(title: str, ref_name: str) -> str:
    key = (title or "").strip().lower()
    base = None
    for k, (txt, _) in _HEADLINE_MAP.items():
        if k == key or (k in key) or (key and key in k):
            base = txt
            break
    if base is None:
        # generic fallback from the title itself
        safe = html.escape((title or "your swing").title())
        base = f'Dial in your <span class="gold">{safe}</span>.'
    ref_first = (ref_name or "the pro")  # full name reads better
    return f'{base}<br>{html.escape(ref_first)} territory is <span class="red">close.</span>'


def _match_card_html(record: Dict[str, Any], score: int, band: str) -> str:
    """Right-hand hero card: MLB match w/ a Rebuild→Decent→Strong→Elite ladder
    and a marker at the score (mirrors the dashboard .tier-card)."""
    ref_name = (record.get("reference_name") or "MLB Match")
    initials = _initials(ref_name)
    ref = _extract_ref_info(record)
    team_pos = (ref.get("team") or "")
    if ref.get("position"):
        team_pos = f"{team_pos} · {ref['position']}" if team_pos else ref["position"]
    if not team_pos:
        team_pos = "MLB · reference"

    # band ladder: 4 bands across 0..100  (Rebuild 0-45, Decent 45-65, Strong 65-85, Elite 85-100)
    bounds = [(0, 45, "Rebuild"), (45, 65, "Decent"), (65, 85, "Strong"), (85, 100, "Elite")]
    cur_idx = 0
    for i, (lo, hi, _n) in enumerate(bounds):
        if lo <= score < hi or (i == len(bounds) - 1 and score >= lo):
            cur_idx = i
            break
    segs = ""
    for i, (lo, hi, _n) in enumerate(bounds):
        if i < cur_idx:
            segs += '<div class="srd2-mc-seg on"></div>'
        elif i == cur_idx:
            fill = int(round((score - lo) / max(hi - lo, 1) * 100))
            fill = max(8, min(100, fill))
            segs += f'<div class="srd2-mc-seg cur" style="--fill:{fill}%"></div>'
        else:
            segs += '<div class="srd2-mc-seg"></div>'
    # marker position across full track (4 equal segments)
    seg_w = 25.0
    lo, hi, cur_name = bounds[cur_idx]
    within = (score - lo) / max(hi - lo, 1)
    marker_pct = cur_idx * seg_w + within * seg_w
    marker_pct = max(2, min(98, marker_pct))
    labels = ""
    for i, (_lo, _hi, nm) in enumerate(bounds):
        cls = "now" if i == cur_idx else ""
        labels += f'<span class="{cls}">{nm}</span>'
    # next band target
    if cur_idx < len(bounds) - 1:
        nxt_name = bounds[cur_idx + 1][2]
        nxt_at = bounds[cur_idx + 1][0]
        gap = nxt_at - score
        next_html = (f'next band <span class="gold">{nxt_name}</span> at {nxt_at} '
                     f'· <span class="gold">+{gap}</span>')
    else:
        next_html = '<span class="gold">Elite</span> band — top tier'

    return f"""
<aside class="srd2-mc">
  <div class="srd2-mc-eyebrow">
    <span>MLB Match · this swing</span>
    <span class="srd2-mc-badge"><span class="dot"></span>{html.escape(cur_name)}</span>
  </div>
  <div class="srd2-mc-id">
    <div class="srd2-mc-avatar">{initials}</div>
    <div>
      <div class="srd2-mc-name">{html.escape(ref_name)}</div>
      <div class="srd2-mc-team">{html.escape(team_pos)}</div>
    </div>
  </div>
  <div class="srd2-mc-track">
    <div class="srd2-mc-segs">{segs}</div>
    <div class="srd2-mc-marker" style="left:{marker_pct:.0f}%;"></div>
  </div>
  <div class="srd2-mc-labels">{labels}</div>
  <div class="srd2-mc-foot">
    <span class="lab">Swing Score {score}</span>
    <span class="next">{next_html}</span>
  </div>
</aside>
"""


def _radar_axes(record: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Five named axes (label, pct) for the You-vs-pro radar, sourced from
    strengths + metric_table. Order is fixed for a stable pentagon."""
    rows = _flatten_metric_table(record)

    def _avg(needles: List[str]) -> Optional[float]:
        vals = []
        for n in needles:
            r = _find_metric_row(rows, n)
            if r is not None and r.get("sim_pct") is not None:
                vals.append(float(r["sim_pct"]))
        return (sum(vals) / len(vals)) if vals else None

    # prefer the strengths category_labels (cleaner) then fall back to metric_table
    by_cat = {s.get("category"): s for s in (record.get("strengths") or [])}

    def _from_strength(cat: str, fallback_needles: List[str]) -> int:
        s = by_cat.get(cat)
        if s and s.get("sim_pct") is not None:
            return int(round(float(s["sim_pct"])))
        v = _avg(fallback_needles)
        return int(round(v)) if v is not None else 0

    rot = _from_strength("hip_rotation",
                         ["Hip rotation at contact", "Hip rotation at foot plant", "Peak hip-shoulder separation"])
    timing = _from_strength("timing", ["Launch → contact", "Foot plant → launch", "Total swing duration"])
    head = _from_strength("head_stability", ["Head drift Δy", "Head drift Δx", "Total head drift"])
    front = _from_strength("knee_extension", ["Most bent (load)", "Re-extension"])
    seq = _avg(["Peak hip-shoulder separation", "Separation at foot plant"])
    seq = int(round(seq)) if seq is not None else int(round((rot + timing) / 2))

    return [
        ("ROTATION", rot),
        ("TIMING", timing),
        ("HEAD STAB.", head),
        ("FRONT-SIDE", front),
        ("SEQUENCING", seq),
    ]


def _radar_compare_svg(axes: List[Tuple[str, int]], size: int = 420) -> str:
    """Pentagon radar: pro reference (bone dashed @ 100%) + you (gold fill)."""
    import math
    n = len(axes)
    if n == 0:
        return ""
    R = 165
    def pt(i, rad):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return (rad * math.cos(ang), rad * math.sin(ang))
    rings = ""
    for rp in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, R * rp) for i in range(n)))
        rings += f'<polygon points="{pts}" fill="none" stroke="rgba(244,239,230,0.05)" stroke-width="1"/>'
    spokes = ""
    for i in range(n):
        x, y = pt(i, R)
        spokes += f'<line x1="0" y1="0" x2="{x:.1f}" y2="{y:.1f}" stroke="rgba(244,239,230,0.10)" stroke-width="0.8"/>'
    # pro reference polygon at full radius (bone dashed)
    pro_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, R) for i in range(n)))
    pro = (f'<polygon points="{pro_pts}" fill="none" stroke="rgba(244,239,230,0.55)" '
           f'stroke-width="1.4" stroke-dasharray="5 4"/>')
    # you polygon
    you_coords = []
    for i, (_lbl, p) in enumerate(axes):
        frac = max(0.04, min(1.0, (p or 0) / 100))
        x, y = pt(i, R * frac)
        you_coords.append(f"{x:.1f},{y:.1f}")
    you = (f'<polygon points="{" ".join(you_coords)}" fill="rgba(232,193,112,0.18)" '
           f'stroke="#E8C170" stroke-width="2" stroke-linejoin="round"/>')
    dots = ""
    for c in you_coords:
        x, y = c.split(",")
        dots += f'<circle cx="{x}" cy="{y}" r="3" fill="#E8C170"/>'
    labels = ""
    for i, (lbl, p) in enumerate(axes):
        x, y = pt(i, R + 24)
        anchor = "middle"
        if x > 12: anchor = "start"
        elif x < -12: anchor = "end"
        labels += (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
                   f'font-family="Geist Mono, monospace" font-size="10" fill="#8B8E94" '
                   f'letter-spacing="0.12em">{html.escape(lbl)}</text>')
        # value tick near vertex
        vx, vy = pt(i, R + 24)
        labels += (f'<text x="{vx:.1f}" y="{vy+13:.1f}" text-anchor="{anchor}" '
                   f'font-family="Geist Mono, monospace" font-size="11" fill="#E8C170" '
                   f'font-weight="500">{p}</text>')
    # Wider-than-tall viewBox so the left/right axis labels (e.g. SEQUENCING,
    # TIMING) have room and never clip at the SVG edge. preserveAspectRatio
    # (default meet) scales the wider box down to fit the square card.
    return (
        f'<svg width="{size}" height="{size}" viewBox="-258 -210 516 420" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
        + rings + spokes + pro + you + dots + labels + '</svg>'
    )


def _build_radar_section(record: Dict[str, Any]) -> str:
    axes = _radar_axes(record)
    if not any(p for _, p in axes):
        return ""
    svg = _radar_compare_svg(axes)
    ref_name = (record.get("reference_name") or "the pro")
    ref_first = ref_name  # full name reads better than first-name only
    # narrative: top 2 matches + bottom 2 gaps
    ranked = sorted(axes, key=lambda a: -a[1])
    top = [a for a in ranked if a[1] >= 70][:2] or ranked[:2]
    gaps = sorted(axes, key=lambda a: a[1])[:2]
    top_words = " and ".join(t[0].lower().replace(".", "") for t in top)
    line = (f'You match {html.escape(ref_first)} on <span class="em">{html.escape(top_words)}</span>. '
            f'Close the gap on <span class="em">{html.escape(gaps[0][0].lower())}</span>.')
    deltas = " · ".join(f"{a[0]} {a[1]}" for a in axes)
    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 05 · You vs {html.escape(ref_name)}</div>
    <h2 class="srd-section-title">Your shape against the reference</h2>
  </div>
  <div class="srd-section-sub">5 pose-derived axes</div>
</div>
<div class="srd2-radar-card">
  <div class="srd2-radar-vis">{svg}</div>
  <div class="srd2-radar-narr">
    <p class="srd2-radar-line">{line}</p>
    <p class="srd2-radar-deltas">{html.escape(deltas)}</p>
    <div class="srd2-radar-legend">
      <div class="row"><span class="sw you"></span><span>your shape</span></div>
      <div class="row"><span class="sw comp"></span><span>{html.escape(ref_name)}</span></div>
    </div>
  </div>
</div>
"""


def _build_strengths_section(record: Dict[str, Any]) -> str:
    axes = _strength_axes(record)
    if not axes:
        return ""
    ref_name = (record.get("reference_name") or "the pro")
    ref_first = ref_name  # full name reads better than first-name only
    cards = ""
    for s in sorted(axes, key=lambda a: -a["pct"])[:4]:
        pct = max(0, min(100, s["pct"]))
        you, ref = s["you"], s["ref"]
        vs = ""
        if you and ref:
            vs = f'{html.escape(you)} vs {html.escape(ref_first)} {html.escape(ref)}'
        elif you:
            vs = html.escape(you)
        cards += f"""
<div class="srd2-str">
  <div class="srd2-str-cat">{html.escape(s["label"])}</div>
  <div class="srd2-str-vals">
    <span class="srd2-str-you">{pct}<span style="font-size:1rem;color:var(--srd-gray);">%</span></span>
    <span class="srd2-str-vs">{vs}</span>
  </div>
  <div class="srd2-str-bar"><span style="width:{pct}%"></span></div>
  <div class="srd2-str-pct"><span class="n">{pct}% match</span> to {html.escape(ref_first)}</div>
</div>
"""
    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 04 · What you crushed</div>
    <h2 class="srd-section-title">Already pro-level here</h2>
  </div>
  <div class="srd-section-sub">{len(axes[:4])} strengths confirmed</div>
</div>
<div class="srd2-str-grid">{cards}</div>
"""


_PHASE_ORDER = [
    ("load_start", "Load"),
    ("foot_plant", "Foot plant"),
    ("launch", "Launch"),
    ("contact", "Contact"),
    ("peak_rotation", "Peak rot."),
    ("finish", "Finish"),
]


def _build_phase_strip(record: Dict[str, Any]) -> str:
    pt = record.get("phases_t") or {}
    if not isinstance(pt, dict) or not pt:
        return ""
    seq = [(key, name, pt.get(key)) for key, name in _PHASE_ORDER if isinstance(pt.get(key), (int, float))]
    if len(seq) < 2:
        return ""
    # ms between consecutive phases (phases_t are in seconds)
    gaps = []
    for i in range(1, len(seq)):
        dt_ms = (seq[i][2] - seq[i - 1][2]) * 1000.0
        gaps.append(dt_ms)
    n = len(seq)
    nodes = ""
    for i, (_k, name, _t) in enumerate(seq):
        gap_html = ""
        if i < n - 1:
            g = gaps[i]
            gap_html = (f'<div class="srd2-phase-gap"></div>'
                        f'<div class="srd2-phase-gaplabel">{g:.0f}ms</div>')
        elapsed = (seq[i][2] - seq[0][2]) * 1000.0
        nodes += f"""
<div class="srd2-phase">
  {gap_html}
  <div class="srd2-phase-node"></div>
  <div class="srd2-phase-name">{html.escape(name)}</div>
  <div class="srd2-phase-ms">+{elapsed:.0f}ms</div>
</div>
"""
    # callout: slowest / fastest link
    seg_labels = [f"{seq[i][1]}→{seq[i+1][1]}" for i in range(n - 1)]
    slow_i = max(range(len(gaps)), key=lambda i: gaps[i]) if gaps else 0
    fast_i = min(range(len(gaps)), key=lambda i: gaps[i]) if gaps else 0
    total = (seq[-1][2] - seq[0][2]) * 1000.0
    callout = (f'Your swing fires in <strong>{total:.0f}ms</strong>. '
               f'Longest link is <strong>{html.escape(seg_labels[slow_i])}</strong> at {gaps[slow_i]:.0f}ms; '
               f'tightest is <strong>{html.escape(seg_labels[fast_i])}</strong> at {gaps[fast_i]:.0f}ms. '
               f'Tempo is about the spacing between these, not just the total.')
    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 06 · Kinetic chain</div>
    <h2 class="srd-section-title">Your swing, phase by phase</h2>
  </div>
  <div class="srd-section-sub">load → finish · {total:.0f}ms</div>
</div>
<div class="srd2-phase-card">
  <div class="srd2-phase-strip">{nodes}</div>
  <div class="srd2-phase-callout">{callout}</div>
</div>
"""


def _build_breakdown_collapsible(record: Dict[str, Any]) -> str:
    """Native <details> collapsible: full per-measurement table vs the pro.
    Collapsed by default. The iframe height-bridge (ResizeObserver) re-pushes
    the new body height on toggle automatically."""
    rows = [r for r in _flatten_metric_table(record) if r.get("sim_pct") is not None]
    rows_sorted = sorted(rows, key=lambda r: r.get("sim_pct", 0))
    ref_name = (record.get("reference_name") or "MLB")
    body = []
    for r in rows_sorted:
        sim = int(round(float(r.get("sim_pct") or 0)))
        if sim >= 75:   st_cls, icon = "ok", "✓"
        elif sim >= 55: st_cls, icon = "warn", "~"
        else:           st_cls, icon = "bad", "↓"
        body.append(
            f'<tr>'
            f'<td>{html.escape(str(r.get("label", "")))}</td>'
            f'<td>{html.escape(str(r.get("player_str", "—")))}</td>'
            f'<td>{html.escape(str(r.get("ref_str", "—")))}</td>'
            f'<td>{sim}%</td>'
            f'<td><span class="srd2-break-st {st_cls}">{icon}</span></td>'
            f'</tr>'
        )
    if not body:
        return ""
    body_html = "".join(body)
    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 08 · Full breakdown</div>
    <h2 class="srd-section-title">Every measurement, on the record</h2>
  </div>
  <div class="srd-section-sub">{len(body)} metrics · tap to expand</div>
</div>
<details class="srd-collapse">
  <summary>
    <span><span class="caret">▸</span>&nbsp;&nbsp;Full breakdown · every measurement vs {html.escape(ref_name)}</span>
    <span style="color:var(--srd-gray);">{len(body)} rows</span>
  </summary>
  <div class="srd2-break-body">
    <table class="srd2-break-table">
      <thead><tr><th>Metric</th><th>You</th><th>{html.escape(ref_name)}</th><th>Match</th><th>Status</th></tr></thead>
      <tbody>{body_html}</tbody>
    </table>
  </div>
</details>
"""



def _severity_tag(sev: str) -> Tuple[str, str]:
    s = (sev or "").lower()
    if s == "high": return ("high", "Major Gap")
    if s == "low":  return ("low",  "Light Tune")
    return ("med", "Worth Fixing")



# MediaPipe pose connections (subset that reads as a body) + the joints we draw.
_POSE_CONNECTIONS = [
    (11, 12), (11, 23), (12, 24), (23, 24),      # torso box
    (11, 13), (13, 15), (12, 14), (14, 16),      # arms
    (23, 25), (25, 27), (24, 26), (26, 28),      # legs
    (27, 31), (28, 32),                          # feet
    (0, 11), (0, 12),                            # head to shoulders
]
_POSE_BODY_IDX = [0, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28, 31, 32]


def _skeleton_svg(kp: list, vw: int, vh: int) -> str:
    """Draw one pose as an SVG skeleton from 33 [x,y,vis] normalized keypoints."""
    try:
        pts = [(float(p[0]) * vw, float(p[1]) * vh, float(p[2])) for p in kp]
    except Exception:
        return ""
    vis = [pts[i] for i in _POSE_BODY_IDX if i < len(pts) and pts[i][2] > 0.3]
    if len(vis) < 6:
        return ""
    xs = [p[0] for p in vis]; ys = [p[1] for p in vis]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w = (maxx - minx) or 1.0; h = (maxy - miny) or 1.0
    padx, pady = w * 0.20, h * 0.10
    vbx, vby, vbw, vbh = minx - padx, miny - pady, w + 2 * padx, h + 2 * pady
    r = max(vbw, vbh) * 0.018
    lines = ""
    for a, b in _POSE_CONNECTIONS:
        if a < len(pts) and b < len(pts) and pts[a][2] > 0.3 and pts[b][2] > 0.3:
            lines += (f'<line x1="{pts[a][0]:.1f}" y1="{pts[a][1]:.1f}" '
                      f'x2="{pts[b][0]:.1f}" y2="{pts[b][1]:.1f}"/>')
    dots = "".join(
        f'<circle cx="{pts[i][0]:.1f}" cy="{pts[i][1]:.1f}" r="{r:.1f}"/>'
        for i in _POSE_BODY_IDX if i < len(pts) and pts[i][2] > 0.3
    )
    return (f'<svg viewBox="{vbx:.1f} {vby:.1f} {vbw:.1f} {vbh:.1f}" '
            f'preserveAspectRatio="xMidYMid meet" class="srd2-skel-svg">'
            f'<g class="skel-lines">{lines}</g><g class="skel-dots">{dots}</g></svg>')


# Per key moment: friendly label, the metric to call out (matched against the
# metric_table labels), which skeleton segments to highlight, and the accent.
_PHASE_ANNOT = [
    ("foot_plant", "Foot plant", "Hip-shoulder separation",
     ["separation at foot plant", "peak hip-shoulder"], [(11, 12), (23, 24)], "#E8C170"),
    ("contact", "Contact", "Front-leg drive",
     ["re-extension"], [(23, 25), (25, 27), (24, 26), (26, 28)], "#E64530"),
    ("finish", "Finish", "Head stability",
     ["total head drift"], [(0, 11), (0, 12), (11, 12)], "#4AE38C"),
]


def _metric_row_for(rows: list, needles: list):
    for nd in needles:
        ndl = nd.lower()
        for r in rows:
            if ndl in str(r.get("label", "")).lower():
                return r
    return None


def _skeleton_overlay_svg(kp: list, vw: int, vh: int,
                          highlight=None, hl_color: str = "#E64530") -> str:
    """Skeleton in FULL-FRAME pixel coords (overlays exactly on the frame). The
    `highlight` connections are drawn thick in `hl_color`; the rest are a dim
    base, so the eye lands on the body part the fix is about."""
    try:
        pts = [(float(p[0]) * vw, float(p[1]) * vh, float(p[2])) for p in kp]
    except Exception:
        return ""
    hlset = {tuple(sorted(c)) for c in (highlight or [])}
    base, hl = "", ""
    for a, b in _POSE_CONNECTIONS:
        if a < len(pts) and b < len(pts) and pts[a][2] > 0.3 and pts[b][2] > 0.3:
            seg = (f'x1="{pts[a][0]:.1f}" y1="{pts[a][1]:.1f}" '
                   f'x2="{pts[b][0]:.1f}" y2="{pts[b][1]:.1f}"')
            if tuple(sorted((a, b))) in hlset:
                hl += f'<line class="hl" stroke="{hl_color}" {seg}/>'
            else:
                base += f'<line class="bl" {seg}/>'
    if not base and not hl:
        return ""
    r = vw * 0.010
    dots = "".join(
        f'<circle cx="{pts[i][0]:.1f}" cy="{pts[i][1]:.1f}" r="{r:.1f}"/>'
        for i in _POSE_BODY_IDX if i < len(pts) and pts[i][2] > 0.3
    )
    return (f'<svg viewBox="0 0 {vw} {vh}" preserveAspectRatio="none" '
            f'class="srd2-frame-overlay">{base}{hl}{dots}</svg>')


def _build_pose_frames(pose_data: Optional[dict], frame_imgs: Optional[dict],
                       record: Dict[str, Any]) -> str:
    """Real swing frames at each key moment, the detected pose overlaid, and the
    one measurement that matters at that moment highlighted + labeled."""
    if not pose_data or not frame_imgs:
        return ""
    frames = pose_data.get("pose_frames") or []
    phases = pose_data.get("phases_t") or pose_data.get("phases") or {}
    meta = pose_data.get("pose_meta") or {}
    vw = int(meta.get("video_width") or 1080); vh = int(meta.get("video_height") or 1920)
    if not frames or not phases:
        return ""
    rows = _flatten_metric_table(record)
    ref_name = record.get("reference_name") or "the pro"
    panels = ""
    for key, label, mname, needles, hl_conns, hl_color in _PHASE_ANNOT:
        t = phases.get(key); img = frame_imgs.get(key)
        if t is None or not img:
            continue
        fr = min(frames, key=lambda f: abs((f.get("t") or 0) - t))
        overlay = _skeleton_overlay_svg(fr.get("kp") or [], vw, vh, hl_conns, hl_color)
        mrow = _metric_row_for(rows, needles)
        # Treat a missing value as "no comparable metric" rather than composing
        # "You — · …" (the em-dash scrub would turn that into "You , · …").
        if mrow and str(mrow.get("player_str") or "—").strip() == "—":
            mrow = None
        if mrow:
            you = str(mrow.get("player_str") or "—").strip()
            ref = str(mrow.get("ref_str") or "—").strip()
            sim = mrow.get("sim_pct")
            if isinstance(sim, (int, float)) and sim >= 75:
                val_html = (f'<span class="srd2-fc-val ok">{html.escape(you)} '
                            f'· {int(sim)}% match</span>')
            else:
                val_html = (f'<span class="srd2-fc-val">You {html.escape(you)} '
                            f'· {html.escape(ref_name)} {html.escape(ref)}</span>')
            metric_html = (f'<span class="srd2-fc-name">{html.escape(mname)}</span>{val_html}')
        else:
            metric_html = f'<span class="srd2-fc-name">{html.escape(mname)}</span>'
        panels += (
            f'<div class="srd2-frame">'
            f'<div class="srd2-frame-inner" style="aspect-ratio:{vw}/{vh};">'
            f'<img class="srd2-frame-img" src="{html.escape(img, quote=True)}" alt="{html.escape(label)}"/>'
            f'{overlay}'
            f'<div class="srd2-frame-tag" style="--c:{hl_color};">{html.escape(label)}</div>'
            f'</div>'
            f'<div class="srd2-frame-cap">{metric_html}</div>'
            f'</div>'
        )
    if not panels:
        return ""
    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 02 · The swing</div>
    <h2 class="srd-section-title">What the numbers look like</h2>
  </div>
  <div class="srd-section-sub">The key measure at each moment, on your body</div>
</div>
<div class="srd2-frame-row">{panels}</div>
"""


def _build_priorities_drills(record: Dict[str, Any],
                              history: Optional[List[Dict[str, Any]]]) -> str:
    fixes = top_three_fixes(record) or []
    if history:
        try:
            fixes = enrich_fixes_with_history(fixes, history) or fixes
        except Exception:
            pass

    pri_rows = []
    if not fixes:
        pri_rows.append(
            '<div class="srd-pri"><div class="srd-pri-num">—</div>'
            '<div><div class="srd-pri-head"><div class="srd-pri-title">No priorities yet</div></div>'
            '<div class="srd-pri-desc">Upload another swing and we\u2019ll surface the biggest unlocks.</div></div></div>'
        )
    for f in fixes[:3]:
        rank = f.get("rank") or "•"
        title = (f.get("title") or "Priority").title()
        desc = (f.get("why") or f.get("headline") or "").strip()
        tag_cls, tag_lbl = _severity_tag(f.get("severity"))
        pri_rows.append(f"""
<div class="srd-pri sev-{tag_cls}">
  <div class="srd-pri-num">{html.escape(str(rank))}</div>
  <div>
    <div class="srd-pri-head">
      <div class="srd-pri-title">{html.escape(title)}</div>
      <div class="srd-pri-tag {tag_cls}">{tag_lbl}</div>
    </div>
    <div class="srd-pri-desc">{html.escape(desc)}</div>
  </div>
</div>
""")

    drill_plan = record.get("drill_plan") or {}
    cats = drill_plan.get("categories", []) if isinstance(drill_plan, dict) else []
    drill_rows, idx = [], 0
    for cat in cats[:2]:
        for d in (cat.get("drills") or [])[:2]:
            idx += 1
            sets, reps = d.get("sets"), d.get("reps")
            freq = d.get("frequency") or d.get("weekly")
            pills = []
            if sets: pills.append(f"{sets} sets")
            if reps: pills.append(f"{reps} reps")
            if freq: pills.append(str(freq))
            pills_html = "".join(
                f'<span class="srd-drill-pill">{html.escape(p)}</span>' for p in pills
            )
            drill_rows.append(f"""
<div class="srd-drill">
  <div class="srd-drill-thumb">DRILL<br/>{idx:02d}</div>
  <div>
    <div class="srd-drill-title">{html.escape(d.get("title") or d.get("name") or "Drill")}</div>
    <div class="srd-drill-pills">{pills_html}</div>
  </div>
  <div class="srd-drill-check">○</div>
</div>
""")
    if not drill_rows:
        drill_rows.append(
            '<div class="srd-drill"><div class="srd-drill-thumb">—</div>'
            '<div><div class="srd-drill-title">No drills assigned yet</div>'
            '<div class="srd-drill-pills"><span class="srd-drill-pill">Complete a swing analysis</span></div></div>'
            '<div class="srd-drill-check">○</div></div>'
        )

    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 03 · Where to spend your next session</div>
    <h2 class="srd-section-title">Your top fixes, prescribed</h2>
  </div>
  <div class="srd-section-sub">{len(fixes[:3])} priorit{'y' if len(fixes[:3]) == 1 else 'ies'} · {idx} drill{'s' if idx != 1 else ''}</div>
</div>

<div class="srd-pd-grid">
  <div class="srd-stack">
    <div class="srd-card-eyebrow"><span class="dot"></span>Your Top Fixes</div>
    <div class="srd-pri-list">{''.join(pri_rows)}</div>
  </div>
  <div class="srd-stack">
    <div class="srd-card-eyebrow"><span class="dot" style="background:var(--srd-gold);"></span>Recommended Drills</div>
    <div class="srd-drill-list">{''.join(drill_rows)}</div>
  </div>
</div>
"""


def _build_key_metrics(record: Dict[str, Any],
                       history: Optional[List[Dict[str, Any]]]) -> str:
    tiles = _compute_key_metrics(record, history) or []
    pro_full = ((record.get("reference") or {}).get("name")
                or record.get("reference_name") or "MLB")
    pro_short = pro_full or "MLB"  # full name, not last-name only
    tile_html = []
    for t in tiles[:4]:
        unit_html = (f'<span class="srd-km-unit">{html.escape(t.get("unit") or "")}</span>'
                     if t.get("unit") else "")
        delta_html = ""
        if t.get("delta_str"):
            dcls = (t.get("delta_class") or "flat").replace("bld2-km-delta", "").strip() or "flat"
            # The v2 helper uses classes like "up"/"down"/"flat" already.
            delta_html = f'<div class="srd-km-delta {dcls}">{html.escape(t["delta_str"])}</div>'
        # Match gauge — how close this metric is to the pro (sim_pct). Turns a
        # bare number into a visual you can read at a glance (green/gold/red).
        sim = t.get("sim_pct")
        gauge_html = ""
        if isinstance(sim, (int, float)):
            simi = max(0, min(100, int(round(sim))))
            gcls = "good" if simi >= 75 else ("mid" if simi >= 55 else "low")
            refc = (t.get("ref_short") or "").strip()
            cap = f"{simi}% match" + (f" · {pro_short} {html.escape(refc)}" if refc else "")
            gauge_html = (
                f'<div class="srd-km-gauge {gcls}"><span style="width:{simi}%"></span></div>'
                f'<div class="srd-km-gauge-cap">{cap}</div>'
            )
        tile_html.append(f"""
<div class="srd-km-tile">
  <div class="srd-km-label">{html.escape(t.get("label",""))}</div>
  <div class="srd-km-val-row">
    <span class="srd-km-val">{html.escape(t.get("value",""))}</span>
    {unit_html}
    {delta_html}
  </div>
  {gauge_html}
</div>
""")
    while len(tile_html) < 4:
        tile_html.append('<div class="srd-km-tile"><div class="srd-km-label">—</div>'
                         '<div class="srd-km-val-row"><span class="srd-km-val">—</span></div></div>')
    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 07 · Key Metrics</div>
    <h2 class="srd-section-title">Biomechanical readout</h2>
  </div>
  <div class="srd-section-sub">vs MLB reference</div>
</div>

<div class="srd-km">
  {''.join(tile_html)}
</div>
"""




# =====================================================================
#                       COMPARE THIS SWING
# =====================================================================
# Side-by-side comparison of the current swing vs a selected prior.
# Only renders metric rows that exist in BOTH records — never invents
# values. Used by both the Streamlit live entry point (with an
# interactive selectbox) and the static HTML preview (default = most
# recent prior, no interactivity).
# =====================================================================

# Display label -> ordered list of label substrings to match against
# rows from `_flatten_metric_table()`. First substring that matches
# wins (per swing) so we always compare like-for-like.
_COMPARE_METRICS: List[Tuple[str, List[str]]] = [
    ("Hip Rotation",       ["Hip rotation at contact", "Hip rotation at foot plant"]),
    ("Hip-Shoulder Sep",   ["Peak hip-shoulder separation", "Separation at foot plant"]),
    ("Contact Timing",     ["Launch → contact", "Foot plant → launch"]),
    ("Swing Duration",     ["Total swing duration"]),
    ("Head Drift",         ["Total head drift", "Head drift Δy"]),
    ("Re-extension",       ["Re-extension"]),
]


def _ts_of(rec: Dict[str, Any]) -> str:
    """Best-effort timestamp string used for chronological sort."""
    return str(rec.get("timestamp") or rec.get("date") or rec.get("date_str") or "")


def _priors_of(record: Dict[str, Any],
               history: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Return prior swings (strictly before current) sorted newest first.

    Excludes the current record by id-or-timestamp match. Filters out
    rows missing a usable score.
    """
    hist = list(history or [])
    cur_id = record.get("id")
    cur_ts = _ts_of(record)
    priors = []
    for r in hist:
        if not isinstance(r, dict):
            continue
        if cur_id and r.get("id") == cur_id:
            continue
        rts = _ts_of(r)
        if cur_ts and rts and rts >= cur_ts:
            continue
        try:
            float(r.get("score"))
        except (TypeError, ValueError):
            continue
        priors.append(r)
    priors.sort(key=_ts_of, reverse=True)
    return priors


def _radar_sim_pct(record: Dict[str, Any]) -> Optional[int]:
    """Same 5-axis biomech radar avg used in the hero MLB card."""
    rows = _flatten_metric_table(record)
    def _avg(needles):
        vals = []
        for n in needles:
            r = _find_metric_row(rows, n)
            if r is not None and r.get("sim_pct") is not None:
                vals.append(float(r["sim_pct"]))
        return (sum(vals) / len(vals)) if vals else None
    axes = [
        _avg(["Hip rotation at foot plant", "Hip rotation at contact"]),
        _avg(["Peak hip-shoulder separation", "Separation at foot plant"]),
        _avg(["Total swing duration", "Foot plant → launch", "Launch → contact"]),
        _avg(["Re-extension", "Most bent (load)"]),
        _avg(["Total head drift", "Head drift Δx", "Head drift Δy"]),
    ]
    real = [a for a in axes if a is not None]
    if not real:
        return None
    return int(round(sum(real) / len(real)))


def _find_first(rows: List[Dict[str, Any]],
                needles: List[str]) -> Optional[Dict[str, Any]]:
    for n in needles:
        r = _find_metric_row(rows, n)
        if r is not None:
            return r
    return None


def _delta_class(delta: Optional[float], higher_is_better: bool = True) -> str:
    """Pick a CSS modifier for a delta value."""
    if delta is None:
        return "flat"
    if abs(delta) < 1e-6:
        return "flat"
    if higher_is_better:
        return "up" if delta > 0 else "down"
    return "down" if delta > 0 else "up"


def _fmt_delta(delta: Optional[float], suffix: str = "") -> str:
    if delta is None:
        return "—"
    if abs(delta) < 1e-6:
        return "±0" + suffix
    sign = "+" if delta > 0 else ""
    if abs(delta) >= 10:
        return f"{sign}{delta:.0f}{suffix}"
    return f"{sign}{delta:.1f}{suffix}"


def _opt_label(rec: Dict[str, Any], idx_from_newest: int) -> str:
    """Build a one-line dropdown label for a prior swing."""
    n = rec.get("swing_number")
    swing_label = f"Swing #{int(n)}" if isinstance(n, (int, float)) else f"Swing {idx_from_newest+1} back"
    date = (rec.get("date_str") or rec.get("date")
            or (str(rec.get("timestamp") or "")[:10]) or "—")
    try:
        sc = int(round(float(rec.get("score"))))
    except (TypeError, ValueError):
        sc = "—"
    ref = (rec.get("reference") or {}).get("name") or rec.get("reference_name")
    pieces = [swing_label, str(date), f"{sc}/100"]
    if ref:
        pieces.append(f"vs {ref}")
    return " · ".join(pieces)


def _compare_rows(curr: Dict[str, Any],
                  prev: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the metric delta rows — score, MLB sim, then biomech rows
    that exist in BOTH swings. Each row: {label, prev, curr, delta_str,
    delta_class}.
    """
    out: List[Dict[str, Any]] = []

    # Score (always present)
    try:
        cs = float(curr.get("score")); ps = float(prev.get("score"))
        d = cs - ps
        out.append({
            "label": "Overall Score",
            "prev": f"{int(round(ps))}",
            "curr": f"{int(round(cs))}",
            "delta_str": _fmt_delta(d),
            "delta_class": _delta_class(d, higher_is_better=True),
        })
    except (TypeError, ValueError):
        pass

    # MLB similarity (radar avg)
    csim = _radar_sim_pct(curr); psim = _radar_sim_pct(prev)
    if csim is not None and psim is not None:
        d = csim - psim
        out.append({
            "label": "MLB Similarity",
            "prev": f"{psim}%",
            "curr": f"{csim}%",
            "delta_str": _fmt_delta(d, "%"),
            "delta_class": _delta_class(d, higher_is_better=True),
        })

    # Biomech rows that exist in BOTH
    crows = _flatten_metric_table(curr)
    prows = _flatten_metric_table(prev)
    for display, needles in _COMPARE_METRICS:
        cr = _find_first(crows, needles)
        pr = _find_first(prows, needles)
        if cr is None or pr is None:
            continue
        # Prefer sim_pct (higher is better, comparable scale).
        c_sim = cr.get("sim_pct"); p_sim = pr.get("sim_pct")
        if c_sim is not None and p_sim is not None:
            d = float(c_sim) - float(p_sim)
            out.append({
                "label": display,
                "prev": f"{int(round(float(p_sim)))}%",
                "curr": f"{int(round(float(c_sim)))}%",
                "delta_str": _fmt_delta(d, "%"),
                "delta_class": _delta_class(d, higher_is_better=True),
            })
        else:
            # Fall back to raw string values if both swings have them —
            # never invent a delta; show "—" if non-numeric.
            cstr = cr.get("player_str"); pstr = pr.get("player_str")
            if cstr and pstr:
                out.append({
                    "label": display,
                    "prev": str(pstr),
                    "curr": str(cstr),
                    "delta_str": "—",
                    "delta_class": "flat",
                })
    return out


def _compare_card_html(rec: Dict[str, Any], *, role: str) -> str:
    """Render one side of the compare pair (Previous Swing | This Swing)."""
    ref = _extract_ref_info(rec)
    initials = _initials(ref.get("name", ""))
    try:
        score = int(round(float(rec.get("score") or 0)))
    except (TypeError, ValueError):
        score = 0
    band = _band_class_srd(rec.get("score_band_color") or "", score)
    swing_n = rec.get("swing_number")
    swing_label = f"Swing #{int(swing_n)}" if isinstance(swing_n, (int, float)) \
        else ("This Swing" if role == "current" else "Previous Swing")
    date = (rec.get("date_str") or rec.get("date")
            or (str(rec.get("timestamp") or "")[:10]) or "—")
    role_eyebrow = "This Swing" if role == "current" else "Previous Swing"
    accent_dot = "var(--srd-red)" if role == "current" else "var(--srd-bone-60)"
    return f"""
<div class="srd-cmp-card">
  <div class="srd-cmp-role"><span class="dot" style="background:{accent_dot};"></span>{role_eyebrow}</div>
  <div class="srd-cmp-head">
    <div class="srd-cmp-avatar srd-cmp-avatar-{band}">{score}</div>
    <div>
      <div class="srd-cmp-swing">{html.escape(swing_label)}</div>
      <div class="srd-cmp-meta">{html.escape(str(date))}</div>
      <div class="srd-cmp-mlb">vs. {html.escape(ref.get('name') or 'Unknown')}</div>
    </div>
  </div>
</div>
"""


def _compare_table_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    body = []
    for r in rows:
        body.append(
            '<tr>'
            f'<td>{html.escape(r["label"])}</td>'
            f'<td class="num">{html.escape(r["prev"])}</td>'
            f'<td class="num">{html.escape(r["curr"])}</td>'
            f'<td class="num"><span class="srd-cmp-delta {r["delta_class"]}">{html.escape(r["delta_str"])}</span></td>'
            '</tr>'
        )
    return f"""
<table class="srd-cmp-table">
  <thead>
    <tr><th>Metric</th><th>Previous</th><th>This Swing</th><th>Δ</th></tr>
  </thead>
  <tbody>{''.join(body)}</tbody>
</table>
"""


def _num_from(s: Any) -> Optional[float]:
    """Pull the first signed/decimal number out of a string like
    '+6%', '-3', '0.18s', '72'. Returns None if there isn't one."""
    import re
    m = re.search(r"-?\d+(?:\.\d+)?", str(s))
    return float(m.group()) if m else None


def _compare_summary_html(prev: Dict[str, Any],
                          rows: List[Dict[str, Any]]) -> str:
    """Dynamic, real-data executive summary shown above the compare cards.

    Built ONLY from the already-computed delta rows (never invents
    data). Covers: metrics improved vs declined, overall score change,
    MLB similarity change, largest improvement, and the largest
    remaining opportunity (lowest current similarity).
    """
    # "Key metrics" = every row that produced a real delta (drop the
    # rows we couldn't compare, marked with an em-dash).
    keyrows = [r for r in rows if r.get("delta_str") not in (None, "—")]
    if not keyrows:
        return ""

    improved = sum(1 for r in keyrows if r["delta_class"] == "up")
    declined = sum(1 for r in keyrows if r["delta_class"] == "down")
    total = len(keyrows)

    score_row = next((r for r in rows if r["label"] == "Overall Score"), None)
    mlb_row = next((r for r in rows if r["label"] == "MLB Similarity"), None)

    prev_n = prev.get("swing_number")
    prev_label = (f"Swing #{int(prev_n)}"
                  if isinstance(prev_n, (int, float)) else "your previous swing")

    sentences: List[str] = []

    # 1. Improved vs declined headline
    verb = "improved"
    lead = f"<strong>{improved} of {total}</strong> key metrics {verb} since {html.escape(prev_label)}"
    if declined:
        lead += f" ({declined} declined)"
    sentences.append(lead + ".")

    # 2. Score + MLB similarity movement
    movement_bits = []
    if score_row and _num_from(score_row["prev"]) is not None:
        sp, sc = score_row["prev"], score_row["curr"]
        dnum = (_num_from(sc) or 0) - (_num_from(sp) or 0)
        direction = ("increased" if dnum > 0 else
                     "decreased" if dnum < 0 else "held")
        movement_bits.append(
            f"Your overall score {direction} from <strong>{html.escape(sp)}</strong> "
            f"to <strong>{html.escape(sc)}</strong>"
        )
    if mlb_row and _num_from(mlb_row["prev"]) is not None:
        mp, mc = mlb_row["prev"], mlb_row["curr"]
        mdir = ("rose" if (_num_from(mc) or 0) > (_num_from(mp) or 0) else
                "slipped" if (_num_from(mc) or 0) < (_num_from(mp) or 0) else "held")
        movement_bits.append(
            f"MLB similarity {mdir} from <strong>{html.escape(mp)}</strong> "
            f"to <strong>{html.escape(mc)}</strong>"
        )
    if movement_bits:
        sentences.append(", and ".join(movement_bits) + ".")

    # 3. Largest improvement (biggest positive delta, excluding score)
    ups = [
        (r, abs(_num_from(r["delta_str"]) or 0))
        for r in keyrows
        if r["delta_class"] == "up" and r["label"] != "Overall Score"
    ]
    closing = []
    if ups:
        best = max(ups, key=lambda t: t[1])[0]
        closing.append(
            f"The biggest improvement was <strong>{html.escape(best['label'])}</strong> "
            f"({html.escape(best['delta_str'])})"
        )

    # 4. Largest remaining opportunity = lowest current %-valued metric
    pct_rows = [
        r for r in rows
        if r["label"] not in ("Overall Score",)
        and str(r["curr"]).strip().endswith("%")
        and _num_from(r["curr"]) is not None
    ]
    if pct_rows:
        weak = min(pct_rows, key=lambda r: _num_from(r["curr"]))
        # Only call it out if it isn't already the headline win.
        if not (closing and weak["label"] in closing[0]):
            phrase = (f"<strong>{html.escape(weak['label'])}</strong> "
                      f"remains the largest opportunity for improvement "
                      f"(now {html.escape(str(weak['curr']))})")
            closing.append(phrase)
    if closing:
        sentences.append(", while ".join(closing) + "."
                         if len(closing) == 2 else closing[0] + ".")

    summary_text = " ".join(sentences)
    trend_cls = "up" if improved >= declined else "down"
    return f"""
<div class="srd-card srd-cmp-summary">
  <div class="srd-card-eyebrow"><span class="dot"></span>Executive Summary</div>
  <div class="srd-cmp-summary-row">
    <div class="srd-cmp-summary-badge {trend_cls}">
      <span class="big">{improved}</span><span class="of">/ {total}</span>
      <span class="lbl">improved</span>
    </div>
    <div class="srd-cmp-summary-text">{summary_text}</div>
  </div>
</div>
"""


def _compare_section_html(record: Dict[str, Any],
                          history: Optional[List[Dict[str, Any]]],
                          *,
                          selected: Optional[Dict[str, Any]] = None,
                          selector_html: str = "") -> str:
    """Full Compare This Swing section.

    `selector_html` is optional — the Streamlit path leaves it empty and
    renders the selectbox via st.selectbox separately; the static HTML
    path injects a non-interactive label here so the preview shows what
    the selector looks like.

    `selected` lets the Streamlit path override the default prior (most
    recent) once the user picks a different one.
    """
    priors = _priors_of(record, history)

    if not priors:
        empty = """
<div class="srd-card srd-cmp-empty">
  <div class="srd-card-eyebrow"><span class="dot"></span>Comparison</div>
  <div class="srd-cmp-empty-msg">Comparison unlocks after your next saved swing.</div>
  <div class="srd-cmp-empty-sub">Once you upload a second swing, this section will show side-by-side deltas vs your previous best.</div>
</div>
"""
        return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">Compare This Swing</div>
    <h2 class="srd-section-title">Side-by-side with a previous swing</h2>
  </div>
  <div class="srd-section-sub">Empty state</div>
</div>
{empty}
"""

    prev = selected if (selected in priors) else priors[0]
    rows = _compare_rows(record, prev)

    # Center delta badge — score change
    score_row = next((r for r in rows if r["label"] == "Overall Score"), None)
    if score_row:
        delta_class = score_row["delta_class"]
        delta_txt = score_row["delta_str"]
    else:
        delta_class, delta_txt = "flat", "—"

    prev_card = _compare_card_html(prev, role="previous")
    curr_card = _compare_card_html(record, role="current")
    table_html = _compare_table_html(rows)
    summary_html = _compare_summary_html(prev, rows)

    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">Compare This Swing</div>
    <h2 class="srd-section-title">Side-by-side with a previous swing</h2>
  </div>
  <div class="srd-section-sub">{len(priors)} prior {'swings' if len(priors) != 1 else 'swing'} on file</div>
</div>

{selector_html}

{summary_html}

<div class="srd-cmp-pair">
  {prev_card}
  <div class="srd-cmp-delta-badge">
    <div class="srd-cmp-delta-label">Score Δ</div>
    <div class="srd-cmp-delta-val {delta_class}">{html.escape(delta_txt)}</div>
  </div>
  {curr_card}
</div>

<div class="srd-card srd-cmp-metrics-card">
  <div class="srd-card-eyebrow"><span class="dot"></span>Metric Deltas</div>
  {table_html}
  <div class="srd-cmp-footnote">Only metrics that exist in both swings are shown.</div>
</div>
"""



def _build_next_session(record: Dict[str, Any]) -> str:
    fixes = top_three_fixes(record) or []
    drill_plan = record.get("drill_plan") or {}
    cats = drill_plan.get("categories", []) if isinstance(drill_plan, dict) else []

    items = []
    # Item 1 — Priority focus (label includes the specific priority name
    # when available; falls back to a generic phrase when the analyzer
    # didn't surface a top fix).
    if fixes:
        f0 = fixes[0]
        feel = (f0.get("fix_feel") or f0.get("headline") or "").strip()
        priority_title = (f0.get("title") or "").strip()
        label = f"Master {priority_title}" if priority_title else "Master the Priority Fix"
        items.append((
            label,
            html.escape(feel) if feel else
            f"Lock in the {priority_title or 'top priority'} mechanic in your next session."
        ))
    else:
        items.append((
            "Master the Priority Fix",
            "Run another swing analysis to surface your top mechanical priority.",
        ))
    # Item 2 — Top drill block
    if cats and (cats[0].get("drills") or []):
        d0 = cats[0]["drills"][0]
        sets, reps = d0.get("sets"), d0.get("reps")
        freq = d0.get("frequency") or d0.get("weekly")
        plan = " · ".join(filter(None, [
            f"{sets} sets" if sets else None,
            f"{reps} reps" if reps else None,
            str(freq) if freq else None,
        ]))
        items.append((
            "Run the top drill block",
            f"{html.escape(d0.get('title') or d0.get('name') or 'Drill')} — {html.escape(plan)}"
        ))
    # Item 3 — Re-measure
    items.append((
        "Re-film in 7 days",
        "Same angle, same setup. Compare side-by-side to confirm the priority gap is closing."
    ))

    list_html = "".join(
        f'<li class="srd-next-item">'
        f'<div class="srd-next-num">{i+1:02d}</div>'
        f'<div><div class="srd-next-title">{title}</div>'
        f'<div class="srd-next-sub">{sub}</div></div></li>'
        for i, (title, sub) in enumerate(items)
    )

    return f"""
<div class="srd-section">
  <div>
    <div class="srd-eyebrow">§ 09 · Next Session</div>
    <h2 class="srd-section-title">Your training plan</h2>
  </div>
  <div class="srd-section-sub">{len(items)} action{'s' if len(items) != 1 else ''} before next swing</div>
</div>

<ul class="srd-next-list">{list_html}</ul>
"""


# =====================================================================
#                     IFRAME DOCUMENT WRAPPER
# =====================================================================
# The report renders as ONE self-contained HTML document inside a
# components.html() iframe — exactly like the Edge dashboard
# (dashboard_v3.py + mock_dashboard_template.py). This is the whole
# reason the report can finally show SVG rings, conic gradients, web
# fonts and shadows: st.html() SANITIZES its payload and strips <svg>
# + <style>, flattening the report to plain boxes. An iframe renders
# the document verbatim, so every premium flourish survives.

# Auto-height bridge — copied from mock_dashboard_template.py. Measures
# the real rendered height and pushes it up through every channel a
# Streamlit components.html iframe honours so the iframe owns no
# scrollbar and the report scrolls as one page with the masthead.
_AUTO_HEIGHT_BRIDGE = """
<script>
(function () {
  function contentHeight() {
    var d = document;
    return Math.ceil(Math.max(
      d.body ? d.body.scrollHeight : 0,
      d.body ? d.body.offsetHeight : 0,
      d.documentElement ? d.documentElement.scrollHeight : 0,
      d.documentElement ? d.documentElement.offsetHeight : 0
    ));
  }
  var last = 0;
  function push() {
    var h = contentHeight();
    if (!h || Math.abs(h - last) < 2) return;
    last = h;
    try { if (window.Streamlit && Streamlit.setFrameHeight) Streamlit.setFrameHeight(h); } catch (e) {}
    try {
      window.parent.postMessage(
        { isStreamlitMessage: true, type: "streamlit:setFrameHeight", height: h }, "*"
      );
    } catch (e) {}
    try {
      if (window.frameElement) {
        window.frameElement.style.height = h + "px";
        window.frameElement.setAttribute("scrolling", "no");
      }
    } catch (e) {}
  }
  window.addEventListener("load", push);
  window.addEventListener("resize", push);
  try { if (window.ResizeObserver) new ResizeObserver(push).observe(document.body); } catch (e) {}
  [60, 200, 600, 1200, 2500].forEach(function (t) { setTimeout(push, t); });
  push();
})();
</script>
"""


def _report_document_html(body_html: str) -> str:
    """Wrap the assembled report body in ONE complete HTML document.

    `_DASHBOARD_CSS` already carries its own `<style>…</style>` block
    (with the Google-fonts @import the dashboard uses), so it drops
    straight into <head>. The <body> background is set to the Edge
    near-black so the iframe matches the page behind it seamlessly.
    """
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"UTF-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>"
        + _DASHBOARD_CSS
        + "<style>html,body{margin:0;padding:0;background:#0A0B0E;}"
          "*,*::before,*::after{box-sizing:border-box;}</style>"
        "</head><body>"
        + body_html
        + _AUTO_HEIGHT_BRIDGE
        + "</body></html>"
    )


# =====================================================================
#                          PUBLIC ENTRY POINTS
# =====================================================================


def build_dashboard_preview_html(record: Dict[str, Any],
                                  history: Optional[List[Dict[str, Any]]] = None,
                                  *, is_sample: bool = False) -> str:
    """Return the full preview as a single HTML string (no Streamlit needed).

    New section order (spec "Report UX, Data Model & Must-Haves"):
      1. MLB Match reveal (hero, full-width)
      2. Reconciliation line
      3. Swing Score card (ring + pillar bars + what-you-did-well)
      4. Top fixes + drills
      5. Key metrics / Breakdown
      6. Kinetic-Chain (power sequence)
      7. Progress / Compare / Next Session

    Used by `scripts/visual_qa/render_swing_report_static.py` to produce a
    standalone preview file you can open in any browser.
    """
    body = (
        '<div class="srd-wrap">'
        + _build_header(record, is_sample)
        + _build_issue_line(record)
        + _build_hero_band(record, history)
        + _build_priorities_drills(record, history)
        + _build_strengths_section(record)
        + _build_radar_section(record)
        + _build_phase_strip(record)
        + _build_key_metrics(record, history)
        + _build_breakdown_collapsible(record)
        + _build_next_session(record)
        + '</div>'
    )
    # Full self-contained document so it renders identically to the live
    # iframe (and opens standalone in any browser).
    return _report_document_html(body)



def render_swing_report_dashboard_preview(
    record: Dict[str, Any],
    history: Optional[List[Dict[str, Any]]] = None,
    *,
    is_sample: bool = False,
    is_preview: bool = True,
) -> None:
    """Streamlit entry point.

    Renders everything EXCEPT the Compare section as a single st.markdown
    chunk, then renders Compare with a live selectbox, then renders the
    Next Session block + closing wrapper. This split lets the selectbox
    drive an interactive comparison without leaving the report.

    Args:
        record: current swing record (must contain `score`, `metric_table`,
            `narratives`, `reference`, etc.).
        history: full saved-swing history list (newest or oldest first
            — sorted internally). Used for sparkline, key-metric trend,
            and Compare section.
        is_sample: True only when a synthetic record is being rendered;
            tags the banner.
        is_preview: When True, a "PREVIEW ONLY" banner is shown at the top.
            Production callers should pass False.
    """
    import streamlit as st  # local — stubbed by the static renderer
    import streamlit.components.v1 as components

    # The report renders as ONE self-contained HTML document inside a
    # components.html() iframe — the SAME pattern as the Edge dashboard
    # (dashboard_v3.py). This is what finally lets the score ring (SVG),
    # conic gauges, web fonts, gradients and shadows render: st.html()
    # sanitizes its payload and strips <svg>/<style>, which is exactly why
    # every prior report flattened into plain boxes of text. Inside an
    # iframe nothing is stripped, so the report can look like the dashboard.
    # Signed URL for the actual swing clip (Pro swings store the video). It's the
    # credibility piece — the rep a coach can scrub. Preview/probe records may
    # carry a pre-signed _video_signed_url; live records carry _video_path.
    # The swing at the key moments: real frames with the detected pose overlaid.
    # Probe injects _pose_data + _pose_frame_imgs; live records carry _pose_path
    # (frames are extracted from the swing video).
    pose_data = record.get("_pose_data")
    if not pose_data and record.get("_pose_path"):
        try:
            from player_storage import load_swing_pose_data
            pose_data = load_swing_pose_data(record["_pose_path"])
        except Exception:
            pose_data = None

    # The 3 key-moment stills the pose is drawn over. Live post-analyze records
    # carry them in-memory (_pose_frame_imgs); saved swings lazy-load them from
    # storage by convention beside the pose JSON. Either way, a swing with no
    # saved frames (older swings, or a failed extract) just drops the panel.
    frame_imgs = record.get("_pose_frame_imgs")
    if not frame_imgs and record.get("_pose_path"):
        try:
            from player_storage import load_swing_frame_images
            frame_imgs = load_swing_frame_images(record["_pose_path"])
        except Exception:
            frame_imgs = None

    body = (
        '<div class="srd-wrap">'
        + (_build_header(record, is_sample) if is_preview else _build_header_production(record))
        + _build_issue_line(record)
        # 1. Hero — dense 3-column band (score ring + headline + match card)
        + _build_hero_band(record, history)
        # 2. The swing — real frames at the key moments with the pose overlaid
        + _build_pose_frames(pose_data, frame_imgs, record)
        # 3. Where to spend your next session — fixes + drills
        + _build_priorities_drills(record, history)
        # 3. What you crushed — strengths
        + _build_strengths_section(record)
        # 4. You vs the pro — radar
        + _build_radar_section(record)
        # 5. Kinetic chain / timing — phase strip
        + _build_phase_strip(record)
        # 6. Biomechanical readout — metric tiles
        + _build_key_metrics(record, history)
        # 7. Full breakdown — collapsible (collapsed by default)
        + _build_breakdown_collapsible(record)
        # 8. Next session — training plan CTA
        + _build_next_session(record)
        + '</div>'
    )

    # Scrub spaced em dashes from the rendered copy. We never use them in our
    # own writing, and the analyzer's narrative strings carry a few. Only the
    # SPACED pattern (" — ") is prose punctuation; the standalone "—" glyph is
    # an empty-value placeholder in metric cells, so it's deliberately left
    # alone. A comma keeps every sentence grammatical.
    body = body.replace(" — ", ", ")

    # Renumber the "§ NN" section markers sequentially in document order so an
    # omitted optional section (e.g. the pose-frame "swing" block on older
    # swings that never saved a video) never leaves a visible gap like 01 → 03.
    import re as _re
    _sec = {"n": 0}
    def _renum(_m):
        _sec["n"] += 1
        return f"§ {_sec['n']:02d} ·"
    body = _re.sub(r"§\s*\d+\s*·", _renum, body)

    full_html = _report_document_html(body)
    # `components.html` renders a FIXED-height iframe — unlike a declared
    # Streamlit component, the in-iframe setFrameHeight/postMessage bridge
    # can't shrink it. So we estimate a tight-but-safe height from the
    # content drivers (fixes, drills, metrics, breakdown rows) instead of
    # leaving a big trailing whitespace ceiling. The bridge stays in the
    # document as a best-effort enhancement where it IS honoured.
    components.html(full_html,
                    height=_estimate_report_height(
                        record, history,
                        has_pose=bool(pose_data and frame_imgs)),
                    scrolling=False)


def _estimate_report_height(record: Dict[str, Any],
                            history: Optional[List[Dict[str, Any]]],
                            has_video: bool = False,
                            has_pose: bool = False) -> int:
    """Estimate the report's rendered pixel height from its content.

    `components.html` needs an explicit iframe height. We size it from the
    number of fixes / drills / breakdown rows (the variable-length parts)
    plus a fixed base for the masthead, hero, score card, metric tiles and
    next-session block. Padded generously and capped so nothing clips, but
    far tighter than a flat 6000px ceiling (which left dead whitespace).
    """
    # Base covers masthead + issue line + dense hero band + strengths +
    # radar card + phase strip + metric tiles + next-session block.
    # Tuned against the real Trout/62 swing: collapsed ~3370px, expanded
    # (breakdown open) ~3950px at desktop width.
    base = 2350
    fixes = top_three_fixes(record) or []
    n_fix = len(fixes)
    drill_plan = record.get("drill_plan") or {}
    cats = drill_plan.get("categories", []) if isinstance(drill_plan, dict) else []
    n_drill = sum(len(c.get("drills") or []) for c in cats if isinstance(c, dict))
    # Size for the EXPANDED breakdown (every row) so it never clips if the
    # height-bridge postMessage isn't honoured. The in-iframe ResizeObserver
    # still shrinks/grows the frame where the parent honours it.
    n_break = len([
        r for r in _flatten_metric_table(record)
        if r.get("sim_pct") is not None
    ])
    # The swing-frames row (portrait frames in 3 columns, ~480px) + header.
    h = (base + n_fix * 165 + n_drill * 90 + n_break * 44
         + (560 if has_pose else 0))
    # Headroom so columns stacking on narrow widths never clip; modest pad.
    h = int(h * 1.04) + 260
    return max(2800, min(h, 8200))


def _facility_cobrand_html() -> str:
    """Logo + name of the active player's sponsoring facility, or '' if none.
    Co-brands the report so every athlete who shares it also markets their
    academy (the Model-B viral hook). Inline styles so it survives st.html."""
    try:
        import streamlit as _st
        import facility_storage as _fac
        active = _st.session_state.get("player") or _st.session_state.get("user") or {}
        pid = (active or {}).get("id")
        if not pid:
            return ""
        facility = _fac.get_facility_for_player(pid)
        if not facility:
            return ""
        name = html.escape(facility.get("name") or "")
        # Only render a PNG data-URI logo, and escape it — defense in depth
        # against a crafted logo_url reaching a sponsored kid's report (XSS).
        logo = facility.get("logo_url") or ""
        if not logo.startswith("data:image/png;base64,"):
            logo = ""
        logo_html = (
            f'<img src="{html.escape(logo, quote=True)}" alt="{name}" style="height:30px;width:auto;'
            f'border-radius:6px;background:rgba(255,255,255,0.05);padding:3px;">'
            if logo else "")
        return (
            '<div style="display:flex;align-items:center;gap:10px;margin-top:12px;">'
            + logo_html
            + '<span style="font-family:\'Geist Mono\',ui-monospace,monospace;'
              'font-size:9.5px;letter-spacing:0.16em;text-transform:uppercase;'
              f'color:#C9A350;">In partnership with <strong style="color:#F4EFE6;">{name}</strong></span>'
            + '</div>')
    except Exception:
        return ""


def _build_header_production(record: Dict[str, Any]) -> str:
    """Same page header as `_build_header` but without the preview banner.

    Used when the renderer is called from the production Open Report flow.
    """
    swing = _swing_label(record)
    date = _fmt_date(record)
    ref = _extract_ref_info(record)
    return f"""
<div class="srd-pagehead">
  <div>
    <div class="srd-eyebrow">Premium Swing Report</div>
    <h1 class="srd-pagehead-title">{html.escape(swing)}</h1>
    {_facility_cobrand_html()}
  </div>
  <div class="srd-pagehead-meta">
    Captured<strong>{html.escape(date)}</strong>
    <div style="margin-top:14px;">Comparison<strong>{html.escape(ref.get('name') or 'Unknown')}</strong></div>
  </div>
</div>
"""


# =====================================================================
#                            SAMPLE DATA
# =====================================================================
# Mirrors the field schema the v2 + v3 renderers read. Clearly labeled.
# =====================================================================

SAMPLE_RECORD: Dict[str, Any] = {
    "id": "preview-sample-001",
    "swing_number": 7,
    "timestamp": "2026-05-18T14:23:00",
    "date_str": "May 18, 2026",
    "score": 72,
    "score_band_color": "amber",
    "score_band_label": "Strong Foundation",
    "reference": {
        "name": "Ronald Acuña Jr.",
        "team": "Atlanta Braves",
        "position": "OF",
        "style": "Explosive rotational hitter with quick hips and a flat bat path.",
        "source": "auto",
    },
    "metric_table": {
        "Hip Mechanics": [
            {"label": "Hip rotation at foot plant", "player_str": "42°",
             "ref_str": "55°", "sim_pct": 76},
            {"label": "Hip rotation at contact", "player_str": "88°",
             "ref_str": "95°", "sim_pct": 92},
        ],
        "Separation": [
            {"label": "Peak hip-shoulder separation", "player_str": "34°",
             "ref_str": "40°", "sim_pct": 85},
            {"label": "Separation at foot plant", "player_str": "18°",
             "ref_str": "20°", "sim_pct": 90},
        ],
        "Timing": [
            {"label": "Total swing duration", "player_str": "0.18s",
             "ref_str": "0.16s", "sim_pct": 88},
            {"label": "Foot plant → launch", "player_str": "0.07s",
             "ref_str": "0.06s", "sim_pct": 85},
            {"label": "Launch → contact", "player_str": "0.11s",
             "ref_str": "0.10s", "sim_pct": 91},
        ],
        "Lower Body": [
            {"label": "Re-extension", "player_str": "12°",
             "ref_str": "15°", "sim_pct": 80},
            {"label": "Most bent (load)", "player_str": "28°",
             "ref_str": "32°", "sim_pct": 78},
        ],
        "Stability": [
            {"label": "Total head drift", "player_str": "2.1\"",
             "ref_str": "1.5\"", "sim_pct": 70},
            {"label": "Head drift Δx", "player_str": "1.2\"",
             "ref_str": "0.8\"", "sim_pct": 72},
            {"label": "Head drift Δy", "player_str": "1.5\"",
             "ref_str": "1.0\"", "sim_pct": 68},
        ],
    },
    "narratives": [
        {"rank": 1, "title": "Hip Separation",
         "paragraphs": [
             "Your hips clear about 80ms after your hands start — the chain is upside down.",
             "Why it costs you: Late hip rotation bleeds 4-6 mph of exit velocity and pulls the barrel out of the zone early.",
             "What the fix feels like: Hips lead torso by a beat — feel the back pocket turn before the shoulder fires.",
         ]},
        {"rank": 2, "title": "Head Stability",
         "paragraphs": [
             "Head drifts 2.1\" toward the ball during the swing.",
             "Why it costs you: Excess head drift breaks visual lock and drops contact quality measurably.",
             "What the fix feels like: Eyes locked on the contact zone — chin stays glued to the back shoulder.",
         ]},
        {"rank": 3, "title": "Lower Body Sequence",
         "paragraphs": [
             "Back leg re-extends 80ms early, pushing power up the chain before the hands are loaded.",
             "Why it costs you: You're spending stored elastic energy before contact — bat speed leaks out the back.",
             "What the fix feels like: Stay coiled into contact — finish the turn, then release.",
         ]},
    ],
    "gaps": [
        {"category": "Hip Separation", "category_label": "Hip Separation",
         "sub_metrics": [{"sim_pct": 45}, {"sim_pct": 52}]},
        {"category": "Head Stability", "category_label": "Head Stability",
         "sub_metrics": [{"sim_pct": 65}, {"sim_pct": 70}]},
        {"category": "Lower Body Sequence", "category_label": "Lower Body Sequence",
         "sub_metrics": [{"sim_pct": 75}, {"sim_pct": 80}]},
    ],
    "drill_plan": {
        "categories": [
            {"priority": "P1", "title": "Hip Separation",
             "why_it_matters": "Builds the elastic torque that drives bat speed.",
             "drills": [
                 {"title": "Wall Hip Turns", "name": "Wall Hip Turns",
                  "sets": 3, "reps": 10, "frequency": "3x/week",
                  "why": "Trains hip-led rotation without compensation.",
                  "how": "Place ball against the wall with bat. Rotate hips fully before shoulders begin moving."},
                 {"title": "Resistance Band Hip Pull", "name": "Resistance Band Hip Pull",
                  "sets": 3, "reps": 12, "frequency": "2x/week",
                  "why": "Reinforces lower-body lead with load.",
                  "how": "Loop band around hips, drive forward into rotation while shoulders stay closed."},
             ]},
            {"priority": "P2", "title": "Head Stability",
             "why_it_matters": "Improves contact consistency.",
             "drills": [
                 {"title": "Quiet Eyes Tee", "name": "Quiet Eyes Tee",
                  "sets": 4, "reps": 8, "frequency": "3x/week",
                  "why": "Reinforces visual tracking through contact.",
                  "how": "Hit off tee with focus on tracking ball deep into contact zone."},
             ]},
        ]
    },
    "score_history": [
        {"score": 64, "date": "2026-04-12"},
        {"score": 68, "date": "2026-04-20"},
        {"score": 69, "date": "2026-04-28"},
        {"score": 71, "date": "2026-05-05"},
        {"score": 70, "date": "2026-05-12"},
        {"score": 72, "date": "2026-05-18"},
    ],
    "strengths": [
        {"category_label": "Bat Path", "sim_pct": 92},
        {"category_label": "Stride Distance", "sim_pct": 89},
        {"category_label": "Shoulder Load", "sim_pct": 86},
    ],
}


def _sample_prior(swing_number: int, score: int, date_str: str,
                   timestamp: str, ref_name: str = "Ronald Acuña Jr.",
                   sim_offsets: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Build a synthetic prior swing record that exercises the compare flow.

    `sim_offsets` lets us shift per-category similarity vs the current
    swing so the metric-delta table has real numbers to surface.
    """
    so = sim_offsets or {}
    def s(base: int, key: str) -> int:
        return max(0, min(100, base + so.get(key, 0)))
    return {
        "id": f"sample-prior-{swing_number}",
        "swing_number": swing_number,
        "timestamp": timestamp,
        "date_str": date_str,
        "score": score,
        "score_band_color": ("amber" if score < 80 else "green") if score >= 60 else "red",
        "score_band_label": "Strong Foundation" if score >= 60 else "Building",
        "reference": {"name": ref_name, "team": "Atlanta Braves",
                       "position": "OF", "style": "", "source": "auto"},
        "metric_table": {
            "Hip Mechanics": [
                {"label": "Hip rotation at foot plant",
                 "player_str": "40°", "ref_str": "55°",
                 "sim_pct": s(76, "hip_fp")},
                {"label": "Hip rotation at contact",
                 "player_str": "86°", "ref_str": "95°",
                 "sim_pct": s(92, "hip_ct")},
            ],
            "Separation": [
                {"label": "Peak hip-shoulder separation",
                 "player_str": "32°", "ref_str": "40°",
                 "sim_pct": s(85, "sep_peak")},
                {"label": "Separation at foot plant",
                 "player_str": "17°", "ref_str": "20°",
                 "sim_pct": s(90, "sep_fp")},
            ],
            "Timing": [
                {"label": "Total swing duration",
                 "player_str": "0.19s", "ref_str": "0.16s",
                 "sim_pct": s(88, "dur")},
                {"label": "Launch → contact",
                 "player_str": "0.11s", "ref_str": "0.10s",
                 "sim_pct": s(91, "lc")},
            ],
            "Lower Body": [
                {"label": "Re-extension",
                 "player_str": "13°", "ref_str": "15°",
                 "sim_pct": s(80, "reext")},
            ],
            "Stability": [
                {"label": "Total head drift",
                 "player_str": "2.3\"", "ref_str": "1.5\"",
                 "sim_pct": s(70, "head")},
            ],
        },
    }


# Rich prior-swing list used by the static preview (and by tests). The
# live production renderer always receives REAL history from saved
# storage — never this list.
SAMPLE_HISTORY: List[Dict[str, Any]] = [
    _sample_prior(6, 70, "May 12, 2026", "2026-05-12T13:05:00",
                   sim_offsets={"hip_fp": -8, "sep_peak": -5, "head": -6,
                                "lc": -6, "dur": -4, "reext": -7, "hip_ct": -3}),
    _sample_prior(5, 71, "May 5, 2026", "2026-05-05T12:20:00",
                   sim_offsets={"hip_fp": -6, "sep_peak": -4, "head": -5,
                                "lc": -4, "dur": -3, "reext": -5, "hip_ct": -2}),
    _sample_prior(4, 69, "April 28, 2026", "2026-04-28T15:55:00",
                   sim_offsets={"hip_fp": -10, "sep_peak": -7, "head": -8,
                                "lc": -8, "dur": -6, "reext": -9, "hip_ct": -5}),
    _sample_prior(3, 68, "April 20, 2026", "2026-04-20T14:10:00",
                   sim_offsets={"hip_fp": -12, "sep_peak": -9, "head": -10,
                                "lc": -10, "dur": -8, "reext": -11, "hip_ct": -7}),
    _sample_prior(2, 64, "April 12, 2026", "2026-04-12T17:00:00",
                   sim_offsets={"hip_fp": -16, "sep_peak": -12, "head": -14,
                                "lc": -14, "dur": -11, "reext": -15, "hip_ct": -11}),
]
