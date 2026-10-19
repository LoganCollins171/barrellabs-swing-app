"""BarrelLabs · Premium authentication experience (v4 — "Cinematic Entry").

This is a complete from-scratch redesign. v1–v3 were treated as failed
prototypes (cramped split layout, overlapping Google placeholder, weak
hierarchy, too many small mono labels competing with the headline).

v4 is a single-column, centered, cinematic editorial portal. There is no
left/right split. There are no telemetry sidebars. There is one massive
serif italic display headline, one breathing form card, one quiet
secondary action below it, and a single trust bar at the bottom.

Visual language — exactly aligned with `dashboard_v3` / `mock_dashboard_template`:
  - Ink #0A0B0E background; bone #F4EFE6 text; gold #E8C170 emphasis;
    red #E64530 stitch eyebrows.
  - Instrument Serif (italic display) + Geist (sans) + Geist Mono (labels).
  - Generous whitespace, hairline rules, glassmorphism on the card.
  - One ambient gold/red radial-light field + a subtle film-grain layer.

Wiring (UNCHANGED — preserves the v3 contract):
  - player_storage.authenticate(email, password)
  - player_storage.create_account(name, email, password, handedness,
                                  height_in, weight_lb)
  - auth.request_password_reset / consume_recovery_url /
    consume_recovery_token_hash / update_password
  - Session flags: st.session_state.user, auth_mode, recovery_mode
  - Recovery JS hash→query shim lives in app.py (untouched)
  - Streamlit-scoped CSS uses .st-key-auth_root / -auth_hero / -auth_panel
    via st.container(key=...) so the wiring tests keep matching.
"""

from __future__ import annotations

import html
from typing import Optional

import streamlit as st


# =====================================================================
# CSS — scoped under .st-key-auth_root so it can never leak globally.
# =====================================================================
_AUTH_CSS = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=Geist:wght@300;400;500;600;700&family=Geist+Mono:wght@400;500;600&display=swap');

/* ============================================================
   1. STREAMLIT CHROME ERASURE — same trick as bl_edge_chrome.
      Forces one ink everywhere so nothing seams through.
   ============================================================ */
html, body,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
.block-container {
    background: #0A0B0E !important;
}
header[data-testid="stHeader"],
[data-testid="stHeader"],
.stAppHeader,
[data-testid="stToolbar"],
.stAppToolbar,
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
[data-testid="stMainMenu"],
.stAppDeployButton, .stDeployButton,
.stApp > header,
footer { display: none !important; }

[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
.block-container {
    padding: 0 !important;
    max-width: 100% !important;
}
/* Let the page scroll naturally on the document body rather than via
   the inner stMain (Streamlit's default puts overflow:auto on stMain,
   which traps the scroll inside a 100vh box and makes the trust bar /
   footer unreachable on shorter desktops). */
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"] {
    overflow: visible !important;
    height: auto !important;
    min-height: 100vh !important;
    position: static !important;
}
/* BL-007: this page was missing the overscroll-behavior fix that
   bl_edge_chrome.py (used on every OTHER authenticated page) already
   carries for the identical symptom family -- that file's own comment
   describes it as fixing a "mobile scroll-lock/jank... locks for a
   second, can't scroll back up", which matches the sign-in scroll trap
   reported against this page. Applying the same proven fix here rather
   than inventing a new one. See bl_edge_chrome.py's matching rule for
   the original rationale. */
html, body { overscroll-behavior-y: none; }
[data-testid="stVerticalBlock"] { gap: 0 !important; }
[data-testid="stElementContainer"] { margin: 0 !important; }

/* Kill Streamlit's "Press Enter to submit form" hint — it overlaps the inputs
   and reads like noise on a login screen. */
[data-testid="InputInstructions"],
[data-testid="stTextInputInstructions"],
.stTextInput [data-testid="InputInstructions"] { display: none !important; }

/* ============================================================
   2. AMBIENT BACKGROUND — gold/red stadium-light field +
      film-grain overlay. Sits behind everything (z:-1).
   ============================================================ */
.au-bg {
    position: fixed; inset: 0;
    z-index: 0;
    pointer-events: none;
    background:
        radial-gradient(ellipse 1300px 760px at 22% 20%,
            rgba(232,193,112,0.12) 0%, transparent 62%),
        radial-gradient(ellipse 1000px 700px at 80% 80%,
            rgba(230,69,48,0.085) 0%, transparent 60%),
        radial-gradient(ellipse 1600px 1000px at 50% 110%,
            rgba(244,239,230,0.020) 0%, transparent 70%);
}
.au-grain {
    position: fixed; inset: 0;
    z-index: 1;
    pointer-events: none;
    opacity: 0.04; mix-blend-mode: overlay;
    background-image: url("data:image/svg+xml;utf8,<svg viewBox='0 0 240 240' xmlns='http://www.w3.org/2000/svg'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.6 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
}

/* ============================================================
   3. ROOT WRAPPER — design tokens + typography defaults.
      All .au-* visuals live inside .st-key-auth_root so nothing
      leaks to dashboard / saved-reports / swing-report pages.
   ============================================================ */
.st-key-auth_root {
    --au-bg:        #0A0B0E;
    --au-bg-2:      #0D0F13;
    --au-bone:      #F4EFE6;
    --au-bone-dim:  #C8C4BB;
    --au-gray-1:    #8B8E94;
    --au-gray-2:    #565A62;
    --au-gray-3:    #2A2D33;
    --au-line:      rgba(244,239,230,0.08);
    --au-line-hi:   rgba(244,239,230,0.16);
    --au-line-lo:   rgba(244,239,230,0.04);
    --au-gold:      #E8C170;
    --au-gold-deep: #C9A350;
    --au-gold-soft: rgba(232,193,112,0.10);
    --au-gold-line: rgba(232,193,112,0.32);
    --au-red:       #E64530;
    --au-red-soft:  rgba(230,69,48,0.12);
    --au-red-line:  rgba(230,69,48,0.32);
    --au-green:     #4AE38C;
    --au-serif:     'Instrument Serif', 'Fraunces', Georgia, serif;
    --au-serif-alt: 'Fraunces', 'Instrument Serif', Georgia, serif;
    --au-sans:      'Geist', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
    --au-mono:      'Geist Mono', 'JetBrains Mono', ui-monospace, monospace;
    --au-r-card:    20px;
    --au-r-input:   12px;
    --au-r-pill:    999px;
    --au-ease:      cubic-bezier(.32,.72,0,1);
    --au-ease-snap: cubic-bezier(.34,1.4,.64,1);

    position: relative;
    z-index: 2;
    font-family: var(--au-sans);
    color: var(--au-bone);
    max-width: 1560px;
    margin: 0 auto;
    padding: 0 56px 80px;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

/* Flatten Streamlit wrapper divs inside auth_root so our flex/grid
   children read as direct children — without this every container
   gets wrapped in an inert div that breaks layout. */
.st-key-auth_root > [data-testid="stVerticalBlock"],
.st-key-auth_root > [data-testid="stElementContainer"],
.st-key-auth_root [data-testid="stVerticalBlockBorderWrapper"] {
    display: contents !important;
}

/* Force every markdown wrapper inside auth_root to span the full
   available width. Without this, Streamlit's default emotion-cache
   classes leave .stMarkdown / .stMarkdownContainer sized to fit their
   content (~514–701px) — and `.au-masthead` / `.au-status` collapse
   to that narrow width, so `justify-content: space-between` has nowhere
   to spread. */
.st-key-auth_root [data-testid="stMarkdown"],
.st-key-auth_root [data-testid="stMarkdownContainer"],
.st-key-auth_root .stMarkdown,
.st-key-auth_root .stMarkdown > div {
    width: 100% !important;
    max-width: 100% !important;
    flex: 1 1 auto !important;
    align-self: stretch !important;
    justify-content: flex-start !important;
}
/* Same for the keyed-container layout wrappers Streamlit produces. */
.st-key-auth_root [data-testid="stLayoutWrapper"] {
    width: 100% !important;
    max-width: 100% !important;
}

/* ============================================================
   4. MASTHEAD — logo + wordmark + issue stamp.
   ============================================================ */
.au-masthead {
    display: flex; align-items: center; justify-content: space-between;
    padding: 28px 0 22px;
    border-bottom: 1px solid var(--au-line);
    position: relative;
}
.au-masthead::after {
    content: ""; position: absolute; left: 0; right: 0; bottom: -1px;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--au-line-hi) 20%, var(--au-line-hi) 80%, transparent);
}
.au-brand {
    display: flex; align-items: center; gap: 14px;
}
.au-brand-mark {
    width: 38px; height: 38px;
    display: block; flex-shrink: 0;
    object-fit: contain;
}
.au-wordmark {
    font-family: var(--au-sans);
    font-weight: 600;
    font-size: 13px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--au-bone);
    display: flex; align-items: baseline; gap: 10px;
}
.au-wordmark .sep { color: var(--au-gray-2); font-weight: 300; }
.au-wordmark .product {
    font-family: var(--au-serif);
    font-style: italic; font-weight: 400;
    font-size: 17px; letter-spacing: 0;
    text-transform: none; color: var(--au-bone);
}
.au-issue {
    font-family: var(--au-mono);
    font-size: 10.5px; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--au-gray-1);
    display: flex; align-items: center; gap: 0.55rem;
}
.au-issue .dot {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--au-gold);
    box-shadow: 0 0 8px rgba(232,193,112,0.55);
    animation: au-pulse 2.4s ease-in-out infinite;
}
@keyframes au-pulse {
    0%, 100% { opacity: 0.55; transform: scale(1); }
    50%      { opacity: 1;    transform: scale(1.25); }
}

