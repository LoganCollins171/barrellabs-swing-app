"""
BarrelLabs SwingAI — Pricing page (v2 editorial redesign).

Reached by routing `st.session_state["page"] = "pricing"` from app.py.
Concept: "BarrelLabs Pro · three sizes" — one product, three seat counts.
Visual language matches auth_screen.py / dashboard_v3 / training plan
/ swing_report_dashboard_preview: Instrument Serif italic display +
Geist sans + Geist Mono eyebrows, bone (#F4EFE6) on ink (#0A0B0E),
gold (#E8C170) emphasis, red (#E64530) accents, hairline borders +
generous whitespace.

PRESERVED CONTRACT (do NOT change without checking app.py):
  • Public functions:    render_pricing_page() · _streamlit_base_url()
  • Stripe success URL:  ?checkout=success&session_id={CHECKOUT_SESSION_ID}
  • Stripe cancel URL:   ?checkout=cancel
  • Same-tab redirect is BLACKLISTED — Stripe Checkout opens in a new
    tab via window.open(...) because Streamlit session_state doesn't
    survive a full same-tab navigation.
  • Session-state keys: pricing_billing_interval, _pending_checkout_url
  • Three plan ids: solo_pro, family_pro, coach_pro (upstream in
    entitlements / subscription_storage / stripe_client)

The page degrades gracefully when Stripe price ids aren't configured
(shows a 'Coming soon' disabled CTA instead of crashing).
"""

from __future__ import annotations

import textwrap

import streamlit as st

from bl_theme import inject_global_theme
from plan_pricing import (
    PLAN_PRICING,
    annual_savings_pct,
    annual_monthly_equivalent_cents,
    format_cents,
    stripe_price_id,
)
from entitlements import is_pro, _resolve_plan_id, plan_display_name
from subscription_storage import load_my_plan


# =====================================================================
#                       EDITORIAL CSS (v2)
# =====================================================================
# Tokens lifted verbatim from auth_screen.py + mock_dashboard_template.py
# so this page reads as a NATIVE part of the v4 editorial system. All
# selectors are namespaced `.pr-*` so they can't collide with anything
# else in the app. The CSS sits inside Streamlit's existing chrome —
# bl_theme.inject_global_theme() still runs to style the broader page,
# this just LAYERS the editorial language on top.

