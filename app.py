"""
Streamlit UI for the baseball swing analyzer — clean visual version.

Run from the project root with the venv python:
    ./venv/bin/python -m streamlit run app.py

Design principle: nothing important should be a wall of monospace text.
The score, the top fixes, and the drill plan all render as proper visual
cards. Raw numbers, full metric tables, and the under-the-hood details are
tucked into expanders so a curious user can dig in, but they don't dominate
the page.

Auth: the whole app sits behind a login/signup screen. A user must be
authenticated (st.session_state.user) before they can upload a video.
"""

import html
import json
import sys
from pathlib import Path

import streamlit as st
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from analyzer import analyze, age_from_birth_year
from bl_theme import inject_global_theme
from pose_init import classify_detect_phases_failure
from upload_paths import unique_upload_name
from proc_utils import run_subprocess
from development_tracker import render_development_tracker
from historical_charts import render_historical_charts
from drill_library import render_drill_library
from pricing import render_pricing_page
from saved_reports_dashboard import render_saved_reports_dashboard
from swing_report import render_swing_report, build_swing_report_pdf
from player_storage import (
    authenticate,
    create_account,
    load_swing_history,
    load_swing_meta,
    save_swing_meta,
    save_swing_record,
    update_profile,
)
from entitlements import (
    can_analyze_swing,
    can_analyze_swing_for_account,
    is_pro,
    plan_display_name,
    FREE_SWING_LIMIT,
    SOLO_PLAN_ID,
)
from subscription_storage import (
    load_my_plan,
    increment_free_swing_count,
    record_successful_swing_analysis,
    charge_nonce_for,
    clear_charge_nonce,
)

# Note: GLOBAL PAGE ROUTING is intentionally placed AFTER set_page_config
# and the premium CSS injection (further below) so sub-pages inherit the
# same theme. Don't move this block above those without moving them too.

# ---------- PATHS ----------
PROJECT_ROOT = Path(__file__).parent.resolve()
UPLOAD_DIR   = PROJECT_ROOT / "uploads_streamlit"
UPLOAD_DIR.mkdir(exist_ok=True)

# Bound local disk growth: drop stale uploads + analysis artifacts once per
# session (only files older than the cutoff, so in-flight analyses are
# untouched). Durable copies of swings live in Supabase.
try:
    from cleanup_utils import prune_stale_files
    if not st.session_state.get("_pruned_stale_files"):
        prune_stale_files(UPLOAD_DIR, PROJECT_ROOT)
        st.session_state["_pruned_stale_files"] = True
except Exception:
    pass

PROFILE_PIC_DIR = PROJECT_ROOT / "profile_pics"
PROFILE_PIC_DIR.mkdir(exist_ok=True)

PY = sys.executable  # same python streamlit is running under = has mediapipe


def show_barrellabs_logo(width=220):
    logo = PROJECT_ROOT / "barrellabs_logo.png"
    if logo.exists():
        st.image(str(logo), width=width)

# ---------- PAGE CONFIG ----------
st.set_page_config(
    page_title="BarrelLabs | Swing Analysis",
    layout="wide",
)

# App-wide em-dash scrubber: wrap st.markdown / st.html / components.html so no
# em dashes reach any page's copy (generated headlines, narratives, drill text,
# trend insights, ...). Installed before any rendering. Operates on output, so
# source/CSS comments are never touched.
from text_clean import install_em_dash_scrubber
install_em_dash_scrubber()

# Error monitoring (Sentry) — reports unhandled prod errors so they don't fail
# silently. No-op until a DSN is configured (see monitoring.py). Never blocks.
try:
    from monitoring import init_monitoring
    init_monitoring()
except Exception:
    pass

# ---------- PERFORMANCE DASHBOARD STYLING ----------
st.markdown("""
<style>
/* Hidden broken icon text */


/* FORCE FIX: clean uploader button */
div[data-testid="stFileUploader"] button {
    font-size: 0 !important;
}

div[data-testid="stFileUploader"] button * {
    font-size: 0 !important;
}

div[data-testid="stFileUploader"] button::after {
    content: "Upload Swing";
    font-size: 0.95rem !important;
    font-weight: 800 !important;
    color: white !important;
}

/* Hide any custom upload overlay Claude added */
.bl-upload-label,
.bl-upload-label-text,
.bl-upload-label-dot,
.bl-upload-label-sub {
    display: none !important;
}
</style>

<style>
    /* ============ ROOT / TYPOGRAPHY ============ */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

    html, body, [class*="css"], .stApp, .stApp * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
    }

    /* CRITICAL: preserve Streamlit's Material Symbols icon font on icon
       spans. Without this exception, the global Inter override above
       replaces the icon font and the raw ligature text (e.g.
       "keyboard_double_arrow_left") shows on the sidebar collapse
       button, expanders, and other Streamlit chrome. */
    [class*="material-symbols"],
    [class*="MaterialSymbols"],
    .material-symbols-rounded,
    .material-symbols-outlined,
    .material-symbols-sharp,
    .material-icons,
    .material-icons-rounded,
    .material-icons-outlined,
    i[class*="material"],
    span[class*="material"],
    [class*="material-symbols"] *,
    [class*="MaterialSymbols"] * {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                     'Material Icons', 'Material Icons Outlined' !important;
        font-feature-settings: 'liga' !important;
        font-variant-ligatures: common-ligatures contextual !important;
        text-rendering: optimizeLegibility !important;
        -webkit-font-feature-settings: 'liga' !important;
    }

    /* Belt-and-suspenders: any element whose text content matches a
       Material Symbol ligature name and is failing to render as an icon
       gets visually hidden. Targets the sidebar collapse arrow + any
       other Streamlit chrome that may leak ligature text on hover. */
    [data-testid="stSidebarCollapseButton"] span:not([class*="material"]),
    [data-testid="stSidebarCollapsedControl"] span:not([class*="material"]) {
        font-size: 0 !important;
    }

    /* ===== EXPANDER CHEVRON FIX =====
       Streamlit's st.expander chevron is a Material Symbols ligature.
       The global Inter override stomps on its font, so the raw text
       "keyboard_arrow_right" leaks as "_arrow_right" over the expander
       label. Three layers: restore icon font, hide raw text as fallback,
       enforce flex layout so nothing overflows. */
    [data-testid="stExpander"] summary span,
    [data-testid="stExpander"] details summary span,
    [data-testid="stExpander"] summary svg,
    [data-testid="stExpanderToggleIcon"],
    [data-testid="stExpanderToggleIcon"] *,
    details summary span:first-child,
    details > summary > span,
    [data-testid="stExpander"] [class*="Icon"],
    [data-testid="stExpander"] [class*="icon"] {
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined',
                     'Material Icons', 'Material Icons Outlined' !important;
        font-feature-settings: 'liga' !important;
        font-variant-ligatures: common-ligatures contextual !important;
        -webkit-font-feature-settings: 'liga' !important;
        text-rendering: optimizeLegibility !important;
    }
    /* Nuclear fallback: zero out the raw ligature text if it can't
       resolve, restore size on any inner element that does carry an
       icon class so real icons still render. */
    [data-testid="stExpander"] summary > span:first-child {
        font-size: 0 !important;
    }
    [data-testid="stExpander"] summary > span:first-child > *,
    [data-testid="stExpander"] summary > span:first-child svg,
    [data-testid="stExpander"] summary > span:first-child [class*="material"] {
        font-size: 1rem !important;
    }
    [data-testid="stExpander"] summary {
        display: flex !important;
        align-items: center !important;
        gap: 0.5rem !important;
    }

    .stApp {
        background:
            radial-gradient(ellipse 1200px 700px at 12% -5%, rgba(220,38,38,0.12) 0%, rgba(220,38,38,0) 60%),
            radial-gradient(ellipse 1000px 600px at 105% 5%, rgba(30,58,138,0.32) 0%, rgba(30,58,138,0) 65%),
            linear-gradient(180deg, #05070b 0%, #070a12 50%, #050810 100%);
        background-attachment: fixed;
        color: #f4f5f8;
    }

    /* Hide ALL Streamlit chrome — every conceivable selector across versions */
    #MainMenu {visibility: hidden !important; display: none !important;}
    footer {visibility: hidden !important; display: none !important;}
    header,
    header[data-testid="stHeader"],
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stToolbarActions"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"],
    [data-testid="stAppDeployButton"],
    .stAppDeployButton,
    .stDeployButton,
    .stApp > header,
    .stApp [data-testid="stHeader"] {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        max-height: 0 !important;
        visibility: hidden !important;
        position: absolute !important;
        top: -9999px !important;
    }
    /* Pull the main view up against the very top of the viewport */
    .stApp { padding-top: 0 !important; }
    section.main, [data-testid="stMain"] { padding-top: 0 !important; }
    [data-testid="stAppViewContainer"] { padding-top: 0 !important; top: 0 !important; }
    [data-testid="stAppViewBlockContainer"] { padding-top: 0 !important; }

    .block-container,
    [data-testid="stMainBlockContainer"],
    section.main > div.block-container {
        max-width: 1240px !important;
        padding-top: 0.3rem !important;
        padding-bottom: 5rem !important;
    }

    h1 {
        font-size: 2.5rem !important;
        font-weight: 900 !important;
        letter-spacing: -0.045em;
        margin-bottom: 0.2rem !important;
        background: linear-gradient(180deg, #ffffff 0%, #c8ccd6 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    h2, h3, h4 {
        letter-spacing: -0.025em;
        color: #f4f5f8;
    }

    /* ============ SIDEBAR ============ */
    section[data-testid="stSidebar"] {
        background:
            linear-gradient(180deg, rgba(10,13,20,0.92) 0%, rgba(8,10,16,0.96) 100%);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border-right: 1px solid rgba(255,255,255,0.06);
    }
    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.4rem;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.06) !important;
        margin: 1rem 0 !important;
    }

    /* ============ GLASS / CARD PRIMITIVES ============ */
    .bl-glass {
        background: linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.02) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        box-shadow: 0 8px 32px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04);
    }

    .bl-card {
        background: linear-gradient(180deg, rgba(17,21,30,0.92) 0%, rgba(11,14,22,0.96) 100%);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 18px;
        padding: 1.1rem 1.2rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        transition: border-color .2s ease, transform .2s ease, box-shadow .2s ease;
    }
    .bl-card:hover {
        border-color: rgba(239,68,68,0.35);
        box-shadow: 0 10px 32px rgba(220,38,38,0.18), 0 4px 20px rgba(0,0,0,0.4);
    }

    /* Red stitching divider, used as an accent under hero titles */
    .bl-stitch {
        height: 2px;
        background:
            repeating-linear-gradient(90deg,
                rgba(239,68,68,0.95) 0 14px,
                transparent 14px 24px);
        border-radius: 2px;
        margin: .55rem 0 1.1rem 0;
        width: 96px;
    }

    /* ============ HERO HEADER ============ */
    .bl-hero {
        background: linear-gradient(135deg, rgba(15,19,27,0.95) 0%, rgba(20,28,48,0.85) 60%, rgba(40,12,16,0.5) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 22px;
        padding: 1.6rem 1.8rem;
        margin-bottom: 1.4rem;
        position: relative;
        overflow: hidden;
        box-shadow: 0 18px 50px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.05);
    }
    .bl-hero::after {
        content: "";
        position: absolute;
        right: -80px; top: -80px;
        width: 280px; height: 280px;
        background: radial-gradient(circle, rgba(239,68,68,0.18) 0%, rgba(239,68,68,0) 70%);
        pointer-events: none;
    }

    .bl-eyebrow {
        color: #ef4444;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        font-size: 0.7rem;
        font-weight: 900;
        margin-bottom: 0.4rem;
    }
    .bl-eyebrow-muted {
        color: #6b7280;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        font-size: 0.68rem;
        font-weight: 800;
    }
    .bl-title {
        font-size: 2.25rem;
        font-weight: 900;
        letter-spacing: -0.045em;
        line-height: 1.05;
        color: #ffffff;
    }
    .bl-subtitle {
        color: #9aa0ac;
        font-size: 1rem;
        margin-top: .5rem;
        line-height: 1.5;
        max-width: 720px;
    }

    /* ============ STREAMLIT METRIC OVERRIDE ============ */
    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, rgba(17,21,30,0.95) 0%, rgba(11,14,22,0.98) 100%);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 16px;
        padding: 1rem 1.1rem;
        box-shadow: 0 4px 16px rgba(0,0,0,0.35);
        min-height: 96px;
    }
    div[data-testid="stMetricLabel"] {
        color: #8b909c;
        font-size: 0.7rem !important;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        font-weight: 800 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #ffffff;
        font-weight: 850 !important;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: unset !important;
        line-height: 1.2 !important;
        word-break: normal !important;
    }

    /* ============ FILE UPLOADER ============ */
    div[data-testid="stFileUploader"] {
        background: linear-gradient(180deg, rgba(17,21,30,0.85) 0%, rgba(11,14,22,0.92) 100%);
        border: 1.5px dashed rgba(239,68,68,0.35);
        border-radius: 18px;
        padding: 1rem;
        transition: border-color .2s ease, background .2s ease;
    }
    div[data-testid="stFileUploader"]:hover {
        border-color: rgba(239,68,68,0.7);
        background: linear-gradient(180deg, rgba(22,16,20,0.9) 0%, rgba(15,10,14,0.95) 100%);
    }
    div[data-testid="stFileUploader"] button {
        background: rgba(239,68,68,0.12) !important;
        border: 1px solid rgba(239,68,68,0.35) !important;
        color: #f4f5f8 !important;
        border-radius: 10px !important;
        font-weight: 700 !important;
    }

    /* ============ EXPANDERS ============ */
    div[data-testid="stExpander"] {
        background: linear-gradient(180deg, rgba(17,21,30,0.85) 0%, rgba(11,14,22,0.92) 100%);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 14px;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 700 !important;
    }

    /* ============ CONTAINERS (used by st.container(border=True)) ============ */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: linear-gradient(180deg, rgba(17,21,30,0.85) 0%, rgba(11,14,22,0.92) 100%);
        border: 1px solid rgba(255,255,255,0.07) !important;
        border-radius: 16px !important;
    }

    /* ============ BUTTONS ============ */
    .stButton > button,
    .stDownloadButton > button,
    .stFormSubmitButton > button {
        background: linear-gradient(180deg, rgba(28,32,42,0.95) 0%, rgba(18,22,30,0.95) 100%);
        color: #f4f5f8;
        border: 1px solid rgba(255,255,255,0.09);
        border-radius: 12px;
        font-weight: 700;
        padding: 0.55rem 1rem;
        letter-spacing: -0.005em;
        transition: all 0.18s ease;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }
    .stButton > button:hover,
    .stDownloadButton > button:hover,
    .stFormSubmitButton > button:hover {
        background: linear-gradient(180deg, rgba(38,42,52,0.98) 0%, rgba(24,28,36,0.98) 100%);
        border-color: rgba(239,68,68,0.5);
        box-shadow: 0 4px 18px rgba(239,68,68,0.18), 0 2px 8px rgba(0,0,0,0.4);
        transform: translateY(-1px);
    }

    button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"] {
        background: linear-gradient(180deg, #ef4444 0%, #b91c1c 100%) !important;
        border: 1px solid rgba(255,255,255,0.18) !important;
        color: #ffffff !important;
        border-radius: 12px !important;
        font-weight: 800 !important;
        letter-spacing: 0.005em !important;
        box-shadow: 0 4px 14px rgba(220,38,38,0.35), inset 0 1px 0 rgba(255,255,255,0.18) !important;
    }
    button[kind="primary"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover {
        background: linear-gradient(180deg, #f87171 0%, #dc2626 100%) !important;
        box-shadow: 0 8px 22px rgba(239,68,68,0.5), inset 0 1px 0 rgba(255,255,255,0.22) !important;
        transform: translateY(-1px);
    }

    /* ============ INPUTS ============ */
    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea,
    div[data-baseweb="select"] > div {
        background: rgba(11,14,22,0.85) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        border-radius: 10px !important;
        color: #f4f5f8 !important;
    }
    .stTextInput input:focus,
    .stNumberInput input:focus,
    .stTextArea textarea:focus {
        border-color: rgba(239,68,68,0.5) !important;
        box-shadow: 0 0 0 2px rgba(239,68,68,0.15) !important;
    }

    /* ============ TABS ============ */
    .stTabs [data-baseweb="tab-list"] {
        background: rgba(11,14,22,0.6);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
        padding: 4px;
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px !important;
        color: #9aa0ac !important;
        font-weight: 700 !important;
        padding: 0.45rem 1rem !important;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(180deg, rgba(239,68,68,0.18) 0%, rgba(220,38,38,0.08) 100%) !important;
        color: #ffffff !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
    }
    .stTabs [data-baseweb="tab-highlight"] {
        background: transparent !important;
    }

    /* ============ MISC ============ */
    hr {
        border-color: rgba(255,255,255,0.06) !important;
    }
    .small-muted {
        color: #8b909c;
        font-size: 0.9rem;
    }
    .full-text-card {
        white-space: normal !important;
        overflow-wrap: anywhere;
        word-break: normal;
    }

    /* Progress bar */
    .stProgress > div > div > div > div {
        background: linear-gradient(90deg, #ef4444 0%, #dc2626 100%) !important;
    }

    /* Dataframe darker */
    div[data-testid="stDataFrame"] {
        background: rgba(11,14,22,0.85);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px;
    }

    /* ============ AUTH SCREEN ============ */
    .auth-bg {
        position: fixed; inset: 0;
        background:
            radial-gradient(ellipse 900px 600px at 25% 20%, rgba(220,38,38,0.18) 0%, transparent 60%),
            radial-gradient(ellipse 900px 600px at 80% 80%, rgba(30,58,138,0.28) 0%, transparent 60%);
        z-index: -1;
        pointer-events: none;
    }
    .auth-hero {
        text-align: center;
        padding-top: .5rem;
        margin-bottom: 1.6rem;
    }
    .auth-eyebrow {
        color: #ef4444;
        font-size: .72rem;
        font-weight: 900;
        letter-spacing: .22em;
        text-transform: uppercase;
        margin-bottom: .65rem;
    }
    .auth-title {
        font-size: 2.9rem;
        font-weight: 950;
        letter-spacing: -.05em;
        line-height: 1.05;
        background: linear-gradient(180deg, #ffffff 0%, #c8ccd6 80%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .auth-sub {
        color: #9aa0ac;
        margin-top: .7rem;
        font-size: 1.04rem;
        max-width: 540px;
        margin-left: auto;
        margin-right: auto;
        line-height: 1.5;
    }
    .auth-card {
        background: linear-gradient(180deg, rgba(17,21,30,0.85) 0%, rgba(11,14,22,0.92) 100%);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 22px;
        padding: 1.4rem 1.6rem 1.2rem 1.6rem;
        box-shadow: 0 24px 60px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.05);
        backdrop-filter: blur(14px);
    }
    .auth-feature {
        display: flex; align-items: center; gap: .6rem;
        color: #c8ccd6;
        font-size: .85rem;
        margin: .35rem 0;
    }
    .auth-feature-dot {
        width: 6px; height: 6px; border-radius: 999px;
        background: #ef4444;
        box-shadow: 0 0 8px rgba(239,68,68,0.7);
    }

    /* ============ SIDEBAR NAV BUTTONS ============ */
    .bl-nav-group-label {
        color: #6b7280;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.65rem;
        font-weight: 900;
        margin: .4rem 0 .55rem 0;
    }
    .bl-sidebar-brand {
        text-align: center;
        padding: 0.4rem 0 1rem 0;
    }
    .bl-sidebar-brand-name {
        font-size: 1.1rem;
        font-weight: 950;
        letter-spacing: 0.22em;
        color: #ffffff;
        margin-top: .4rem;
    }
    .bl-sidebar-brand-tagline {
        font-size: 0.6rem;
        font-weight: 800;
        letter-spacing: 0.26em;
        color: #ef4444;
        margin-top: 4px;
    }
    .bl-sidebar-profile {
        background: linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 16px;
        padding: 1rem;
        margin-bottom: .8rem;
        text-align: center;
    }
    .bl-avatar-frame {
        width: 96px; height: 96px;
        border-radius: 999px;
        border: 2px solid rgba(239,68,68,0.45);
        padding: 3px;
        margin: 0 auto .7rem auto;
        background: linear-gradient(180deg, rgba(239,68,68,0.18), rgba(30,58,138,0.18));
    }
    .bl-avatar-fallback {
        width: 100%; height: 100%;
        border-radius: 999px;
        background: linear-gradient(180deg, #1a1f2b 0%, #0c1018 100%);
        display:flex; align-items:center; justify-content:center;
        font-size: 2rem;
        color: #9aa0ac;
    }
    .bl-chip {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.06);
        border-radius: 10px;
        padding: 0.45rem 0.3rem;
        text-align: center;
    }
    .bl-chip-label {
        font-size: 0.6rem;
        color: #6b7280;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        font-weight: 800;
    }
    .bl-chip-value {
        font-weight: 850;
        color: #f4f5f8;
        margin-top: 2px;
        font-size: 0.95rem;
    }

    /* ============ WORKFLOW STEP CARDS ============ */
    .bl-step-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 0.8rem;
        margin: 1rem 0 1.4rem 0;
    }
    .bl-step {
        background: linear-gradient(180deg, rgba(17,21,30,0.92) 0%, rgba(11,14,22,0.96) 100%);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 16px;
        padding: 1rem 1.1rem;
        position: relative;
        overflow: hidden;
        transition: border-color .2s ease, transform .2s ease;
    }
    .bl-step:hover {
        border-color: rgba(239,68,68,0.4);
        transform: translateY(-2px);
    }
    .bl-step-num {
        font-size: 0.65rem;
        font-weight: 900;
        letter-spacing: 0.18em;
        color: #ef4444;
        text-transform: uppercase;
    }
    .bl-step-title {
        font-weight: 850;
        margin-top: 0.4rem;
        color: #f4f5f8;
        font-size: 1rem;
    }
    .bl-step-desc {
        color: #8b909c;
        font-size: 0.78rem;
        margin-top: .25rem;
        line-height: 1.4;
    }

    /* ============ "Mode" pill ============ */
    .bl-mode-pill {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        background: rgba(34,197,94,0.1);
        border: 1px solid rgba(34,197,94,0.35);
        color: #4ade80;
        border-radius: 999px;
        padding: 0.35rem 0.85rem;
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }
    .bl-mode-pill-dot {
        width: 7px; height: 7px;
        border-radius: 999px;
        background: #22c55e;
        box-shadow: 0 0 8px rgba(34,197,94,0.8);
    }

    /* ============ Welcome-hero layout (was inline styles) ============ */
    .bl-hero-row {
        display: flex; align-items: flex-start; justify-content: space-between;
        gap: 1.5rem; position: relative; z-index: 1;
    }
    .bl-hero-main { flex: 1; min-width: 0; }
    .bl-hero-meta {
        display: flex; flex-direction: column; align-items: flex-end;
        gap: 0.6rem; min-width: 200px;
    }
    .bl-hero-version {
        font-size: 0.7rem; color: #6b7280; letter-spacing: 0.12em;
        text-transform: uppercase; font-weight: 800;
    }

    /* ============ MOBILE: stack the hero + step grid (phones) ============
       Without these the LIVE pill (forced to min-width:200px) overflowed the
       right edge and the 4-up step grid crushed into unreadable columns. */
    @media (max-width: 760px) {
        .bl-hero-row { flex-direction: column; gap: 0.9rem; }
        .bl-hero-meta {
            flex-direction: row; align-items: center; min-width: 0;
            gap: 0.8rem; flex-wrap: wrap;
        }
        .bl-step-grid { grid-template-columns: repeat(2, 1fr) !important; }
    }
    @media (max-width: 360px) {
        .bl-step-grid { grid-template-columns: 1fr !important; }
    }
</style>
""", unsafe_allow_html=True)
# NOTE: The auth-bg overlay div is intentionally NOT rendered here. It was
# previously being injected globally and leaking onto the dashboard / sub-pages,
# causing perceived "overlays" on cards and the drill plan section. It is now
# only injected from inside render_auth_screen() where it belongs.