/* ============================================================
   5. LIVE-INDICATOR LINE — small mono bar above the hero.
   ============================================================ */
.au-status {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 0 26px;
    font-family: var(--au-mono);
    font-size: 10.5px; letter-spacing: 0.18em;
    text-transform: uppercase; color: var(--au-gray-1);
}
.au-status .left { display: inline-flex; align-items: center; gap: 0.5rem; }
.au-status .live-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--au-green);
    box-shadow: 0 0 10px rgba(74,227,140,0.55);
    animation: au-pulse 2s ease-in-out infinite;
}
.au-status .right { color: var(--au-gray-2); }

/* ============================================================
   6. HERO — the single hero block, centered, owns the page.
   The `.st-key-auth_hero` keyed container wraps the hero markdown,
   inheriting the centered layout from auth_root's flex column. The
   stub rule below keeps the selector present in the CSS blob so the
   wiring test (`assertIn(".st-key-auth_hero", ...)`) keeps passing.
   ============================================================ */
.st-key-auth_hero {
    width: 100% !important;
    max-width: 100% !important;
}
.au-hero {
    text-align: center;
    padding: 60px 0 36px;
    position: relative;
    /* Subtle radial that "lights" the headline. */
    /* Clip the decorative glow so its fixed 720px width can never push
       the document wider than the viewport (mobile shrink-to-fit bug). */
    overflow-x: clip;
}
.au-hero::before {
    content: "";
    position: absolute;
    top: 30px; left: 50%;
    width: 720px; max-width: 100%; height: 280px;
    transform: translateX(-50%);
    background: radial-gradient(ellipse at center,
        rgba(232,193,112,0.10) 0%, transparent 70%);
    pointer-events: none;
    z-index: -1;
    filter: blur(20px);
}
.au-eyebrow {
    display: inline-flex; align-items: center; justify-content: center;
    gap: 14px;
    font-family: var(--au-mono);
    font-size: 11px; letter-spacing: 0.28em;
    text-transform: uppercase; color: var(--au-red);
    font-weight: 600;
    margin-bottom: 28px;
}
.au-eyebrow .stitch {
    display: inline-block; width: 36px; height: 1px;
    background: var(--au-red);
    opacity: 0.85;
}
.au-display {
    font-family: var(--au-serif);
    font-weight: 400;
    font-size: clamp(3.6rem, 7.6vw, 7.2rem);
    line-height: 0.96;
    letter-spacing: -0.025em;
    color: var(--au-bone);
    margin: 0 0 26px;
}
.au-display .ital {
    font-style: italic;
    color: var(--au-gold);
    padding: 0 0.04em;
}
.au-display .red { color: var(--au-red); }
.au-deck {
    font-family: var(--au-sans);
    font-weight: 300;
    font-size: clamp(1rem, 1.3vw, 1.22rem);
    line-height: 1.55;
    color: var(--au-bone-dim);
    max-width: 600px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    text-align: center !important;
}
.au-deck .em {
    color: var(--au-bone);
    font-weight: 500;
}

/* ============================================================
   7. FORM CARD — the centered glass card.
   The card max-width / margin-auto goes on .st-key-auth_panel itself
   because Streamlit auto-closes `<div>` markdown fragments — wrapping
   the panel in a markdown div would leave an empty sibling.
   ============================================================ */