_PRICING_CSS = """
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --pr-bg:           #0A0B0E;
  --pr-bg-elev:      #11141A;
  --pr-bg-glass:     rgba(255,255,255,0.025);
  --pr-bg-glass-hi:  rgba(255,255,255,0.045);
  --pr-bone:         #F4EFE6;
  --pr-bone-dim:     #C8C4BB;
  --pr-gray-1:       #8B8E94;
  --pr-gray-2:       #565A62;
  --pr-line:         rgba(244,239,230,0.08);
  --pr-line-hi:      rgba(244,239,230,0.16);
  --pr-line-lo:      rgba(244,239,230,0.04);
  --pr-red:          #E64530;
  --pr-red-soft:     rgba(230,69,48,0.12);
  --pr-gold:         #E8C170;
  --pr-gold-deep:    #C9A350;
  --pr-gold-soft:    rgba(232,193,112,0.10);
  --pr-gold-line:    rgba(232,193,112,0.42);
  --pr-green:        #4AE38C;
  --pr-serif:        'Instrument Serif','Fraunces',Georgia,serif;
  --pr-sans:         'Geist',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;
  --pr-mono:         'Geist Mono','JetBrains Mono',ui-monospace,monospace;
  --pr-r:            14px;
  --pr-r-lg:         20px;
  --pr-r-pill:       100px;
}

/* ───── Ambient lighting (radial wash + grain) ───── */
.pr-bg {
    position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background:
      radial-gradient(ellipse 1300px 760px at 22% 20%, rgba(232,193,112,0.18) 0%, transparent 62%),
      radial-gradient(ellipse 1000px 700px at 80% 80%, rgba(230,69,48,0.11) 0%, transparent 60%),
      radial-gradient(ellipse 1600px 1000px at 50% 110%, rgba(244,239,230,0.025) 0%, transparent 70%);
}
.pr-grain {
    position: fixed; inset: 0; z-index: 1; pointer-events: none;
    opacity: 0.06; mix-blend-mode: overlay;
    background-image: url("data:image/svg+xml;utf8,<svg viewBox='0 0 220 220' xmlns='http://www.w3.org/2000/svg'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.55 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}

/* ───── Page chrome ───── */
.pr-wrap { position: relative; z-index: 2; max-width: 1280px; margin: 0 auto;
    padding: 0 28px 96px 28px;
    color: var(--pr-bone);
    font-family: var(--pr-sans); font-weight: 400;
}
.pr-back-row { margin: 12px 0 -4px 0; }

/* ───── Hero ───── */
.pr-hero { padding: 96px 0 72px 0; text-align: center; }
.pr-hero-eyebrow {
    font-family: var(--pr-mono); font-size: 11px; font-weight: 600;
    letter-spacing: 0.28em; text-transform: uppercase;
    color: var(--pr-gold);
    margin-bottom: 22px;
}
.pr-hero-title {
    font-family: var(--pr-serif);
    font-size: clamp(4.2rem, 8.8vw, 8.6rem);
    line-height: 0.98; letter-spacing: -0.028em;
    color: var(--pr-bone); font-weight: 400;
    margin: 0;
}
.pr-hero-title .ital {
    font-style: italic; color: var(--pr-gold);
}
.pr-hero-sub {
    font-family: var(--pr-serif); font-style: italic;
    font-size: clamp(1.15rem, 1.5vw, 1.42rem);
    font-weight: 400; line-height: 1.45;
    color: var(--pr-bone-dim);
    max-width: 44ch; margin: 28px auto 0;
    letter-spacing: -0.005em;
}

/* ───── Already-Pro thin gold underline (replaces the glass card) ───── */
.pr-already {
    margin: 0 auto 36px; max-width: 640px;
    padding: 12px 4px 14px;
    border-top: 1px solid var(--pr-gold-line);
    border-bottom: 1px solid var(--pr-gold-line);
    color: var(--pr-bone-dim);
    text-align: center;
    font-family: var(--pr-mono); font-size: 11px;
    letter-spacing: 0.16em; text-transform: uppercase;
}
.pr-already strong { color: var(--pr-gold); font-weight: 600; }

/* ───── Monthly/Annual toggle ───── */
.pr-toggle-wrap {
    display: flex; align-items: center; justify-content: center;
    gap: 18px; margin: 8px 0 44px 0;
}
.pr-toggle-pill {
    display: inline-flex; gap: 0;
    padding: 4px; border-radius: var(--pr-r-pill);
    background: var(--pr-bg-glass);
    border: 1px solid var(--pr-line);
}
.pr-save-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 14px; border-radius: var(--pr-r-pill);
    background: var(--pr-gold-soft);
    border: 1px solid var(--pr-gold-line);
    color: var(--pr-gold);
    font-family: var(--pr-mono); font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.16em; text-transform: uppercase;
    white-space: nowrap;
}

/* Restyle the Streamlit radio that powers the toggle */
.pr-toggle-wrap [data-testid="stRadio"] > div {
    flex-direction: row !important; gap: 0 !important;
    background: var(--pr-bg-glass);
    border: 1px solid var(--pr-line);
    border-radius: var(--pr-r-pill); padding: 4px;
}
.pr-toggle-wrap [data-testid="stRadio"] label {
    margin: 0 !important;
    padding: 8px 18px !important;
    border-radius: var(--pr-r-pill) !important;
    cursor: pointer;
    transition: background 0.18s ease, color 0.18s ease;
    color: var(--pr-bone-dim) !important;
    font-family: var(--pr-mono) !important;
    font-size: 11px !important; font-weight: 600 !important;
    letter-spacing: 0.16em; text-transform: uppercase;
}
.pr-toggle-wrap [data-testid="stRadio"] label > div:first-child { display: none !important; }
.pr-toggle-wrap [data-testid="stRadio"] label:has(input:checked) {
    background: var(--pr-bone); color: var(--pr-bg) !important;
}

/* ───── Plan cards ───── */
/* The middle card is widened to 1.12fr so the featured Solo plan
   (rendered as the middle column via family→solo→coach order) reads
   as the visual hero. */
.pr-grid {
    display: grid; grid-template-columns: 1fr 1.14fr 1fr; gap: 20px;
    margin-top: 8px;
    align-items: stretch;
}
@media (max-width: 960px) {
    .pr-grid { grid-template-columns: 1fr; gap: 24px; }
}

/* Soft entrance — every card fades up on page settle. Subtle. */
@keyframes pr-card-settle {
    from { opacity: 0; transform: translateY(14px); }
    to   { opacity: 1; transform: translateY(0); }
}

.pr-card {
    position: relative;
    padding: 38px 30px 28px;
    border: 1px solid var(--pr-line);
    border-radius: var(--pr-r-lg);
    background:
      radial-gradient(120% 60% at 50% 0%, rgba(232,193,112,0.045), transparent 60%),
      var(--pr-bg-glass);
    display: flex; flex-direction: column;
    transform-style: preserve-3d;
    will-change: transform;
    transition: transform 0.42s cubic-bezier(.21,.79,.31,1.02),
                border-color 0.28s ease,
                box-shadow 0.32s ease;
    animation: pr-card-settle 0.55s cubic-bezier(.21,.79,.31,1.02) both;
}
.pr-card:nth-child(1) { animation-delay: 0.04s; }
.pr-card:nth-child(2) { animation-delay: 0.12s; }
.pr-card:nth-child(3) { animation-delay: 0.20s; }
.pr-card:hover {
    transform: translateY(-6px);
    border-color: var(--pr-line-hi);
    box-shadow:
      0 24px 60px rgba(0,0,0,0.55),
      0 0 0 1px var(--pr-line-hi);
}
/* While JS is actively tilting the card on mousemove, switch to a
   snappier transition so the parallax feels live, not laggy. */
.pr-card.is-tilting {
    transition: transform 0.08s ease-out,
                border-color 0.28s ease,
                box-shadow 0.32s ease;
}

/* Featured card — bigger lift, outer gold glow, shimmer sweep on
   top edge, mouse-tracked spotlight (wired via JS below). */
.pr-card.is-featured {
    transform: translateY(-16px) scale(1.025);
    border-color: var(--pr-gold-line);
    background:
      radial-gradient(120% 60% at 50% 0%, rgba(232,193,112,0.16), transparent 60%),
      var(--pr-bg-elev);
    box-shadow:
      0 0 0 1px rgba(232,193,112,0.55),
      0 30px 80px -20px rgba(232,193,112,0.32),
      0 0 120px -30px rgba(232,193,112,0.22),
      0 22px 60px rgba(0,0,0,0.55);
}
.pr-card.is-featured:hover {
    transform: translateY(-20px) scale(1.025);
}
@keyframes pr-shimmer-sweep {
    0%   { background-position: -200% 0; }
    50%  { background-position: 200% 0;  }
    100% { background-position: 200% 0;  }
}
.pr-card.is-featured::after {
    content: ""; position: absolute; left: 0; right: 0; top: -1px;
    height: 2px; border-radius: 2px;
    background: linear-gradient(90deg,
        transparent 0%,
        var(--pr-gold) 50%,
        transparent 100%);
    background-size: 220% 100%;
    animation: pr-shimmer-sweep 3.8s ease-in-out infinite;
}
/* Cursor-tracked spotlight on every card (vars set by JS). Non-featured
   cards get a soft bone-tinted glow; featured upgrades to gold. */
.pr-card::before {
    content: ""; position: absolute; inset: 0;
    border-radius: inherit; pointer-events: none;
    background: radial-gradient(
        360px circle at var(--pr-mx, 50%) var(--pr-my, -120px),
        rgba(244,239,230,0.08), transparent 62%);
    opacity: 0; transition: opacity 0.32s ease;
    z-index: 2;
}
.pr-card.is-featured::before {
    background: radial-gradient(
        360px circle at var(--pr-mx, 50%) var(--pr-my, -120px),
        rgba(232,193,112,0.18), transparent 62%);
}
.pr-card:hover::before { opacity: 1; }

/* "Recommended" floating badge */
.pr-card-badge {
    position: absolute; top: -1px; left: 50%;
    transform: translate(-50%, -50%) translateZ(40px);
    padding: 7px 22px; border-radius: var(--pr-r-pill);
    background: var(--pr-gold); color: #1a1206;
    font-family: var(--pr-mono); font-size: 10.5px; font-weight: 700;
    letter-spacing: 0.20em; text-transform: uppercase;
    white-space: nowrap; z-index: 4;
    box-shadow:
      0 0 0 4px var(--pr-bg-elev),
      0 6px 18px -8px rgba(232,193,112,0.6);
}

/* Inner content layers — when the card tilts on mousemove,
   these get a slight translateZ so they parallax above the
   card surface, giving the parallax a real 3D feel rather
   than a flat skew. */
.pr-card .pr-card-name,
.pr-card .pr-price-row,
.pr-card .pr-price-equiv,
.pr-card .pr-price-save,
.pr-card .pr-card-cta-wrap {
    transform: translateZ(0);
    transition: transform 0.18s ease-out;
}
.pr-card.is-tilting .pr-card-name      { transform: translateZ(22px); }
.pr-card.is-tilting .pr-price-row      { transform: translateZ(34px); }
.pr-card.is-tilting .pr-price-equiv    { transform: translateZ(18px); }
.pr-card.is-tilting .pr-price-save     { transform: translateZ(18px); }
.pr-card.is-tilting .pr-card-cta-wrap  { transform: translateZ(24px); }

.pr-card-eyebrow {
    font-family: var(--pr-mono); font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.22em; text-transform: uppercase;
    color: var(--pr-gray-1);
    margin-bottom: 10px; position: relative; z-index: 3;
}
.pr-card.is-featured .pr-card-eyebrow { color: var(--pr-gold); }
.pr-card-name {
    font-family: var(--pr-serif); font-size: 2.15rem; font-weight: 400;
    line-height: 1; letter-spacing: -0.022em;
    color: var(--pr-bone);
    margin: 0 0 8px 0; position: relative; z-index: 3;
}
.pr-card-seats {
    font-family: var(--pr-sans); font-size: 0.88rem; font-weight: 400;
    color: var(--pr-bone-dim);
    margin-bottom: 28px; position: relative; z-index: 3;
}
.pr-card-seats strong { color: var(--pr-bone); font-weight: 500; }

/* Price: the hero of the card. Featured card gets a serif italic
   number with a bone→gold→deep-gold vertical gradient mask. The
   italic serif "9" has both a high ascender and a swash descender,
   so we give the price element generous padding + line-height +
   inline-block so the gradient mask doesn't clip the glyph. */
.pr-price-row {
    display: flex; align-items: baseline; gap: 10px;
    margin-bottom: 8px; position: relative; z-index: 3;
    overflow: visible;
}
.pr-price-big {
    display: inline-block;
    font-family: var(--pr-serif); font-style: italic;
    font-size: clamp(4.4rem, 6.2vw, 5.6rem);
    font-weight: 400;
    /* The italic Instrument Serif "9" has a swash descender that
       drops ~22% below the baseline. The text element's line-box
       must contain both ascender and descender, and the gradient
       text-fill needs explicit bottom padding so its mask doesn't
       crop the descender curl. */
    line-height: 1.4;
    letter-spacing: -0.045em; color: var(--pr-bone);
    padding: 0.10em 0.06em 0.30em 0;
    overflow: visible;
    /* Pulls the next sibling element back up to compensate for the
       generous bottom padding — visual rhythm stays tight. */
    margin-bottom: -0.22em;
}
.pr-card.is-featured .pr-price-big {
    background: linear-gradient(180deg,
        #F4EFE6 0%, #E8C170 55%, #C9A350 100%);
    -webkit-background-clip: text;
            background-clip: text;
    -webkit-text-fill-color: transparent;
            color: transparent;
}
.pr-price-period {
    font-family: var(--pr-mono); font-size: 12px; font-weight: 500;
    color: var(--pr-bone-dim); letter-spacing: 0.12em;
    text-transform: lowercase;
}
.pr-price-equiv {
    font-family: var(--pr-sans); font-size: 0.84rem;
    color: var(--pr-gray-1); margin-bottom: 4px;
    position: relative; z-index: 3;
}
.pr-price-save {
    font-family: var(--pr-mono); font-size: 9.5px; font-weight: 600;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--pr-gold);
    margin-bottom: 28px; min-height: 12px;
    position: relative; z-index: 3;
}

.pr-features {
    list-style: none; padding: 0; margin: 0 0 28px 0;
    border-top: 1px solid var(--pr-line-lo);
    padding-top: 22px;
    flex-grow: 1;
}
.pr-features li {
    position: relative; padding: 7px 0 7px 22px;
    color: var(--pr-bone-dim); font-size: 0.93rem; line-height: 1.45;
}
.pr-features li::before {
    content: "✓"; position: absolute; left: 0; top: 7px;
    color: var(--pr-gold);
    font-size: 0.86rem; font-weight: 700;
}
.pr-card.is-featured .pr-features li { color: var(--pr-bone); }

/* The actual Streamlit button inside each card column — styled to
   match the card design. */
.pr-card-cta-wrap [data-testid="stButton"] button {
    width: 100%;
    padding: 14px 18px !important;
    border-radius: var(--pr-r-pill) !important;
    background: var(--pr-bone) !important;
    color: var(--pr-bg) !important;
    border: none !important;
    font-family: var(--pr-mono) !important;
    font-size: 11px !important; font-weight: 700 !important;
    letter-spacing: 0.18em !important; text-transform: uppercase !important;
    transition: background 0.22s ease, transform 0.22s ease, box-shadow 0.22s ease !important;
    box-shadow: 0 12px 28px -16px rgba(244,239,230,0.40),
                inset 0 -1px 0 rgba(0,0,0,0.08) !important;
}
.pr-card-cta-wrap [data-testid="stButton"] button:hover {
    background: var(--pr-gold) !important;
    color: #1a1206 !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 18px 40px -14px rgba(232,193,112,0.45),
                inset 0 -1px 0 rgba(0,0,0,0.12) !important;
}
.pr-card-cta-wrap.is-featured [data-testid="stButton"] button {
    background: var(--pr-gold) !important;
    color: #1a1206 !important;
    box-shadow: 0 18px 40px -14px rgba(232,193,112,0.55),
                inset 0 -1px 0 rgba(0,0,0,0.12) !important;
}
.pr-card-cta-wrap.is-featured [data-testid="stButton"] button:hover {
    background: var(--pr-bone) !important;
    color: var(--pr-bg) !important;
}
.pr-card-cta-wrap [data-testid="stButton"] button:disabled,
.pr-card-cta-wrap [data-testid="stButton"] button[disabled] {
    background: transparent !important;
    color: var(--pr-gray-1) !important;
    border: 1px solid var(--pr-line) !important;
    box-shadow: none !important;
    cursor: default !important;
}
/* ───── Refresh-my-plan strip ───── */
.pr-refresh-wrap {
    margin: 38px 0 0 0;
    padding: 18px 22px;
    border: 1px solid var(--pr-line);
    border-radius: var(--pr-r);
    background: var(--pr-bg-glass);
    color: var(--pr-bone-dim);
    font-size: 0.93rem; text-align: center;
}

/* ───── Reassurance line — single italic-serif statement ───── */
.pr-reassure-rule {
    margin: 72px auto 0;
    max-width: 760px;
    border-top: 1px solid var(--pr-line);
    border-bottom: 1px solid var(--pr-line);
    padding: 32px 0;
    text-align: center;
}
.pr-reassure-line {
    font-family: var(--pr-serif);
    font-size: clamp(1.3rem, 2vw, 1.7rem);
    font-weight: 400; line-height: 1.5; letter-spacing: -0.01em;
    color: var(--pr-bone); margin: 0;
}
.pr-reassure-line em {
    font-style: italic; color: var(--pr-gold);
}

/* ───── FAQ ───── */
.pr-faq-wrap {
    margin: 88px auto 0; max-width: 800px;
}
.pr-faq-eyebrow {
    font-family: var(--pr-mono); font-size: 11px; font-weight: 600;
    letter-spacing: 0.24em; text-transform: uppercase;
    color: var(--pr-gold);
    text-align: center; margin-bottom: 12px;
}
.pr-faq-title {
    font-family: var(--pr-serif);
    font-size: clamp(2.2rem, 3.6vw, 3.2rem);
    line-height: 1.05; letter-spacing: -0.022em;
    color: var(--pr-bone); font-weight: 400;
    text-align: center; margin: 0 0 42px 0;
}
.pr-faq-title .ital { font-style: italic; color: var(--pr-gold); }
.pr-faq-wrap details {
    border-top: 1px solid var(--pr-line);
    padding: 18px 4px;
}
.pr-faq-wrap details:last-child { border-bottom: 1px solid var(--pr-line); }
.pr-faq-wrap summary {
    list-style: none; cursor: pointer;
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px;
    color: var(--pr-bone); font-family: var(--pr-sans);
    font-size: 1.05rem; font-weight: 500;
}
.pr-faq-wrap summary::-webkit-details-marker { display: none; }
.pr-faq-wrap summary::after {
    content: "+"; color: var(--pr-bone-dim);
    font-family: var(--pr-mono); font-size: 1.5rem; font-weight: 300;
    width: 22px; text-align: center;
    transition: transform 0.22s ease, color 0.22s ease;
}
.pr-faq-wrap details[open] summary::after {
    content: "−"; color: var(--pr-gold);
}
.pr-faq-wrap details > p {
    margin: 12px 0 4px 0; color: var(--pr-bone-dim);
    line-height: 1.6; font-size: 0.96rem; max-width: 64ch;
}

/* ───── Beta strip — clean hairline rule, no dashed Bootstrap callout ───── */
.pr-beta {
    margin: 40px auto 0;
    max-width: 720px;
    padding: 22px 4px;
    border-top: 1px solid var(--pr-line);
    color: var(--pr-bone-dim);
    font-family: var(--pr-mono); font-size: 11px;
    letter-spacing: 0.16em; text-transform: uppercase;
    text-align: center;
}
.pr-beta strong { color: var(--pr-bone); font-weight: 600; }
.pr-beta em { color: var(--pr-gold); font-style: normal; font-weight: 600; }

/* ───── Back-to-Dashboard button overrides ───── */
[data-testid="stButton"]:has(button:contains("Back to Dashboard")) button {
    background: transparent !important;
    color: var(--pr-bone-dim) !important;
    border: none !important;
}
</style>
"""