# ---------- PREMIUM POLISH OVERRIDES ----------
# Surgical refinements layered on top of the main theme. Kept in a second
# block so the original theme stays diff-able and easy to roll back.
st.markdown("""
<style>
    /* ---- Suppress any accidental Material Symbols ligature rendering.
       Forces the system font on all button labels so character sequences
       like "_arrow_right" can never resolve to icons. */
    .stButton button,
    .stDownloadButton button,
    .stFormSubmitButton button,
    .stButton button *,
    .stDownloadButton button *,
    .stFormSubmitButton button * {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        font-feature-settings: "liga" 0, "clig" 0 !important;
        font-variant-ligatures: none !important;
    }

    /* ============ SIDEBAR NAV TABS — cleaner, app-like ============ */
    section[data-testid="stSidebar"] .stButton > button {
        background: transparent;
        border: 1px solid transparent;
        color: #c8ccd6;
        text-align: left;
        font-weight: 600;
        font-size: 0.88rem;
        letter-spacing: -0.005em;
        padding: 0.55rem 0.85rem;
        margin: 2px 0;
        border-radius: 10px;
        justify-content: flex-start !important;
        box-shadow: none;
        transition: background .15s ease, color .15s ease, border-color .15s ease;
        position: relative;
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,0.04);
        border-color: rgba(255,255,255,0.08);
        color: #ffffff;
        transform: none;
        box-shadow: none;
    }
    section[data-testid="stSidebar"] .stButton > button:hover::before {
        content: "";
        position: absolute;
        left: 0; top: 22%; bottom: 22%;
        width: 2px;
        border-radius: 2px;
        background: linear-gradient(180deg, #ef4444 0%, #b91c1c 100%);
        box-shadow: 0 0 8px rgba(239,68,68,0.5);
    }
    section[data-testid="stSidebar"] .stButton > button:focus {
        outline: none;
        box-shadow: none;
    }
    section[data-testid="stSidebar"] .stButton > button p {
        font-size: 0.88rem !important;
        font-weight: 600 !important;
    }

    /* Sidebar nav group label — tighter, more refined */
    .bl-nav-group-label {
        margin: 0.6rem 0 0.3rem 0.4rem !important;
        font-size: 0.6rem !important;
        letter-spacing: 0.18em !important;
    }

    /* Sidebar dividers more subtle */
    section[data-testid="stSidebar"] hr {
        margin: 0.7rem 0 !important;
        opacity: 0.5;
    }

    /* Profile card a touch more refined */
    .bl-sidebar-profile {
        padding: 0.9rem 0.85rem !important;
    }

    /* ============ TYPOGRAPHY HIERARCHY ============ */
    .bl-eyebrow {
        font-size: 0.62rem !important;
        letter-spacing: 0.22em !important;
        margin-bottom: 0.5rem !important;
        font-weight: 800 !important;
    }
    .bl-title {
        font-size: 2.05rem !important;
        letter-spacing: -0.04em !important;
        line-height: 1.08 !important;
    }
    .bl-subtitle {
        font-size: 0.92rem !important;
        color: #8b909c !important;
        font-weight: 400 !important;
        line-height: 1.55 !important;
        margin-top: 0.6rem !important;
    }
    .bl-stitch {
        height: 1.5px !important;
        margin: 0.7rem 0 0.95rem 0 !important;
        width: 72px !important;
        opacity: 0.85;
    }

    /* Hero block — softer, less heavy shadow */
    .bl-hero {
        padding: 1.5rem 1.7rem !important;
        margin-bottom: 1.6rem !important;
        box-shadow: 0 12px 36px rgba(0,0,0,0.32), inset 0 1px 0 rgba(255,255,255,0.04) !important;
    }

    /* Step cards — tighter, more refined */
    .bl-step {
        padding: 0.85rem 1rem !important;
        border-radius: 14px !important;
    }
    .bl-step-num {
        font-size: 0.58rem !important;
        letter-spacing: 0.2em !important;
    }
    .bl-step-title {
        font-size: 0.92rem !important;
        margin-top: 0.35rem !important;
    }
    .bl-step-desc {
        font-size: 0.74rem !important;
        margin-top: 0.18rem !important;
    }

    /* ============ UPLOAD BOX — premium feel, no overlap ============ */
    /* Hide the default uploader label - we render our own above it. */
    div[data-testid="stFileUploader"] > label {
        display: none !important;
    }
    div[data-testid="stFileUploader"] {
        background: linear-gradient(180deg, rgba(20,24,34,0.65) 0%, rgba(12,15,22,0.85) 100%) !important;
        border: 1.5px dashed rgba(239,68,68,0.28) !important;
        border-radius: 16px !important;
        padding: 1.3rem 1.4rem !important;
        transition: border-color .2s ease, background .2s ease, box-shadow .2s ease !important;
    }
    div[data-testid="stFileUploader"]:hover {
        border-color: rgba(239,68,68,0.55) !important;
        background: linear-gradient(180deg, rgba(22,18,24,0.7) 0%, rgba(14,12,18,0.92) 100%) !important;
        box-shadow: 0 0 0 4px rgba(239,68,68,0.06), 0 10px 30px rgba(0,0,0,0.35) !important;
    }
    div[data-testid="stFileUploader"] section {
        padding: 0 !important;
    }
    div[data-testid="stFileUploader"] section > div {
        gap: 0.55rem !important;
    }
    div[data-testid="stFileUploader"] section small {
        color: #6b7280 !important;
        font-size: 0.74rem !important;
    }
    div[data-testid="stFileUploader"] button {
        background: rgba(239,68,68,0.1) !important;
        border: 1px solid rgba(239,68,68,0.3) !important;
        color: #f4f5f8 !important;
        border-radius: 10px !important;
        font-weight: 700 !important;
        padding: 0.5rem 1.1rem !important;
        transition: all .15s ease !important;
    }
    div[data-testid="stFileUploader"] button:hover {
        background: rgba(239,68,68,0.18) !important;
        border-color: rgba(239,68,68,0.5) !important;
    }

    /* The custom label we render above the uploader */
/* ============ GLASS DEPTH — slightly subtler ============ */
    .bl-card, .bl-step, div[data-testid="stVerticalBlockBorderWrapper"] {
        box-shadow: 0 2px 12px rgba(0,0,0,0.28), inset 0 1px 0 rgba(255,255,255,0.03) !important;
    }
    .bl-card:hover, .bl-step:hover {
        box-shadow: 0 8px 24px rgba(220,38,38,0.12), 0 2px 12px rgba(0,0,0,0.32), inset 0 1px 0 rgba(255,255,255,0.04) !important;
    }
</style>
""", unsafe_allow_html=True)


# ===== GLOBAL PAGE ROUTING =====
# Placed AFTER the premium CSS injection so sub-pages inherit the theme.
#
# NOTE: development_tracker / historical_charts / drill_library used to be
# dispatched HERE (before the auth gate). That rendered their Edge masthead at
# a different element-tree position than the post-auth pages (dashboard,
# sessions, compare), so navigating between an early page and a late one made
# Streamlit tear down + rebuild the full-bleed masthead, leaving a stale
# "ghost" nav bar that flashed on every Library/Progress/Training-Plan click.
# They are now dispatched alongside the other nav pages (see "NAV PAGE ROUTING"
# further down) so the masthead renders at a consistent position and Streamlit
# reuses it in place. Keep pricing/legal here (they have no masthead).
if st.session_state.get("page") == "pricing":
    render_pricing_page()
    st.stop()

if st.session_state.get("page") == "legal_terms":
    from pathlib import Path
    if st.button("← Back", key="legal_terms_back"):
        prev = st.session_state.get("_legal_return_to") or "auth"
        st.session_state["page"] = prev
        st.rerun()
    md = Path("legal/TERMS.md").read_text()
    st.markdown(md, unsafe_allow_html=False)
    st.stop()

if st.session_state.get("page") == "legal_privacy":
    from pathlib import Path
    if st.button("← Back", key="legal_privacy_back"):
        prev = st.session_state.get("_legal_return_to") or "auth"
        st.session_state["page"] = prev
        st.rerun()
    md = Path("legal/PRIVACY.md").read_text()
    st.markdown(md, unsafe_allow_html=False)
    st.stop()


# ============================================================
# ---------- AUTH GATE ----------
# ============================================================
# Premium split-screen login/signup lives in `auth_screen.py`. The two
# entry points keep the exact contract the legacy in-app renderer had:
#   - render_auth_screen() sets st.session_state.user on success.
#   - render_recovery_screen() consumes a Supabase recovery token and
#     calls auth.update_password().
# All Supabase wiring (player_storage.authenticate / create_account,
# auth.request_password_reset / consume_recovery_url /
# consume_recovery_token_hash / update_password) is preserved.
from auth_screen import (
    render_auth_screen,
    render_recovery_screen,
)



# --- Recovery URL detection ------------------------------------------
# Supabase appends the recovery token to the URL as a *hash fragment*
# (after `#`), which Streamlit cannot see server-side. This tiny JS
# shim runs once on page load: if it spots a recovery hash, it rewrites
# the URL to put those values into the query string and reloads. The
# Python side then picks them up via st.query_params.
#
# IMPORTANT: Streamlit strips <script> tags from st.markdown(), so we
# must use st.components.v1.html() which renders in an iframe. From the
# iframe we reach back out to window.parent (same origin) to read and
# rewrite the top-level URL.
import streamlit.components.v1 as _components
_components.html(
    """
    <script>
    (function () {
        try {
            const w = window.parent;
            const h = (w && w.location && w.location.hash) || "";
            if (h && h.indexOf("access_token=") !== -1 && h.indexOf("type=recovery") !== -1) {
                const clean = h.startsWith("#") ? h.substring(1) : h;
                const target = w.location.pathname + "?" + clean;
                w.location.replace(target);
            }
        } catch (e) {
            // Cross-origin or other error — nothing we can do from here.
            console.warn("recovery shim failed", e);
        }
    })();
    </script>
    """,
    height=0,
)

# If the URL has a recovery token in its query params, hydrate the
# Supabase client with it so update_password() can authenticate the
# call, then show the new-password screen and stop here.
#
# We support TWO formats:
#   1. token_hash flow (preferred): ?token_hash=...&type=recovery
#      — clean, server-readable, generated when the email template
#      uses {{ .TokenHash }}.
#   2. access_token flow (legacy):  ?access_token=...&refresh_token=...&type=recovery
#      — only reachable via the JS shim or paste-URL fallback because
#      Supabase puts these in the URL hash fragment by default.
_qp = st.query_params

# Token-hash flow
if (
    _qp.get("type") == "recovery"
    and _qp.get("token_hash")
):
    try:
        from auth import consume_recovery_token_hash
        if consume_recovery_token_hash(token_hash=_qp.get("token_hash")):
            st.session_state["recovery_mode"] = True
            # Clear the token from the URL so a refresh doesn't try
            # to re-verify it (token_hashes are single-use).
            try:
                st.query_params.clear()
            except Exception:
                pass
        else:
            # Consume returned False (expired/used/invalid link). Don't fall
            # through silently to the normal screen, or the user has no idea
            # their reset link failed.
            st.session_state["recovery_link_error"] = True
    except Exception:
        st.session_state["recovery_link_error"] = True

# Access-token flow (kept for the paste-URL fallback)
elif (
    _qp.get("type") == "recovery"
    and _qp.get("access_token")
    and _qp.get("refresh_token")
):
    try:
        from auth import consume_recovery_url
        if consume_recovery_url(
            access_token=_qp.get("access_token"),
            refresh_token=_qp.get("refresh_token"),
        ):
            st.session_state["recovery_mode"] = True
            # Strip the access/refresh tokens from the URL so they don't
            # linger in browser history / Referer / proxy logs (a leaked
            # refresh token is account-takeover). Mirrors the token_hash
            # branch above.
            try:
                st.query_params.clear()
            except Exception:
                pass
        else:
            st.session_state["recovery_link_error"] = True
    except Exception:
        st.session_state["recovery_link_error"] = True

if st.session_state.get("recovery_mode"):
    render_recovery_screen()
    st.stop()

if st.session_state.pop("recovery_link_error", False):
    # The reset link was invalid/expired and didn't put us in recovery mode.
    # Tell the user explicitly instead of silently showing the login screen.
    st.error(
        "This password-reset link is invalid or has expired. Request a new one "
        "from the login screen and use it within the hour."
    )


# --- Auth gate -------------------------------------------------------
# On every Streamlit rerun, try to restore the user's profile from the
# active Supabase session. This means a refresh / hot-reload doesn't
# bounce a logged-in user back to the login screen.
#
# BL-008C.32: the durable-login bridge renders on EVERY run, here, before the
# gate. It applies any pending cookie write/clear, and while no one is signed
# in it reads the saved-login cookie in the browser and reports it back
# (Community Cloud's proxy strips cookies, so the server can't read them).
# Fixed position so its iframe isn't remounted.
try:
    import session_persist as _session_persist
    _session_persist.bridge()
except Exception:
    _session_persist = None
# A session that died mid-run (its refresh token was rejected -- e.g. another
# tab rotated it) is reset HERE, at the top of the next run, so the gate below
# re-reads the browser's current saved login instead of leaving a signed-in
# looking page with no session behind it.
if st.session_state.get("_bl_reauth_needed"):
    try:
        from supabase_client import reset_local_session as _reset_local
        _reset_local()
    except Exception:
        st.session_state.pop("_bl_reauth_needed", None)
if "user" not in st.session_state:
    # Durable login: a full reload / new tab has an empty in-memory session, so
    # first try to rebuild it from the persisted refresh-token cookie. Safe
    # no-op if there's no cookie or it's expired — we just fall through to the
    # normal restore + auth gate below.
    try:
        from supabase_client import restore_session_from_cookie
        restore_session_from_cookie()
    except Exception:
        pass
    try:
        from auth import current_profile
        _restored = current_profile()
        if _restored:
            st.session_state.user = _restored
    except Exception:
        pass

    try:
        import auth_diag as _auth_diag
        _auth_diag.event("auth_gate", authenticated="user" in st.session_state,
                         restore_error=bool(st.session_state.get("_auth_restore_error")))
    except Exception:
        pass

# BL-008C.32: while the browser hasn't reported yet whether it holds a saved
# login, show a neutral "restoring" state -- never the login form (which
# would flash for a signed-in user) and never app content. The bridge reports
# within one round trip; the button keeps a user with JS/iframes blocked
# from being stuck here.
if "user" not in st.session_state and _session_persist is not None \
        and _session_persist.restore_pending():
    if _session_persist.deliver_now():
        st.rerun()
    # BL-008C.36: this screen renders before the app theme is injected, so
    # it carries its own dark background -- it used to be light-grey text on
    # Streamlit's default WHITE page, i.e. it looked like a blank white
    # screen (e.g. after a dropped connection reconnects into a new session).
    st.markdown(
        "<style>[data-testid='stAppViewContainer'],[data-testid='stApp'],"
        "[data-testid='stHeader']{background:#0A0B0E !important;}</style>"
        "<div style='max-width:420px;margin:22vh auto 0;text-align:center;"
        "font-family:system-ui,sans-serif;color:#C8C4BB;'>"
        "<div style='font-size:1.05rem;'>Signing you back in&hellip;</div></div>",
        unsafe_allow_html=True,
    )
    _rc1, _rc2, _rc3 = st.columns([1, 1, 1])
    with _rc2:
        if st.button("Sign in instead", key="_bl_restore_skip_btn",
                     width="stretch"):
            _session_persist.skip_restore()
            st.rerun()
    st.stop()