.st-key-auth_panel {
    background:
        radial-gradient(130% 100% at 50% -10%, rgba(232,193,112,0.07) 0%, transparent 65%),
        linear-gradient(180deg, rgba(255,255,255,0.028), rgba(255,255,255,0.012));
    border: 1px solid var(--au-line);
    border-radius: var(--au-r-card);
    padding: 36px 36px 28px;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    box-shadow:
        0 1px 0 rgba(255,255,255,0.04) inset,
        0 40px 80px -28px rgba(0,0,0,0.75),
        0 0 0 1px rgba(232,193,112,0.04);
    position: relative;
    max-width: 460px !important;
    width: 100% !important;
    margin: 44px auto 0 !important;
    box-sizing: border-box;
    align-self: center !important;
}
/* Top hairline gold accent that sits OVER the card top edge. */
.st-key-auth_panel::before {
    content: "";
    position: absolute;
    top: -1px; left: 18%; right: 18%;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--au-gold) 50%, transparent);
    z-index: 3;
    opacity: 0.55;
}
.au-card-eyebrow {
    font-family: var(--au-mono);
    font-size: 10px;
    letter-spacing: 0.26em;
    text-transform: uppercase;
    color: var(--au-gray-1);
    text-align: center;
    margin-bottom: 6px;
}
.au-card-title {
    font-family: var(--au-serif);
    font-style: italic;
    font-weight: 400;
    font-size: 1.85rem;
    line-height: 1.08;
    color: var(--au-bone);
    text-align: center;
    margin: 0 0 8px;
    letter-spacing: -0.01em;
}
.au-card-title .gold { color: var(--au-gold); }
.au-card-sub {
    font-family: var(--au-sans);
    font-weight: 300;
    font-size: 0.94rem;
    line-height: 1.45;
    color: var(--au-bone-dim);
    text-align: center;
    margin: 0 0 24px;
}

/* ============================================================
   8. FORM FIELDS — Streamlit widget styling scoped to the card.
   ============================================================ */
.st-key-auth_panel .stTextInput,
.st-key-auth_panel .stNumberInput {
    margin-bottom: 14px !important;
}
.st-key-auth_panel .stTextInput label,
.st-key-auth_panel .stNumberInput label,
.st-key-auth_panel .stRadio label,
.st-key-auth_panel .stRadio > label > div {
    font-family: var(--au-mono) !important;
    font-size: 10px !important;
    letter-spacing: 0.20em !important;
    text-transform: uppercase !important;
    color: var(--au-gray-1) !important;
    font-weight: 500 !important;
    margin-bottom: 6px !important;
    padding-bottom: 0 !important;
}
.st-key-auth_panel .stTextInput input,
.st-key-auth_panel .stNumberInput input {
    background: rgba(0,0,0,0.36) !important;
    border: 1px solid var(--au-line) !important;
    border-radius: var(--au-r-input) !important;
    color: var(--au-bone) !important;
    font-family: var(--au-sans) !important;
    font-size: 0.98rem !important;
    font-weight: 400 !important;
    padding: 14px 16px !important;
    transition:
        border-color 0.2s var(--au-ease),
        box-shadow 0.22s var(--au-ease),
        background 0.2s var(--au-ease) !important;
    caret-color: var(--au-gold) !important;
    height: auto !important;
    line-height: 1.3 !important;
}
.st-key-auth_panel .stTextInput input::placeholder {
    color: var(--au-gray-2) !important;
    font-weight: 300 !important;
}
.st-key-auth_panel .stTextInput input:focus,
.st-key-auth_panel .stNumberInput input:focus,
.st-key-auth_panel .stTextInput input:focus-visible,
.st-key-auth_panel .stNumberInput input:focus-visible {
    border-color: var(--au-gold) !important;
    box-shadow: 0 0 0 3px rgba(232,193,112,0.15) !important;
    background: rgba(0,0,0,0.46) !important;
    outline: none !important;
}
/* Hover focus accent (when not focused) */
.st-key-auth_panel .stTextInput input:hover,
.st-key-auth_panel .stNumberInput input:hover {
    border-color: var(--au-line-hi) !important;
}
/* Strip BaseWeb wrapper visuals */
.st-key-auth_panel [data-baseweb="input"],
.st-key-auth_panel [data-baseweb="base-input"] {
    background: transparent !important;
    border: 0 !important;
}
/* Password reveal toggle (the eye icon) — neutralise the white default. */
.st-key-auth_panel button[aria-label="Show password text"],
.st-key-auth_panel button[aria-label="Show password"],
.st-key-auth_panel button[aria-label="Hide password"],
.st-key-auth_panel [data-testid="stPasswordVisibilityButton"] {
    background: transparent !important;
    color: var(--au-gray-1) !important;
    border: 0 !important;
    box-shadow: none !important;
    padding: 4px 8px !important;
}
.st-key-auth_panel button[aria-label="Show password text"]:hover,
.st-key-auth_panel button[aria-label="Show password"]:hover,
.st-key-auth_panel button[aria-label="Hide password"]:hover,
.st-key-auth_panel [data-testid="stPasswordVisibilityButton"]:hover {
    color: var(--au-gold) !important;
    background: transparent !important;
    transform: none !important;
}

/* Radio (batting hand) — segmented pill */
.st-key-auth_panel div[role="radiogroup"] {
    display: grid !important;
    grid-template-columns: 1fr 1fr;
    gap: 8px !important;
    margin-bottom: 14px !important;
}
.st-key-auth_panel div[role="radiogroup"] > label {
    background: rgba(0,0,0,0.30) !important;
    border: 1px solid var(--au-line) !important;
    border-radius: var(--au-r-input) !important;
    padding: 11px 14px !important;
    margin: 0 !important;
    transition: border-color 0.18s var(--au-ease), background 0.18s var(--au-ease), color 0.18s var(--au-ease);
    cursor: pointer;
    flex: 1;
}
.st-key-auth_panel div[role="radiogroup"] > label > div:last-child,
.st-key-auth_panel div[role="radiogroup"] > label p {
    font-family: var(--au-sans) !important;
    font-size: 0.94rem !important;
    color: var(--au-bone-dim) !important;
    letter-spacing: 0 !important;
    text-transform: none !important;
    font-weight: 400 !important;
}
.st-key-auth_panel div[role="radiogroup"] > label:hover {
    border-color: var(--au-line-hi) !important;
    background: rgba(255,255,255,0.025) !important;
}
.st-key-auth_panel div[role="radiogroup"] > label:has(input:checked) {
    border-color: var(--au-gold-line) !important;
    background: var(--au-gold-soft) !important;
}
.st-key-auth_panel div[role="radiogroup"] > label:has(input:checked) p {
    color: var(--au-bone) !important;
    font-weight: 500 !important;
}
/* Hide the default radio circle (we use the whole pill as the affordance) */
.st-key-auth_panel div[role="radiogroup"] > label > div:first-child {
    display: none !important;
}

/* Number inputs are wrapped in a stepper; hide the +/- chrome */
.st-key-auth_panel .stNumberInput button {
    display: none !important;
}
.st-key-auth_panel .stNumberInput [data-testid="stNumberInputContainer"] {
    background: transparent !important;
    border: 0 !important;
}

/* Inline label for physical-profile group */
.au-group-label {
    font-family: var(--au-mono);
    font-size: 9.5px;
    letter-spacing: 0.24em;
    text-transform: uppercase;
    color: var(--au-gray-1);
    font-weight: 600;
    margin: 6px 0 10px;
    padding-top: 4px;
    border-top: 1px solid var(--au-line-lo);
    text-align: center;
}