# =====================================================================
#                       FEATURE COPY
# =====================================================================
# UX copy only — the actual entitlement gating lives in entitlements.py.
# Refined for the v2 redesign to lead with what the user GETS, not what
# they CAN do. Active voice, no jargon.

_FEATURES_BASE = [
    "Unlimited swing analyses",
    "Personalized drill plan",
    "Swing video saved with every analysis",
    "Side-by-side swing comparison",
    "PDF report export",
    "Full Development Tracker (XP, streaks, achievements)",
    "Full MLB comp library",
    "Rewards Roadmap — incl. limited-edition hoodie at 180 days",
]

_FEATURES_FAMILY_EXTRAS = [
    "Up to 4 family member accounts",
    "Separate swing history per member",
]

_FEATURES_COACH_EXTRAS = [
    "Up to 20 player rosters",
    "Read-only views of each player's swings",
    "Priority support",
]

_PLAN_FEATURES = {
    "solo_pro":   _FEATURES_BASE,
    "family_pro": _FEATURES_BASE + _FEATURES_FAMILY_EXTRAS,
    "coach_pro":  _FEATURES_BASE + _FEATURES_COACH_EXTRAS,
}


# =====================================================================
#                       FAQ COPY
# =====================================================================

_FAQ = [
    (
        "What's the difference between Solo, Family, and Coach?",
        "Solo Pro is one player — your account, your swing history, your "
        "drill plan. Family Pro is the same Pro experience but with four "
        "separate accounts (one parent, three kids, your dog who hits "
        "lefty — your call). Coach Pro lets a coach roster up to 20 "
        "players, each with their own private swing history and a "
        "read-only roll-up view for you. All three tiers get every Pro "
        "feature unlocked.",
    ),
    (
        "Can I cancel anytime?",
        "Yes. Cancel from Account Settings → Subscription in two clicks. "
        "Your Pro access stays active through the end of your paid period "
        "(no proration drama), and your swing history stays in your "
        "account whether you come back or not.",
    ),
    (
        "Do you offer refunds?",
        "100% refund within 7 days of your first charge, no questions "
        "asked — email us from the address on file. After 7 days we "
        "pro-rate refunds case by case if you reach out.",
    ),
    (
        "What equipment do I need?",
        "A smartphone or laptop camera, that's it. Film one swing from "
        "the side at 30–60 fps, upload through the app, get your "
        "biomechanical report. No HitTrax, no Rapsodo, no batting cage "
        "required — though the analysis gets sharper with slow-motion clips.",
    ),
    (
        "Is my swing data private? Can coaches see my data without permission?",
        "Yes, your data is private. Coach Pro accounts can only see "
        "players who actively join their roster — there's no one-way "
        "visibility. You can leave any roster at any time and your "
        "history stays yours.",
    ),
    (
        "Do you offer team or program discounts?",
        "Coach Pro covers most travel programs and HS teams. For larger "
        "rollouts (D1 programs, academies, multi-team orgs), drop us a "
        "line — we'll work something out.",
    ),
]