# A successful-checkout redirect can land in a FRESH browser tab (auth is
# session-only), so the user isn't signed in here — don't dump them at a raw
# login form. Show a clean, on-brand "payment received" screen instead. Their
# payment is already synced by the Stripe webhook; they sign back in to enter
# Pro. (Without this, the post-checkout tab showed the bare login screen, which
# read as "it logged me out / nothing happened".)
if "user" not in st.session_state and _qp.get("checkout") == "success":
    st.markdown(
        "<style>"
        "@import url('https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600&family=Geist+Mono:wght@500;600&display=swap');"
        "[data-testid='stAppViewContainer'],[data-testid='stApp']{background:#0A0B0E;}"
        ".st-key-_co_success_signin button{border-radius:100px !important;"
        "background:#E8C170 !important;color:#1a1206 !important;border:none !important;"
        "font-family:'Geist Mono',monospace !important;font-weight:700 !important;"
        "letter-spacing:.12em !important;text-transform:uppercase !important;font-size:12px !important;"
        "box-shadow:0 14px 34px -14px rgba(232,193,112,.6) !important;}"
        ".st-key-_co_success_signin button:hover{background:#F4EFE6 !important;}"
        ".bl-co{max-width:560px;margin:14vh auto 0;text-align:center;"
        "font-family:'Geist',system-ui,sans-serif;color:#F4EFE6;}"
        ".bl-co-check{width:78px;height:78px;border-radius:50%;margin:0 auto 26px;"
        "display:flex;align-items:center;justify-content:center;font-size:2.1rem;"
        "color:#1a1206;background:linear-gradient(135deg,#E8C170,#C9A350);"
        "box-shadow:0 0 0 1px rgba(232,193,112,.5),0 18px 50px -16px rgba(232,193,112,.55);}"
        ".bl-co-eyebrow{font-family:'Geist Mono',monospace;font-size:11px;font-weight:600;"
        "letter-spacing:.24em;text-transform:uppercase;color:#E8C170;margin-bottom:14px;}"
        ".bl-co-title{font-family:'Instrument Serif',serif;font-size:3.2rem;line-height:1.04;"
        "letter-spacing:-.02em;margin:0 0 16px;}"
        ".bl-co-title .ital{font-style:italic;color:#E8C170;}"
        ".bl-co-sub{font-size:1.04rem;line-height:1.55;color:#C8C4BB;max-width:42ch;margin:0 auto;}"
        "</style>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='bl-co'>"
        "<div class='bl-co-check'>&#10003;</div>"
        "<div class='bl-co-eyebrow'>Payment received</div>"
        "<h1 class='bl-co-title'>Welcome to <span class='ital'>Pro.</span></h1>"
        "<p class='bl-co-sub'>Your subscription is active. Sign back in to jump into your "
        "upgraded account — or just close this tab and head back to the BarrelLabs tab "
        "you already had open.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _cs1, _cs2, _cs3 = st.columns([1, 1.4, 1])
    with _cs2:
        if st.button("Sign in to enter Pro →", type="primary",
                     use_container_width=True, key="_co_success_signin"):
            try:
                st.query_params.clear()
            except Exception:
                pass
            st.rerun()
    st.stop()

if "user" not in st.session_state:
    # BL-008C.32: a saved sign-in that couldn't be checked (network / Supabase
    # outage) is not the same as being signed out -- the cookie was kept, so
    # say so rather than silently presenting a plain login screen.
    if st.session_state.pop("_auth_restore_error", False):
        st.warning("We couldn't reach the sign-in service to restore your session. "
                   "Reload the page in a moment, or sign in below.")
    render_auth_screen()
    st.stop()


# From here on, the user is authenticated.
user = st.session_state.user


# --- Household sub-account picker gate --------------------------------
# A Family Pro login can hold up to N player profiles. When the household
# has >1 active profile and one hasn't been explicitly chosen THIS session,
# show the "Who's training?" picker before any page renders. The auth
# restore above (current_profile) may have auto-set st.session_state["player"]
# to the first profile; for multi-profile households we override that with an
# explicit pick. Solo/Free users (1 profile) never see the picker.
if st.session_state.get("_action") == "switch_profile":
    # Triggered by the nav "Switch profile" control — drop the active
    # profile + the picked flag so the picker reappears.
    st.session_state.pop("player", None)
    st.session_state.pop("_profile_picked", None)
    st.session_state.pop("_action", None)
    st.rerun()

if not st.session_state.get("_profile_picked"):
    try:
        import auth as _auth_pick
        _hh_uid = _auth_pick._current_user_id()
        if _hh_uid:
            _hh_profiles = _auth_pick.list_household_players(_hh_uid)
            if len(_hh_profiles) > 1:
                import household_picker
                household_picker.render_household_picker(_hh_uid)
                st.stop()
            # 0 or 1 profile → nothing to pick; resolve so we don't recheck.
            st.session_state["_profile_picked"] = True
    except Exception:
        # Picker must never wedge the app — fall through to normal render.
        st.session_state["_profile_picked"] = True


# --- Active player = single source of truth --------------------------
# auth writes BOTH profile edits (update_profile) and household
# child-switches (set_active_player) to st.session_state["player"]. The
# app-level `user` copy is only refreshed at login and on MLB lock-save,
# so it silently drifts: a freshly-entered birth year — or a switch to a
# different child — never reaches the upload flow, and the analysis would
# run the wrong age bracket. Re-point `user` at the active player on every
# render so age, the MLB lock, swing ownership and the settings form all
# read the player actually being trained.
if st.session_state.get("player"):
    st.session_state["user"] = st.session_state["player"]
    user = st.session_state["user"]
    # When the active player changes (household child-switch, re-pick, or a new
    # login), drop the per-player Player Settings "extras" (display name,
    # position slugs, grad year, default view/hand/focus, privacy toggles).
    # They live only in a single session-state dict keyed by nothing, so
    # without this they bleed from one child into another child's settings.
    _active_pid = (user or {}).get("id")
    if st.session_state.get("_active_player_id") != _active_pid:
        st.session_state["_active_player_id"] = _active_pid
        st.session_state.pop("player_settings_extras", None)
        # Drop the cached plan + per-player facility-sponsorship so the newly
        # active profile re-resolves its OWN entitlement (a sponsored kid must
        # not leak Pro to a non-sponsored sibling on profile switch).
        try:
            from subscription_storage import invalidate_my_plan_cache
            invalidate_my_plan_cache()
        except Exception:
            pass


# ---------- STRIPE CHECKOUT RETURN HANDLER ----------
# After a successful Checkout, Stripe redirects to ?checkout=success.
# We invalidate the plan cache (so the next entitlement check sees the
# new Pro subscription) and show a celebratory toast. The actual
# subscription row is written by the Stripe webhook — but if the user
# beats the webhook to a page render, the worst case is a few seconds
# where the cache shows the old plan; load_my_plan(force_refresh=True)
# below mitigates that for the immediate return view.
_checkout_status = _qp.get("checkout")
if _checkout_status == "success":
    try:
        from subscription_storage import invalidate_my_plan_cache, load_my_plan
        invalidate_my_plan_cache()
        # Force-refresh once so the dashboard shows the new plan even
        # if the webhook hasn't fired yet (small race condition window).
        load_my_plan(force_refresh=True)
    except Exception:
        pass
    st.success(
        "Welcome to Pro! Your subscription is active — enjoy the full app.",
        icon="🎉",
    )
    try:
        st.query_params.clear()
    except Exception:
        pass
elif _checkout_status == "cancel":
    st.info(
        "Checkout was canceled. You can pick a plan any time from the "
        "Pricing page.",
        icon="ℹ️",
    )
    try:
        st.query_params.clear()
    except Exception:
        pass


# ---------- SESSION EXPIRED GUARD ----------
# Storage helpers flag st.session_state["_session_expired"] when a PostgREST
# call returns JWT-expired (PGRST303). That happens when the Supabase
# access token's TTL is up *and* the proactive refresh in get_client()
# couldn't mint a new one (refresh token also expired/revoked, network
# blip, etc.). In that case the cached `user` dict is stale: the UI
# looks logged-in but every DB call will fail. Show one clean banner
# and a re-login CTA instead of letting raw Postgrest blobs leak into
# every section.
if st.session_state.get("_session_expired"):
    st.markdown("""
<style>
.bl-session-expired {
    margin: 1.2rem 0 0.4rem 0;
    border-radius: 14px;
    border: 1px solid rgba(251,191,36,0.35);
    background: linear-gradient(180deg, rgba(251,191,36,0.10), rgba(251,191,36,0.04));
    padding: 1.1rem 1.25rem;
}
.bl-session-expired-eyebrow {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.62rem; letter-spacing: 0.24em; font-weight: 700;
    color: #fbbf24; text-transform: uppercase; margin-bottom: 0.4rem;
}
.bl-session-expired-title {
    font-family: 'Inter', system-ui, sans-serif;
    font-size: 1.15rem; font-weight: 800; color: #fafafa;
    margin-bottom: 0.25rem; letter-spacing: -0.01em;
}
.bl-session-expired-sub {
    color: #d4d4d4; font-size: 0.92rem; line-height: 1.5;
}
</style>
<div class="bl-session-expired">
  <div class="bl-session-expired-eyebrow">Session expired</div>
  <div class="bl-session-expired-title">You've been signed out for security.</div>
  <div class="bl-session-expired-sub">
    Your Supabase session timed out. Click below to log back in &mdash; your saved swings and reports are safe.
  </div>
</div>
""", unsafe_allow_html=True)
    se_cols = st.columns([1, 1, 3])
    if se_cols[0].button("Log back in", type="primary",
                         width="stretch", key="session_expired_relogin"):
        # Hard reset: clear local session state and let the auth gate
        # re-render on the next run. BL-008C.32: reset, not clear_session --
        # the gate silently restores from the browser's saved login if another
        # tab holds a newer valid one, and shows the login form otherwise.
        try:
            from supabase_client import reset_local_session as _reset_sess
            _reset_sess()
        except Exception:
            pass
        for _k in ("user", "player", "_profile_picked", "_action",
                   "supabase_session", "auth_user",
                   "_session_expired", "page", "view",
                   "view_swing_path", "view_swing_record"):
            st.session_state.pop(_k, None)
        st.rerun()
    if se_cols[1].button("Dismiss", width="stretch", key="session_expired_dismiss"):
        # Just clear the flag — useful if get_client() managed to refresh
        # behind the scenes between renders.
        st.session_state.pop("_session_expired", None)
        st.rerun()
    st.stop()

# Inject the BarrelLabs global design system once at the top of every
# authenticated page render. This makes the design tokens (--bl-red,
# --bl-ink-*, --bl-line, --bl-radius-*, etc.) and the reusable component
# classes (.bl-page, .bl-card, .bl-cta, .bl-section-header, ...) available
# to ALL downstream views — dashboard, upload flow, saved report,
# development tracker, performance over time, compare swings, settings,
# billing. Pages can then layer page-local CSS on top without redefining
# the design language.
inject_global_theme()

# ---------- URL → session-state routing bridge ----------
# Lets deep-links like `/?page=saved_reports` actually navigate. The
# masthead nav is now pure-HTML <a href="?page=KEY"> anchors, so EVERY
# nav click arrives here. This MUST run BEFORE the "default to
# dashboard" fallback below: otherwise a fresh reload from a nav anchor
# (empty session_state) would get page="dashboard" assigned first, the
# dashboard route would st.stop() before the saved_reports route, and
# clicking Sessions would silently land on the Dashboard. Consuming
# `?page=` first means the fallback only fires when there is genuinely
# no target.
_ALLOWED_PAGES_FROM_URL = {
    "dashboard", "saved_reports", "swing_report", "compare_swings",
    "development_tracker", "historical_charts", "drill_library", "billing",
    "pricing", "upload", "family", "facility",
    "player_settings",  # Stripe billing-portal return URL routes here
    # BL-004B (C.2): "launch_progress" deliberately removed. It was an
    # internal pre-launch readiness checklist with no admin/role gate —
    # any logged-in user who typed ?page=launch_progress could read it,
    # and its content (stale as of this removal — see
    # docs/internal/LAUNCH_PROGRESS_ROADMAP_ARCHIVED.md) included internal
    # compliance/legal TODOs that shouldn't be customer-visible. No admin-
    # role concept exists anywhere else in this codebase, so building a
    # "properly verified" auth boundary for one page would be a new,
    # separately-scoped feature rather than a narrow fix — removing it
    # from customer-accessible routing is the correct minimal fix today.
}
try:
    _url_page = st.query_params.get("page")
    if _url_page and _url_page in _ALLOWED_PAGES_FROM_URL:
        st.session_state["page"] = _url_page
        # Clear any stale open-report state so a nav click to Sessions
        # (or any tab) doesn't get hijacked by a lingering
        # view_swing_record/path from a previously opened report.
        if _url_page != "swing_report":
            for _k in ("view_swing_record", "view_swing_path",
                       "view_swing_report_id", "view"):
                st.session_state.pop(_k, None)
        # Don't leave it lingering — once consumed, drop it so refreshes
        # don't keep forcing us back to the URL-specified page after
        # in-app navigation.
        try:
            del st.query_params["page"]
        except Exception:
            pass
except Exception:
    pass


# Default landing for a fresh authenticated session = Dashboard. Only
# applies when the user hasn't already navigated somewhere else (a saved
# report, settings, or an explicit page) AND no ?page= deep-link was
# just consumed above.
if not any(
    k in st.session_state for k in ("page", "view", "view_swing_path", "view_swing_record")
):
    st.session_state["page"] = "dashboard"


# ---------- HELPERS ----------
@st.cache_data(show_spinner=False)
def list_library_references():
    refs_dir = PROJECT_ROOT / "references"
    if not refs_dir.is_dir():
        return []
    refs = []
    for path in sorted(refs_dir.glob("*.json")):
        try:
            with open(path) as f:
                data = json.load(f)
            refs.append({
                "slug": path.stem,
                "name": data.get("player_name", path.stem),
                "handedness": data.get("handedness", "?"),
            })
        except Exception:
            continue
    return refs


def score_color(band_color):
    """Map our 3-band color to Streamlit-friendly hex + emoji."""
    return {
        "green":  ("#22c55e", "🟢"),
        "yellow": ("#eab308", "🟡"),
        "red":    ("#ef4444", "🔴"),
    }.get(band_color, ("#888", "⚪"))


def format_height(height_in):
    if not height_in:
        return "—"
    ft, inches = divmod(int(height_in), 12)
    return f"{ft}'{inches}\""


def stat_card(col, label, value, big=False, delta=None, delta_positive=None):
    """
    Render a uniform stat card. Unlike st.metric, this never truncates
    long values (player names, MLB ref names) — text wraps cleanly.

      col              : the Streamlit column / container to render into
      label            : eyebrow label above the value (UPPERCASE styled)
      value            : the main value (any length, wraps gracefully)
      big              : True for hero metric (e.g. score). Bigger font.
      delta            : optional change string (e.g. "+5", "-3"). Shown below value.
      delta_positive   : True=green, False=red, None=auto-detect from sign
    """
    size = "2.05rem" if big else "1.35rem"
    value_str = "—" if value is None or str(value).strip() == "" else str(value)

    delta_html = ""
    if delta is not None and str(delta).strip():
        if delta_positive is None:
            d = str(delta).strip()
            delta_positive = not d.startswith("-")
        color = "#22c55e" if delta_positive else "#ef4444"
        delta_html = (
            f'<div style="color:{color};font-size:.85rem;font-weight:750;'
            f'margin-top:.35rem;">{delta}</div>'
        )

    col.markdown(
        f"""
        <div style="background:#0f131b;border:1px solid #272a33;border-radius:14px;
                    padding:1rem;min-height:112px;display:flex;flex-direction:column;
                    justify-content:center;">
            <div style="color:#8b909c;font-size:.72rem;text-transform:uppercase;
                        letter-spacing:.08em;font-weight:800;margin-bottom:.45rem;">
                {label}
            </div>
            <div style="color:#f5f5f5;font-size:{size};font-weight:850;
                        line-height:1.18;white-space:normal;
                        overflow-wrap:break-word;word-break:normal;">
                {value_str}
            </div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_profile_pic_path(user_slug):
    for ext in ["png", "jpg", "jpeg"]:
        p = PROFILE_PIC_DIR / f"{user_slug}.{ext}"
        if p.exists():
            return p
    return None


def load_saved_swing_record(record_path):
    """Load a saved swing report from disk without rerunning analysis."""
    try:
        return json.loads(Path(record_path).read_text())
    except Exception:
        return None


def get_top_focus_from_record(record):
    narratives_saved = record.get("narratives", [])
    if narratives_saved:
        return narratives_saved[0].get("title", "Top Fix").title()
    return "No focus area saved"


def render_swing_history_cards(history, limit=6, title="Recent Swing History"):
    """Render clickable swing history cards."""
    if not history:
        return

    st.markdown(f"#### {title}")

    for i, record in enumerate(reversed(history[-limit:])):
        record_path = record.get("_record_path")
        focus = get_top_focus_from_record(record)

        with st.container(border=True):
            cols = st.columns([1.4, 0.8, 1.4, 2.2, 1.1])

            swing_num = record.get("swing_number", "")
            swing_label = f"Swing #{swing_num}" if swing_num else "Swing"

            cols[0].markdown(f"**{swing_label}**<br><span style='color:#8b909c;font-size:.85rem;'>{record.get('date', 'Unknown')}</span>", unsafe_allow_html=True)
            cols[1].markdown(f"<span style='color:#8b909c;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;font-weight:800;'>Score</span><br><strong>{record.get('score', 'N/A')}/100</strong>", unsafe_allow_html=True)
            cols[2].markdown(f"<span style='color:#8b909c;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;font-weight:800;'>MLB Comp</span><br><strong>{record.get('reference_name', 'N/A')}</strong>", unsafe_allow_html=True)
            cols[3].markdown(f"<span style='color:#8b909c;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;font-weight:800;'>Focus Area</span><br><strong>{focus}</strong>", unsafe_allow_html=True)

            if record_path:
                if cols[4].button("Open report", key=f"open_saved_swing_{i}_{record.get('timestamp', '')}"):
                    st.session_state.view_swing_path = record_path
                    st.rerun()



def get_record_label(record):
    swing_num = record.get("swing_number", "")
    date = record.get("date", "Unknown date")
    score = record.get("score", "N/A")
    comp = record.get("reference_name", "N/A")
    return f"Swing #{swing_num} — {date} — {score}/100 vs {comp}"


def get_metric_rows(record):
    rows = []
    metric_table = record.get("metric_table", {})
    if not isinstance(metric_table, dict):
        return rows

    for group, group_rows in metric_table.items():
        if not isinstance(group_rows, list):
            continue
        for r in group_rows:
            try:
                rows.append({
                    "group": group,
                    "label": r.get("label", "Metric"),
                    "match": float(r.get("sim_pct", 0)),
                    "you": r.get("player_str", "N/A"),
                    "ref": r.get("ref_str", "N/A"),
                    "flagged": r.get("flagged", False),
                })
            except Exception:
                continue
    return rows


# ====================================================================
# Premium "Swing Progress & Comparison" CSS
# ====================================================================
# Scoped under .bl-cmp-* so it can't bleed into other pages. Must be
# re-injected on every render (Streamlit reruns the script — a
# session_state guard would strip the <style> tag from the DOM after
# the first interaction, see _ensure_css() in swing_report.py).
# ====================================================================
_BL_CMP_CSS = """
<style>
.bl-cmp-section { margin: 2rem 0 1.4rem; }
.bl-cmp-eyebrow {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.7rem; letter-spacing: 0.22em; color: #FF3B30;
    font-weight: 700; text-transform: uppercase; margin-bottom: 0.45rem;
}
.bl-cmp-title {
    font-size: 1.8rem; font-weight: 800; letter-spacing: -0.02em;
    color: #fafafa; line-height: 1.1;
}
.bl-cmp-sub { margin-top: 0.45rem; color: #8b909c; font-size: 0.95rem; line-height: 1.5; max-width: 720px; }

/* KPI tiles */
.bl-cmp-kpis {
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 0.7rem; margin: 1rem 0 1.8rem;
}
.bl-cmp-kpi {
    background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.012));
    border: 1px solid rgba(255,255,255,0.06); border-radius: 14px;
    padding: 0.95rem 1rem;
    display: flex; flex-direction: column; gap: 0.5rem;
    transition: border-color .2s ease, transform .2s ease;
}
.bl-cmp-kpi:hover { border-color: rgba(255,59,48,0.28); transform: translateY(-1px); }
.bl-cmp-kpi.is-pb { border-color: rgba(52,211,153,0.28); }
.bl-cmp-kpi-lbl {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.62rem; letter-spacing: 0.18em; color: #6b7280;
    font-weight: 700; text-transform: uppercase;
}
.bl-cmp-kpi-val {
    font-size: 1.85rem; font-weight: 800; letter-spacing: -0.025em;
    color: #fafafa; line-height: 1;
}
.bl-cmp-kpi-unit { font-size: 0.7rem; color: #6b7280; margin-left: 0.18rem; font-weight: 700; }
.bl-cmp-kpi-foot {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.68rem; font-weight: 700; letter-spacing: 0.05em;
}
.bl-cmp-kpi-foot.is-up { color: #34d399; }
.bl-cmp-kpi-foot.is-down { color: #ff6058; }
.bl-cmp-kpi-foot.is-flat { color: #6b7280; }
@media (max-width: 1100px) { .bl-cmp-kpis { grid-template-columns: repeat(2, 1fr); } }

/* Pick section header */
.bl-cmp-pick { margin: 1.6rem 0 0.8rem; }
.bl-cmp-pick-eyebrow {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.66rem; letter-spacing: 0.22em; color: #FF3B30;
    font-weight: 700; text-transform: uppercase; margin-bottom: 0.3rem;
}
.bl-cmp-pick-title {
    font-size: 1.4rem; font-weight: 800; letter-spacing: -0.015em;
    color: #fafafa; line-height: 1.1;
}
.bl-cmp-pick-sub { margin-top: 0.3rem; color: #8b909c; font-size: 0.88rem; }

/* Pair cards */
.bl-cmp-pair {
    display: grid; grid-template-columns: 1fr 84px 1fr;
    gap: 0.7rem; margin: 1rem 0 1.4rem; align-items: stretch;
}
.bl-cmp-pair-card {
    background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015));
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 18px; padding: 1.2rem 1.3rem;
    display: flex; flex-direction: column; gap: 0.55rem; position: relative;
}
.bl-cmp-pair-card.is-newer { border-color: rgba(255,59,48,0.22); }
.bl-cmp-pair-card.is-newer::after {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, #FF3B30, transparent);
    border-radius: 18px 18px 0 0;
}
.bl-cmp-pair-tag {
    display: inline-block; width: fit-content;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.6rem; letter-spacing: 0.2em; font-weight: 700;
    color: #6b7280; background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.08);
    padding: 0.22rem 0.6rem; border-radius: 999px; text-transform: uppercase;
}
.bl-cmp-pair-tag.is-newer {
    color: #FF3B30; background: rgba(255,59,48,0.08);
    border-color: rgba(255,59,48,0.25);
}
.bl-cmp-pair-date {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.72rem; letter-spacing: 0.14em; color: #8b909c;
    font-weight: 700; text-transform: uppercase; margin-top: 0.25rem;
}
.bl-cmp-pair-score {
    font-size: 2.6rem; font-weight: 800; letter-spacing: -0.03em;
    color: #fafafa; line-height: 1; margin: 0.1rem 0 0.4rem;
}
.bl-cmp-pair-unit { font-size: 0.95rem; color: #6b7280; margin-left: 0.15rem; font-weight: 700; }
.bl-cmp-pair-meta {
    display: flex; flex-direction: column; gap: 0.45rem;
    padding-top: 0.7rem; border-top: 1px solid rgba(255,255,255,0.06);
    margin-top: 0.4rem;
}
.bl-cmp-pair-meta > div {
    display: flex; justify-content: space-between; align-items: center;
    gap: 0.7rem; font-size: 0.85rem;
}
.bl-cmp-pair-meta > div > span {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.65rem; letter-spacing: 0.16em; color: #6b7280;
    font-weight: 700; text-transform: uppercase;
}
.bl-cmp-pair-meta > div > strong {
    color: #fafafa; font-weight: 600; font-size: 0.88rem;
    max-width: 65%; text-align: right; word-break: break-word;
}
.bl-cmp-pair-arrow {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 0 0.2rem; gap: 0.5rem;
}
.bl-cmp-arrow-line {
    flex: 1; width: 1px; min-height: 30px;
    background: linear-gradient(180deg, transparent, rgba(255,255,255,0.18), transparent);
}
.bl-cmp-arrow-delta {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.72rem; letter-spacing: 0.05em; font-weight: 700;
    text-align: center; white-space: nowrap;
    padding: 0.42rem 0.6rem; border-radius: 10px; line-height: 1;
}
.bl-cmp-arrow-delta.is-up { color: #34d399; background: rgba(52,211,153,0.1); border: 1px solid rgba(52,211,153,0.28); }
.bl-cmp-arrow-delta.is-down { color: #ff6058; background: rgba(255,96,88,0.1); border: 1px solid rgba(255,96,88,0.28); }
.bl-cmp-arrow-delta.is-flat { color: #8b909c; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); }
@media (max-width: 900px) {
    .bl-cmp-pair { grid-template-columns: 1fr; }
    .bl-cmp-pair-arrow { flex-direction: row; padding: 0.4rem 0; }
    .bl-cmp-arrow-line { width: 30px; height: 1px; min-height: 0;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent); flex: none; }
}

/* Note pill */
.bl-cmp-note {
    display: flex; align-items: flex-start; gap: 0.55rem;
    padding: 0.7rem 0.9rem;
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px; color: #8b909c; font-size: 0.83rem;
    line-height: 1.5; margin: 0.5rem 0 1.4rem;
}
.bl-cmp-note-icon { color: #FF3B30; font-weight: 700; flex-shrink: 0; }

/* Deltas (improved / regressed) */
.bl-cmp-deltas {
    display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem;
    margin: 1.4rem 0;
}
.bl-cmp-delta-col {
    background: linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.01));
    border: 1px solid rgba(255,255,255,0.06); border-radius: 16px;
    padding: 1.1rem 1.1rem 0.95rem;
}
.bl-cmp-delta-col.is-up { border-color: rgba(52,211,153,0.22); }
.bl-cmp-delta-col.is-down { border-color: rgba(255,96,88,0.22); }
.bl-cmp-delta-head {
    display: flex; align-items: center; gap: 0.6rem;
    margin-bottom: 1rem; font-size: 1.05rem; font-weight: 800;
    color: #fafafa;
}
.bl-cmp-delta-icon { font-size: 0.85rem; font-weight: 700; }
.bl-cmp-delta-col.is-up .bl-cmp-delta-icon { color: #34d399; }
.bl-cmp-delta-col.is-down .bl-cmp-delta-icon { color: #ff6058; }
.bl-cmp-delta-count {
    margin-left: auto;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.68rem; letter-spacing: 0.12em;
    color: #6b7280; font-weight: 700;
    padding: 0.2rem 0.55rem;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 999px;
}
.bl-cmp-delta-row {
    padding: 0.8rem 0;
    border-top: 1px solid rgba(255,255,255,0.04);
}
.bl-cmp-delta-row:first-of-type { border-top: none; padding-top: 0.2rem; }
.bl-cmp-delta-row-head {
    display: flex; justify-content: space-between; align-items: center;
    gap: 0.7rem;
}
.bl-cmp-delta-row-name { font-weight: 600; color: #fafafa; font-size: 0.93rem; }
.bl-cmp-delta-row-num {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-weight: 700; font-size: 0.85rem;
    padding: 0.2rem 0.55rem; border-radius: 8px;
    line-height: 1; white-space: nowrap;
}
.bl-cmp-delta-row-num.is-up { color: #34d399; background: rgba(52,211,153,0.1); }
.bl-cmp-delta-row-num.is-down { color: #ff6058; background: rgba(255,96,88,0.1); }
.bl-cmp-delta-bar {
    display: flex; align-items: center; gap: 0.55rem;
    margin-top: 0.55rem;
    font-size: 0.72rem; color: #8b909c;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    letter-spacing: 0.04em; font-weight: 700;
}
.bl-cmp-delta-bar-track {
    flex: 1; height: 6px;
    background: rgba(255,255,255,0.05);
    border-radius: 999px; overflow: hidden; position: relative;
}
.bl-cmp-delta-bar-prev,
.bl-cmp-delta-bar-curr {
    position: absolute; top: 0; left: 0; height: 100%; border-radius: 999px;
}
.bl-cmp-delta-bar-prev { background: rgba(255,255,255,0.2); }
.bl-cmp-delta-bar-curr.is-up { background: linear-gradient(90deg, #34d399, #10b981); }
.bl-cmp-delta-bar-curr.is-down { background: linear-gradient(90deg, #ff6058, #ef4444); }
.bl-cmp-delta-row-detail {
    margin-top: 0.45rem; font-size: 0.74rem; color: #6b7280;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    letter-spacing: 0.04em; line-height: 1.4;
}
.bl-cmp-delta-empty {
    padding: 1.8rem 0.8rem; text-align: center; color: #6b7280;
    font-size: 0.88rem; line-height: 1.55;
}
@media (max-width: 900px) { .bl-cmp-deltas { grid-template-columns: 1fr; } }

/* Timeline */
.bl-cmp-timeline-wrap { margin: 1.8rem 0 0.5rem; }
.bl-cmp-timeline-head { margin-bottom: 0.8rem; }
.bl-cmp-timeline-eyebrow {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.66rem; letter-spacing: 0.22em; color: #FF3B30;
    font-weight: 700; text-transform: uppercase; margin-bottom: 0.3rem;
}
.bl-cmp-timeline-title {
    font-size: 1.25rem; font-weight: 800; letter-spacing: -0.015em;
    color: #fafafa; line-height: 1.1;
}
.bl-cmp-timeline-sub { margin-top: 0.25rem; color: #8b909c; font-size: 0.85rem; }
.bl-cmp-timeline {
    position: relative; padding-left: 1.4rem; margin-top: 0.9rem;
}
.bl-cmp-timeline::before {
    content: ""; position: absolute;
    left: 5px; top: 12px; bottom: 12px;
    width: 1px;
    background: linear-gradient(180deg, rgba(255,59,48,0.4), rgba(255,255,255,0.08));
}
.bl-cmp-tl-item {
    position: relative; padding: 0.45rem 0;
    display: flex; gap: 0.7rem;
}
.bl-cmp-tl-dot {
    position: absolute; left: -1.2rem; top: 1.2rem;
    width: 11px; height: 11px; border-radius: 50%;
    background: #0b0f19; border: 2px solid #FF3B30;
    box-shadow: 0 0 0 3px rgba(255,59,48,0.1);
}
.bl-cmp-tl-item.is-current .bl-cmp-tl-dot {
    background: #FF3B30;
    box-shadow: 0 0 0 3px rgba(255,59,48,0.18), 0 0 12px rgba(255,59,48,0.5);
}
.bl-cmp-tl-body {
    flex: 1;
    background: rgba(255,255,255,0.022);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 0.8rem 1rem;
    display: flex; align-items: center; gap: 1rem;
    flex-wrap: wrap;
}
.bl-cmp-tl-item.is-current .bl-cmp-tl-body {
    background: rgba(255,59,48,0.04);
    border-color: rgba(255,59,48,0.22);
}
.bl-cmp-tl-head {
    display: flex; flex-direction: column; gap: 0.15rem;
    min-width: 130px;
}
.bl-cmp-tl-num { font-weight: 800; color: #fafafa; font-size: 0.95rem; }
.bl-cmp-tl-date {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.68rem; letter-spacing: 0.14em; color: #8b909c;
    font-weight: 700; text-transform: uppercase;
}
.bl-cmp-tl-focus { flex: 1; color: #d4d6db; font-size: 0.88rem; line-height: 1.4; min-width: 200px; }
.bl-cmp-tl-score {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.82rem; font-weight: 800; letter-spacing: 0.04em;
    color: #fafafa;
    padding: 0.32rem 0.65rem;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
    white-space: nowrap;
}
.bl-cmp-tl-item.is-current .bl-cmp-tl-score {
    color: #FF3B30; background: rgba(255,59,48,0.08);
    border-color: rgba(255,59,48,0.25);
}
</style>
"""


def _bl_cmp_delta_rows(rows, direction: str) -> str:
    """Render improved-/regressed-metric rows as premium HTML cards."""
    if not rows:
        if direction == "up":
            msg = "No clear metric improvements between these two swings."
        else:
            msg = "No major regressions between these two swings."
        return f'<div class="bl-cmp-delta-empty">{msg}</div>'

    cls = "is-up" if direction == "up" else "is-down"
    parts = []
    for c in rows:
        change = c["Change"]
        num_str = f"+{change:.0f}%" if direction == "up" else f"{change:.0f}%"
        # Parse percent strings back to floats for the bar width
        try:
            old_pct = float(str(c["Older Match"]).rstrip("%"))
            new_pct = float(str(c["Newer Match"]).rstrip("%"))
        except (ValueError, TypeError):
            old_pct = 0.0
            new_pct = 0.0
        prev_w = max(0.0, min(100.0, old_pct))
        curr_w = max(0.0, min(100.0, new_pct))
        parts.append(
            f'<div class="bl-cmp-delta-row">'
            f'<div class="bl-cmp-delta-row-head">'
            f'<div class="bl-cmp-delta-row-name">{html.escape(str(c["Metric"]))}</div>'
            f'<div class="bl-cmp-delta-row-num {cls}">{num_str}</div>'
            f'</div>'
            f'<div class="bl-cmp-delta-bar">'
            f'<span>{html.escape(str(c["Older Match"]))}</span>'
            f'<div class="bl-cmp-delta-bar-track">'
            f'<div class="bl-cmp-delta-bar-prev" style="width:{prev_w:.1f}%;"></div>'
            f'<div class="bl-cmp-delta-bar-curr {cls}" style="width:{curr_w:.1f}%;"></div>'
            f'</div>'
            f'<span>{html.escape(str(c["Newer Match"]))}</span>'
            f'</div>'
            f'<div class="bl-cmp-delta-row-detail">'
            f'You: {html.escape(str(c["Older You"]))} \u2192 {html.escape(str(c["Newer You"]))}'
            f'</div>'
            f'</div>'
        )
    return "".join(parts)


def render_swing_progress_compare(history):
    """Premium 'Swing Progress & Comparison' section.

    Top: 5 KPI tiles summarising the whole history.
    Mid: pick-two-swings dropdowns + side-by-side premium swing cards
         with a center delta pill.
    Then: improved / regressed grids with prev→curr bar visualisations.
    Bottom: a focus-area timeline marking the most recent swing as current.

    Renders nothing if there are fewer than two swings on file.
    """
    if not history or len(history) < 2:
        return

    # CSS is namespaced under .bl-cmp-* — must re-inject every render.
    st.markdown(_BL_CMP_CSS, unsafe_allow_html=True)

    # ---- Aggregate stats ----
    first_score = history[0].get("score", 0)
    latest_score = history[-1].get("score", 0)
    best_score = max([r.get("score", 0) for r in history])
    avg_score = round(sum([r.get("score", 0) for r in history]) / len(history), 1)
    score_change = latest_score - first_score
    on_pb = latest_score >= best_score
    pb_gap = max(0, best_score - latest_score)

    if score_change > 0:
        change_cls, change_str = "is-up", f"+{score_change} vs first"
    elif score_change < 0:
        change_cls, change_str = "is-down", f"{score_change} vs first"
    else:
        change_cls, change_str = "is-flat", "= first score"

    pb_cls_kpi = "is-pb" if on_pb else ""
    pb_foot_cls = "is-up" if on_pb else "is-flat"
    pb_foot_str = "\u2713 on PB" if on_pb else f"\u2212{pb_gap} pts to PB"

    # ---- Section header ----
    st.markdown(
        '<div class="bl-cmp-section">'
        '<div class="bl-cmp-eyebrow">SWING HISTORY \u00B7 COMPARISON</div>'
        '<div class="bl-cmp-title">Swing Progress &amp; Comparison</div>'
        '<div class="bl-cmp-sub">Compare any two swings side-by-side to see what improved, what regressed, and what still needs work.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ---- KPI grid ----
    st.markdown(
        f'<div class="bl-cmp-kpis">'
        f'<div class="bl-cmp-kpi">'
        f'<div class="bl-cmp-kpi-lbl">FIRST SCORE</div>'
        f'<div class="bl-cmp-kpi-val">{first_score}<span class="bl-cmp-kpi-unit">/100</span></div>'
        f'<div class="bl-cmp-kpi-foot is-flat">starting line</div>'
        f'</div>'
        f'<div class="bl-cmp-kpi">'
        f'<div class="bl-cmp-kpi-lbl">LATEST SCORE</div>'
        f'<div class="bl-cmp-kpi-val">{latest_score}<span class="bl-cmp-kpi-unit">/100</span></div>'
        f'<div class="bl-cmp-kpi-foot {change_cls}">{change_str}</div>'
        f'</div>'
        f'<div class="bl-cmp-kpi {pb_cls_kpi}">'
        f'<div class="bl-cmp-kpi-lbl">BEST SCORE</div>'
        f'<div class="bl-cmp-kpi-val">{best_score}<span class="bl-cmp-kpi-unit">/100</span></div>'
        f'<div class="bl-cmp-kpi-foot {pb_foot_cls}">{pb_foot_str}</div>'
        f'</div>'
        f'<div class="bl-cmp-kpi">'
        f'<div class="bl-cmp-kpi-lbl">AVERAGE</div>'
        f'<div class="bl-cmp-kpi-val">{avg_score}<span class="bl-cmp-kpi-unit">/100</span></div>'
        f'<div class="bl-cmp-kpi-foot is-flat">across {len(history)} swings</div>'
        f'</div>'
        f'<div class="bl-cmp-kpi">'
        f'<div class="bl-cmp-kpi-lbl">TOTAL SWINGS</div>'
        f'<div class="bl-cmp-kpi-val">{len(history)}</div>'
        f'<div class="bl-cmp-kpi-foot is-flat">logged</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- Pick header ----
    st.markdown(
        '<div class="bl-cmp-pick">'
        '<div class="bl-cmp-pick-eyebrow">PICK TWO SWINGS</div>'
        '<div class="bl-cmp-pick-title">Compare side-by-side</div>'
        '<div class="bl-cmp-pick-sub">Defaults to your two most recent reps \u2014 swap either dropdown to compare any pair.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ---- Selectboxes (native Streamlit — picks up the global theme) ----
    labels = [get_record_label(r) for r in history]
    older_default = max(0, len(history) - 2)
    newer_default = len(history) - 1

    compare_cols = st.columns(2)
    older_label = compare_cols[0].selectbox(
        "Older swing",
        labels,
        index=older_default,
        key="compare_older_swing",
    )
    newer_label = compare_cols[1].selectbox(
        "Newer swing",
        labels,
        index=newer_default,
        key="compare_newer_swing",
    )

    older = history[labels.index(older_label)]
    newer = history[labels.index(newer_label)]

    old_focus = get_top_focus_from_record(older)
    new_focus = get_top_focus_from_record(newer)
    old_score = older.get("score", 0)
    new_score = newer.get("score", 0)
    diff = new_score - old_score

    if diff > 0:
        diff_cls, diff_str = "is-up", f"\u25B2 +{diff} pts"
    elif diff < 0:
        diff_cls, diff_str = "is-down", f"\u25BC {diff} pts"
    else:
        diff_cls, diff_str = "is-flat", "\u2192 no change"

    # ---- Side-by-side pair cards w/ center delta pill ----
    st.markdown(
        f'<div class="bl-cmp-pair">'
        f'<div class="bl-cmp-pair-card">'
        f'<div class="bl-cmp-pair-tag">OLDER</div>'
        f'<div class="bl-cmp-pair-date">{html.escape(str(older.get("date", "Unknown")))}</div>'
        f'<div class="bl-cmp-pair-score">{old_score}<span class="bl-cmp-pair-unit">/100</span></div>'
        f'<div class="bl-cmp-pair-meta">'
        f'<div><span>MLB Comp</span><strong>{html.escape(str(older.get("reference_name", "—")))}</strong></div>'
        f'<div><span>Focus</span><strong>{html.escape(str(old_focus))}</strong></div>'
        f'<div><span>Duration</span><strong>{html.escape(str(older.get("swing_duration_ms", "—")))} ms</strong></div>'
        f'</div>'
        f'</div>'
        f'<div class="bl-cmp-pair-arrow">'
        f'<div class="bl-cmp-arrow-line"></div>'
        f'<div class="bl-cmp-arrow-delta {diff_cls}">{diff_str}</div>'
        f'<div class="bl-cmp-arrow-line"></div>'
        f'</div>'
        f'<div class="bl-cmp-pair-card is-newer">'
        f'<div class="bl-cmp-pair-tag is-newer">NEWER</div>'
        f'<div class="bl-cmp-pair-date">{html.escape(str(newer.get("date", "Unknown")))}</div>'
        f'<div class="bl-cmp-pair-score">{new_score}<span class="bl-cmp-pair-unit">/100</span></div>'
        f'<div class="bl-cmp-pair-meta">'
        f'<div><span>MLB Comp</span><strong>{html.escape(str(newer.get("reference_name", "—")))}</strong></div>'
        f'<div><span>Focus</span><strong>{html.escape(str(new_focus))}</strong></div>'
        f'<div><span>Duration</span><strong>{html.escape(str(newer.get("swing_duration_ms", "—")))} ms</strong></div>'
        f'</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ---- Metric-level diffs ----
    old_metrics = get_metric_rows(older)
    new_metrics = get_metric_rows(newer)

    if old_metrics and new_metrics:
        old_map = {m["label"]: m for m in old_metrics}
        new_map = {m["label"]: m for m in new_metrics}

        changes = []
        flagged_skipped = 0
        for label, new_m in new_map.items():
            old_m = old_map.get(label)
            if not old_m:
                continue
            # Skip metrics where either swing was flagged (e.g. rotation
            # method mismatch, mixed 2D/3D camera method) — the sim_pcts
            # aren't apples-to-apples, so showing them as "improved" or
            # "regressed" would mislead the player.
            if old_m.get("flagged") or new_m.get("flagged"):
                flagged_skipped += 1
                continue
            change = new_m["match"] - old_m["match"]
            changes.append({
                "Metric": label,
                "Group": new_m["group"],
                "Older Match": f"{old_m['match']:.0f}%",
                "Newer Match": f"{new_m['match']:.0f}%",
                "Change": change,
                "Older You": old_m["you"],
                "Newer You": new_m["you"],
            })

        if flagged_skipped:
            st.markdown(
                f'<div class="bl-cmp-note">'
                f'<span class="bl-cmp-note-icon">\u24D8</span>'
                f'<span><strong>{flagged_skipped}</strong> metric{"s" if flagged_skipped != 1 else ""} excluded from this comparison \u2014 '
                f'different measurement methods (camera angle / 2D vs 3D) between the two swings make those numbers unreliable to diff.</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

        improved = sorted([c for c in changes if c["Change"] > 0], key=lambda x: x["Change"], reverse=True)[:5]
        regressed = sorted([c for c in changes if c["Change"] < 0], key=lambda x: x["Change"])[:5]

        st.markdown(
            f'<div class="bl-cmp-deltas">'
            f'<div class="bl-cmp-delta-col is-up">'
            f'<div class="bl-cmp-delta-head">'
            f'<span class="bl-cmp-delta-icon">\u25B2</span>'
            f'<span>What Improved</span>'
            f'<span class="bl-cmp-delta-count">{len(improved)}</span>'
            f'</div>'
            f'{_bl_cmp_delta_rows(improved, "up")}'
            f'</div>'
            f'<div class="bl-cmp-delta-col is-down">'
            f'<div class="bl-cmp-delta-head">'
            f'<span class="bl-cmp-delta-icon">\u25BC</span>'
            f'<span>Still Needs Work</span>'
            f'<span class="bl-cmp-delta-count">{len(regressed)}</span>'
            f'</div>'
            f'{_bl_cmp_delta_rows(regressed, "down")}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ---- Focus-area progression timeline ----
    tl_items = []
    last_idx = len(history) - 1
    for i, r in enumerate(history):
        swing_num = r.get("swing_number", "?")
        focus = html.escape(str(get_top_focus_from_record(r)))
        score = r.get("score", "N/A")
        date = html.escape(str(r.get("date", "")))
        is_current = (i == last_idx)
        item_cls = "bl-cmp-tl-item is-current" if is_current else "bl-cmp-tl-item"
        tl_items.append(
            f'<div class="{item_cls}">'
            f'<div class="bl-cmp-tl-dot"></div>'
            f'<div class="bl-cmp-tl-body">'
            f'<div class="bl-cmp-tl-head">'
            f'<span class="bl-cmp-tl-num">Swing #{html.escape(str(swing_num))}</span>'
            f'<span class="bl-cmp-tl-date">{date}</span>'
            f'</div>'
            f'<div class="bl-cmp-tl-focus">{focus}</div>'
            f'<div class="bl-cmp-tl-score">{html.escape(str(score))}/100</div>'
            f'</div>'
            f'</div>'
        )

    st.markdown(
        f'<div class="bl-cmp-timeline-wrap">'
        f'<div class="bl-cmp-timeline-head">'
        f'<div class="bl-cmp-timeline-eyebrow">PROGRESSION</div>'
        f'<div class="bl-cmp-timeline-title">Focus area \u00B7 swing by swing</div>'
        f'<div class="bl-cmp-timeline-sub">Every swing on file, oldest first \u2014 most recent rep is highlighted.</div>'
        f'</div>'
        f'<div class="bl-cmp-timeline">'
        f'{"".join(tl_items)}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_swing_practice_log(record: dict, player_id: str) -> None:
    """
    Premium "Practice Log" block that lives below a saved swing report.
    Lets the player:
      - tick off the drills they've actually done for this swing
      - jot quick per-swing notes (what felt off, cage observations)

    Backed by training_logs.drill_state._swing_meta via
    player_storage.load_swing_meta / save_swing_meta — no schema migration.
    Silently no-ops if the record has no id or player_id.
    """
    swing_id = record.get("id") or record.get("timestamp")
    if not swing_id or not player_id:
        return

    # Pull the drill plan (use same keys as the report renderer expects).
    drill_plan = record.get("drill_plan") or {}
    cats = drill_plan.get("categories", []) if isinstance(drill_plan, dict) else []

    # Build a flat list of drills: each entry { priority, cat_title, name, reps }
    flat_drills = []
    for cat in cats:
        priority = cat.get("priority", "·")
        cat_title = cat.get("title", "Drills")
        for d in (cat.get("drills") or []):
            flat_drills.append({
                "priority":  str(priority),
                "cat_title": str(cat_title),
                "name":      str(d.get("name", "Drill")),
                "reps":      str(d.get("reps", "")),
            })

    # Load existing meta (notes + per-drill completion bools).
    try:
        meta = load_swing_meta(player_id, str(swing_id))
    except Exception:
        meta = {"notes": "", "drills_completed": {}}
    saved_notes = meta.get("notes", "") or ""
    saved_done  = meta.get("drills_completed", {}) or {}

    # ---- styling (scoped to .bl-plog-*) ----
    st.markdown("""
<style>
.bl-plog-wrap {
  margin: 1.4rem 0 0.6rem 0;
  border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.07);
  background: linear-gradient(180deg, rgba(255,255,255,0.022), rgba(255,255,255,0.008));
  padding: 1.15rem 1.25rem 1.05rem 1.25rem;
}
.bl-plog-head {
  display:flex; align-items:baseline; justify-content:space-between;
  margin-bottom: 0.55rem; gap: 0.6rem; flex-wrap: wrap;
}
.bl-plog-eyebrow {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 0.6rem; letter-spacing: 0.26em; font-weight: 700;
  color: #FF3B30; text-transform: uppercase;
}
.bl-plog-title {
  font-family: 'Inter', system-ui, sans-serif;
  font-size: 1.18rem; font-weight: 800; letter-spacing: -0.01em;
  color: #fafafa;
}
.bl-plog-sub {
  font-size: 0.86rem; color: #9ca3af; margin-top: 0.05rem;
}
.bl-plog-section-eyebrow {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 0.58rem; letter-spacing: 0.22em; font-weight: 700;
  color: #fbbf24; text-transform: uppercase;
  margin-top: 0.85rem; margin-bottom: 0.35rem;
}
.bl-plog-progress-pill {
  display:inline-flex; align-items:center; gap: 0.4rem;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 0.62rem; letter-spacing: 0.18em; font-weight: 700;
  text-transform: uppercase;
  padding: 0.28rem 0.7rem; border-radius: 999px;
  background: rgba(110,231,183,0.08); color: #6ee7b7;
  border: 1px solid rgba(110,231,183,0.35);
}
.bl-plog-progress-pill.empty {
  background: rgba(255,255,255,0.04); color: #9ca3af;
  border-color: rgba(255,255,255,0.10);
}
.bl-plog-meta {
  font-size: 0.82rem; color: #9ca3af; margin-top: 0.55rem;
}
</style>
""", unsafe_allow_html=True)

    # ---- session_state defaults so the widgets keep their state across reruns ----
    form_key = f"plog_{swing_id}"
    notes_key = f"{form_key}_notes"
    if notes_key not in st.session_state:
        st.session_state[notes_key] = saved_notes
    for fd in flat_drills:
        ck = f"{form_key}_drill_{fd['priority']}_{fd['name']}"
        if ck not in st.session_state:
            st.session_state[ck] = bool(saved_done.get(fd["name"], False))

    done_count = sum(
        1 for fd in flat_drills
        if st.session_state.get(f"{form_key}_drill_{fd['priority']}_{fd['name']}")
    )
    total_count = len(flat_drills)

    # ---- header strip ----
    pill_cls = "" if done_count > 0 else "empty"
    if total_count > 0:
        pill_text = f"{done_count}/{total_count} drills logged"
    else:
        pill_text = "No drill plan attached"

    st.markdown(
        f"""
<div class="bl-plog-wrap">
  <div class="bl-plog-head">
    <div>
      <div class="bl-plog-eyebrow">Practice Log</div>
      <div class="bl-plog-title">Track this swing's training</div>
      <div class="bl-plog-sub">Check off drills you've actually put reps on, and stash any notes for next time.</div>
    </div>
    <div class="bl-plog-progress-pill {pill_cls}">{html.escape(pill_text)}</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # ---- body: drill checkboxes + notes ----
    if total_count > 0:
        st.markdown(
            '<div class="bl-plog-section-eyebrow">Drills · check what you trained</div>',
            unsafe_allow_html=True,
        )
        # Group by priority/category for readability.
        seen_cat = None
        cols = None
        cols_used = 0
        for fd in flat_drills:
            cat_label = f"Priority {fd['priority']} · {fd['cat_title']}"
            if cat_label != seen_cat:
                if cols is not None and cols_used < 2:
                    pass  # let the previous row finish
                st.markdown(
                    f"<div style='font-family:Inter,system-ui,sans-serif;font-size:0.78rem;"
                    f"font-weight:700;color:#d4d4d4;margin:0.6rem 0 0.25rem 0;'>"
                    f"{html.escape(cat_label)}</div>",
                    unsafe_allow_html=True,
                )
                seen_cat = cat_label
                cols = st.columns(2)
                cols_used = 0

            with cols[cols_used % 2]:
                ck = f"{form_key}_drill_{fd['priority']}_{fd['name']}"
                drill_label = fd["name"]
                if fd["reps"]:
                    drill_label = f"{fd['name']}  ·  {fd['reps']}"
                st.checkbox(drill_label, key=ck)
            cols_used += 1

    st.markdown(
        '<div class="bl-plog-section-eyebrow">Notes · what stood out this swing</div>',
        unsafe_allow_html=True,
    )
    st.text_area(
        "Swing notes",
        key=notes_key,
        placeholder="e.g. felt steep into the zone, will video from the side next round, hands getting better…",
        height=110,
        label_visibility="collapsed",
    )

    # ---- save action ----
    save_col, status_col = st.columns([1.2, 3.5])
    if save_col.button("Save practice log", key=f"{form_key}_save", width="stretch"):
        new_done = {
            fd["name"]: bool(
                st.session_state.get(f"{form_key}_drill_{fd['priority']}_{fd['name']}")
            )
            for fd in flat_drills
        }
        try:
            save_swing_meta(
                player_id=player_id,
                swing_id=str(swing_id),
                notes=st.session_state.get(notes_key, ""),
                drills_completed=new_done,
            )
            status_col.success("Practice log saved.")
        except Exception as e:
            status_col.error(f"Couldn't save: {e}")


def _plan_feature_list(plan_id: str) -> list:
    """
    Return a list of (title, description) tuples describing what a plan
    unlocks. Used by the Billing page's "what's included" panel.
    """
    from entitlements import FREE_PLAN_ID, FAMILY_PLAN_ID, COACH_PLAN_ID
    if plan_id == FREE_PLAN_ID:
        return [
            ("3 lifetime swing analyses",  "Get a feel for the analyzer before upgrading."),
            ("Basic MLB comparisons",      "Quick match from a starter library of pros."),
            ("On-screen swing report",     "Score, biomechanical breakdown, and key gaps."),
        ]
    base = [
        ("Unlimited swing analyses",       "No swing cap. Upload as many as you want."),
        ("AI-generated drill plans",       "Targeted drills tailored to your weaknesses."),
        ("Compare any two swings",         "Frame-aligned side-by-side biomechanical view."),
        ("HD swing video storage",         "Every swing saved and replayable in your reports."),
        ("Premium PDF reports",            "Coach-ready, shareable, beautifully laid out."),
        ("Full MLB comp library",          "Match against the entire MLB swing database."),
        ("Development Tracker",            "Track progress across weeks and months."),
        ("Priority support",               "Real humans, fast responses."),
    ]
    if plan_id == FAMILY_PLAN_ID:
        base.insert(0, ("4 player seats",   "Share Pro access with the whole family."))
    elif plan_id == COACH_PLAN_ID:
        base.insert(0, ("20 player seats",  "Invite your full roster under one subscription."))
    return base


def _render_billing_page():
    """
    Full premium Billing page (page == 'billing').

    Layout:
      • Hero header
      • Status banner (plan name, ACTIVE/FREE pill, renewal/expiry/usage line)
      • Primary action row (Manage Subscription / Pick a plan / Compare plans)
      • "What's included in <plan>" feature checklist
      • Beta code redemption (hidden if already on a beta-code Pro)
    """
    from entitlements import (
        FREE_SWING_LIMIT,
        is_pro,
        plan_display_name,
        _resolve_plan_id,
    )
    from subscription_storage import (
        load_my_plan,
        invalidate_my_plan_cache,
        redeem_beta_code,
    )

    snap        = load_my_plan()
    plan_id     = _resolve_plan_id(snap)
    plan_name   = plan_display_name(plan_id)
    on_pro      = is_pro(snap)
    source      = (snap or {}).get("source") or ""
    comp_until  = (snap or {}).get("comp_until")
    period_end  = (snap or {}).get("current_period_end")
    swings_used = int((snap or {}).get("free_swings_used") or 0)
    swings_left = max(0, FREE_SWING_LIMIT - swings_used)

    # ---- Page header ----
    st.markdown(
        """
        <div style="padding: 2.4rem 0 0.4rem 0; max-width: 760px; margin: 0 auto;
                    font-family: 'Inter', -apple-system, system-ui, sans-serif;">
          <div style="font-family:'JetBrains Mono',ui-monospace,monospace;
                      font-size:.7rem; letter-spacing:.24em; color:#FF3B30;
                      font-weight:600; margin-bottom:.7rem;">BILLING &amp; SUBSCRIPTION</div>
          <div style="font-size:2.4rem; font-weight:850; letter-spacing:-.04em;
                      color:#fafafa; line-height:1.05;">Manage your plan.</div>
          <div style="margin-top:.7rem; color:#8b8b8b; font-size:1rem; line-height:1.55;">
            Update your subscription, view billing details, or redeem a beta code.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _l, _c, _r = st.columns([1, 6, 1])
    with _c:
        # ---- Hero status banner ----
        if on_pro:
            pill_color    = "#16a34a"
            pill_bg       = "rgba(22,163,74,0.16)"
            pill_border   = "rgba(22,163,74,0.42)"
            pill_label    = "ACTIVE"
            banner_bg     = "linear-gradient(135deg, rgba(22,163,74,0.13), rgba(22,163,74,0.02))"
            banner_border = "rgba(22,163,74,0.38)"

            if source == "stripe" and period_end:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
                    renews_str = dt.strftime("%b %d, %Y").replace(" 0", " ")
                    sub_line = (f"Billed through Stripe · Renews "
                                f"<strong style='color:#fafafa;'>{renews_str}</strong>")
                except Exception:
                    sub_line = "Billed through Stripe."
            elif source == "beta_code" and comp_until:
                sub_line = (f"Beta access · Expires "
                            f"<strong style='color:#fafafa;'>{str(comp_until)[:10]}</strong>")
            elif source == "manual":
                sub_line = "Manually granted access."
            else:
                sub_line = "Pro features unlocked."
        else:
            pill_color    = "#a3a3a3"
            pill_bg       = "rgba(255,255,255,0.06)"
            pill_border   = "rgba(255,255,255,0.18)"
            pill_label    = "FREE"
            banner_bg     = "rgba(255,255,255,0.02)"
            banner_border = "rgba(255,255,255,0.08)"
            sub_line = (f"Swings used <strong style='color:#fafafa;'>{swings_used} of "
                        f"{FREE_SWING_LIMIT}</strong> · "
                        f"<strong style='color:#fafafa;'>{swings_left} remaining</strong>")

        st.markdown(
            f"""
            <div style="margin: 1.2rem 0 1.5rem 0; padding: 1.7rem 1.9rem;
                        border-radius: 16px; background: {banner_bg};
                        border: 1px solid {banner_border};
                        font-family: 'Inter', -apple-system, system-ui, sans-serif;">
              <div style="display:inline-flex; align-items:center; gap:.4rem;
                          padding:.24rem .7rem; border-radius:999px;
                          font-size:.7rem; font-weight:800; letter-spacing:.12em;
                          color:{pill_color}; background:{pill_bg};
                          border:1px solid {pill_border};">
                <span style="display:inline-block; width:6px; height:6px;
                             border-radius:50%; background:{pill_color};
                             box-shadow:0 0 8px {pill_color};"></span>
                {pill_label}
              </div>
              <div style="font-size:2.1rem; font-weight:850; color:#fafafa;
                          margin-top:.7rem; letter-spacing:-.025em;">
                {plan_name}
              </div>
              <div style="color:#a8a8a8; font-size:.97rem; margin-top:.4rem;
                          line-height:1.5;">
                {sub_line}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ---- Primary actions ----
        if on_pro and source == "stripe":
            ac_cols = st.columns([2, 1])
            with ac_cols[0]:
                manage_clicked = st.button(
                    "⚙️  Manage subscription (billing, cancel)",
                    type="primary",
                    width="stretch",
                    key="billing_manage_sub",
                )
            with ac_cols[1]:
                if st.button("Compare plans", width="stretch",
                             key="billing_compare_plans"):
                    st.session_state["page"] = "pricing"
                    st.rerun()
            if manage_clicked:
                portal_url = None
                try:
                    from stripe_client import create_portal_session
                    from pricing import _streamlit_base_url
                    with st.spinner("Opening Stripe billing portal…"):
                        portal_url = create_portal_session(
                            return_url=f"{_streamlit_base_url()}?page=billing",
                        )
                except ValueError as ve:
                    st.error(str(ve))
                except Exception as exc:
                    st.error(f"Couldn't open the billing portal: {exc}")
                else:
                    st.markdown(
                        f"""
<meta http-equiv="refresh" content="0; url={portal_url}">
<script>window.top.location.href = "{portal_url}";</script>
<div style="margin-top:.7rem; padding:.8rem 1rem; border-radius:10px;
            background:rgba(255,59,48,0.08); border:1px solid rgba(255,59,48,.32);
            color:#fafafa;">
  Opening Stripe billing portal…&nbsp;
  <a href="{portal_url}" target="_top" style="color:#ef4444; font-weight:800;">
    Click here if it doesn't redirect.
  </a>
</div>
""",
                        unsafe_allow_html=True,
                    )
                    st.stop()
        elif on_pro:
            # Pro via beta/manual — no Stripe management surface, but offer
            # a plan-comparison entry point.
            if st.button("Compare plans",
                         width="stretch",
                         key="billing_compare_plans_nostripe"):
                st.session_state["page"] = "pricing"
                st.rerun()
        else:
            if st.button("⚡  Pick a plan & upgrade",
                         type="primary",
                         width="stretch",
                         key="billing_upgrade_cta"):
                st.session_state["page"] = "pricing"
                st.rerun()

        st.markdown("<div style='margin-top:1.6rem;'></div>", unsafe_allow_html=True)

        # ---- What's included panel ----
        features = _plan_feature_list(plan_id)
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:1.08rem; font-weight:800; color:#fafafa;'>"
                f"What's included in {plan_name}</div>"
                f"<div style='color:#8b8b8b; font-size:.86rem; margin:.25rem 0 .8rem 0;'>"
                f"Features unlocked by your current plan."
                f"</div>",
                unsafe_allow_html=True,
            )
            rows_html = []
            for title, desc in features:
                rows_html.append(f"""
                <div style="display:flex; gap:.9rem; padding:.6rem 0;
                            border-top:1px solid rgba(255,255,255,0.06);">
                  <div style="color:#16a34a; font-weight:900; font-size:1rem;
                              line-height:1.45; min-width:1.3rem;">✓</div>
                  <div style="flex:1;">
                    <div style="color:#fafafa; font-weight:700; font-size:.98rem;">{title}</div>
                    <div style="color:#8b8b8b; font-size:.85rem; line-height:1.5;
                                margin-top:.12rem;">{desc}</div>
                  </div>
                </div>
                """)
            st.markdown("".join(rows_html), unsafe_allow_html=True)

        # ---- Beta code redemption (hidden if already on beta-comp) ----
        already_beta = on_pro and source == "beta_code"
        if not already_beta:
            st.markdown("<div style='margin-top:1.4rem;'></div>", unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown(
                    "<div style='font-weight:800; color:#fafafa;'>Redeem a beta code</div>"
                    "<div style='color:#8b8b8b; font-size:.86rem; margin:.25rem 0 .6rem 0;'>"
                    "Got a 30-day BarrelLabs beta code? Paste it here to unlock the full app."
                    "</div>",
                    unsafe_allow_html=True,
                )
                # bottom-align so the Redeem button lines up with the input
                # (a collapsed-label input next to a button otherwise sits lower,
                # making the button look like it overlaps the field).
                beta_cols = st.columns([3, 1], vertical_alignment="bottom")
                code_input = beta_cols[0].text_input(
                    "Beta code",
                    key="billing_beta_code_input",
                    placeholder="e.g. BL-LAUNCH-AB12CD",
                    label_visibility="collapsed",
                )
                redeem_clicked = beta_cols[1].button(
                    "Redeem",
                    key="billing_beta_code_redeem",
                    width="stretch",
                )
                if redeem_clicked:
                    try:
                        redeem_beta_code(code_input)
                        invalidate_my_plan_cache()
                        st.success(
                            "Beta code redeemed. You now have full Pro access — enjoy!"
                        )
                        st.rerun()
                    except ValueError as ve:
                        st.error(str(ve))
                    except Exception:
                        st.error("Something went wrong redeeming that code. Please try again.")


def _render_settings_billing_pointer():
    """
    Thin pointer card on the Settings page. Bounces the user to the
    real Billing tab instead of duplicating the subscription UI here.
    """
    from entitlements import is_pro, plan_display_name, _resolve_plan_id
    from subscription_storage import load_my_plan

    snap      = load_my_plan()
    plan_id   = _resolve_plan_id(snap)
    plan_name = plan_display_name(plan_id)
    on_pro    = is_pro(snap)

    pill_color  = "#16a34a" if on_pro else "#a3a3a3"
    pill_bg     = "rgba(22,163,74,0.14)" if on_pro else "rgba(255,255,255,0.06)"
    pill_border = "rgba(22,163,74,0.36)" if on_pro else "rgba(255,255,255,0.18)"
    pill_label  = "ACTIVE" if on_pro else "FREE"

    with st.container(border=True):
        st.markdown(
            f"""
            <div style="display:flex; align-items:flex-start; justify-content:space-between;
                        gap:1rem; margin-bottom:.8rem;">
              <div>
                <div style="display:inline-block; padding:.18rem .55rem; border-radius:999px;
                            font-size:.65rem; font-weight:800; letter-spacing:.12em;
                            color:{pill_color}; background:{pill_bg};
                            border:1px solid {pill_border};">
                  {pill_label}
                </div>
                <div style="font-size:1.08rem; font-weight:800; color:#fafafa; margin-top:.35rem;">
                  You're on {plan_name}
                </div>
                <div style="color:#8b8b8b; font-size:.87rem; margin-top:.2rem;
                            line-height:1.5; max-width:520px;">
                  Manage your subscription, view invoices, or redeem a beta code in the
                  Billing tab.
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("→  Go to Billing", width="stretch",
                     key="settings_goto_billing"):
            st.session_state["page"] = "billing"
            st.session_state.pop("view", None)
            st.rerun()


def _render_subscription_section():
    """
    Settings-page block: current plan summary + beta-code redemption form.

    Behavior:
      • Pro users see a green "You're on <Plan>" card + (if comp/beta) the
        expiry date. No redemption form (avoid duplicate redemptions of
        their own codes).
      • Free users see plan summary, swings-used-out-of-3, and a
        redemption form. Successful redemption flips them to Pro and
        reruns so all gates pick up the new caps immediately.
    """
    from entitlements import (
        FREE_SWING_LIMIT,
        is_pro,
        plan_display_name,
        _resolve_plan_id,
    )
    from subscription_storage import (
        load_my_plan,
        invalidate_my_plan_cache,
        redeem_beta_code,
    )

    snap = load_my_plan()
    plan_id = _resolve_plan_id(snap)
    plan_name = plan_display_name(plan_id)
    on_pro = is_pro(snap)
    source = (snap or {}).get("source") or ""
    comp_until = (snap or {}).get("comp_until")
    swings_used = int((snap or {}).get("free_swings_used") or 0)
    swings_left = max(0, FREE_SWING_LIMIT - swings_used)

    with st.container(border=True):
        st.markdown("#### Subscription")
        st.caption("Your current plan, billing, and beta-code redemption.")

        # ---- Plan summary card ----
        if on_pro:
            pill_color = "#16a34a"
            pill_label = "ACTIVE"
            sub_line = ""
            if source == "beta_code" and comp_until:
                sub_line = f"Beta access — expires {str(comp_until)[:10]}."
            elif source == "stripe":
                sub_line = "Billed through Stripe."
            elif source == "manual":
                sub_line = "Manually granted access."
            st.markdown(f"""
<div style="
    margin: 0.4rem 0 0.8rem 0;
    padding: 1rem 1.1rem;
    border-radius: 12px;
    background: linear-gradient(135deg, rgba(22,163,74,0.10), rgba(22,163,74,0.02));
    border: 1px solid rgba(22,163,74,0.32);
">
  <div style="
      display:inline-block;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.1em;
      color: {pill_color};
      background: rgba(22,163,74,0.14);
      border: 1px solid rgba(22,163,74,0.32);
  ">{pill_label}</div>
  <div style="font-size:1.15rem;font-weight:800;color:#fafafa;margin-top:0.4rem;">
    You're on {plan_name}.
  </div>
  <div style="color:#a3a3a3;font-size:0.88rem;margin-top:0.25rem;">
    {sub_line or "Unlimited swings, drill plans, video saving, Development Tracker, and PDF exports."}
  </div>
</div>
""", unsafe_allow_html=True)
        else:
            st.markdown(f"""
<div style="
    margin: 0.4rem 0 0.8rem 0;
    padding: 1rem 1.1rem;
    border-radius: 12px;
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.08);
">
  <div style="
      display:inline-block;
      padding: 0.15rem 0.55rem;
      border-radius: 999px;
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.1em;
      color: #a3a3a3;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(255,255,255,0.1);
  ">FREE</div>
  <div style="font-size:1.15rem;font-weight:800;color:#fafafa;margin-top:0.4rem;">
    You're on the Free plan.
  </div>
  <div style="color:#d4d4d4;font-size:0.92rem;margin-top:0.25rem;">
    Swings used: <strong>{swings_used} / {FREE_SWING_LIMIT}</strong>
    &nbsp;·&nbsp; Remaining: <strong>{swings_left}</strong>
  </div>
</div>
""", unsafe_allow_html=True)

            # Direct upgrade CTA for free users.
            if st.button("⚡  See plans & upgrade", type="primary",
                         width="stretch",
                         key="settings_upgrade_cta"):
                st.session_state["page"] = "pricing"
                st.rerun()

        # ---- Manage subscription (Pro + Stripe-billed users only) ----
        if on_pro and source == "stripe":
            if st.button("⚙️  Manage subscription (billing, cancel)",
                         width="stretch",
                         key="settings_manage_sub"):
                portal_url = None
                try:
                    from stripe_client import create_portal_session
                    from pricing import _streamlit_base_url
                    with st.spinner("Opening Stripe billing portal…"):
                        portal_url = create_portal_session(
                            return_url=f"{_streamlit_base_url()}?page=player_settings",
                        )
                except ValueError as ve:
                    st.error(str(ve))
                except Exception as exc:
                    st.error(f"Couldn't open the billing portal: {exc}")
                else:
                    st.markdown(
                        f"""
<meta http-equiv="refresh" content="0; url={portal_url}">
<script>window.top.location.href = "{portal_url}";</script>
<div style="margin-top:0.6rem;padding:0.7rem 0.9rem;border-radius:10px;
            background:rgba(255,59,48,0.08);border:1px solid rgba(255,59,48,0.32);
            color:#fafafa;">
  Opening Stripe billing portal…
  &nbsp;<a href="{portal_url}" target="_top"
       style="color:#ef4444;font-weight:800;">
    Click here if it doesn't redirect.
  </a>
</div>
""",
                        unsafe_allow_html=True,
                    )
                    st.stop()

        # ---- Beta code form ----
        # Hide the form for users already on a beta-comp Pro (no double-redeem).
        already_beta = on_pro and source == "beta_code"
        if not already_beta:
            st.markdown(
                "<div style='font-weight:700;color:#fafafa;margin-top:0.4rem;'>Redeem a beta code</div>"
                "<div style='color:#a3a3a3;font-size:0.88rem;margin-bottom:0.4rem;'>"
                "Got a 30-day BarrelLabs beta code? Paste it here to unlock the full app."
                "</div>",
                unsafe_allow_html=True,
            )
            beta_cols = st.columns([3, 1])
            code_input = beta_cols[0].text_input(
                "Beta code",
                key="settings_beta_code_input",
                placeholder="e.g. BL-LAUNCH-AB12CD",
                label_visibility="collapsed",
            )
            redeem_clicked = beta_cols[1].button(
                "Redeem",
                key="settings_beta_code_redeem",
                width="stretch",
            )

            if redeem_clicked:
                try:
                    redeem_beta_code(code_input)
                    invalidate_my_plan_cache()
                    st.success(
                        "Beta code redeemed. You now have full Pro access — "
                        "enjoy the app!"
                    )
                    st.rerun()
                except ValueError as ve:
                    st.error(str(ve))
                except Exception:
                    st.error("Something went wrong redeeming that code. Please try again.")


# ---------- HEADER ----------
# Skip the legacy upload-flow header on pages that render their own hero
# (dashboard, saved report viewer, account/settings pages, launch progress,
# etc). The legacy hero is ONLY meant for the upload flow.
_viewing_saved_swing = (
    "view_swing_record" in st.session_state
    or "view_swing_path" in st.session_state
)
_pages_with_own_hero = {
    "dashboard",
    "saved_reports",
    "compare_swings",
    "development_tracker",
    "historical_charts",
    "drill_library",
    "facility",
    "family",
    "billing",
    "launch_progress",
    "player_settings",
    "manual_review",   # BL-008C.36: renders its own masthead; the upload hero used to stack above it
}
if (
    not _viewing_saved_swing
    and st.session_state.get("page") not in _pages_with_own_hero
):
    # Top nav first — this is the upload/landing page (every other page
    # renders its own Edge masthead). Without this the nav painted BELOW
    # the welcome hero, stranding it mid-page.
    from bl_edge_chrome import render_edge_masthead as _render_edge_masthead
    _render_edge_masthead(user, active_page="upload")
    st.markdown(f"""
<div class="bl-hero">
  <div class="bl-hero-row">
    <div class="bl-hero-main">
      <div class="bl-eyebrow">BarrelLabs Performance Lab</div>
      <div class="bl-title">Welcome back, {user['name'].split()[0]}.</div>
      <div class="bl-stitch"></div>
      <div class="bl-subtitle">
        Drop your next swing and get a side-by-side MLB comparison,
        biomechanical breakdown, and a personalized drill plan in under a minute.
      </div>
    </div>
    <div class="bl-hero-meta">
      <div class="bl-mode-pill">
        <span class="bl-mode-pill-dot"></span> Live · Hitting Report
      </div>
      <div class="bl-hero-version">SwingAI v1.0</div>
    </div>
  </div>
</div>

<div class="bl-step-grid">
  <div class="bl-step">
    <div class="bl-step-num">01 · Upload</div>
    <div class="bl-step-title">Drop a swing clip</div>
    <div class="bl-step-desc">Side angle, one swing, 2 seconds.</div>
  </div>
  <div class="bl-step">
    <div class="bl-step-num">02 · Analyze</div>
    <div class="bl-step-title">Track mechanics</div>
    <div class="bl-step-desc">Pose tracking + 40+ swing metrics.</div>
  </div>
  <div class="bl-step">
    <div class="bl-step-num">03 · Compare</div>
    <div class="bl-step-title">MLB swing match</div>
    <div class="bl-step-desc">Auto-paired vs an MLB reference.</div>
  </div>
  <div class="bl-step">
    <div class="bl-step-num">04 · Train</div>
    <div class="bl-step-title">Close the gap</div>
    <div class="bl-step-desc">Personalized drills + progress log.</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ---------- SIDEBAR ----------
# Refreshed minimal sidebar. Big logo, clean nav, no chrome.
# Supports collapse-to-icons via st.session_state["sidebar_collapsed"].

if "sidebar_collapsed" not in st.session_state:
    st.session_state["sidebar_collapsed"] = False
_sb_collapsed = bool(st.session_state["sidebar_collapsed"])
_sb_width = "76px" if _sb_collapsed else "272px"

# --- Sidebar CSS (width + look depends on collapsed state) ---
st.markdown(f"""
<style>
/* ============ SIDEBAR — MINIMAL ============ */
section[data-testid="stSidebar"] {{
    background: #050507 !important;
    border-right: 1px solid rgba(255,255,255,0.04) !important;
    transition: width .35s cubic-bezier(.2,.7,.2,1),
                min-width .35s cubic-bezier(.2,.7,.2,1) !important;
    width: {_sb_width} !important;
    min-width: {_sb_width} !important;
    max-width: {_sb_width} !important;
}}
section[data-testid="stSidebar"] > div:first-child {{
    width: {_sb_width} !important;
    padding: 1rem 0.7rem 1.1rem 0.7rem !important;
    transition: width .35s cubic-bezier(.2,.7,.2,1) !important;
}}
section[data-testid="stSidebar"] hr {{
    margin: 0.5rem 0.2rem !important;
    border-color: rgba(255,255,255,0.04) !important;
    opacity: 0.7;
}}

/* ============ BRAND — confident presence, refined spacing ============ */
.bl-sb-brand {{
    padding: 0.25rem 0 0.85rem 0;
    display: flex;
    justify-content: center;
    align-items: center;
}}
.bl-sb-brand img {{
    max-width: {('40px' if _sb_collapsed else '112px')} !important;
    width: {('40px' if _sb_collapsed else '112px')} !important;
    height: auto !important;
    opacity: 0.98;
}}

/* Thin divider beneath brand */
.bl-sb-divider {{
    height: 1px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent);
    margin: 0.1rem 0.4rem 0.7rem 0.4rem;
}}

/* Section group label — micro, almost invisible until you look */
.bl-sb-group-label {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    color: #4a4a4a;
    text-transform: uppercase;
    letter-spacing: 0.28em;
    font-size: 0.54rem;
    font-weight: 600;
    margin: 1.1rem 0 0.4rem 0.85rem;
}}

/* Nav buttons — Linear/Vercel style */
section[data-testid="stSidebar"] .stButton > button {{
    background: transparent !important;
    border: 1px solid transparent !important;
    color: #b8b8b8 !important;
    text-align: left !important;
    font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.9rem !important;
    letter-spacing: -0.005em !important;
    padding: 0.55rem 0.8rem !important;
    margin: 1px 0 !important;
    border-radius: 10px !important;
    justify-content: {'center' if _sb_collapsed else 'flex-start'} !important;
    box-shadow: none !important;
    transition: background .15s ease, color .15s ease !important;
    position: relative !important;
    min-height: 34px !important;
    height: 34px !important;
}}
section[data-testid="stSidebar"] .stButton > button:hover {{
    background: rgba(255,255,255,0.035) !important;
    border-color: transparent !important;
    color: #ffffff !important;
    transform: none !important;
}}
section[data-testid="stSidebar"] .stButton > button:focus {{
    outline: none !important;
    box-shadow: none !important;
}}
section[data-testid="stSidebar"] .stButton > button p {{
    font-size: 0.9rem !important;
    font-weight: 500 !important;
    line-height: 1.2 !important;
    margin: 0 !important;
}}

/* Active item — thin red bar on the left, subtle bg */
.bl-sb-active-marker .stButton > button {{
    background: rgba(255,255,255,0.045) !important;
    color: #ffffff !important;
    border-color: transparent !important;
    box-shadow: inset 3px 0 0 0 #FF3B30 !important;
    padding-left: 0.95rem !important;
}}
.bl-sb-active-marker .stButton > button p {{
    color: #ffffff !important;
    font-weight: 600 !important;
}}

/* CTA primary (Analyze New Swing) — red pill */
.bl-sb-cta .stButton > button {{
    background: #FF3B30 !important;
    border-color: #FF3B30 !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    margin-bottom: 0.4rem !important;
    box-shadow: 0 6px 18px -6px rgba(255,59,48,0.55) !important;
}}
.bl-sb-cta .stButton > button:hover {{
    background: #ff5147 !important;
    border-color: #ff5147 !important;
    color: #ffffff !important;
    box-shadow: 0 8px 22px -6px rgba(255,59,48,0.7) !important;
}}
.bl-sb-cta .stButton > button p {{
    color: #ffffff !important;
    font-weight: 600 !important;
}}

/* Collapse toggle — tiny chevron, top-right */
.bl-sb-toggle {{
    display: flex;
    justify-content: {'center' if _sb_collapsed else 'flex-end'};
    align-items: center;
    margin: -0.4rem 0.1rem 0.4rem 0.1rem;
}}
.bl-sb-toggle [data-testid="stButton"],
.bl-sb-toggle .stButton {{
    width: 28px !important;
    min-width: 28px !important;
    max-width: 28px !important;
    flex: 0 0 auto !important;
}}
.bl-sb-toggle .stButton > button {{
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.05) !important;
    color: #6b6b6b !important;
    font-weight: 500 !important;
    font-size: 0.95rem !important;
    line-height: 1 !important;
    padding: 0 !important;
    width: 28px !important;
    height: 28px !important;
    min-height: 28px !important;
    border-radius: 999px !important;
    display: inline-flex !important;
    justify-content: center !important;
    align-items: center !important;
}}
.bl-sb-toggle .stButton > button:hover {{
    background: rgba(255,255,255,0.05) !important;
    color: #ffffff !important;
    border-color: rgba(255,255,255,0.14) !important;
}}
.bl-sb-toggle .stButton > button p {{
    margin: 0 !important;
    line-height: 1 !important;
    font-size: 0.95rem !important;
    color: inherit !important;
}}

/* ============ USER CHIP (bottom) — tiny + plan badge ============ */
.bl-sb-userchip {{
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.5rem 0.6rem;
    background: rgba(255,255,255,0.022);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px;
    margin-top: 0.9rem;
}}
.bl-sb-userchip-avatar {{
    width: 28px; height: 28px;
    border-radius: 50%;
    background: linear-gradient(135deg, #FF3B30, #c4221a);
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: 'Inter', sans-serif;
    font-weight: 700;
    font-size: 0.72rem;
    letter-spacing: 0.04em;
    flex-shrink: 0;
}}
.bl-sb-userchip-body {{
    display: flex;
    flex-direction: column;
    min-width: 0;
    flex: 1;
}}
.bl-sb-userchip-name {{
    color: #f4f4f4;
    font-size: 0.78rem;
    font-weight: 600;
    line-height: 1.1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.bl-sb-userchip-plan {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    color: #FF3B30;
    font-size: 0.56rem;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    margin-top: 2px;
}}

/* Expander (analysis options) — minimal */
section[data-testid="stSidebar"] details {{
    background: transparent !important;
    border: 1px solid rgba(255,255,255,0.05) !important;
    border-radius: 10px !important;
    margin-top: 0.8rem !important;
}}
section[data-testid="stSidebar"] details > summary {{
    padding: 0.55rem 0.8rem !important;
    font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
    font-size: 0.8rem !important;
    font-weight: 500 !important;
    color: #b0b0b0 !important;
    letter-spacing: -0.005em !important;
}}
section[data-testid="stSidebar"] details > summary:hover {{
    color: #ffffff !important;
}}

/* Hide labels in collapsed mode for non-button content */
{'.bl-collapsible-hide { display: none !important; }' if _sb_collapsed else ''}
</style>
""", unsafe_allow_html=True)


def _clear_saved_report_view():
    """Pop both the legacy disk-path key and the new in-memory record."""
    st.session_state.pop("view_swing_path", None)
    st.session_state.pop("view_swing_record", None)


def _go_upload():
    # Set an explicit "upload" page rather than popping. The default-
    # landing fallback re-assigns page=dashboard whenever the key is
    # missing, which made clicking "Analyze new swing" silently bounce
    # back to the dashboard. Using a real page value keeps us on the
    # upload UI (no routing block claims "upload", so we fall through).
    st.session_state["page"] = "upload"
    st.session_state.pop("view", None)
    _clear_saved_report_view()


def _go_dashboard():
    st.session_state["page"] = "dashboard"
    st.session_state.pop("view", None)
    _clear_saved_report_view()


def _go_page(p):
    def _f():
        st.session_state["page"] = p
        st.session_state.pop("view", None)
        _clear_saved_report_view()
    return _f


def _go_logout():
    try:
        from auth import sign_out
        sign_out()
    except Exception:
        pass
    # Clear the active profile + the picker flag too, so logging into a
    # DIFFERENT household in the same browser re-shows "Who's training?"
    # instead of inheriting the previous session's picked state.
    for _k in ("user", "player", "_profile_picked", "_action"):
        st.session_state.pop(_k, None)


# Determine active item (used for highlight glow)
_active_key = None
if st.session_state.get("page") == "dashboard":
    _active_key = "dashboard"
elif st.session_state.get("page") == "development_tracker":
    _active_key = "tracker"
elif st.session_state.get("page") == "historical_charts":
    _active_key = "history"
elif st.session_state.get("page") == "compare_swings":
    _active_key = "compare"
elif st.session_state.get("page") == "saved_reports":
    _active_key = "saved"
elif st.session_state.get("page") == "billing":
    _active_key = "billing"
elif st.session_state.get("page") == "launch_progress":
    _active_key = "launch"
elif st.session_state.get("page") == "player_settings":
    _active_key = "settings"


def _nav_btn(icon: str, label: str, key: str, action, primary: bool = False):
    """Render one sidebar nav row. Icon-only when collapsed; full label otherwise."""
    is_active = (_active_key == key)
    wrap_cls = "bl-sb-active-marker" if is_active else (
        "bl-sb-cta" if primary else ""
    )
    btn_label = icon if _sb_collapsed else f"{icon}    {label}"

    if wrap_cls:
        st.markdown(f'<div class="{wrap_cls}">', unsafe_allow_html=True)
    clicked = st.button(
        btn_label,
        width="stretch",
        key=f"nav_{key}",
        help=label if _sb_collapsed else None,
    )
    if wrap_cls:
        st.markdown('</div>', unsafe_allow_html=True)

    if clicked:
        action()
        st.rerun()


# ---------- LEFT SIDEBAR (removed) ----------
# The left st.sidebar nav was removed in favor of a single navigation
# system: the top Edge masthead (bl_edge_chrome.render_edge_masthead),
# which is now rendered on every destination including the upload and
# billing pages. What used to live here:
#   - nav buttons (Dashboard, Development tracker, etc.) → now masthead
#     nav tabs; "Analyze new swing" is the masthead's primary CTA.
#   - logout / billing / launch-progress → still reachable: logout +
#     "Manage billing" live in Player Settings; launch via
#     ?page=launch_progress.
#   - the "Analysis options" expander (hand_override / ref_choice) →
#     relocated to the upload page body next to the file uploader; its
#     default-assignment logic runs unconditionally on the upload path
#     so the analysis flow always has these values.
# The Streamlit sidebar element itself is hidden via global CSS
# (bl_theme.BL_GLOBAL_CSS) so no empty rail or collapse arrow remains.


# ---------- DASHBOARD PAGE ----------
# Render the BarrelLabs dashboard. Acts as the default landing screen
# for authenticated users; sits above the legacy upload flow.
#
# v3 ("Edge") is now the DEFAULT dashboard.  v2 and v1 remain in the
# codebase as URL-flag fallbacks so any user can hot-revert mid-session
# if v3 breaks for them — no redeploy required.
#
# Escape hatches (per-session, no code change):
#   /?v3=0          → fall back to v2 (the previous default)
#   /?v3=0&v2=0     → fall back to v1 (legacy layout)
#   /?v3=1          → explicitly force v3 (redundant under the new default)
#
# To revert the default itself: flip the v3 default below from True
# back to False. v2 will resume as default; v3 stays available via
# ?v3=1.  The full pre-flip app.py is also snapshotted at
# _backup_dashboards_2026-05-17/app.py.backup-pre-v3-default.
if st.session_state.get("page") == "dashboard":
    # URL overrides — per-session toggles.
    try:
        qp = st.query_params
        if "v2" in qp:
            st.session_state["use_dashboard_v2"] = str(qp["v2"]).strip() in ("1", "true", "yes", "on")
        if "v3" in qp:
            st.session_state["use_dashboard_v3"] = str(qp["v3"]).strip() in ("1", "true", "yes", "on")
    except Exception:
        pass

    # v3 is the only dashboard now (the v1/v2 renderers were retired).
    from dashboard_v3 import render_dashboard_v3
    render_dashboard_v3(user)
    st.stop()


# ---------- SAVED REPORTS PAGE (DASHBOARD-STYLE) ----------
# Sessions tab in the Edge masthead lands here. Renders the dashboard-
# style saved-reports archive (see saved_reports_dashboard.py). Clicking
# Open Report sets `page = "swing_report"` which routes through
# swing_report_page.render_swing_report_page to the new dashboard-style
# Premium Swing Report renderer. PDF download wiring is unchanged —
# build_swing_report_pdf is passed through identically to the legacy page.
if st.session_state.get("page") == "saved_reports":
    render_saved_reports_dashboard(user, build_pdf_fn=build_swing_report_pdf)
    st.stop()


# ---------- "COMING SOON" STUB (Compare Swings, Billing) ----------
def _render_coming_soon(eyebrow: str, title: str, sub: str) -> None:
    """Premium 'coming soon' placeholder for nav items not yet built."""
    st.markdown(
        f"""
        <div style="padding: 5.5rem 2rem 2rem 2rem; text-align: center;
                    max-width: 680px; margin: 0 auto;
                    font-family: 'Inter', -apple-system, system-ui, sans-serif;">
          <div style="font-family: 'JetBrains Mono', ui-monospace, monospace;
                      font-size: 0.7rem; letter-spacing: 0.24em; color: #FF3B30;
                      font-weight: 600; margin-bottom: 1.2rem;">{eyebrow}</div>
          <div style="font-size: 3rem; font-weight: 700; letter-spacing: -0.045em;
                      color: #fafafa; line-height: 1.05;">{title}</div>
          <div style="margin-top: 1.1rem; color: #8b8b8b; font-size: 1.02rem;
                      line-height: 1.55; max-width: 520px;
                      margin-left: auto; margin-right: auto;">{sub}</div>
          <div style="margin-top: 2.4rem; display: inline-flex; gap: 0.55rem;
                      align-items: center; padding: 0.5rem 1rem;
                      background: rgba(255,59,48,0.06);
                      border: 1px solid rgba(255,59,48,0.22);
                      border-radius: 999px;
                      font-family: 'JetBrains Mono', ui-monospace, monospace;
                      font-size: 0.66rem; letter-spacing: 0.2em;
                      color: #ffb4b4; text-transform: uppercase; font-weight: 600;">
            <span style="display:inline-block;width:6px;height:6px;border-radius:50%;
                         background:#FF3B30;box-shadow:0 0 8px rgba(255,59,48,0.6);"></span>
            In development
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


if st.session_state.get("page") == "compare_swings":
    # Full editorial side-by-side comparison of the player's own swings.
    from compare_swings_page import render_compare_swings_page
    render_compare_swings_page(user)
    st.stop()


if st.session_state.get("page") == "manual_review":
    # BL-008C.19: separate, experimental user-guided swing review path
    # (engine_v2.user_guided_flow). Completely independent of the
    # automatic analyze()/Edge-Score/MLB-match pipeline above -- does not
    # import from or modify analyzer.py, swing_score.py, or mlb_match.py.
    # Reached only via the "select & confirm manually" button on the
    # upload page, not a top-level nav tab.
    from manual_swing_review import render_manual_swing_review
    render_manual_swing_review(user)
    st.stop()


if st.session_state.get("page") == "billing":
    # Full premium Billing page — hero status banner, plan-aware primary
    # actions (Manage Subscription / Compare Plans / Upgrade), "what's
    # included" feature checklist, and beta-code redemption.
    # Carry the unified Edge masthead so Billing isn't stranded without
    # nav now that the left sidebar is gone. active_page="billing" isn't
    # a nav tab, so no tab highlights — which is correct (billing is
    # reached from Settings, not a top-level tab).
    from bl_edge_chrome import render_edge_masthead as _render_edge_masthead
    _render_edge_masthead(user, active_page="billing")
    _render_billing_page()
    st.stop()


# ---------- NAV PAGE ROUTING (Training Plan / Progress / Library) ----------
# Moved here from the pre-auth GLOBAL PAGE ROUTING block so their Edge masthead
# renders at the SAME element-tree position as the other nav pages above
# (dashboard / sessions / compare). That lets Streamlit reuse the single
# full-bleed masthead in place instead of tearing it down and leaving a ghost
# nav bar that flashed on navigation.
if st.session_state.get("page") == "development_tracker":
    render_development_tracker()
    st.stop()

if st.session_state.get("page") == "historical_charts":
    render_historical_charts()
    st.stop()

if st.session_state.get("page") == "drill_library":
    render_drill_library()
    st.stop()


# ---------- LAUNCH PROGRESS PAGE ----------
def _render_launch_progress() -> None:
    """Pre-launch readiness checklist — tracks what's done vs. still needed."""
    # Sections, each item: (status, title, detail)
    #   status ∈ {"done", "in_progress", "todo"}
    sections = [
        ("Core Product", [
            ("done",        "Swing analysis pipeline",        "Pose tracking, slow-mo correction, MLB comparison engine."),
            ("done",        "Premium swing report (on-screen)", "Coach's summary, Top 3 Fixes, Swing DNA, vs-last-swing, MLB comp card, drill plan."),
            ("done",        "Saved report viewer",            "Past swings open into the same premium report."),
            ("done",        "PDF export",                     "Full premium report rendered to PDF for download / share."),
            ("done",        "PDF overlap fixes",              "Section text overlap bugs in the multi-page PDF resolved."),
            ("done",        "Premium Swing Progress section", "Replaces 'vs. last swing' — KPI tiles, recent trend, biggest mover, per-category deltas. Renders on every report (with first-swing baseline)."),
            ("done",        "Premium Compare Swings page",    "Pick-two-swings dropdowns, side-by-side cards w/ delta pill, improved/regressed grids, focus-area timeline."),
            ("done",        "History-aware Top Fixes",        "Recurring pill + 'coach memory' line — Top 3 Fixes now reference your last swing's gap."),
            ("done",        "Embedded swing video in report", "Original swing video plays under the hero in-app, plus clickable signed link in the PDF. Saved via Supabase Storage."),
            ("done",        "End-of-report next-step CTA",    "Drill-aware closing strip: Priority 1 + reps + 'Train it. Compare it.' guidance. Mirrored in PDF."),
            ("done",        "Drill-completion tracking",      "Per-swing checkbox grid on saved reports — piggybacks on training_logs.drill_state."),
            ("done",        "Per-swing notes",                "Quick notes textarea on every saved swing, persisted per swing id."),
            ("done",        "Development Tracker auto-updates", "Latest swing's drill plan replaces the previous one automatically."),
            ("done",        "Performance Over Time charts",   "Score trend, mechanics-by-category, milestone insights."),
            ("done",        "Premium upload page",            "Hero, recording tips, branded drop zone."),
            ("done",        "Premium login / signup page",    "Updated to match the new design system."),
            ("todo",        "Shareable public swing report",  "Player gets a /swings/[uuid] page they can send to friends / coaches."),
            ("todo",        "Camera-angle re-upload prompt",  "Modal that surfaces re-upload CTA when rotation confidence is low."),
            ("todo",        "Mobile-friendly upload flow",    "Verified end-to-end on iOS Safari / Android Chrome."),
        ]),
        ("Monetization", [
            ("todo", "Stripe Checkout integration",       "Hosted Checkout for new subscribers."),
            ("todo", "Stripe Customer Portal",            "Let users self-manage card, plan, cancel."),
            ("todo", "Supabase `subscriptions` table",    "stripe_customer_id, stripe_subscription_id, status, period_end, tier."),
            ("todo", "Stripe webhooks handler",           "subscription.created/updated/deleted, invoice.payment_failed."),
            ("todo", "Beta free trial",                   "7- or 14-day Stripe trial with/without card."),
            ("todo", "Subscription gating on analyze",    "Block new uploads when status != active/trialing."),
            ("todo", "Free-tier analysis cap",            "Hard cap on free uploads (e.g., 3) to prevent compute abuse."),
            ("todo", "Refund policy + page",              "Written, linked from billing screen."),
            ("todo", "Coach / Team accounts (v2)",        "Multi-player rosters under one subscription — major upsell."),
        ]),
        ("Compliance & Legal", [
            ("todo", "Terms of Service",                  "Required before charging. Termly / iubenda templates."),
            ("todo", "Privacy Policy",                    "GDPR / CCPA compliant — covers video data handling."),
            ("todo", "Account deletion + data export",    "Required for app stores and most privacy regs."),
            ("todo", "Cookie consent banner",             "If serving EU users."),
        ]),
        ("Reliability & Ops", [
            ("todo", "Error tracking (Sentry)",           "Capture crashes in analyzer and Streamlit UI."),
            ("todo", "Product analytics",                 "PostHog or Mixpanel — which swings convert, which drills get clicked."),
            ("todo", "Uptime monitoring",                 "Better Stack free tier on /healthz endpoint."),
            ("todo", "Supabase backup verification",      "Confirm automated daily backups are on + restorable."),
            ("todo", "Video upload validation",           "File size cap (100MB), duration cap (15s), virus scan."),
            ("todo", "Rate limiting at API edge",         "IP-level + user-level caps to absorb spikes."),
        ]),
        ("Growth & Onboarding", [
            ("todo", "Transactional email pipeline",      "Resend or Postmark — welcome, trial-ending, payment-failed, canceled."),
            ("todo", "First-time onboarding flow",        "Filming guide, sample swing, expectation-setting before first upload."),
            ("todo", "Example MLB swings to browse",      "Lets visitors see the analysis quality before signing up."),
            ("todo", "Drills library (fielding / pitching)", "Static curated library for non-hitting drills as upsell content."),
            ("todo", "Personalized practice plan generator", "Multi-week plan combining hitting drills + supplemental work."),
            ("todo", "Referral / invite-a-teammate",      "Bonus swings for both sides when a friend signs up."),
        ]),
    ]

    # --- Compute totals for the header ring ---
    total = 0
    done = 0
    in_prog = 0
    for _, items in sections:
        for status, *_ in items:
            total += 1
            if status == "done":     done += 1
            if status == "in_progress": in_prog += 1
    pct = (done / total * 100.0) if total else 0.0

    # SVG ring math
    import math as _m
    _radius = 78
    _stroke = 12
    _circ = 2 * _m.pi * _radius
    _dash_off = _circ * (1 - pct / 100.0)
    _ring_color = "#6ee7b7" if pct >= 80 else ("#fbbf24" if pct >= 50 else "#FF3B30")

    # ---- LOCAL STYLES (scoped to .bl-lp-*) ----
    st.markdown("""
<style>
.bl-lp-hero {
    position: relative;
    border-radius: 24px;
    border: 1px solid rgba(255,255,255,0.07);
    background:
        radial-gradient(ellipse at 80% -10%, rgba(255,59,48,0.10) 0%, transparent 60%),
        linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.012));
    padding: 2rem 2.2rem 1.6rem 2.2rem;
    margin-bottom: 1.6rem;
}
.bl-lp-hero-grid {
    display: grid;
    grid-template-columns: 200px 1fr;
    gap: 2rem;
    align-items: center;
}
@media (max-width: 760px) {
    .bl-lp-hero-grid { grid-template-columns: 1fr; }
}
.bl-lp-ring-wrap {
    position: relative;
    width: 200px;
    height: 200px;
    display: flex;
    align-items: center;
    justify-content: center;
}
.bl-lp-ring-center {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
}
.bl-lp-ring-num {
    font-size: 2.5rem;
    font-weight: 800;
    letter-spacing: -0.04em;
    color: #fafafa;
    line-height: 1;
}
.bl-lp-ring-pct { font-size: 1.4rem; font-weight: 700; color: #a3a3a3; }
.bl-lp-ring-sub {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.58rem;
    letter-spacing: 0.22em;
    color: #8b8b8b;
    text-transform: uppercase;
    font-weight: 600;
    margin-top: 0.4rem;
}
.bl-lp-eyebrow {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.62rem;
    letter-spacing: 0.26em;
    color: #FF3B30;
    font-weight: 700;
    text-transform: uppercase;
    margin-bottom: 0.6rem;
}
.bl-lp-title {
    font-size: 2.3rem;
    font-weight: 800;
    letter-spacing: -0.035em;
    line-height: 1.05;
    color: #fafafa;
    margin-bottom: 0.55rem;
}
.bl-lp-title .accent { color: #FF3B30; }
.bl-lp-sub {
    color: #a3a3a3;
    font-size: 1.0rem;
    line-height: 1.5;
}
.bl-lp-kpi {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.7rem;
    margin-top: 1rem;
}
.bl-lp-kpi-tile {
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.07);
    background: rgba(255,255,255,0.025);
    padding: 0.7rem 0.85rem;
}
.bl-lp-kpi-label {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.55rem;
    letter-spacing: 0.22em;
    color: #8b8b8b;
    text-transform: uppercase;
    font-weight: 600;
}
.bl-lp-kpi-value {
    font-size: 1.45rem;
    font-weight: 800;
    color: #fafafa;
    letter-spacing: -0.02em;
    margin-top: 0.15rem;
}
.bl-lp-kpi-value.is-done { color: #6ee7b7; }
.bl-lp-kpi-value.is-prog { color: #fbbf24; }
.bl-lp-kpi-value.is-todo { color: #FF3B30; }

/* Section */
.bl-lp-sec-head {
    display: flex;
    align-items: baseline;
    gap: 0.85rem;
    margin: 1.6rem 0 0.7rem 0;
}
.bl-lp-sec-num {
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.62rem;
    letter-spacing: 0.24em;
    color: #FF3B30;
    font-weight: 700;
    text-transform: uppercase;
}
.bl-lp-sec-title {
    font-size: 1.45rem;
    font-weight: 700;
    color: #fafafa;
    letter-spacing: -0.015em;
}
.bl-lp-sec-meta {
    margin-left: auto;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.6rem;
    letter-spacing: 0.2em;
    color: #8b8b8b;
    text-transform: uppercase;
}

/* Item rows */
.bl-lp-list {
    display: flex;
    flex-direction: column;
    gap: 0.55rem;
}
.bl-lp-item {
    display: grid;
    grid-template-columns: 28px 1fr 110px;
    gap: 0.9rem;
    align-items: start;
    padding: 0.85rem 1rem;
    border-radius: 12px;
    background: rgba(255,255,255,0.022);
    border: 1px solid rgba(255,255,255,0.05);
    transition: all 0.15s ease;
}
.bl-lp-item:hover {
    border-color: rgba(255,59,48,0.18);
}
.bl-lp-item.is-done {
    background: rgba(110,231,183,0.04);
    border-color: rgba(110,231,183,0.18);
}
.bl-lp-item.is-prog {
    background: rgba(251,191,36,0.05);
    border-color: rgba(251,191,36,0.22);
}
.bl-lp-mark {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.95rem;
    font-weight: 800;
}
.bl-lp-mark.is-done {
    background: rgba(110,231,183,0.12);
    color: #6ee7b7;
    border: 1px solid rgba(110,231,183,0.32);
}
.bl-lp-mark.is-prog {
    background: rgba(251,191,36,0.12);
    color: #fbbf24;
    border: 1px solid rgba(251,191,36,0.32);
}
.bl-lp-mark.is-todo {
    background: rgba(255,59,48,0.08);
    color: #FF3B30;
    border: 1px solid rgba(255,59,48,0.28);
}
.bl-lp-item-title {
    color: #fafafa;
    font-weight: 700;
    font-size: 0.98rem;
    letter-spacing: -0.005em;
}
.bl-lp-item-detail {
    color: #a3a3a3;
    font-size: 0.85rem;
    line-height: 1.4;
    margin-top: 0.18rem;
}
.bl-lp-pill {
    justify-self: end;
    padding: 0.28rem 0.65rem;
    border-radius: 999px;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    font-size: 0.55rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    font-weight: 700;
    align-self: center;
    white-space: nowrap;
}
.bl-lp-pill.is-done {
    background: rgba(110,231,183,0.10);
    color: #6ee7b7;
    border: 1px solid rgba(110,231,183,0.32);
}
.bl-lp-pill.is-prog {
    background: rgba(251,191,36,0.10);
    color: #fbbf24;
    border: 1px solid rgba(251,191,36,0.32);
}
.bl-lp-pill.is-todo {
    background: rgba(255,255,255,0.04);
    color: #d4d4d4;
    border: 1px solid rgba(255,255,255,0.12);
}
</style>
""", unsafe_allow_html=True)

    # ---- HERO + summary ring ----
    st.markdown(f"""
<div class="bl-lp-hero">
  <div class="bl-lp-hero-grid">
    <div class="bl-lp-ring-wrap">
      <svg width="200" height="200" viewBox="0 0 200 200">
        <circle cx="100" cy="100" r="{_radius}" fill="none"
                stroke="rgba(255,255,255,0.08)" stroke-width="{_stroke}" />
        <circle cx="100" cy="100" r="{_radius}" fill="none"
                stroke="{_ring_color}" stroke-width="{_stroke}"
                stroke-linecap="round"
                stroke-dasharray="{_circ:.2f}"
                stroke-dashoffset="{_dash_off:.2f}"
                transform="rotate(-90 100 100)" />
      </svg>
      <div class="bl-lp-ring-center">
        <div class="bl-lp-ring-num">{int(round(pct))}<span class="bl-lp-ring-pct">%</span></div>
        <div class="bl-lp-ring-sub">Launch Ready</div>
      </div>
    </div>
    <div>
      <div class="bl-lp-eyebrow">Pre-Launch · Launch Checklist</div>
      <div class="bl-lp-title">Road to <span class="accent">launch.</span></div>
      <div class="bl-lp-sub">Everything that has to be true before BarrelLabs SwingAI is paid-customer ready. Updated as we ship.</div>
      <div class="bl-lp-kpi">
        <div class="bl-lp-kpi-tile">
          <div class="bl-lp-kpi-label">Shipped</div>
          <div class="bl-lp-kpi-value is-done">{done}</div>
        </div>
        <div class="bl-lp-kpi-tile">
          <div class="bl-lp-kpi-label">In Progress</div>
          <div class="bl-lp-kpi-value is-prog">{in_prog}</div>
        </div>
        <div class="bl-lp-kpi-tile">
          <div class="bl-lp-kpi-label">To Do</div>
          <div class="bl-lp-kpi-value is-todo">{total - done - in_prog}</div>
        </div>
      </div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    # ---- SECTIONS ----
    _mark = {"done": "✓", "in_progress": "◐", "todo": "○"}
    _pill = {"done": "SHIPPED", "in_progress": "IN PROGRESS", "todo": "TO DO"}
    _cls  = {"done": "is-done", "in_progress": "is-prog", "todo": "is-todo"}

    import html as _html
    for i, (sec_title, items) in enumerate(sections, 1):
        sec_done = sum(1 for it in items if it[0] == "done")
        st.markdown(f"""
<div class="bl-lp-sec-head">
  <div class="bl-lp-sec-num">{i:02d}</div>
  <div class="bl-lp-sec-title">{_html.escape(sec_title)}</div>
  <div class="bl-lp-sec-meta">{sec_done} / {len(items)} shipped</div>
</div>
""", unsafe_allow_html=True)

        rows_html = '<div class="bl-lp-list">'
        for status, title, detail in items:
            rows_html += f"""
<div class="bl-lp-item {_cls[status]}">
  <div class="bl-lp-mark {_cls[status]}">{_mark[status]}</div>
  <div>
    <div class="bl-lp-item-title">{_html.escape(title)}</div>
    <div class="bl-lp-item-detail">{_html.escape(detail)}</div>
  </div>
  <div class="bl-lp-pill {_cls[status]}">{_pill[status]}</div>
</div>
"""
        rows_html += "</div>"
        # strip per-line leading whitespace so Streamlit's markdown parser
        # doesn't treat indented lines as code blocks.
        st.markdown("\n".join(ln.lstrip() for ln in rows_html.splitlines()),
                    unsafe_allow_html=True)


if st.session_state.get("page") == "launch_progress":
    # BL-004B (C.2): fail closed, defense-in-depth. "launch_progress" is no
    # longer in _ALLOWED_PAGES_FROM_URL, so this should be unreachable —
    # but if session_state["page"] ever ends up here some other way (a
    # stale value from before this fix, a future code path that forgets
    # the allowlist), do NOT render the internal roadmap. Send the user
    # somewhere real instead of exposing it.
    st.session_state["page"] = "dashboard"
    st.rerun()


# ---------- PLAYER SETTINGS PAGE (new dashboard-styled route) ----------
# Triggered by clicking the avatar circle in the Edge masthead. The page
# itself lives in player_settings_page.py — full edit surface for the
# player profile, baseball context, swing preferences, account/billing,
# privacy toggles, and the danger-zone account-delete flow.
if st.session_state.get("page") == "player_settings":
    from player_settings_page import render_player_settings_page
    render_player_settings_page(user, build_pdf_fn=build_swing_report_pdf)
    st.stop()


# ---------- FAMILY DASHBOARD (Family Pro households only) ----------
if st.session_state.get("page") == "family":
    # Render the masthead ONCE at the top (consistent position, and so the
    # upload/landing block can't ALSO render one -> previously a duplicate-key
    # crash for a non-Family-Pro user reaching ?page=family).
    from bl_edge_chrome import render_edge_masthead as _render_edge_masthead
    from bl_theme import inject_global_theme as _inject_theme
    _inject_theme()
    _render_edge_masthead(user, active_page="family")
    import family_storage as _fam_storage
    _fam_user_id = (
        (user or {}).get("user_id")
        or (user or {}).get("id")
        or ""
    )
    _has_family = (
        _fam_storage.is_family_pro_member(_fam_user_id)
        or _fam_storage.load_family_for_user(_fam_user_id) is not None
    )
    if not _has_family:
        from bl_edge_chrome import render_edge_page_wrapper_open, render_edge_page_wrapper_close
        render_edge_page_wrapper_open()
        st.error("The Family Dashboard is for Family Pro households.")
        if st.button("View pricing →", key="family_guard_pricing"):
            st.session_state["page"] = "pricing"
            st.rerun()
        render_edge_page_wrapper_close()
        st.stop()
    import family_dashboard as _fam_dash
    _fam_dash.render_family_dashboard()
    st.stop()


# ---------- FACILITY / COACH ROSTER (facility owners only) ----------
if st.session_state.get("page") == "facility":
    from bl_edge_chrome import render_edge_masthead as _render_edge_masthead
    from bl_theme import inject_global_theme as _inject_theme
    _inject_theme()
    _render_edge_masthead(user, active_page="facility")
    import facility_dashboard as _fac_dash
    _fac_dash.render_facility_dashboard()
    st.stop()


# ---------- DASHBOARD-STYLE REPORT PREVIEW (NOT live; design approval) ----
# Additive route — does NOT change the production Open Report flow below.
# Reachable only via:
#     ?page=swing_report_preview          (URL hint, sets session state)
#  OR st.session_state["page"] == "swing_report_preview"
# If a real swing is selected (view_swing_record / view_swing_path), it
# renders with that record. Otherwise it falls back to a clearly-labeled
# SAMPLE_RECORD inside swing_report_dashboard_preview.py.
_qp_page = st.query_params.get("page") if hasattr(st, "query_params") else None
if (
    st.session_state.get("page") == "swing_report_preview"
    or (isinstance(_qp_page, str) and _qp_page == "swing_report_preview")
    or (isinstance(_qp_page, list) and "swing_report_preview" in _qp_page)
):
    st.session_state["page"] = "swing_report_preview"
    try:
        from swing_report_dashboard_preview import (
            render_swing_report_dashboard_preview,
            SAMPLE_RECORD,
        )
        _preview_rec = st.session_state.get("view_swing_record")
        if not (isinstance(_preview_rec, dict) and _preview_rec):
            _pv_path = st.session_state.get("view_swing_path")
            if _pv_path:
                _preview_rec = load_saved_swing_record(_pv_path)
        _is_sample = not (isinstance(_preview_rec, dict) and _preview_rec)
        if _is_sample:
            _preview_rec = SAMPLE_RECORD
            _preview_hist = SAMPLE_RECORD.get("score_history") or []
        else:
            try:
                _preview_hist = load_swing_history(user["slug"]) or []
            except Exception:
                _preview_hist = []
        render_swing_report_dashboard_preview(
            _preview_rec, _preview_hist, is_sample=_is_sample,
        )
    except Exception as _prev_err:
        st.error(f"Preview render failed: {_prev_err}")
    st.stop()


# ---------- SAVED REPORT VIEWER (now: dedicated swing_report page) ----------
# When the user clicks "Open Report" on the Sessions page, we route to a
# DEDICATED individual-swing-report page (swing_report_page.py). This
# replaces the old behavior of re-skinning the entire dashboard with the
# selected swing's data via render_dashboard_v3(force_record=...), which
# made "Open Report" feel like it landed the user back on the dashboard.
#
# Triggers:
#   - st.session_state["page"] == "swing_report"  (set by Open Report)
#   - OR view_swing_record / view_swing_path is set without an explicit
#     page (legacy callers — still honoured for backward compatibility)
_should_open_report = (
    st.session_state.get("page") == "swing_report"
    or "view_swing_record" in st.session_state
    or "view_swing_path" in st.session_state
)

if _should_open_report:
    saved_record = None

    in_mem = st.session_state.get("view_swing_record")
    if isinstance(in_mem, dict) and in_mem:
        saved_record = in_mem
    elif "view_swing_path" in st.session_state:
        saved_record = load_saved_swing_record(st.session_state.view_swing_path)

    if saved_record:
        # The dedicated swing_report_page module renders ONE focused swing
        # report (NOT the dashboard) plus the redesigned comparison.
        try:
            from swing_report_page import render_swing_report_page
            # Load the player's full history so the redesigned
            # comparison can find the previous swing.
            hist = load_swing_history(user["slug"]) or []
            render_swing_report_page(user, saved_record, history=hist)
        except Exception as _srp_err:
            # If the new page errors, surface a graceful message rather
            # than show a blank screen or a stack trace.
            st.error(
                f"Could not render the report page: {_srp_err}. "
                "Please retry."
            )

        st.stop()
    else:
        st.error("Could not load that saved swing report.")
        st.session_state.pop("view_swing_path", None)
        st.session_state.pop("view_swing_record", None)
        st.session_state.pop("view_swing_report_id", None)
        if st.session_state.get("page") == "swing_report":
            st.session_state["page"] = "saved_reports"
        st.stop()


# ---------- UPLOAD ----------
# The Edge masthead for this page is rendered earlier (alongside the welcome
# hero, gated by the same condition) so the nav sits at the very top of the
# page rather than below the hero.

# ===================== ANALYSIS OPTIONS (defaults) =====================
# Relocated out of the old left sidebar. These variables are consumed by
# the analysis flow downstream (HAND_MAP[hand_override] at pose
# detection; ref_choice / ref_options / refs at MLB-comp selection), so
# they MUST be defined unconditionally on every upload-page render —
# before the user even uploads a clip. The interactive override widgets
# (batting-hand radio + "Compare to" selectbox) render in the page body
# next to the uploader below and rebind these names when shown.
profile_hand_label = "Right-handed" if user["handedness"] == "RIGHT" else "Left-handed"
HAND_MAP = {
    f"Use profile ({profile_hand_label})": user["handedness"],
    "Right-handed": "RIGHT",
    "Left-handed":  "LEFT",
    "Auto-detect":  "AUTO",
}
hand_override = f"Use profile ({profile_hand_label})"
refs = list_library_references()
ref_options = ["Auto-pick best match"] + [
    f"{r['name']}  ({r['handedness'][0]}H)" for r in refs
]
ref_choice = ref_options[0]

if "upload_reset_id" not in st.session_state:
    st.session_state.upload_reset_id = 0

# Premium upload page styles (scoped to .bl-up-* namespace).
st.markdown("""
<style>
.bl-up-hero {
    position: relative;
    border-radius: var(--bl-radius-lg);
    border: 1px solid var(--bl-line);
    background:
        radial-gradient(ellipse at 85% -15%, rgba(230,69,48,0.11) 0%, transparent 55%),
        radial-gradient(ellipse at 10% 130%, rgba(232,193,112,0.05) 0%, transparent 50%),
        linear-gradient(180deg, rgba(244,239,230,0.025), rgba(244,239,230,0.010));
    padding: 2.2rem 2.4rem 1.9rem 2.4rem;
    margin-bottom: 1.2rem;
    overflow: hidden;
}
.bl-up-hero::before {
    content: "";
    position: absolute;
    inset: 0;
    background:
        repeating-linear-gradient(
            45deg,
            transparent 0,
            transparent 22px,
            rgba(244,239,230,0.012) 22px,
            rgba(244,239,230,0.012) 44px
        );
    pointer-events: none;
    z-index: 0;
}
.bl-up-hero > * { position: relative; z-index: 1; }
.bl-up-eyebrow {
    font-family: var(--mono);
    font-size: 0.66rem;
    letter-spacing: 0.26em;
    color: var(--red);
    font-weight: 600;
    text-transform: uppercase;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 0.55rem;
}
.bl-up-eyebrow-dot {
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--red);
    box-shadow: 0 0 10px rgba(230,69,48,0.7);
    animation: bl-up-pulse 2s ease-in-out infinite;
}
@keyframes bl-up-pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.55; transform: scale(0.85); }
}
.bl-up-title {
    font-family: var(--serif);
    font-size: 3.1rem;
    font-weight: 400;
    letter-spacing: -0.02em;
    line-height: 1.02;
    color: var(--bone);
    margin-bottom: 0.7rem;
}
.bl-up-title .accent { font-style: italic; color: var(--gold); }
.bl-up-sub {
    color: var(--bl-ink-60);
    font-size: 1.02rem;
    line-height: 1.55;
    max-width: 620px;
}
.bl-up-chip-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 1.2rem;
}
.bl-up-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.4rem 0.8rem;
    border-radius: 999px;
    background: rgba(244,239,230,0.035);
    border: 1px solid var(--bl-line);
    font-family: var(--mono);
    font-size: 0.62rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    font-weight: 500;
    color: var(--bone-dim);
}
.bl-up-chip-num {
    color: var(--red);
    font-weight: 700;
}
/* Payoff strip — shown to first-time users in place of the empty stat chips
   ("0 Swings Analyzed / — Last Score" reads sad on a brand-new account). */
.bl-up-payoff {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.55rem 0.85rem;
    margin-top: 1.4rem;
    padding-top: 1.3rem;
    border-top: 1px solid var(--bl-line);
}
.bl-up-payoff-label {
    font-family: var(--mono);
    font-size: 0.6rem;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--bl-ink-60);
    font-weight: 500;
}
.bl-up-payoff-item {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    color: var(--bone);
    font-size: 0.92rem;
    font-weight: 500;
}
.bl-up-payoff-item .dot {
    width: 5px; height: 5px; border-radius: 50%;
    background: var(--gold);
    box-shadow: 0 0 8px rgba(232,193,112,0.55);
}
.bl-up-payoff-sep { color: var(--bl-ink-40); }

/* How-it-works step rail (Film → Drop → Report) */
.bl-up-tips {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.8rem;
    margin-bottom: 1.4rem;
}
@media (max-width: 760px) {
    .bl-up-tips { grid-template-columns: 1fr; }
}
.bl-up-tip {
    border-radius: var(--bl-radius-md);
    border: 1px solid var(--bl-line);
    background: linear-gradient(180deg, rgba(244,239,230,0.025), rgba(244,239,230,0.008));
    padding: 1.15rem 1.2rem;
    transition: all 0.22s cubic-bezier(.2,.7,.2,1);
}
.bl-up-tip:hover {
    border-color: var(--bl-line-hi);
    background: linear-gradient(180deg, rgba(230,69,48,0.05), rgba(244,239,230,0.012));
    transform: translateY(-2px);
}
.bl-up-tip-icon {
    width: 34px;
    height: 34px;
    border-radius: 10px;
    background: rgba(230,69,48,0.10);
    border: 1px solid rgba(230,69,48,0.22);
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--mono);
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--red);
    margin-bottom: 0.7rem;
}
.bl-up-tip-label {
    font-family: var(--mono);
    font-size: 0.62rem;
    letter-spacing: 0.22em;
    color: var(--red);
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 0.35rem;
}
.bl-up-tip-body {
    color: var(--bone-dim);
    font-size: 0.88rem;
    line-height: 1.5;
}

/* Drop zone framing — the actual st.file_uploader is wrapped in a custom
   container right after; this just adds the section header above it. */
.bl-up-dropzone-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 0.7rem;
}
.bl-up-dropzone-num {
    font-family: var(--mono);
    font-size: 0.6rem;
    letter-spacing: 0.24em;
    color: var(--red);
    font-weight: 600;
    text-transform: uppercase;
}
.bl-up-dropzone-title {
    font-size: 1.2rem;
    font-weight: 600;
    color: var(--bone);
    letter-spacing: -0.01em;
    margin-top: 0.15rem;
}
.bl-up-dropzone-meta {
    font-family: var(--mono);
    font-size: 0.6rem;
    letter-spacing: 0.2em;
    color: var(--bl-ink-60);
    text-transform: uppercase;
}

/* Style the native Streamlit file uploader to feel premium. */
section[data-testid="stFileUploaderDropzone"] {
    border-radius: var(--bl-radius-md) !important;
    border: 1.5px dashed var(--bl-line-hi) !important;
    background:
        radial-gradient(ellipse at 50% 0%, rgba(230,69,48,0.06) 0%, transparent 65%),
        linear-gradient(180deg, rgba(244,239,230,0.02), rgba(244,239,230,0.006)) !important;
    padding: 1.8rem 1.4rem !important;
    transition: all 0.2s ease;
}
section[data-testid="stFileUploaderDropzone"]:hover {
    border-color: rgba(230,69,48,0.45) !important;
    background:
        radial-gradient(ellipse at 50% 0%, rgba(230,69,48,0.10) 0%, transparent 65%),
        linear-gradient(180deg, rgba(230,69,48,0.04), rgba(244,239,230,0.012)) !important;
}
</style>
""", unsafe_allow_html=True)

# Stats for the chip row.
_swing_history_for_chips = load_swing_history(user["slug"])
_total_swings = len(_swing_history_for_chips) if _swing_history_for_chips else 0
_latest_score_chip = _swing_history_for_chips[-1].get("score", "—") if _swing_history_for_chips else "—"
_latest_comp_chip = _swing_history_for_chips[-1].get("reference_name", "—") if _swing_history_for_chips else "—"

# ===== Premium hero =====
# First-time users (no swing history yet) see a "what you'll get" payoff strip;
# returning users see their live stat chips. Empty "0 / —" chips read sad on a
# brand-new account, so we promise the reward instead.
if _total_swings == 0:
    _hero_meta = """
  <div class="bl-up-payoff">
    <span class="bl-up-payoff-label">What you'll get</span>
    <span class="bl-up-payoff-item"><span class="dot"></span>Closest MLB match</span>
    <span class="bl-up-payoff-sep">·</span>
    <span class="bl-up-payoff-item"><span class="dot"></span>Your top 3 fixes</span>
    <span class="bl-up-payoff-sep">·</span>
    <span class="bl-up-payoff-item"><span class="dot"></span>Personalized drill plan</span>
    <span class="bl-up-payoff-sep">·</span>
    <span class="bl-up-payoff-item"><span class="dot"></span>In ~30s</span>
  </div>"""
else:
    _hero_meta = f"""
  <div class="bl-up-chip-row">
    <div class="bl-up-chip"><span class="bl-up-chip-num">{_total_swings}</span> Swings Analyzed</div>
    <div class="bl-up-chip"><span class="bl-up-chip-num">{_latest_score_chip}</span> Last Score</div>
    <div class="bl-up-chip">Last vs · {_latest_comp_chip}</div>
    <div class="bl-up-chip"><span class="bl-up-chip-num">~30s</span> Analysis Time</div>
  </div>"""

st.markdown(f"""
<div class="bl-up-hero">
  <div class="bl-up-eyebrow"><span class="bl-up-eyebrow-dot"></span>Performance Lab · Swing Analyzer</div>
  <div class="bl-up-title">Analyze your <span class="accent">next swing.</span></div>
  <div class="bl-up-sub">Drop in one clip and BarrelLabs SwingAI will pose-track every frame, compare it against the MLB reference library, and return a full premium report with a personalized drill plan.</div>
{_hero_meta}
</div>
""", unsafe_allow_html=True)

# ===== Sample swing video =====
import os as _os
_sample_path = "assets/sample_swing.mp4"
if _os.path.exists(_sample_path):
    import base64 as _base64
    with open(_sample_path, "rb") as _f:
        _b64 = _base64.b64encode(_f.read()).decode("ascii")
    st.markdown(
        f"""
        <div class="bl-up-sample">
          <video autoplay muted loop playsinline preload="auto"
                 style="width:100%;max-width:560px;border-radius:14px;
                        display:block;margin:0 auto;
                        border:1px solid rgba(244,239,230,0.10);">
            <source src="data:video/mp4;base64,{_b64}" type="video/mp4">
          </video>
          <p style="text-align:center;margin-top:14px;color:#C8C4BB;
                    font-family:'Geist',sans-serif;font-size:0.95rem;
                    max-width: 480px; margin-left:auto; margin-right:auto;">
            Film like this — <strong style="color:#F4EFE6;">side angle</strong>,
            <strong style="color:#F4EFE6;">full body in frame</strong>,
            <strong style="color:#F4EFE6;">one swing</strong>.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ===== How it works — 3-step rail (Film → Drop → Report) =====
st.markdown("""
<div class="bl-up-tips">
  <div class="bl-up-tip">
    <div class="bl-up-tip-icon">1</div>
    <div class="bl-up-tip-label">Film</div>
    <div class="bl-up-tip-body">Side angle, full body in frame, one swing. 3–6 seconds in bright, even light.</div>
  </div>
  <div class="bl-up-tip">
    <div class="bl-up-tip-icon">2</div>
    <div class="bl-up-tip-label">Drop</div>
    <div class="bl-up-tip-body">Drop the clip in the box below — straight from your phone is perfect. MP4 or MOV.</div>
  </div>
  <div class="bl-up-tip">
    <div class="bl-up-tip-icon">3</div>
    <div class="bl-up-tip-label">Report</div>
    <div class="bl-up-tip-body">In ~30s: your Edge Score, closest MLB match, and your top 3 fixes with drills.</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ===== Drop zone header =====
st.markdown("""
<div class="bl-up-dropzone-head">
  <div>
    <div class="bl-up-dropzone-num">01 · Upload Clip</div>
    <div class="bl-up-dropzone-title">Drop your swing video here</div>
  </div>
  <div class="bl-up-dropzone-meta">MP4 · MOV · M4V</div>
</div>
""", unsafe_allow_html=True)

upload = st.file_uploader(
    " ",
    type=["mp4", "mov", "m4v"],
    help="MP4 or MOV. One swing per clip.",
    key=f"swing_upload_{st.session_state.upload_reset_id}",
    label_visibility="collapsed",
)

# ===================== ANALYSIS OPTIONS (override widgets) =====================
# Relocated from the old left sidebar to the upload page body. Defaults
# for hand_override / ref_choice were already assigned unconditionally at
# the top of this UPLOAD section; these widgets rebind them when the user
# opens the expander. Semantics/values are unchanged — only the location.
with st.expander("Analysis options", expanded=False):
    hand_override = st.radio(
        "Batting hand for this clip",
        options=[f"Use profile ({profile_hand_label})", "Right-handed",
                 "Left-handed", "Auto-detect"],
        index=0,
        help="Defaults to your profile. Override only if pose detection misreads.",
    )
    ref_choice = st.selectbox(
        "Compare to",
        ref_options,
        index=0,
        help="Auto-pick uses your camera angle + handedness to choose the closest reference.",
    )

reset_l, _, _ = st.columns([1.3, 1, 3])
with reset_l:
    if st.button("Clear current upload", width="stretch", key="clear_upload"):
        st.session_state.upload_reset_id += 1
        st.session_state.pop("view_swing_path", None)
        st.rerun()

if upload is None:
    # Even without an upload, show the player's clickable history below the upload box.
    swing_history = load_swing_history(user["slug"])
    if swing_history:
        st.markdown("""
<div style="margin-top: 2rem; margin-bottom: 0.6rem;">
  <div style="font-family: 'JetBrains Mono', ui-monospace, monospace;
              font-size: 0.6rem; letter-spacing: 0.24em; color: #FF3B30;
              font-weight: 700; text-transform: uppercase;">02 · Past Swings</div>
  <div style="font-size: 1.15rem; font-weight: 700; color: #fafafa;
              letter-spacing: -0.01em; margin-top: 0.2rem;">Open a recent swing report</div>
</div>
""", unsafe_allow_html=True)
        render_swing_history_cards(swing_history, limit=6, title="")
    else:
        # First-time user empty state — premium "what happens next" preview,
        # in the editorial palette (bone/ink, gold accents, Geist Mono labels).
        st.html("""
<div style="margin-top: 1.6rem; border-radius: var(--bl-radius-lg);
            border: 1px solid var(--bl-line);
            background: linear-gradient(180deg, rgba(244,239,230,0.022), rgba(244,239,230,0.006));
            padding: 1.7rem 1.9rem;">
  <div style="font-family: var(--mono);
              font-size: 0.6rem; letter-spacing: 0.24em; color: var(--red);
              font-weight: 600; text-transform: uppercase; margin-bottom: 1.1rem;">
    Inside your first report
  </div>
  <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.4rem;">
    <div>
      <div style="font-family: var(--serif); font-style: italic; font-size: 1.9rem;
                  font-weight: 400; color: var(--gold); letter-spacing: -0.02em; line-height: 1;">01</div>
      <div style="color: var(--bone); font-weight: 600; margin-top: 0.45rem;">MLB Comparison</div>
      <div style="color: var(--bl-ink-60); font-size: 0.88rem; line-height: 1.5; margin-top: 0.35rem;">
        Matched frame-by-frame against the closest pro hitter in our reference library.
      </div>
    </div>
    <div>
      <div style="font-family: var(--serif); font-style: italic; font-size: 1.9rem;
                  font-weight: 400; color: var(--gold); letter-spacing: -0.02em; line-height: 1;">02</div>
      <div style="color: var(--bone); font-weight: 600; margin-top: 0.45rem;">Top 3 Fixes</div>
      <div style="color: var(--bl-ink-60); font-size: 0.88rem; line-height: 1.5; margin-top: 0.35rem;">
        Ranked by impact, each with the "why it costs you" and the feel to fix it.
      </div>
    </div>
    <div>
      <div style="font-family: var(--serif); font-style: italic; font-size: 1.9rem;
                  font-weight: 400; color: var(--gold); letter-spacing: -0.02em; line-height: 1;">03</div>
      <div style="color: var(--bone); font-weight: 600; margin-top: 0.45rem;">Drill Plan</div>
      <div style="color: var(--bl-ink-60); font-size: 0.88rem; line-height: 1.5; margin-top: 0.35rem;">
        Personalized drills with reps, tracked over time in your Development Tracker.
      </div>
    </div>
  </div>
</div>
""")
    st.stop()

# Reject oversized uploads before writing them — a too-large clip wastes disk
# + compute and usually means a very long or unsupported video. (Pose
# detection itself is additionally bounded by run_subprocess's timeout.)
_MAX_UPLOAD_MB = 150
if getattr(upload, "size", 0) and upload.size > _MAX_UPLOAD_MB * 1024 * 1024:
    st.error(
        f"That video is {upload.size / (1024 * 1024):.0f} MB — please upload a "
        f"clip under {_MAX_UPLOAD_MB} MB. A 3–6 second swing filmed from the "
        f"side is all we need."
    )
    st.stop()

# Persist upload under a collision-resistant per-user name. Streamlit shares
# one process + working dir across all users, and every artifact derives from
# this stem, so a raw client filename (phone exports collide constantly) would
# let concurrent users overwrite each other's video/fingerprint mid-analysis.
video_path = UPLOAD_DIR / unique_upload_name(upload.name,
                                             owner=(user or {}).get("slug"))
_upload_bytes = upload.getbuffer()
with open(video_path, "wb") as f:
    f.write(_upload_bytes)

# BL-008C.23/.24: a stable-ACROSS-RETRIES, but NOT stable-forever,
# idempotency key for the usage-charge RPC below.
#
# video_path itself is NOT stable across a rerun -- unique_upload_name
# mints a fresh random token on every script run, so re-clicking
# "Analyze swing" after a failed charge would get a different video_path
# each time and defeat retry-safety. BL-008C.23 used a bare content hash
# (SHA-256 of the uploaded bytes) to fix that -- but BL-008C.24 found a
# real flaw in that design: a content hash alone is stable FOREVER, not
# just across the retries of one attempt. Re-uploading the exact same
# video file later (a legitimate, deliberate re-analysis -- case D in
# the BL-008C.24 audit) would reuse the SAME key as the original,
# already-charged attempt, and the migration's UNIQUE(user_id,
# idempotency_key) constraint would make the RPC a silent no-op --
# a free, uncounted second analysis of the same clip, forever.
#
# Fix: scope the key to a per-"pending attempt" nonce, keyed by content
# hash in session_state, that is DELETED the moment a charge actually
# succeeds (see the call site below) -- so a retry of a STILL-FAILING
# attempt reuses the same nonce (retry-safe, no double charge), but a
# brand-new "Analyze swing" click for the SAME content after an earlier
# SUCCESS mints a fresh nonce (correctly charged again, not free).
import hashlib as _hashlib
_upload_content_hash = _hashlib.sha256(bytes(_upload_bytes)).hexdigest()
_upload_idempotency_key = (
    f"{(user or {}).get('slug', 'anon')}:{_upload_content_hash}:"
    f"{charge_nonce_for(st.session_state, _upload_content_hash)}"
)

# Keep the preview compact so the Analyze button stays in view. A full-width
# video pushed the button far below the fold, forcing a scroll to analyze.
_pv_col, _pv_rest = st.columns([1, 1.5])
with _pv_col:
    st.video(str(video_path))

_analyze_col, _manual_col = st.columns([1, 1])
with _analyze_col:
    _analyze_clicked = st.button("Analyze swing", type="primary", width="stretch",
                                 key="analyze_swing_btn")
with _manual_col:
    # BL-008C.19: fallback path for when automatic analysis doesn't find
    # the swing reliably. Reuses this same already-saved video_path --
    # no second upload. Does not touch the automatic pipeline below.
    if st.button("Select & confirm manually (experimental)", width="stretch",
                 key="manual_review_btn", help="Pick the exact swing interval yourself if automatic "
                                                "analysis seems off."):
        # BL-008C.36: a new clip starts a clean review -- no frames, checks,
        # results or selections from a previous clip can carry over.
        for _k in [k for k in list(st.session_state.keys()) if str(k).startswith("manual_review_")]:
            st.session_state.pop(_k, None)
        st.session_state["manual_review_video_path"] = str(video_path)
        st.session_state["page"] = "manual_review"
        st.rerun()

if not _analyze_clicked:
    st.stop()


# ---------- ENTITLEMENT GATE: free-swing wall ----------
# Free users get FREE_SWING_LIMIT lifetime analyses. Any Pro plan is
# unlimited. We check this AFTER the button click but BEFORE pose
# detection runs so we don't waste 30–60s of compute on a blocked user.
#
# IMPORTANT: force_refresh=True bypasses the session_state cache. If a
# user's tier changed mid-session (beta-code redemption, Stripe checkout
# completion, manual comp), the cache could still hold the old "Free"
# snapshot — causing this exact upload to be treated as Free even though
# the DB row already says Pro. That stale read also bypasses pose
# extraction at line ~5160, leaving the user side of the side-by-side
# comparison empty. Always re-read fresh on a swing upload — DB hit is
# cheap and entitlement correctness matters far more than the ~50ms.
_plan_snapshot = load_my_plan(force_refresh=True)
# BL-008C.20 Deliverable 5: narrowly scoped pilot allowance -- a no-op
# for every account except the small, explicit, committed allowlist in
# entitlements.PILOT_ALLOWLIST. See that module for how to approve an
# account for pilot testing.
_swing_check = can_analyze_swing_for_account(_plan_snapshot, (user or {}).get("email"))
if not _swing_check.allowed:
    st.markdown(f"""
<div style="
    margin: 1.25rem 0 0.5rem 0;
    padding: 1.4rem 1.5rem;
    border-radius: 14px;
    border: 1px solid rgba(255, 59, 48, 0.45);
    background: linear-gradient(135deg, rgba(255,59,48,0.10), rgba(255,59,48,0.02));
">
  <div style="
      display: inline-block;
      padding: 0.2rem 0.65rem;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #FF3B30;
      background: rgba(255,59,48,0.14);
      border: 1px solid rgba(255,59,48,0.35);
  ">Free trial limit reached</div>
  <div style="font-size: 1.25rem; font-weight: 800; color: #fafafa; margin-top: 0.55rem;">
    You've used all {FREE_SWING_LIMIT} of your free swing analyses.
  </div>
  <div style="font-size: 1.55rem; font-weight: 900; color:#E8C170;
              letter-spacing: -0.01em; margin-top: 0.35rem;
              font-feature-settings: 'tnum';">
    Solo Pro · <span style="color:#fafafa;">$14.99/mo</span>
    <span style="font-size: 0.9rem; font-weight: 600; color:#d4d4d4;
                 margin-left: 0.4rem; letter-spacing: 0;">
      or $99/yr (save 45%)
    </span>
  </div>
  <div style="color: #d4d4d4; line-height: 1.55; margin-top: 0.55rem;">
    Unlimited swings, personalized drill plans, swing video saving, the full
    Development Tracker, PDF reports, the complete MLB comp library.
    <span style="color:#a3a3a3;">Cancel anytime.</span>
  </div>
  <div style="color: #a3a3a3; font-size: 0.86rem; margin-top: 0.55rem;">
    Got a beta code? Redeem it from <em>Account Settings → Subscription</em> to
    unlock the full app for 30 days.
  </div>
</div>
""", unsafe_allow_html=True)
    _up_l, _up_r = st.columns([1, 1])
    if _up_l.button("See plans & upgrade", type="primary",
                    width="stretch", key="paywall_swing_upgrade"):
        st.session_state["page"] = "pricing"
        st.rerun()
    if _up_r.button("Go to Account Settings", width="stretch",
                    key="paywall_swing_settings"):
        st.session_state["page"] = "player_settings"
        st.rerun()
    st.stop()

# Friendly remaining-count notice for Free users mid-trial.
if _swing_check.remaining is not None and not is_pro(_plan_snapshot):
    _left = _swing_check.remaining
    _word = "analysis" if _left == 1 else "analyses"
    st.info(
        f"You have **{_left} free swing {_word} left** on the Free plan. "
        f"After that, upgrade to Pro for unlimited swings.",
        icon="ℹ️",
    )


# ---------- RUN POSE DETECTION ----------
with st.spinner("Tracking pose and detecting swing phases (~30–60 seconds)..."):
    _detect_cmd = [PY, "detect_phases.py", str(video_path),
                   HAND_MAP[hand_override]]
    _player_age = age_from_birth_year((user or {}).get("birth_year"))
    if _player_age is not None:
        _detect_cmd += ["--age", str(_player_age)]
    rc, out, err = run_subprocess(_detect_cmd, cwd=PROJECT_ROOT)

if rc != 0:
    # BL-003Q: a failure before any frame is processed (e.g. the pose model
    # failing to initialize) is a technical problem on our end, not
    # something re-filming fixes — don't blame the user's video for it.
    # The RuntimeError("No frames had a detected pose.") path, which only
    # fires after frames were actually processed, is the genuine filming
    # case and keeps the original guidance below.
    if classify_detect_phases_failure(err) == "model_init":
        st.error("Our swing analyzer couldn't start up for this attempt — "
                  "that's a technical issue on our end, not your video.")
        st.markdown(
            "Your free analysis was **not** used. Please try again in a "
            "moment; if it keeps happening, let us know."
        )
    else:
        st.error("We couldn't find a clear view of the hitter in that clip.")
        st.markdown(
            "This is almost always a filming issue, not your swing. For a clean read:\n\n"
            "- **Film from the side**, perpendicular to the pitcher\n"
            "- Keep the **whole body in frame** with good lighting\n"
            "- A **3–6 second** clip of the swing is plenty\n\n"
            "Your free analysis was **not** used — re-film and upload again."
        )
    with st.expander("Technical details (for support)"):
        st.code(err or out, language="text")
    st.stop()

out_base = video_path.stem
fingerprint_path = PROJECT_ROOT / f"{out_base}_fingerprint.json"
phase_chart_path = PROJECT_ROOT / f"{out_base}_phases.png"

if not fingerprint_path.is_file():
    st.error("Something went wrong processing that clip — your free analysis "
             "was not used. Please try uploading again.")
    with st.expander("Technical details (for support)"):
        st.code(out, language="text")
    st.stop()


# ---------- RUN ANALYSIS ----------
# MLB comp lock: once a player's first swing locks them to a reference
# (e.g. Trout), every future "Auto-pick best match" swing keeps using
# that same reference so the player builds toward one swing model
# instead of remodeling weekly against whichever comp the picker
# happened to favor. Manual sidebar picks bypass the lock for one-off
# comparisons (and do NOT change the lock).
reference_arg = None
manual_override = ref_choice != "Auto-pick best match"
if manual_override:
    picked_idx = ref_options.index(ref_choice) - 1
    reference_arg = refs[picked_idx]["slug"]
else:
    # Auto-pick path — honor the saved lock if one exists.
    _locked_slug = (user or {}).get("locked_mlb_slug") or None
    if _locked_slug:
        reference_arg = _locked_slug

# Thread the player's training-goal (set on Player Settings) into the
# analyzer so the drill plan can weight categories that move that goal.
# Gap-derived weights still dominate — the goal just breaks ties between
# roughly-equal categories. Soft-falls back to None if no goal is set.
_pref_goal = (user or {}).get("primary_goal") or None

try:
    result = analyze(str(fingerprint_path), reference_arg,
                     preferred_goal=_pref_goal)
except Exception as e:
    st.error(f"Analysis failed: {e}")
    st.stop()

# Activation event — running a swing is the core "aha". Best-effort, no-op
# until PostHog is configured.
try:
    import analytics
    analytics.track(
        "swing_analyzed",
        (user or {}).get("user_id") or (user or {}).get("id"),
        edge_score=(result.get("edge_score") if isinstance(result, dict) else None),
    )
except Exception:
    pass

# After analysis: if the player has no lock yet AND we just auto-picked
# their reference, persist that slug as the lock so future swings stay
# anchored to the same hitter. Skip when the user manually overrode
# from the sidebar — manual picks shouldn't change the lock.
if not manual_override and not (user or {}).get("locked_mlb_slug"):
    _picked = (result.get("reference") or {}).get("slug")
    if _picked:
        try:
            from player_storage import update_profile as _save_lock
            _updated = _save_lock(user["slug"], locked_mlb_slug=_picked)
            if _updated:
                st.session_state.user = _updated
                user = _updated
        except Exception:
            # Fail-soft — if the lock write fails the analysis still
            # renders. Next swing will try again.
            pass

# ---------- EXTRACT PER-FRAME POSE (Pro users only) ----------
# Captures the 33-keypoint arrays that drive the v2 swing-overlay feature
# (side-by-side skeleton vs MLB ghost). Pro-only — gated here so we don't
# burn ~15-20s of MediaPipe CPU on Free swings that won't render the
# overlay. Free users still get the full analysis, just no overlay.
#
# Fail-soft: if extraction errors for any reason, the swing still saves
# without pose data and the report falls back to "overlay unavailable".
pose_payload = None
key_frames = None
if is_pro(_plan_snapshot):
    try:
        from pose_extract import (
            extract_pose_frames, build_pose_meta, extract_key_frames,
        )
        with st.spinner("Capturing your pose data for swing comparison..."):
            _pose_data = extract_pose_frames(video_path)
        _phases_t = result.get("phases_t", {}) or {}
        pose_payload = {
            "pose_frames": _pose_data["frames"],
            "pose_meta":   build_pose_meta(_pose_data),
            # phases_t carried alongside the pose frames so the
            # side-by-side comparison viewer can sync at foot plant
            # even on older deployments where the swings table doesn't
            # yet have the phases_t JSONB column. Pulled from the
            # analyzer result via the surfaced field added for v2.
            "phases_t":    _phases_t,
        }
        # Stills at foot plant / contact / finish so the report can draw the
        # pose overlay on the real swing frames. Reuses the just-decoded clip;
        # fail-soft so a missing frame only drops that one report panel.
        try:
            key_frames = extract_key_frames(video_path, _phases_t)
        except Exception:
            key_frames = None
    except Exception:
        # Silent fail — pose is a nice-to-have on top of the analysis,
        # not a hard requirement. Surface only if we hit it repeatedly.
        pose_payload = None
        key_frames = None

# ---------- SAVE SWING TO LOGGED-IN PLAYER'S HISTORY ----------
saved_record = save_swing_record(
    player=user,
    upload_name=upload.name,
    result=result,
    phase_chart_path=phase_chart_path,
    video_path=str(video_path),
    pose_payload=pose_payload,
    key_frames=key_frames,
)

# ---------- USAGE COUNTER: bump free-swing tally for Free users ----------
# Tamper-resistant: counter lives in a separate table, incremented via
# SECURITY DEFINER RPC, so deleting the swings row can't restore quota.
# BL-008C.22: this is the ONE shared post-success call used by both this
# automatic pipeline AND the manual-review pathway (see
# subscription_storage.record_successful_swing_analysis) -- not a second
# counter system, the same RPC, called from the one place both pathways
# now share.
#
# BL-008C.23: a failed charge now BLOCKS the report below instead of
# silently delivering it anyway -- the prior soft-fail-and-continue
# behavior let a Free user get an uncounted analysis for free whenever
# the RPC hiccuped, which is exactly the gap this milestone closes.
# _upload_idempotency_key (content hash, stable across reruns of the
# same selected file) means a user who sees this error and clicks
# "Analyze swing" again for the SAME upload cannot be double-charged
# even if the first attempt's success response was merely lost in
# transit. The swing record itself is already saved above regardless
# (it's real data the user produced); only the REPORT DISPLAY is gated
# on the charge succeeding.
#
# BL-008C.24: on SUCCESS, the pending-attempt nonce (keyed by content
# hash, minted above) is deleted from session_state. This is what makes
# a later, deliberate re-upload of the exact same video bytes correctly
# chargeable again -- without this pop, the nonce would persist for the
# life of the session and a second legitimate analysis of the same clip
# would silently reuse the first (already-spent) idempotency key,
# becoming free forever. On FAILURE, the nonce is deliberately left in
# place so a retry of THIS SAME attempt stays safe from double-charging.
if not record_successful_swing_analysis(_plan_snapshot, idempotency_key=_upload_idempotency_key):
    st.error(
        "Your swing was analyzed, but we couldn't record it against your account "
        "(a temporary connection issue on our end, not something you did wrong). "
        "Please click “Analyze swing” again to retry -- you will not be "
        "double-charged for this attempt."
    )
    st.stop()
clear_charge_nonce(st.session_state, _upload_content_hash)

swing_history = load_swing_history(user["slug"])


# ============================================================
# ---------- BIND LIVE RESULT LOCALS ----------
# ============================================================
st.divider()

score        = result["score"]
band_color   = result["score_band_color"]
band_label   = result["score_band_label"]
hex_color, emoji = score_color(band_color)
ref          = result["reference"]
slow_mo      = result["slow_mo"]
cam          = result["camera_view"]


# ============================================================
# ---------- CAMERA ANGLE WARNING (banner above report) ----------
# ============================================================
if cam["rotation_view_sensitive"]:
    if cam["rotation_flag_reason"] == "mixed_method":
        st.warning(
            f"**Camera angle confidence is low.** Your clip may be slightly off-profile "
            f"(hip/torso ratio {cam['player_ratio']:.2f}) while {ref['name']}'s "
            f"is profile ({cam['ref_ratio']:.2f}). Rotation numbers aren't on the "
            f"same scale, so they're excluded from the score. **Re-film from "
            f"the side, perpendicular to the pitcher** for the cleanest read on "
            f"hip & shoulder rotation.",
            icon="⚠️",
        )
    elif cam["rotation_flag_reason"] == "off_profile":
        st.warning(
            f"**Camera angle confidence is low.** Your clip looks filmed too "
            f"front-on (hip/torso ratio {cam['player_ratio']:.2f}); 2D rotation "
            f"isn't reliable from this angle, so rotation is excluded from the "
            f"score. **Re-film from the side, perpendicular to the pitcher** for "
            f"a clean read on hip & shoulder rotation.",
            icon="⚠️",
        )
    else:
        st.warning(
            f"**Camera angle confidence is medium.** Both clips use 2D rotation measurement, "
            f"but viewpoints differ by Δ={cam['view_diff']:.2f}. Rotation gaps "
            f"may partly reflect viewpoint, not pure swing differences.",
            icon="⚠️",
        )


# ============================================================
# ---------- FULL SWING REPORT (shared premium renderer) ----------
# ============================================================
# Build a record-shaped dict from `result` so the renderer can use
# the same data path it uses for saved reports. Adds a couple of
# extras the renderer reads (date, swing_number, swing_duration_ms).
_live_record = dict(result)
_live_record["date"] = "Just analyzed"
_live_record["swing_number"] = len(swing_history) if swing_history else 1
_live_record["swing_duration_ms"] = slow_mo.get("player_corrected_swing_ms")

# Feed the pose overlay straight from memory so the post-analyze report shows
# the annotated key-moment frames immediately, with no storage round-trip.
# (Saved swings reopened from Sessions lazy-load the same data from storage.)
if pose_payload:
    _live_record["_pose_data"] = pose_payload
if key_frames:
    import base64 as _b64_kf
    _live_record["_pose_frame_imgs"] = {
        _k: "data:image/jpeg;base64," + _b64_kf.b64encode(_v).decode()
        for _k, _v in key_frames.items() if _v
    }

# Phase audit follow-up: unify the post-analyze swing report onto the
# same editorial design used when re-opening from Sessions. Previously
# the post-analyze path rendered the OLD bld2-* design (Inter + iOS red)
# while Sessions→Open Report used the NEW srd-* design (Instrument Serif
# + bone/gold). Same conceptual page, two different design languages —
# trust-eroding. This call now goes through swing_report_dashboard_preview
# (the renderer Sessions→Open Report already uses) so every user sees
# the new design at the most important moment: right after analysis.
#
# Falls back to the legacy render_swing_report if the new renderer
# raises — never block the user from seeing their analysis.
try:
    from swing_report_dashboard_preview import (
        render_swing_report_dashboard_preview,
    )
    render_swing_report_dashboard_preview(
        _live_record,
        swing_history or [],
        is_sample=False,
        is_preview=False,
    )
except Exception as _post_analyze_render_err:
    # Log + fall back to the legacy renderer so the user still gets a
    # report even if the new renderer has a regression.
    import traceback
    print(
        f"⚠  post-analyze render via swing_report_dashboard_preview "
        f"failed: {_post_analyze_render_err!r} — falling back to legacy."
    )
    traceback.print_exc()
    render_swing_report(
        _live_record,
        history=swing_history,
        phase_chart_path=str(phase_chart_path) if phase_chart_path and phase_chart_path.is_file() else None,
    )


# ============================================================
# ---------- SAVED CONFIRMATION ----------
# ============================================================
# The first report is the highest-emotion moment; make it explicit that the
# swing is saved and reachable (the Sessions report page has the PDF/Print
# export bar), so it doesn't feel like a throwaway one-off.
st.success(
    "✓ Saved to your Sessions — reopen this report (and download a PDF for "
    "your coach) anytime."
)
if st.button("Go to Sessions →", key="post_analyze_to_sessions"):
    st.session_state["page"] = "saved_reports"
    st.rerun()


# ============================================================
# ---------- POST-ANALYSIS UPGRADE NUDGE (Free tier only) ----------
# ============================================================
# Conversion-funnel audit quick-win: the old report renderer had a
# text-only red strip with no CTA after every analysis. Now that the
# post-analyze path renders the new editorial design, surface a real
# clickable nudge with price + value prop directly under the report.
# Only fires for Free users — Pro users see nothing (we already have
# their money).
if not is_pro(_plan_snapshot):
    st.markdown(
        """
<div style="
    margin: 1.8rem 0 0.4rem 0;
    padding: 1.6rem 1.8rem;
    border-radius: 16px;
    border: 1px solid rgba(232,193,112,0.35);
    background:
      radial-gradient(120% 100% at 0% 0%, rgba(232,193,112,0.10), transparent 55%),
      linear-gradient(180deg, rgba(255,255,255,0.018), rgba(255,255,255,0.004));
">
  <div style="
      font-family: 'Geist Mono', 'JetBrains Mono', monospace;
      font-size: 0.72rem; font-weight: 600;
      letter-spacing: 0.18em; text-transform: uppercase;
      color: #E8C170;
      margin-bottom: 0.4rem;
  ">↗  Loved your analysis?</div>
  <div style="
      font-family: 'Instrument Serif', 'Fraunces', Georgia, serif;
      font-style: italic;
      font-size: 1.75rem;
      line-height: 1.15;
      color: #F4EFE6;
      letter-spacing: -0.01em;
  ">Unlock the full BarrelLabs experience.</div>
  <div style="
      color: #d4d4d4;
      line-height: 1.55;
      margin-top: 0.5rem;
      max-width: 60ch;
  ">
    Save this video, compare it side-by-side with your next swing,
    download a PDF for your coach, and unlock the full MLB reference
    library and personalized drill plans.
    <strong style="color:#E8C170;">Solo Pro — $14.99/mo</strong>
    <span style="color:#a3a3a3;">or $99/yr (save 45%) · Cancel anytime.</span>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
    _upgrade_col, _spacer = st.columns([1.6, 4])
    with _upgrade_col:
        if st.button(
            "↗  Upgrade to Solo Pro",
            type="primary",
            width="stretch",
            key="post_analysis_upgrade_cta",
        ):
            st.session_state["page"] = "pricing"
            st.rerun()


# ============================================================
# ---------- PLAYER DEVELOPMENT OVER TIME ----------
# ============================================================
if swing_history:
    st.divider()
    st.subheader("📈 Player Development Over Time")
    st.caption("Every analyzed swing is saved so you can track measurable improvement over time.")

    latest_score = swing_history[-1].get("score", 0)
    first_score = swing_history[0].get("score", 0)
    score_change = latest_score - first_score if len(swing_history) > 1 else 0

    top_cols = st.columns(4)
    stat_card(top_cols[0], "Player", user["name"])
    stat_card(top_cols[1], "Swings Analyzed", str(len(swing_history)))
    stat_card(
        top_cols[2],
        "Current Score",
        f"{latest_score}/100",
        delta=f"{score_change:+}" if score_change != 0 else None,
    )
    stat_card(
        top_cols[3],
        "Current MLB Comp",
        swing_history[-1].get("reference_name", "N/A"),
    )

    if len(swing_history) >= 2:
        import pandas as pd

        chart_data = pd.DataFrame([
            {
                "Swing": i + 1,
                "Score": record.get("score", 0),
            }
            for i, record in enumerate(swing_history)
        ])

        st.markdown("#### Similarity Score Trend")
        st.line_chart(chart_data, x="Swing", y="Score", height=260)

    render_swing_history_cards(swing_history, limit=6, title="Recent Swing History")
    render_swing_progress_compare(swing_history)

# Camera view diagnostic
with st.expander("🎥  Camera view diagnostic"):
    st.markdown(
        f"**Your clip:** hip-width / torso ratio = `{cam['player_ratio']:.2f}` "
        f"(measurement method: `{cam['player_method']}`)"
    )
    st.markdown(
        f"**{ref['name']}:** hip-width / torso ratio = `{cam['ref_ratio']:.2f}` "
        f"(measurement method: `{cam['ref_method']}`)"
    )
    st.markdown(
        "*The ratio tells the app how 'side-on' a clip is. ~0.3–0.5 = profile (side) view; "
        "0.6+ = three-quarter view. When ratios are similar, rotation comparison is direct; "
        "when they differ a lot, rotation metrics get flagged.*"
    )

with st.expander("📋  Why this reference was picked"):
    if ref["source"] == "auto":
        st.markdown(f"**Auto-picked.** {ref['auto_reason']}")
    elif ref["source"] == "library":
        st.markdown(f"**Manually selected** via the sidebar.")
    else:
        st.markdown(f"**Direct file reference:** `{ref['override_arg']}`")

    if ref["also_in_library"]:
        st.markdown(
            f"**Also in the library:** {', '.join(ref['also_in_library'])}. "
            f"Use the sidebar 'Compare to' selector to switch."
        )

if result["other_observations"]:
    with st.expander("⚠️  Other observations (couldn't be reliably scored)"):
        st.caption(
            "Camera-angle mismatch means these rotation gaps could be real OR "
            "could be the viewpoint. Re-film from the same side as the reference "
            "for a direct comparison."
        )
        for obs in result["other_observations"]:
            st.markdown(f"- **{obs['label']}**  ·  you: `{obs['player_str']}`  ·  ref: `{obs['ref_str']}`")


if st.session_state.get("page") == "development_tracker":
    render_development_tracker()
    st.stop()


if st.session_state.get("page") == "historical_charts":
    render_historical_charts()
    st.stop()