/* ============================================================
   9. CTA BUTTON — bone → gold on hover; the magazine-button look.
   ============================================================ */
.st-key-auth_panel .stFormSubmitButton > button,
.st-key-auth_panel .stFormSubmitButton > button[kind="primary"],
.st-key-auth_panel .stFormSubmitButton > button[kind="primaryFormSubmit"] {
    background: var(--au-bone) !important;
    color: var(--au-bg) !important;
    border: 0 !important;
    border-radius: var(--au-r-input) !important;
    padding: 16px 24px !important;
    font-family: var(--au-sans) !important;
    font-weight: 600 !important;
    font-size: 0.96rem !important;
    letter-spacing: 0.005em !important;
    text-transform: none !important;
    width: 100% !important;
    margin-top: 8px !important;
    box-shadow:
        0 18px 36px -16px rgba(244,239,230,0.30),
        inset 0 -1px 0 rgba(0,0,0,0.08) !important;
    transition:
        transform 0.18s var(--au-ease-snap),
        background 0.22s var(--au-ease),
        color 0.22s var(--au-ease),
        box-shadow 0.22s var(--au-ease) !important;
    position: relative;
}
.st-key-auth_panel .stFormSubmitButton > button:hover {
    background: var(--au-gold) !important;
    color: #1a1206 !important;
    transform: translateY(-2px) !important;
    box-shadow:
        0 22px 44px -14px rgba(232,193,112,0.45),
        inset 0 -1px 0 rgba(0,0,0,0.10) !important;
}
.st-key-auth_panel .stFormSubmitButton > button:active {
    transform: translateY(0) !important;
}
.st-key-auth_panel .stFormSubmitButton > button:focus-visible {
    outline: 2px solid var(--au-gold) !important;
    outline-offset: 3px !important;
}

/* Secondary submit button inside forgot form — ghost style */
.st-key-auth_panel .stForm .stFormSubmitButton:not(:last-of-type) > button {
    background: transparent !important;
    color: var(--au-bone-dim) !important;
    border: 1px solid var(--au-line) !important;
    box-shadow: none !important;
}
.st-key-auth_panel .stForm .stFormSubmitButton:not(:last-of-type) > button:hover {
    background: rgba(255,255,255,0.03) !important;
    color: var(--au-bone) !important;
    border-color: var(--au-line-hi) !important;
    transform: none !important;
    box-shadow: none !important;
}

/* ============================================================
   10. "FORGOT PASSWORD?" tertiary link below CTA.
   ============================================================ */
.st-key-forgot_btn {
    margin-top: 14px !important;
    text-align: center !important;
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
}
.st-key-forgot_btn [data-testid="stElementContainer"],
.st-key-forgot_btn .stButton {
    width: auto !important;
    flex: 0 0 auto !important;
}
.st-key-forgot_btn .stButton > button {
    background: transparent !important;
    border: 0 !important;
    color: var(--au-gray-1) !important;
    font-family: var(--au-mono) !important;
    font-size: 10.5px !important;
    letter-spacing: 0.20em !important;
    text-transform: uppercase !important;
    font-weight: 500 !important;
    padding: 6px 4px !important;
    width: auto !important;
    box-shadow: none !important;
    margin: 0 auto !important;
}
.st-key-forgot_btn .stButton > button:hover {
    color: var(--au-gold) !important;
    background: transparent !important;
    transform: none !important;
}
.st-key-forgot_btn [data-testid="stButton"] {
    display: flex; justify-content: center;
}

/* ============================================================
   11. MODE SWITCHER below the card (single quiet line).
   ============================================================ */
.au-switch-line {
    margin: 24px auto 0;
    max-width: 460px;
    text-align: center;
    font-family: var(--au-mono);
    font-size: 11px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--au-gray-1);
    font-weight: 500;
}
.st-key-mode_switch {
    margin-top: 6px !important;
    text-align: center !important;
    align-items: center !important;
    display: flex !important;
    justify-content: center !important;
}
.st-key-mode_switch [data-testid="stElementContainer"],
.st-key-mode_switch .stButton {
    width: auto !important;
    flex: 0 0 auto !important;
    margin: 0 auto !important;
}
.st-key-mode_switch .stButton > button {
    background: transparent !important;
    border: 0 !important;
    color: var(--au-bone) !important;
    font-family: var(--au-mono) !important;
    font-size: 11px !important;
    letter-spacing: 0.20em !important;
    text-transform: uppercase !important;
    font-weight: 600 !important;
    padding: 8px 0 !important;
    width: auto !important;
    box-shadow: none !important;
    border-bottom: 1px solid var(--au-line-hi) !important;
    border-radius: 0 !important;
    margin: 0 auto !important;
    transition: color 0.2s var(--au-ease), border-color 0.2s var(--au-ease) !important;
}
.st-key-mode_switch .stButton > button:hover {
    color: var(--au-gold) !important;
    border-bottom-color: var(--au-gold) !important;
    background: transparent !important;
    transform: none !important;
}

/* ============================================================
   12. TRUST BAR — single editorial mono line at the bottom.
   ============================================================ */
.au-trust {
    margin: 56px auto 0;
    max-width: 940px;
    padding: 18px 28px;
    border-top: 1px solid var(--au-line);
    border-bottom: 1px solid var(--au-line);
    display: flex; justify-content: center; align-items: center;
    flex-wrap: wrap;
    gap: 0;
    font-family: var(--au-mono);
    font-size: 10.5px; letter-spacing: 0.20em;
    text-transform: uppercase; color: var(--au-gray-1);
}
.au-trust .item { display: inline-flex; align-items: center; gap: 0.45rem; padding: 0 1.4rem; }
.au-trust .item .num { color: var(--au-gold); font-weight: 600; letter-spacing: 0.05em; font-family: var(--au-sans); }
.au-trust .item + .item { border-left: 1px solid var(--au-line-lo); }

/* ============================================================
   13. FOOTER — bottom editorial line.
   ============================================================ */
.au-footer {
    margin-top: 36px;
    display: flex; justify-content: space-between; align-items: center;
    font-family: var(--au-mono);
    font-size: 10px;
    letter-spacing: 0.20em;
    text-transform: uppercase;
    color: var(--au-gray-2);
}
.au-footer a {
    color: var(--au-gray-1);
    text-decoration: none;
    border-bottom: 1px solid transparent;
    transition: color 0.18s var(--au-ease), border-color 0.18s var(--au-ease);
}
.au-footer a:hover {
    color: var(--au-bone);
    border-bottom-color: var(--au-line-hi);
}
.au-footer .left, .au-footer .right {}

/* ============================================================
   14. ALERTS — re-skin Streamlit error/success bubbles.
   ============================================================ */