# =====================================================================
#                       ENTRY POINT
# =====================================================================

def render_pricing_page() -> None:
    """Streamlit page entry point. Routed from app.py via
    `st.session_state["page"] = "pricing"`."""
    # Keep bl_theme so the broader Streamlit chrome (sidebar, top bar) is
    # consistent. Then layer the editorial pricing CSS on top.
    inject_global_theme()
    # Inject the <link> font imports and the <style> block as TWO separate
    # markdown calls. In one string the leading <link> starts a CommonMark
    # "type 6" HTML block that TERMINATES at the first blank line inside the
    # CSS — leaking everything after it onto the page as raw text, with the
    # styles never applying (this was the "page shows raw CSS / looks basic"
    # bug). Starting the second call with <style> makes it a "type 1" block
    # that runs to </style> regardless of blank lines — same as the family
    # dashboard CSS, which always worked because it starts with <style>.
    _css_head, _css_style = _PRICING_CSS.split("<style>", 1)
    st.markdown(_css_head, unsafe_allow_html=True)
    st.markdown("<style>" + _css_style, unsafe_allow_html=True)
    # Ambient lighting + grain layers behind everything (fixed-position).
    st.markdown('<div class="pr-bg"></div><div class="pr-grain"></div>',
                unsafe_allow_html=True)
    st.markdown('<div class="pr-wrap">', unsafe_allow_html=True)

    # ── Back nav ─────────────────────────────────────────────────────
    st.markdown('<div class="pr-back-row">', unsafe_allow_html=True)
    back_col, _ = st.columns([1, 5])
    with back_col:
        if st.button("← Back to Dashboard", key="pricing_back_btn"):
            st.session_state["page"] = "dashboard"
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Hero ─────────────────────────────────────────────────────────
    st.markdown(textwrap.dedent("""
    <div class="pr-hero">
      <div class="pr-hero-eyebrow">§ 03 · Pricing</div>
      <h1 class="pr-hero-title">BarrelLabs&nbsp;Pro. <span class="ital">Three sizes.</span></h1>
      <p class="pr-hero-sub">
        One product. Three seat counts. Built for serious hitters and the
        coaches who train them. Unlimited swings, personalized drills,
        the full MLB comp library — every Pro plan, every feature.
      </p>
    </div>
    """), unsafe_allow_html=True)

    # ── Already-Pro banner ──────────────────────────────────────────
    snap = load_my_plan()
    on_pro = is_pro(snap)
    if on_pro:
        current = plan_display_name(_resolve_plan_id(snap))
        st.markdown(
            f"""
            <div class="pr-already">
              <strong>You're on {current}.</strong>
              &nbsp;Manage your subscription from Account Settings.
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Monthly / Annual toggle (with gold "Save 45%" pill) ─────────
    interval_key = "pricing_billing_interval"
    if interval_key not in st.session_state:
        st.session_state[interval_key] = "annual"

    # Compute the savings percent for the cheapest plan (used as the
    # global "save X%" pill — annual_savings_pct returns a non-negative
    # int and we default to 45 if anything's misconfigured).
    try:
        _solo_savings = annual_savings_pct("solo_pro")
    except Exception:
        _solo_savings = 45
    if _solo_savings <= 0:
        _solo_savings = 45

    t_left, t_mid, t_right = st.columns([1, 2, 1])
    with t_mid:
        st.markdown('<div class="pr-toggle-wrap">', unsafe_allow_html=True)
        # Streamlit radio inside the pr-toggle-wrap div — CSS turns it
        # into a pill toggle.
        choice = st.radio(
            label="Billing interval",
            options=["Monthly", "Annual"],
            index=0 if st.session_state[interval_key] == "monthly" else 1,
            horizontal=True,
            key="pricing_interval_radio",
            label_visibility="collapsed",
        )
        st.session_state[interval_key] = "monthly" if choice == "Monthly" else "annual"
        st.markdown(
            f'<span class="pr-save-pill">Save {_solo_savings}% · 2 months free</span>',
            unsafe_allow_html=True,
        )
        st.markdown('</div>', unsafe_allow_html=True)

    interval = st.session_state[interval_key]

    # ── Plan cards ──────────────────────────────────────────────────
    # Solo Pro is featured (conversion audit: first-time converters
    # overwhelmingly want individual, not multi-seat). The order is
    # FAMILY → SOLO → COACH so the featured Solo card sits in the
    # visual center column where the eye lands.
    col_fam, col_solo, col_coach = st.columns([1, 1.14, 1], gap="large")
    _render_plan_card(col_fam,   "family_pro", interval, featured=False)
    _render_plan_card(col_solo,  "solo_pro",   interval, featured=True)
    _render_plan_card(col_coach, "coach_pro",  interval, featured=False)

    # Cursor-tracked spotlight + 3D parallax tilt on every plan card.
    # Re-binds every render because Streamlit recreates the DOM on
    # every interaction. Featured card keeps its baseline lift/scale,
    # non-featured cards tilt from rest. Inline transform overrides
    # CSS during mousemove and clears on mouseleave so the CSS
    # :hover/baseline state takes back over smoothly.
    st.markdown("""
    <script>
      (function() {
          const cards = window.parent.document.querySelectorAll('.pr-card');
          cards.forEach(card => {
              if (card.dataset._prTracked === '1') return;
              card.dataset._prTracked = '1';
              const isFeatured = card.classList.contains('is-featured');
              const baseScale  = isFeatured ? 1.025 : 1.0;
              const restY      = isFeatured ? -16 : 0;
              const hoverY     = isFeatured ? -20 : -6;

              card.addEventListener('mouseenter', () => {
                  card.classList.add('is-tilting');
              });
              card.addEventListener('mousemove', (e) => {
                  const r = card.getBoundingClientRect();
                  const localX = e.clientX - r.left;
                  const localY = e.clientY - r.top;
                  const px = localX / r.width;
                  const py = localY / r.height;
                  // tilt range: ±7° on Y (left/right), ±5° on X (up/down)
                  const ry = (px - 0.5) * 14;
                  const rx = (0.5 - py) * 10;
                  card.style.setProperty('--pr-mx', localX + 'px');
                  card.style.setProperty('--pr-my', localY + 'px');
                  card.style.transform =
                      'translateY(' + hoverY + 'px) ' +
                      'scale(' + baseScale + ') ' +
                      'perspective(1100px) ' +
                      'rotateX(' + rx.toFixed(2) + 'deg) ' +
                      'rotateY(' + ry.toFixed(2) + 'deg)';
              });
              card.addEventListener('mouseleave', () => {
                  card.classList.remove('is-tilting');
                  card.style.transform = '';
                  card.style.setProperty('--pr-mx', '50%');
                  card.style.setProperty('--pr-my', '-120px');
              });
          });
      })();
    </script>
    """, unsafe_allow_html=True)

    # ── "Refresh my plan" CTA (only when Stripe checkout is pending) ─
    if st.session_state.get("_pending_checkout_url"):
        _url = st.session_state["_pending_checkout_url"]
        st.markdown('<div class="pr-refresh-wrap">', unsafe_allow_html=True)
        # The real, visible checkout link — a direct click opens Stripe in a new
        # tab (no popup block) and keeps THIS tab's sign-in alive.
        st.markdown(
            f"""
<div style="text-align:center; margin-bottom:16px;">
  <a href="{_url}" target="_blank" rel="noopener noreferrer"
     style="display:inline-block; padding:14px 32px; border-radius:100px;
            background:#E8C170; color:#1a1206; font-weight:700;
            font-family:'Geist Mono','JetBrains Mono',monospace; font-size:13px;
            letter-spacing:0.10em; text-transform:uppercase; text-decoration:none;
            box-shadow:0 12px 30px -12px rgba(232,193,112,0.6);">
    Continue to secure Stripe checkout →
  </a>
  <div style="margin-top:11px; color:#C8C4BB; font-size:0.9rem;
              font-family:'Geist', system-ui, sans-serif;">
    Opens in a new tab so your sign-in stays alive here. After you pay, come
    back and click <em style="color:#E8C170;">refresh my plan</em> below.
  </div>
</div>
            """,
            unsafe_allow_html=True,
        )
        rc1, rc2, _rc3 = st.columns([1.5, 1.5, 3], gap="small")
        if rc1.button(
            "✓ I've completed payment — refresh my plan",
            type="primary", width="stretch",
            key="bl_refresh_plan_after_checkout",
        ):
            try:
                from subscription_storage import (
                    invalidate_my_plan_cache, load_my_plan as _lmp,
                )
                invalidate_my_plan_cache()
                _lmp(force_refresh=True)
                st.session_state.pop("_pending_checkout_url", None)
                st.success(
                    "Plan refreshed. If you don't see Pro yet, give the "
                    "webhook a few more seconds and click again.",
                    icon="🔄",
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Couldn't refresh plan: {exc}")
        if rc2.button(
            "Cancel — I didn't pay", width="stretch",
            key="bl_cancel_pending_checkout",
        ):
            st.session_state.pop("_pending_checkout_url", None)
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Reassurance line ───────────────────────────────────────────
    st.markdown("""
    <div class="pr-reassure-rule">
      <p class="pr-reassure-line">
        Refund within 7 days. <em>Cancel in two clicks.</em>
        Your swings stay yours.
      </p>
    </div>
    """, unsafe_allow_html=True)

    # ── FAQ ──────────────────────────────────────────────────────────
    st.markdown('<div class="pr-faq-wrap">', unsafe_allow_html=True)
    st.markdown("""
      <div class="pr-faq-eyebrow">§ 04 · Common questions</div>
      <h2 class="pr-faq-title">Before you <span class="ital">commit.</span></h2>
    """, unsafe_allow_html=True)
    for question, answer in _FAQ:
        st.markdown(
            f"""
            <details>
              <summary>{question}</summary>
              <p>{answer}</p>
            </details>
            """,
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # ── Beta code strip ─────────────────────────────────────────────
    st.markdown("""
    <div class="pr-beta">
      Got a <strong>BarrelLabs beta code</strong>? Redeem it in
      <em>Account Settings → Subscription</em> for 30 days of full Pro
      access — no card required.
    </div>
    """, unsafe_allow_html=True)

    # Close .pr-wrap
    st.markdown('</div>', unsafe_allow_html=True)


# =====================================================================
#                       PLAN CARDS
# =====================================================================

def _render_plan_card(col, plan_id: str, interval: str, *, featured: bool):
    """Render one plan card into the given column container."""
    cfg = PLAN_PRICING.get(plan_id) or {}
    name = cfg.get("name") or plan_id
    seats = cfg.get("seats") or 1

    if interval == "monthly":
        price_cents = cfg.get("monthly_cents") or 0
        period_label = "/mo"
        equiv_line = ""
        save_line = ""
    else:
        price_cents = cfg.get("annual_cents") or 0
        period_label = "/yr"
        equiv = annual_monthly_equivalent_cents(plan_id)
        equiv_line = f"{format_cents(equiv)}/mo billed annually" if equiv else ""
        pct = annual_savings_pct(plan_id)
        save_line = f"↗ Save {pct}% vs monthly" if pct > 0 else ""

    features_html = "".join(
        f"<li>{f}</li>" for f in _PLAN_FEATURES.get(plan_id, [])
    )

    # Seats microcopy
    if seats <= 1:
        seats_line = "For <strong>1 player</strong>"
        eyebrow = "Solo · 1 seat"
    elif plan_id == "family_pro":
        seats_line = f"Up to <strong>{seats} family members</strong>"
        eyebrow = f"Family · {seats} seats"
    else:
        seats_line = f"Up to <strong>{seats} players</strong>"
        eyebrow = f"Coach · {seats} seats"

    badge_html = ('<div class="pr-card-badge">Recommended</div>'
                  if featured else "")

    with col:
        st.markdown(
            textwrap.dedent(f"""
            <div class="pr-card {'is-featured' if featured else ''}">
              {badge_html}
              <div class="pr-card-eyebrow">{eyebrow}</div>
              <div class="pr-card-name">{name}</div>
              <div class="pr-card-seats">{seats_line}</div>
              <div class="pr-price-row">
                <span class="pr-price-big">{format_cents(price_cents)}</span>
                <span class="pr-price-period">{period_label}</span>
              </div>
              <div class="pr-price-equiv">{equiv_line}</div>
              <div class="pr-price-save">{save_line}</div>
              <ul class="pr-features">{features_html}</ul>
              <div class="pr-card-cta-anchor"></div>
            </div>
            """).strip(),
            unsafe_allow_html=True,
        )
        # CTA button lives outside the static HTML block because it's a
        # real Streamlit widget. The wrapping div lets us style it via
        # CSS while keeping the underlying st.button interaction model.
        cta_class = "pr-card-cta-wrap is-featured" if featured else "pr-card-cta-wrap"
        st.markdown(f'<div class="{cta_class}">', unsafe_allow_html=True)
        _render_upgrade_button(plan_id, interval, featured=featured)
        st.markdown('</div>', unsafe_allow_html=True)


# BL-004C (3): Coach Pro is sold at $79.99/mo with advertised features
# (20-player roster, read-only coach views of each player's swing) that
# have no implementation anywhere in this codebase — verified in BL-004B:
# the only roster/seat infrastructure that exists (family_storage.py /
# family_dashboard.py) checks exclusively for `is_family_pro_member`, with
# no reference to coach_pro at all. A new Coach Pro subscriber today pays
# for a roster feature they cannot actually use.
#
# This is a narrowly scoped, reversible, application-level safeguard: it
# blocks NEW purchases/switches into coach_pro specifically (Solo and
# Family are completely untouched — see the plan_id check below), and does
# NOT touch Stripe prices/products/webhooks, does NOT cancel or alter any
# existing subscription, and does NOT affect a user who is already on
# Coach Pro (their "Current plan" button still renders normally, checked
# above this gate). Reverting is a one-line flip of this constant.
#
# Known gap this does NOT close (see BL-004C report): the Billing /
# Player Settings "redeem a beta code" flow (subscription_storage.py
# redeem_beta_code) calls a Supabase RPC that is the sole authority on
# which plan a given code grants — this Python layer never sees that
# mapping before the RPC runs, so this flag cannot inspect or block a
# beta code that happens to grant coach_pro. That path needs either a
# live-DB check (out of this task's bounds — no Supabase schema/RPC
# access was used or requested) or for no coach_pro codes to be minted
# while this flag is on (mint_beta_code.py was separately hardened to
# refuse --plan coach_pro, as a local, non-infra, reversible companion
# fix — see that file).
_COACH_PRO_SALES_PAUSED = True
_COACH_PRO_PAUSED_MESSAGE = (
    "Coach Pro's roster and player-viewing features aren't built yet, so "
    "we've paused new signups for this plan rather than sell something "
    "that doesn't work. Solo and Family plans are unaffected. Already on "
    "Coach Pro? Nothing changes for you — reach out if you have questions."
)


def _render_upgrade_button(plan_id: str, interval: str, *, featured: bool):
    """Render the per-card CTA button. Handles every failure mode:
        - already on this exact plan          → 'Current plan' disabled
        - coach_pro while sales are paused     → disabled, honest copy
        - no Stripe price id configured       → 'Coming soon' disabled
        - happy path                           → opens Stripe in new tab
    """
    snap = load_my_plan()
    current_plan_id = _resolve_plan_id(snap)
    on_pro = is_pro(snap)

    btn_key = f"upgrade_{plan_id}_{interval}"

    if on_pro and current_plan_id == plan_id:
        st.button("✓ Current plan", key=btn_key, disabled=True, width="stretch")
        return

    if plan_id == "coach_pro" and _COACH_PRO_SALES_PAUSED:
        st.button(
            "Signups paused",
            key=btn_key, disabled=True, width="stretch",
            help=_COACH_PRO_PAUSED_MESSAGE,
        )
        return

    price_id = stripe_price_id(plan_id, interval)
    if not price_id:
        st.button(
            "Coming soon",
            key=btn_key, disabled=True, width="stretch",
            help="Checkout for this plan isn't wired up yet. Set the "
                 "Stripe price ID in secrets.toml.",
        )
        return

    # WHOOP-style CTA copy: tier-specific, action-oriented.
    cta_label = {
        "solo_pro":   "Start with Solo",
        "family_pro": "Start with Family",
        "coach_pro":  "Start with Coach",
    }.get(plan_id, "Upgrade now")
    # If already on a Pro plan, switching plans uses a different label.
    if on_pro:
        cta_label = f"Switch to {plan_display_name(plan_id)}"

    if st.button(
        cta_label,
        key=btn_key,
        type=("primary" if featured else "secondary"),
        width="stretch",
    ):
        _start_checkout(plan_id, interval)


# =====================================================================
#                       STRIPE CHECKOUT FLOW
# =====================================================================
# PRESERVED VERBATIM from the previous version. Do not change:
#   - success_url must include literal {CHECKOUT_SESSION_ID}
#   - cancel_url must use ?checkout=cancel
#   - Open Stripe in a NEW tab via window.open — same-tab redirect
#     destroys Streamlit session_state
#   - No st.stop() after redirect (the page keeps rendering so user
#     sees the 'refresh my plan' CTA)

def _start_checkout(plan_id: str, interval: str) -> None:
    """Create a Stripe Checkout session and open it in a new tab."""
    # BL-004C (3): defense-in-depth. The button that calls this already
    # returns early for coach_pro while _COACH_PRO_SALES_PAUSED is True
    # (see _render_upgrade_button) — this second check means a future
    # caller can't bypass the pause by calling _start_checkout directly.
    if plan_id == "coach_pro" and _COACH_PRO_SALES_PAUSED:
        st.error(_COACH_PRO_PAUSED_MESSAGE)
        return
    try:
        # Lazy import — keeps pricing.py importable when Stripe SDK is
        # missing (e.g. a CI environment without secrets.toml).
        from stripe_client import create_checkout_session
    except ImportError as exc:
        st.error(f"Checkout isn't available yet: {exc}")
        return

    base = _streamlit_base_url()
    success_url = f"{base}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url  = f"{base}?checkout=cancel"

    try:
        url = create_checkout_session(
            plan_id=plan_id,
            interval=interval,
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except ValueError as ve:
        st.error(str(ve))
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't start checkout: {exc}")
        return

    # We do NOT navigate the current tab — Streamlit's session state (incl.
    # auth) doesn't survive a full same-tab redirect. Stash the URL and rerun;
    # the pending-checkout block below renders a REAL, visible link the user
    # clicks. (The old approach used <script>window.open()</script> via
    # st.markdown — but st.markdown STRIPS <script>, so it never ran, and the
    # fallback link was hidden behind display:none. There was no way to reach
    # Stripe. A direct user click on a real <a target=_blank> opens a new tab
    # with no popup block.)
    try:
        import analytics
        from auth import current_profile
        _p = current_profile() or {}
        analytics.track("checkout_started", _p.get("user_id") or _p.get("id"),
                        plan_id=plan_id, interval=interval)
    except Exception:
        pass
    st.session_state["_pending_checkout_url"] = url
    st.rerun()


def _streamlit_base_url() -> str:
    """Best-effort guess at the public URL of this Streamlit app.

    PRESERVED — imported by app.py for the Stripe billing portal
    return_url (app.py:2578, app.py:2857). Same signature, same return.
    """
    try:
        host = st.context.headers.get("Host") or "localhost:8501"
        proto = (
            st.context.headers.get("X-Forwarded-Proto")
            or ("https" if "localhost" not in host else "http")
        )
        return f"{proto}://{host}/"
    except Exception:
        return "http://localhost:8501/"


__all__ = ["render_pricing_page", "_streamlit_base_url"]