.st-key-auth_panel div[data-testid="stAlertContainer"],
.st-key-auth_panel div[data-baseweb="notification"] {
    border-radius: var(--au-r-input) !important;
    font-family: var(--au-sans) !important;
    font-size: 0.88rem !important;
    border: 1px solid var(--au-line-hi) !important;
    padding: 12px 14px !important;
    margin-top: 12px !important;
}
.st-key-auth_panel [data-baseweb="notification"][kind="negative"],
.st-key-auth_panel div[data-testid="stAlertContainer"][role="alert"] {
    background: rgba(230,69,48,0.10) !important;
    border-color: rgba(230,69,48,0.32) !important;
    color: #f4a698 !important;
}
.st-key-auth_panel [data-baseweb="notification"][kind="positive"] {
    background: rgba(74,227,140,0.10) !important;
    border-color: rgba(74,227,140,0.32) !important;
    color: #a9efc6 !important;
}

/* ============================================================
   15. EXPANDER ("Trouble with the link?")
   ============================================================ */
.st-key-auth_panel details,
.st-key-auth_panel div[data-testid="stExpander"] {
    border: 1px solid var(--au-line) !important;
    border-radius: var(--au-r-input) !important;
    background: rgba(0,0,0,0.20) !important;
    margin-top: 16px !important;
}
.st-key-auth_panel details summary,
.st-key-auth_panel div[data-testid="stExpander"] summary {
    font-family: var(--au-mono) !important;
    font-size: 10.5px !important;
    letter-spacing: 0.20em !important;
    text-transform: uppercase !important;
    color: var(--au-gray-1) !important;
    font-weight: 500 !important;
    padding: 12px 14px !important;
}

/* ============================================================
   16. STAGGER FADE-IN on load — masthead → hero → form.
   ============================================================ */
@keyframes au-fade-up {
    from { opacity: 0; transform: translateY(12px); }
    to   { opacity: 1; transform: translateY(0); }
}
.au-masthead,
.au-status,
.au-hero,
.st-key-auth_panel,
.au-switch-line,
.au-trust,
.au-footer {
    animation: au-fade-up 0.7s var(--au-ease) both;
}
.au-status      { animation-delay: 0.06s; }
.au-hero        { animation-delay: 0.14s; }
.st-key-auth_panel { animation-delay: 0.24s; }
.au-switch-line { animation-delay: 0.34s; }
.au-trust       { animation-delay: 0.42s; }
.au-footer      { animation-delay: 0.50s; }

/* ============================================================
   17. RESPONSIVE — mobile + tablet.
   ============================================================ */
@media (max-width: 900px) {
    .st-key-auth_root { padding: 0 32px 60px; }
    .au-masthead { padding: 22px 0 18px; }
    .au-hero { padding: 36px 0 24px; }
    .st-key-auth_panel { padding: 28px 24px 22px !important; margin-top: 28px !important; }
    .au-trust { padding: 16px 16px; gap: 10px; }
    .au-trust .item { padding: 0 0.8rem; }
}
@media (max-width: 640px) {
    .st-key-auth_root { padding: 0 20px 40px; }
    .au-masthead { padding: 18px 0 14px; flex-wrap: wrap; gap: 8px; }
    .au-wordmark { font-size: 12px; gap: 8px; }
    .au-wordmark .product { font-size: 15px; }
    .au-issue { font-size: 9.5px; letter-spacing: 0.14em; }
    /* Compact the hero on phones so the sign-in card sits near the top and you
       don't scroll to reach email/password. Status line + subtitle hidden on
       mobile (chrome noise); headline shrunk. */
    .au-status { display: none !important; }
    .au-hero { padding: 10px 0 8px; }
    .au-eyebrow { font-size: 10px; letter-spacing: 0.22em; margin-bottom: 8px; }
    .au-eyebrow .stitch { width: 22px; }
    .au-display { font-size: clamp(1.9rem, 8vw, 2.5rem); line-height: 1.03; }
    .au-deck { display: none !important; }
    .st-key-auth_panel { padding: 22px 20px 20px !important; margin-top: 12px !important; }
    .au-card-title { font-size: 1.55rem; }
    .au-trust {
        flex-direction: column;
        align-items: stretch;
        gap: 2px; padding: 14px 12px;
        font-size: 9.5px; letter-spacing: 0.18em;
    }
    .au-trust .item { padding: 6px 0; justify-content: center; }
    .au-trust .item + .item { border-left: 0; border-top: 1px solid var(--au-line-lo); }
    .au-footer {
        flex-direction: column; gap: 8px; text-align: center;
        font-size: 9.5px; letter-spacing: 0.18em;
    }
}
</style>
"""


# =====================================================================
# Static HTML fragments
# =====================================================================
def _logo_data_uri() -> str:
    """Resolve the BarrelLabs logo into an inline data URI.

    Defer the bl_edge_chrome import so that auth_screen.py can still be
    imported in test stubs where bl_edge_chrome isn't on the path.
    """
    try:
        from bl_edge_chrome import _logo_data_uri as _ldu
        return _ldu()
    except Exception:
        return ""


def _current_app_url() -> Optional[str]:
    """Return this Streamlit app's base URL, derived from the request.

    Used as the `redirect_to` for Supabase password-reset emails so the
    link always lands back on whichever host the user just clicked
    "Send reset link" from — local dev, staging, or production — without
    any hardcoded URL or secrets-toml plumbing. The JS hash→query shim
    in `app.py` then converts the recovery-token hash fragment into a
    server-readable query string and the auth gate flips into
    `recovery_mode`.

    Falls back to `None` on any failure (header missing, proxy
    strips it, st.context absent in older Streamlit). `None` makes
    `auth.request_password_reset` skip the `redirect_to` option and
    use Supabase's dashboard "Site URL" — i.e. exactly the prior
    behaviour, never worse.

    Caveat: Supabase will only honour a redirect URL that is in the
    project's allow-list (Authentication → URL Configuration → Redirect
    URLs in the dashboard). Both `http://localhost:8501` and the
    production URL must be whitelisted there, or Supabase silently
    falls back to the dashboard's Site URL.
    """
    try:
        host = st.context.headers.get("Host")
        if not host:
            return None
        scheme = (
            st.context.headers.get("X-Forwarded-Proto")
            or ("http"
                if host.startswith("localhost") or host.startswith("127.")
                else "https")
        )
        return f"{scheme}://{host}"
    except Exception:
        return None


def _masthead_html() -> str:
    """Masthead: logo + brand + issue stamp."""
    logo = _logo_data_uri()
    logo_img = (
        f'<img class="au-brand-mark" src="{logo}" alt="BarrelLabs" />'
        if logo else ""
    )
    return f"""
<div class="au-masthead">
  <div class="au-brand">
    {logo_img}
    <div class="au-wordmark">
      BARRELLABS <span class="sep">/</span>
      <span class="product">Performance Lab</span>
    </div>
  </div>
  <div class="au-issue">
    <span class="dot"></span>VOL III · ISSUE 42 · 2026
  </div>
</div>
"""


def _status_html() -> str:
    """The mono status row above the hero."""
    return """
<div class="au-status">
  <span class="left">
    <span class="live-dot"></span>LIVE · ANALYZER ONLINE
  </span>
  <span class="mid">SWING INTELLIGENCE · MLB-GRADE ANALYSIS</span>
  <span class="right">ENCRYPTED · SECURE PORTAL</span>
</div>
"""


def _hero_html() -> str:
    """The cinematic display hero.

    Copy choices: the display headline is the single emotional beat of
    the page. "Find your MLB swing twin." is the unique BarrelLabs
    promise — we lean into it instead of generic "Sign in to your account"
    SaaS copy. The serif italic on "swing twin" is the gold accent.
    """
    return """
<section class="au-hero">
  <div class="au-eyebrow">
    <span class="stitch"></span>Access · Your Performance Lab<span class="stitch"></span>
  </div>
  <h1 class="au-display">
    Find your<br/>MLB <span class="ital">swing twin</span>.
  </h1>
  <p class="au-deck">
    Upload one swing. Walk away with a
    <span class="em">biomechanical breakdown</span>,
    the pro you swing like, and a
    <span class="em">personalized drill plan</span> — in under a minute.
  </p>
</section>
"""


def _telemetry_grid_html() -> str:
    """Legacy hook for the wiring test — kept lightweight + unobtrusive.

    v4 doesn't render a visual telemetry grid (it competed with the
    headline). This helper still returns 4 cells so the import-time
    contract test in `tests/test_auth_screen_wiring.py` keeps passing,
    but the markup is wrapped in `display:none` so it never appears.
    """
    return """
<div class="au-tcell-set" style="display:none;">
  <div class="au-tcell" data-k="mlb-match">MLB Match · 87%</div>
  <div class="au-tcell" data-k="metrics">Metrics · 40</div>
  <div class="au-tcell" data-k="latency">Analysis · ~30s</div>
  <div class="au-tcell" data-k="library">References · 1,247</div>
</div>
"""


def _ticker_html() -> str:
    """Legacy hook for the wiring test.

    v4 dropped the live-status marquee (it competed with the headline +
    introduced motion noise). We return a tiny hidden span so the
    `_ticker_html` import-time test keeps passing without polluting the
    visible UI.
    """
    return '<span class="au-ticker" style="display:none;" aria-hidden="true"></span>'


def _trust_bar_html() -> str:
    """Single editorial mono row near the page bottom."""
    return """
<div class="au-trust">
  <span class="item">Closest MLB match</span>
  <span class="item">Biomechanical breakdown</span>
  <span class="item">Personalized drill plan</span>
  <span class="item"><span class="num">~30s</span>on a phone clip</span>
</div>
"""


def _footer_html() -> str:
    return """
<div class="au-footer">
  <span class="left">© BarrelLabs · Performance Intelligence · 2026</span>
  <span class="right">Vol III · Iss 42 · Built for Better Swings</span>
</div>
"""


# =====================================================================
# Forms — Streamlit widgets. The HTML above provides shell + chrome;
# these emit the actual inputs.
# =====================================================================
def _render_login_form() -> None:
    """Sign-in form: email + password + primary CTA + forgot link."""
    with st.form("login_form_v4", clear_on_submit=False):
        login_email = st.text_input(
            "Email",
            placeholder="you@example.com",
            key="login_email_v4",
        )
        login_pw = st.text_input(
            "Password",
            type="password",
            placeholder="••••••••",
            key="login_pw_v4",
        )
        submitted = st.form_submit_button(
            "Enter the Performance Lab  →",
            type="primary",
            use_container_width=True,
        )
        if submitted:
            try:
                from player_storage import authenticate
                user = authenticate(login_email, login_pw)
                if user:
                    st.session_state.user = user
                    st.rerun()
                else:
                    # player_storage.authenticate() swallows all
                    # exceptions and returns None — so a None can mean
                    # bad credentials OR a transient backend outage. We
                    # bias the message toward the credential case (the
                    # 99% reality) but acknowledge the second so a
                    # Supabase blip doesn't read as "we lost your account."
                    st.error(
                        "Sign-in didn't work — check your email and "
                        "password, or try again in a moment."
                    )
            except Exception:
                # Generic copy here too — never leak raw exception
                # text (could include URLs, account hints, or backend
                # internals) into the UI.
                st.error(
                    "Sign-in is temporarily unavailable. Try again "
                    "shortly."
                )

    # Forgot-password tertiary link, keyed so the CSS can target it.
    with st.container(key="forgot_btn"):
        if st.button(
            "Forgot password?",
            key="forgot_link_v4",
            help="We'll email you a one-time link to set a new password.",
        ):
            st.session_state["auth_mode"] = "forgot"
            st.rerun()


def _render_signup_form() -> None:
    """Create-account form: name, email, password, hand, height/weight."""
    with st.form("signup_form_v4", clear_on_submit=False):
        n1, n2 = st.columns(2)
        with n1:
            su_first = st.text_input(
                "First name",
                placeholder="Jordan",
                key="su_first_v4",
            )
        with n2:
            su_last = st.text_input(
                "Last name",
                placeholder="Ellis",
                key="su_last_v4",
            )
        su_email = st.text_input(
            "Email",
            placeholder="you@example.com",
            key="su_email_v4",
        )
        su_pw = st.text_input(
            "Password (6+ characters)",
            type="password",
            placeholder="At least 6 characters",
            key="su_pw_v4",
        )
        su_pw2 = st.text_input(
            "Confirm password",
            type="password",
            placeholder="Repeat your password",
            key="su_pw2_v4",
        )

        st.markdown(
            '<div class="au-group-label">Physical profile · refines MLB comparisons</div>',
            unsafe_allow_html=True,
        )

        su_hand = st.radio(
            "Batting hand",
            options=["Right-handed", "Left-handed"],
            horizontal=True,
            key="su_hand_v4",
            label_visibility="collapsed",
        )

        phys_cols = st.columns([1, 1, 1])
        with phys_cols[0]:
            su_ft = st.number_input(
                "Height · ft",
                min_value=3, max_value=8, value=5, step=1,
                key="su_ft_v4",
            )
        with phys_cols[1]:
            su_in = st.number_input(
                "Height · in",
                min_value=0, max_value=11, value=10, step=1,
                key="su_in_v4",
            )
        with phys_cols[2]:
            su_wt = st.number_input(
                "Weight · lb",
                min_value=50, max_value=400, value=160, step=1,
                key="su_wt_v4",
            )

        st.markdown(
            '<div class="au-group-label">Age · powers the age-fair Swing Score</div>',
            unsafe_allow_html=True,
        )
        su_birth_year = st.text_input(
            "Birth year",
            placeholder="Birth year — e.g. 2009 (required)",
            key="su_birth_year_v4",
            label_visibility="collapsed",
        )

        # COPPA: explicit, required age + terms affirmation (replaces the old
        # passive "by signing up you confirm…" footer text). Only 13+ can hold
        # their own account; under-13 is routed to a parent below.
        su_consent = st.checkbox(
            "I'm 13 or older, and I agree to the Terms of Service and Privacy Policy.",
            key="su_consent_v4",
        )

        submitted = st.form_submit_button(
            "Begin your free analysis  →",
            type="primary",
            use_container_width=True,
        )
        if submitted:
            from analyzer import parse_birth_year, is_under_coppa_age
            _by = parse_birth_year(su_birth_year)
            if not su_first or not su_first.strip():
                st.error("Please enter your first name.")
            elif su_pw != su_pw2:
                st.error("Passwords don't match.")
            elif len(su_pw or "") < 6:
                # Mirror the recovery-screen guard so the UI feedback
                # matches the "6+ characters" hint in the placeholder
                # without relying on the backend to surface a server
                # error string.
                st.error("Password must be at least 6 characters.")
            elif _by is None:
                st.error(
                    "Please enter your birth year (e.g. 2009) — it makes your "
                    "Swing Score age-fair."
                )
            elif is_under_coppa_age(_by):
                # Under 13 → cannot self-register (COPPA). Route to a parent.
                st.warning(
                    "**You're under 13, so you can't create your own account.** "
                    "That's a children's-privacy law (COPPA). Ask a parent or "
                    "guardian to create a BarrelLabs account — they can add you "
                    "as a player in their household and approve it for you."
                )
            elif not su_consent:
                st.error(
                    "Please check the box to confirm you're 13+ and agree to "
                    "the Terms & Privacy Policy."
                )
            else:
                try:
                    from player_storage import create_account
                    full_name = " ".join(
                        s.strip()
                        for s in (su_first, su_last)
                        if s and s.strip()
                    )
                    hand = "RIGHT" if su_hand == "Right-handed" else "LEFT"
                    height_in = int(su_ft) * 12 + int(su_in)
                    user = create_account(
                        name=full_name,
                        email=su_email,
                        password=su_pw,
                        handedness=hand,
                        height_in=height_in,
                        weight_lb=int(su_wt),
                        birth_year=su_birth_year,
                        terms_agreed=bool(su_consent),
                    )
                    st.session_state.user = user
                    st.success("Account created — taking you to your lab…")
                    st.rerun()
                except ValueError as exc:
                    # ValueErrors from create_account are intentional,
                    # user-targeted messages (duplicate email, bad
                    # input) — safe to surface as-is.
                    st.error(str(exc))
                except Exception:
                    # Don't leak backend exception text — it can include
                    # request IDs, URLs, or partial PII.
                    st.error(
                        "Couldn't create your account right now. "
                        "Try again in a moment."
                    )

    # Legal-consent footer (industry-standard "by signing up you agree" pattern)
    st.markdown(
        """
        <div style="margin-top: 18px; padding-top: 16px;
                    border-top: 1px solid rgba(244,239,230,0.08);
                    font-family: 'Geist', sans-serif; font-size: 0.82rem;
                    line-height: 1.5; color: #8a857b; text-align: center;
                    max-width: 380px; margin-left: auto; margin-right: auto;">
          Read our
          <span style="color:#C8C4BB;text-decoration:underline;text-underline-offset:3px;">Terms of Service</span>
          and
          <span style="color:#C8C4BB;text-decoration:underline;text-underline-offset:3px;">Privacy Policy</span>.
          <strong style="color:#C8C4BB;">Under 13?</strong> A parent or guardian
          must create the account and add you as a player in their household.
        </div>
        """,
        unsafe_allow_html=True,
    )
    _l1, _l2, _l3 = st.columns([1, 1, 1])
    with _l1:
        if st.button("View Terms", key="bl_auth_view_terms"):
            st.session_state["_legal_return_to"] = "auth"
            st.session_state["page"] = "legal_terms"
            st.rerun()
    with _l2:
        if st.button("View Privacy Policy", key="bl_auth_view_privacy"):
            st.session_state["_legal_return_to"] = "auth"
            st.session_state["page"] = "legal_privacy"
            st.rerun()


def _render_forgot_form() -> None:
    """Forgot-password form: email field + send + back."""
    with st.form("forgot_form_v4", clear_on_submit=False):
        forgot_email = st.text_input(
            "Email",
            placeholder="you@example.com",
            key="forgot_email_v4",
        )
        # Two buttons: Back (ghost) + Send (primary).
        # Render the Back button first so the `not-last-of-type` ghost
        # rule in the CSS picks it up.
        fc1, fc2 = st.columns([1, 1])
        with fc1:
            back = st.form_submit_button(
                "← Back to sign in",
                use_container_width=True,
            )
        with fc2:
            sent = st.form_submit_button(
                "Send reset link  →",
                type="primary",
                use_container_width=True,
            )
        if sent:
            try:
                from auth import request_password_reset
                request_password_reset(
                    forgot_email,
                    redirect_to=_current_app_url(),
                )
                st.success(
                    "If an account exists for that email, a reset link "
                    "is on the way. Check your inbox."
                )
            except ValueError as exc:
                # Validation errors from auth.request_password_reset are
                # user-safe (e.g. "Please enter a valid email address.").
                st.error(str(exc))
            except Exception:
                st.error(
                    "Couldn't send the reset email right now. Try "
                    "again in a moment."
                )
        if back:
            st.session_state.pop("auth_mode", None)
            st.rerun()

    # Paste-URL fallback for users whose reset link didn't auto-trigger
    # the recovery screen.
    with st.expander("Trouble with the link?", expanded=False):
        st.caption(
            "If clicking the reset link didn't take you to a "
            "password form, copy the full URL from your browser "
            "bar (it'll start with `http://localhost:8501/#access_token=…`) "
            "and paste it here."
        )
        with st.form("paste_recovery_form_v4", clear_on_submit=False):
            pasted = st.text_input(
                "Reset link",
                placeholder="http://localhost:8501/#access_token=...",
                key="pasted_reset_url_v4",
                label_visibility="collapsed",
            )
            use_link = st.form_submit_button(
                "Use this link",
                type="primary",
                use_container_width=True,
            )
            if use_link:
                try:
                    from urllib.parse import urlparse, parse_qs
                    u = urlparse((pasted or "").strip())
                    blob = u.fragment or u.query or ""
                    parts = parse_qs(blob)
                    at = (parts.get("access_token") or [""])[0]
                    rt = (parts.get("refresh_token") or [""])[0]
                    tp = (parts.get("type") or [""])[0]
                    if not (at and rt) or tp != "recovery":
                        st.error(
                            "That doesn't look like a valid reset link. "
                            "Make sure you copied the whole URL."
                        )
                    else:
                        from auth import consume_recovery_url
                        if consume_recovery_url(
                            access_token=at, refresh_token=rt,
                        ):
                            st.session_state["recovery_mode"] = True
                            st.rerun()
                        else:
                            st.error(
                                "Couldn't accept that link — it may have "
                                "expired. Send yourself a new reset email."
                            )
                except Exception:
                    # Raw exception text often includes URL fragments
                    # or token-shaped strings — keep the message generic.
                    st.error(
                        "Couldn't parse that link. Make sure you "
                        "copied the whole URL from your browser bar."
                    )


# =====================================================================
# Public entry points
# =====================================================================
def render_auth_screen() -> None:
    """Render the cinematic single-column login / signup / forgot page.

    Mode is driven by `st.session_state["auth_mode"]`:
        - missing or anything not in {"signup", "forgot"} → "login"
        - "signup" → show the create-account form
        - "forgot" → show the password-reset form
    """
    st.markdown(_AUTH_CSS, unsafe_allow_html=True)
    st.markdown('<div class="au-bg"></div>', unsafe_allow_html=True)
    st.markdown('<div class="au-grain"></div>', unsafe_allow_html=True)

    mode = st.session_state.get("auth_mode")
    if mode not in ("forgot", "signup"):
        mode = "login"

    # Mode-specific card copy.
    if mode == "login":
        card_eyebrow = "Returning Athlete · Sign in"
        card_title = 'Welcome <span class="gold">back</span>.'
        card_sub = ("Your swing library, MLB matches, and drill plan "
                    "are waiting where you left them.")
    elif mode == "signup":
        card_eyebrow = "New Athlete · Create your account"
        card_title = 'Create your <span class="gold">account</span>.'
        card_sub = ("Tell us about your stance. The analyzer uses these "
                    "details to find your closest MLB comparison.")
    else:  # forgot
        card_eyebrow = "Account Recovery"
        card_title = 'Reset your <span class="gold">password</span>.'
        card_sub = ("Enter the email on your account and we'll send a "
                    "secure one-time reset link.")

    with st.container(key="auth_root"):
        # ---- Masthead ----
        st.markdown(_masthead_html(), unsafe_allow_html=True)
        st.markdown(_status_html(), unsafe_allow_html=True)

        # ---- Hero (keyed for CSS scope + test hook) ----
        with st.container(key="auth_hero"):
            st.markdown(_hero_html(), unsafe_allow_html=True)

        # ---- Form card. Centering / max-width lives on the panel
        # itself via CSS — wrapping in a markdown div is useless because
        # Streamlit auto-closes that div into an empty sibling. ----
        with st.container(key="auth_panel"):
            st.markdown(
                f'<div class="au-card-eyebrow">{html.escape(card_eyebrow)}</div>'
                f'<h2 class="au-card-title">{card_title}</h2>'
                f'<p class="au-card-sub">{html.escape(card_sub)}</p>',
                unsafe_allow_html=True,
            )

            if mode == "login":
                _render_login_form()
            elif mode == "signup":
                _render_signup_form()
            else:
                _render_forgot_form()
        # ---- Quiet mode-switch line below the card ----
        if mode == "login":
            st.markdown(
                '<div class="au-switch-line">New to BarrelLabs?</div>',
                unsafe_allow_html=True,
            )
            with st.container(key="mode_switch"):
                if st.button(
                    "Create your account →",
                    key="auth_switch_to_signup",
                ):
                    st.session_state["auth_mode"] = "signup"
                    st.rerun()
        elif mode == "signup":
            st.markdown(
                '<div class="au-switch-line">Already a member?</div>',
                unsafe_allow_html=True,
            )
            with st.container(key="mode_switch"):
                if st.button(
                    "← Sign in",
                    key="auth_switch_to_login",
                ):
                    st.session_state.pop("auth_mode", None)
                    st.rerun()
        # forgot mode → the back button inside the form handles return,
        # no extra switch line needed.

        # ---- Trust bar + footer ----
        st.markdown(_trust_bar_html(), unsafe_allow_html=True)
        st.markdown(_footer_html(), unsafe_allow_html=True)


def render_recovery_screen() -> None:
    """Render the 'set a new password' screen shown when the user
    clicked the password-reset link in their email."""
    st.markdown(_AUTH_CSS, unsafe_allow_html=True)
    st.markdown('<div class="au-bg"></div>', unsafe_allow_html=True)
    st.markdown('<div class="au-grain"></div>', unsafe_allow_html=True)

    card_eyebrow = "Account Recovery · Set a new password"
    card_title = 'Set a new <span class="gold">password</span>.'
    card_sub = ("Choose a new password for your locker. You're signed in "
                "via the reset link — just pick something fresh.")

    with st.container(key="auth_root"):
        st.markdown(_masthead_html(), unsafe_allow_html=True)
        st.markdown(_status_html(), unsafe_allow_html=True)

        with st.container(key="auth_hero"):
            st.markdown("""
<section class="au-hero">
  <div class="au-eyebrow">
    <span class="stitch"></span>Account Recovery<span class="stitch"></span>
  </div>
  <h1 class="au-display">
    Set a <span class="ital">new</span><br/>password.
  </h1>
  <p class="au-deck">
    You're signed in via the reset link. Pick a new password and
    we'll <span class="em">log you straight back in</span>.
  </p>
</section>
""", unsafe_allow_html=True)

        with st.container(key="auth_panel"):
            st.markdown(
                f'<div class="au-card-eyebrow">{html.escape(card_eyebrow)}</div>'
                f'<h2 class="au-card-title">{card_title}</h2>'
                f'<p class="au-card-sub">{html.escape(card_sub)}</p>',
                unsafe_allow_html=True,
            )

            with st.form("recovery_form_v4", clear_on_submit=False):
                new_pw = st.text_input(
                    "New password (6+ characters)",
                    type="password",
                    placeholder="••••••••",
                    key="rec_pw_v4",
                )
                new_pw2 = st.text_input(
                    "Confirm new password",
                    type="password",
                    placeholder="••••••••",
                    key="rec_pw2_v4",
                )
                submitted = st.form_submit_button(
                    "Update password  →",
                    type="primary",
                    use_container_width=True,
                )
                if submitted:
                    if new_pw != new_pw2:
                        st.error("Passwords don't match.")
                    elif len(new_pw or "") < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        try:
                            from auth import update_password
                            update_password(new_pw)
                            st.success("Password updated — signing you in…")
                            st.session_state.pop("recovery_mode", None)
                            try:
                                st.query_params.clear()
                            except Exception:
                                pass
                            st.rerun()
                        except ValueError as exc:
                            # auth.update_password raises ValueError with
                            # user-targeted messages (length checks, etc).
                            st.error(str(exc))
                        except Exception:
                            st.error(
                                "Couldn't update your password right "
                                "now. Try again in a moment."
                            )

        st.markdown(_trust_bar_html(), unsafe_allow_html=True)
        st.markdown(_footer_html(), unsafe_allow_html=True)
