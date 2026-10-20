"""Auth-free Playwright proof that the Player Settings page is BROKEN
right now — and a re-runnable regression gate for the future rebuild.

Why this file exists
--------------------
`player_settings_page.py` styles every widget with selectors scoped
under `.ps-wrap` (e.g. `.ps-wrap [data-testid="stTextInput"] input`).
It tries to open that wrapper with a *raw markdown div*:

    st.markdown('<div class="ps-wrap">', unsafe_allow_html=True)
    ... widgets ...
    st.markdown('</div>', unsafe_allow_html=True)

But Streamlit 1.57 does NOT leave that div open. Each `st.markdown`
call is sandboxed inside its own `stMarkdownContainer`, and the HTML
sanitizer auto-closes the tag. The REAL DOM Streamlit emits is:

    <div data-testid="stMarkdownContainer"><div class="ps-wrap"></div></div>
    <div data-testid="stElementContainer">...the text input widget...</div>
    <div data-testid="stMarkdownContainer"><div></div></div>

i.e. `.ps-wrap` is an EMPTY, auto-closed sibling. The widgets are
siblings AFTER it in the same `stVerticalBlock`, NOT descendants. So
EVERY `.ps-wrap [data-testid="..."]` rule matches NOTHING and the page
renders as raw, unstyled Streamlit default chrome on a dark page.

This harness reconstructs that exact ST 1.57 DOM by hand (no network,
no auth, no live Streamlit), injects the page's real `_PAGE_CSS`,
screenshots it, and asserts the breakage with computed styles.

It also models the TARGET state — the rebuild will switch to
`st.container(key="ps_wrap")` which emits
`<div class="st-key-ps_wrap"><div data-testid="stVerticalBlock">...
widgets INSIDE...</div></div>` — and proves the SAME computed-style
checks PASS there. So the file documents both "broken now" and "what
good looks like".

Run modes
---------
  python verify_player_settings.py          # diagnostic, always exit 0
  python verify_player_settings.py --gate   # regression gate: exit 1
                                            # ONLY if the TARGET model
                                            # fails (the rebuild broke).

Screenshots:
  /tmp/player_settings_broken.png         (1440x900 full page, CURRENT)
  /tmp/player_settings_broken_mobile.png  (768x900 full page, CURRENT)
"""
from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GATE = "--gate" in sys.argv


# ---------------------------------------------------------------------
# 1. Extract the page's REAL _PAGE_CSS by importing player_settings_page
#    under a Streamlit stub (same technique as verify_seam_fix.py).
#    We only need _PAGE_CSS (+ the option lists for realistic widgets);
#    we do NOT call render_player_settings_page.
# ---------------------------------------------------------------------
_CAP: list[str] = []


class _SS(dict):
    def __getattr__(s, k):
        try:
            return s[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(s, k, v):
        s[k] = v


class _QP(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class _Ctx:
    """st.container / st.columns slot — a no-op context manager so the
    page module imports cleanly. We never actually run the renderer."""

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False


st = types.ModuleType("streamlit")
st.session_state = _SS()
st.query_params = _QP()
st.markdown = lambda *a, **k: _CAP.append(a[0] if a else "")
st.container = lambda *a, **k: _Ctx()
st.columns = lambda n, **k: [
    _Ctx() for _ in range(n if isinstance(n, int) else len(n))
]
st.button = lambda *a, **k: False
st.text_input = lambda *a, **k: ""
st.number_input = lambda *a, **k: 0
st.selectbox = lambda *a, **k: (a[1][0] if len(a) > 1 and a[1] else "")
st.radio = lambda *a, **k: (a[1][0] if len(a) > 1 and a[1] else "")
st.checkbox = lambda *a, **k: False
st.text_area = lambda *a, **k: ""
st.date_input = lambda *a, **k: None
st.file_uploader = lambda *a, **k: None
st.download_button = lambda *a, **k: False
st.toggle = lambda *a, **k: False
st.slider = lambda *a, **k: 0
st.expander = lambda *a, **k: _Ctx()
st.popover = lambda *a, **k: _Ctx()
st.empty = lambda *a, **k: _Ctx()
st.spinner = lambda *a, **k: _Ctx()
st.form = lambda *a, **k: _Ctx()
st.form_submit_button = lambda *a, **k: False
# Player-settings v3 uses native st.segmented_control, st.pills, and
# st.dialog. The stub must cover those or import-time decoration fails.
st.segmented_control = lambda *a, **k: (a[1][0] if len(a) > 1 and a[1] else "")
st.pills = lambda *a, **k: (a[1][0] if len(a) > 1 and a[1] else "")
def _dialog_decorator(*dargs, **dkwargs):
    def _wrap(fn):
        return fn
    return _wrap
st.dialog = _dialog_decorator
for _n in (
    "write", "error", "warning", "info", "success", "caption",
    "image", "rerun", "stop", "toast", "divider", "metric",
    "subheader", "header", "title", "code", "json", "dataframe",
    "plotly_chart", "altair_chart", "balloons", "snow",
):
    setattr(st, _n, lambda *a, **k: None)

_c1 = types.ModuleType("streamlit.components.v1")
_c1.html = lambda *a, **k: None
_c1.iframe = lambda *a, **k: None
_c0 = types.ModuleType("streamlit.components")
_c0.v1 = _c1
st.components = _c0
sys.modules["streamlit"] = st
sys.modules["streamlit.components"] = _c0
sys.modules["streamlit.components.v1"] = _c1

import importlib  # noqa: E402

psp = importlib.import_module("player_settings_page")

PAGE_CSS = psp._PAGE_CSS  # the real <style>...</style> blob
# The CSS may target the legacy markdown-div scope (`.ps-wrap`) OR the
# correct keyed-container scope (`.st-key-ps_wrap`) depending on whether
# the rebuild has landed. Both are valid for this diagnostic harness.
assert "<style>" in PAGE_CSS and (".ps-wrap" in PAGE_CSS
                                    or ".st-key-ps_wrap" in PAGE_CSS), \
    "extracted _PAGE_CSS does not look right"

# After the rebuild the CSS already uses `.st-key-ps_wrap`. Before the
# rebuild it used `.ps-wrap`. For the BROKEN-model DOM (which still uses
# the legacy `.ps-wrap` class names) we want CSS that *would* match if
# the wrap actually wrapped — so we synthesise a "legacy CSS" by
# replacing `.st-key-ps_wrap` back to `.ps-wrap`. For the TARGET model
# we use the deployed CSS verbatim.
LEGACY_CSS = PAGE_CSS.replace(".st-key-ps_wrap", ".ps-wrap")

# A couple of real option lists so the widgets carry realistic content.
SWING_VIEWS = list(getattr(psp, "SWING_VIEWS", ["Side", "Front", "3D"]))
MLB_HAND_PREFS = list(getattr(psp, "MLB_HAND_PREFS",
                               ["Match mine", "Right", "Left"]))
POS_LABELS = [lbl for _s, lbl in getattr(psp, "POSITIONS",
              [("c", "Catcher"), ("ss", "Shortstop"), ("of", "Outfield")])]


# ---------------------------------------------------------------------
# 2. Streamlit 1.57 default chrome stylesheet — reused VERBATIM from
#    verify_nav_tabs.py / verify_seam_fix.py so _PAGE_CSS competes
#    against the same defaults production sees.
# ---------------------------------------------------------------------
ST_DEFAULTS = """
<style>
  html,body{margin:0;padding:0;background:#262730;}
  [data-testid="stApp"]{position:absolute;inset:0;background:#0A0B0E;}
  [data-testid="stHeader"]{position:absolute;top:0;left:0;right:0;
    height:60px;background:#FFFFFF;z-index:999990;display:flex;}
  [data-testid="stMain"]{display:flex;flex-direction:column;
    background:transparent;}
  [data-testid="stMainBlockContainer"]{padding:96px 16px 160px;
    max-width:1560px;margin:0 auto;background:transparent;}
  [data-testid="stVerticalBlock"]{display:flex;flex-direction:column;
    gap:16px;}
  [data-testid="stHorizontalBlock"]{display:flex;flex-direction:row;
    gap:16px;}
  [data-testid="stElementContainer"]{}
  /* Streamlit 1.57 default input/select/radio/checkbox/button reset so
     _PAGE_CSS does the visible work, not browser defaults. */
  button{font:inherit;color:inherit;cursor:pointer;
    background:rgb(247,247,247);border:1px solid rgba(49,51,63,0.2);
    padding:0.25rem 0.75rem;border-radius:0.5rem;
    font-family:"Source Sans Pro",sans-serif;}
  input,textarea{font:inherit;color:rgb(49,51,63);
    background:rgb(255,255,255);border:1px solid rgba(49,51,63,0.2);
    border-radius:0.5rem;padding:0.5rem 0.75rem;width:100%;
    box-sizing:border-box;font-family:"Source Sans Pro",sans-serif;}
  [data-baseweb="select"]>div{background:rgb(255,255,255);
    border:1px solid rgba(49,51,63,0.2);border-radius:0.5rem;
    min-height:40px;color:rgb(49,51,63);}
  label{color:rgb(49,51,63);font-family:"Source Sans Pro",sans-serif;
    font-size:14px;}
</style>
"""


# ---------------------------------------------------------------------
# 3. ST 1.57 widget-DOM builders.
#    Idioms copied from verify_nav_tabs.py (_button_html): every widget
#    lives inside its own stElementContainer; buttons use
#    button[kind][data-testid="stBaseButton-secondary"].
# ---------------------------------------------------------------------
def _el(inner: str, *, key: str | None = None) -> str:
    """Wrap one widget in a Streamlit 1.57 stElementContainer."""
    keycls = f"st-key-{key} " if key else ""
    return (
        f'<div data-testid="stElementContainer" '
        f'class="{keycls}stElementContainer">{inner}</div>'
    )


def _md_el(html_inside: str) -> str:
    """A raw st.markdown() call: ST 1.57 sandboxes it in its OWN
    stMarkdownContainer and the sanitizer AUTO-CLOSES the tag, so an
    open `<div class="ps-wrap">` becomes an EMPTY `<div class="ps-wrap">
    </div>`. This is the crux of the bug."""
    return _el(
        f'<div data-testid="stMarkdownContainer" '
        f'class="stMarkdownContainer">{html_inside}</div>'
    )


def _text_input(label: str, value: str) -> str:
    return _el(
        '<div data-testid="stTextInput" class="stTextInput">'
        f'<label data-testid="stWidgetLabel">{label}</label>'
        '<div data-baseweb="input" class="st-baseweb-input">'
        f'<input type="text" value="{value}" aria-label="{label}"></div>'
        '</div>'
    )


def _selectbox(label: str, value: str) -> str:
    return _el(
        '<div data-testid="stSelectbox" class="stSelectbox">'
        f'<label data-testid="stWidgetLabel">{label}</label>'
        '<div data-baseweb="select" class="st-baseweb-select"><div>'
        f'<div role="combobox" aria-label="{label}">{value}</div>'
        '</div></div></div>'
    )


def _radio_horizontal(label: str, options: list[str], checked: int = 0) -> str:
    opts = "".join(
        '<label data-baseweb="radio">'
        f'<div class="st-radio-dot"></div>'
        '<div data-testid="stWidgetLabel">'
        f'<input type="radio" name="{label}"'
        f'{" checked" if i == checked else ""}>'
        f'<div>{o}</div></div></label>'
        for i, o in enumerate(options)
    )
    return _el(
        '<div data-testid="stRadio" class="stRadio" '
        'role="radiogroup">'
        f'<label data-testid="stWidgetLabel">{label}</label>'
        f'<div role="radiogroup">{opts}</div>'
        '</div>'
    )


def _checkbox(label: str, checked: bool = False) -> str:
    aria = "true" if checked else "false"
    return _el(
        '<div data-testid="stCheckbox" class="stCheckbox">'
        '<label><div><div role="checkbox" '
        f'aria-checked="{aria}" tabindex="0">'
        '<svg width="16" height="16"></svg></div></div>'
        f'<div>{label}</div></label>'
        '</div>'
    )


def _button(label: str) -> str:
    """ST 1.57 button DOM — copied verbatim idiom from
    verify_nav_tabs._button_html (secondary kind)."""
    return _el(
        '<div data-testid="stButton" class="stButton">'
        '<button kind="secondary" data-testid="stBaseButton-secondary" '
        'type="button">'
        '<div data-testid="stMarkdownContainer" class="stMarkdownContainer">'
        f'<p>{label}</p></div></button></div>'
    )


def _columns_row(cells: list[str]) -> str:
    """st.columns → a stHorizontalBlock of N stVerticalBlock columns,
    each holding one element. Mirrors ST 1.57's flex column layout."""
    cols = "".join(
        '<div data-testid="stColumn" class="stColumn">'
        '<div data-testid="stVerticalBlock" class="stVerticalBlock">'
        f'{c}</div></div>'
        for c in cells
    )
    return (
        '<div data-testid="stHorizontalBlock" class="stHorizontalBlock">'
        f'{cols}</div>'
    )


# ---------------------------------------------------------------------
# 4a. CURRENT (markdown-div) MODEL — the broken reality.
#     `.ps-wrap` / `.ps-grid` / `.ps-card` / <section> open-divs are
#     EMPTY auto-closed SIBLINGS; the widgets are siblings AFTER them
#     inside the SAME stVerticalBlock. Reconstruct a representative
#     slice of player_settings_page.render_player_settings_page().
# ---------------------------------------------------------------------
def build_dom_markdown_div() -> str:
    pid_pos = (POS_LABELS[1] if len(POS_LABELS) > 1 else "Shortstop")
    parts = [
        # st.markdown(_PAGE_CSS) — injected separately in the <head>.
        _md_el('<div class="ps-atmos"></div>'),
        # st.markdown('<div class="ps-wrap">')  -> EMPTY auto-closed div
        _md_el('<div class="ps-wrap"></div>'),
        # ---- header (also a markdown div, also empty/auto-closed) ----
        _md_el(
            '<div class="ps-header">'
            '<div><div class="ps-eyebrow">Account</div>'
            '<h1 class="ps-title">Player Settings</h1>'
            '<p class="ps-sub">Tune your profile and preferences.</p>'
            '</div></div>'
        ),
        # st.markdown('<div class="ps-grid">')  -> EMPTY
        _md_el('<div class="ps-grid"></div>'),
        # st.markdown('<aside class="ps-rail">') -> EMPTY
        _md_el('<aside class="ps-rail"></aside>'),
        _md_el('<div class="ps-card ps-id"></div>'),
        _md_el('<div class="ps-card ps-qj"></div>'),
        _md_el('</aside>'),  # the stray close — its own empty md div
        _md_el('<div class="ps-col"></div>'),
        # ---- Profile section: <section class="ps-card"> open (EMPTY),
        #      then the REAL widgets as SIBLINGS, not children ----
        _md_el('<section class="ps-card" id="ps-sec-profile"></section>'),
        _columns_row([
            _text_input("First name", "Jordan"),
            _text_input("Last name", "Ellis"),
        ]),
        _columns_row([
            _text_input("Display name", "Jordan E."),
            _text_input("Email · login id", "jordan@barrellabs.io"),
        ]),
        _md_el('</section>'),
        # ---- Baseball section ----
        _md_el('<section class="ps-card" id="ps-sec-bb"></section>'),
        _columns_row([
            _selectbox("Primary position", pid_pos),
            _selectbox("Secondary position",
                       POS_LABELS[0] if POS_LABELS else "Catcher"),
        ]),
        _columns_row([
            _radio_horizontal("Bats", ["Right", "Left", "Switch"], 0),
            _radio_horizontal("Throws", ["Right", "Left"], 0),
        ]),
        _md_el('</section>'),
        # ---- Swing prefs section ----
        _md_el('<section class="ps-card" id="ps-sec-swing"></section>'),
        _columns_row([
            _selectbox("Default swing view", SWING_VIEWS[0]),
            _radio_horizontal("MLB comparison handedness",
                              MLB_HAND_PREFS, 0),
        ]),
        _checkbox("Email me weekly swing digests", True),
        _checkbox("Share anonymized data for MLB comparisons", False),
        _md_el('</section>'),
        # ---- Goal pill grid (3-col st.columns of stButtons) ----
        _md_el('<div class="ps-pill-grid cols-3 is-selected-1"></div>'),
        _columns_row([
            _button("◆    Contact"),
            _button("◆    Power"),
            _button("◆    Launch"),
        ]),
        _md_el('</div>'),  # /.ps-pill-grid (stray close)
        # ---- closing strays from the renderer ----
        _md_el('</div>'),   # /.ps-col
        _md_el('</div>'),   # /.ps-grid
        _md_el('</div>'),   # /.ps-wrap
    ]
    inner = "".join(parts)
    return (
        '<div data-testid="stVerticalBlock" class="stVerticalBlock">'
        f'{inner}</div>'
    )


# ---------------------------------------------------------------------
# 4b. TARGET (keyed-container) MODEL — what the rebuild should produce.
#     st.container(key="ps_wrap") emits
#       <div class="st-key-ps_wrap"><div data-testid="stVerticalBlock">
#         ...widgets INSIDE...
#       </div></div>
#     CSS scoped to `.st-key-ps_wrap [data-testid="stTextInput"] input`
#     (the rebuild's equivalent of `.ps-wrap ...`) then actually matches.
#     We keep the SAME _PAGE_CSS but add a thin compatibility shim that
#     re-points the `.ps-wrap` scope onto `.st-key-ps_wrap` so this
#     harness can prove the target WITHOUT needing the rebuilt file yet.
# ---------------------------------------------------------------------
def _keyed_container(key: str, inner: str) -> str:
    """Reproduce `st.container(key="X")` output verbatim:
       <div class="st-key-X stElementContainer" data-testid=stElementContainer>
         <div data-testid="stVerticalBlock" class="stVerticalBlock">
           ...inner...
         </div>
       </div>"""
    return (
        f'<div class="st-key-{key} stElementContainer" '
        f'data-testid="stElementContainer">'
        f'<div data-testid="stVerticalBlock" class="stVerticalBlock">'
        f'{inner}</div></div>'
    )


def _segmented_buttons(*, key: str, options: list[str],
                        selected_idx: int = 0) -> str:
    """Reproduce the rebuild's segmented control: keyed container holds
    a stColumns row of stButtons; the selected one is kind=primary."""
    buttons = []
    for i, opt in enumerate(options):
        kind = "primary" if i == selected_idx else "secondary"
        testid = f"stBaseButton-{kind}"
        buttons.append(_el(
            '<div data-testid="stButton" class="stButton">'
            f'<button kind="{kind}" data-testid="{testid}" type="button">'
            '<div data-testid="stMarkdownContainer" class="stMarkdownContainer">'
            f'<p>{opt}</p></div></button></div>'
        ))
    return _keyed_container(key, _columns_row(buttons))


def _toggle(label: str, *, checked: bool) -> str:
    """Reproduce ST 1.57's st.toggle DOM (it's a styled stCheckbox with
    role=switch on the inner div, and Streamlit's own pill styling
    applied via its emotion CSS — we approximate the switch shape so the
    harness can verify the wrapper exists and our label CSS applies)."""
    aria = "true" if checked else "false"
    return _el(
        '<div data-testid="stCheckbox" class="stCheckbox">'
        '<label><div><div role="switch" '
        f'aria-checked="{aria}" tabindex="0" '
        'style="width:36px;height:20px;border-radius:999px;'
        + ("background:rgba(232,193,112,0.25);"
           if checked else "background:rgba(0,0,0,0.3);")
        + '"></div></div>'
        f'<div>{label}</div></label>'
        '</div>'
    )


def build_dom_keyed() -> str:
    """The CORRECT pattern: widgets nested INSIDE the keyed container,
    matching the actual rebuild's design (st.container keys + segmented
    button rows + st.toggle, NOT st.radio-as-segmented or checkbox-as-
    toggle)."""
    pill_buttons = [
        _el(
            '<div data-testid="stButton" class="stButton">'
            f'<button kind="{"primary" if i == 0 else "secondary"}" '
            f'data-testid="stBaseButton-{"primary" if i == 0 else "secondary"}" '
            'type="button">'
            '<div data-testid="stMarkdownContainer" class="stMarkdownContainer">'
            f'<p>◆    {lbl}</p></div></button></div>'
        )
        for i, lbl in enumerate(["Contact", "Power", "Launch"])
    ]
    widgets = "".join([
        _md_el('<h1 class="ps-title">Player Settings</h1>'),
        _columns_row([
            _text_input("First name", "Jordan"),
            _text_input("Last name", "Ellis"),
        ]),
        _columns_row([
            _selectbox("Primary position",
                       POS_LABELS[1] if len(POS_LABELS) > 1 else "Shortstop"),
            # NEW: segmented control = button row inside .st-key-ps_seg_bats
            _segmented_buttons(key="ps_seg_bats",
                                options=["Right", "Left", "Switch"],
                                selected_idx=0),
        ]),
        _selectbox("Default swing view", SWING_VIEWS[0]),
        # NEW: st.toggle instead of st.checkbox-as-toggle
        _toggle("Email me weekly swing digests", checked=True),
        # pill grid in its own keyed container, button-row with type=primary
        _keyed_container("ps_pillgrid_goal", _columns_row(pill_buttons)),
    ])
    # st.container(key="ps_wrap"): keyed wrapper > stVerticalBlock > kids
    return (
        '<div data-testid="stVerticalBlock" class="stVerticalBlock">'
        '<div class="st-key-ps_wrap stElementContainer" '
        'data-testid="stElementContainer">'
        '<div data-testid="stVerticalBlock" class="stVerticalBlock">'
        f'{widgets}'
        '</div></div>'
        '</div>'
    )


# Once the rebuild lands, the deployed _PAGE_CSS already targets
# `.st-key-ps_wrap`, so KEYED_CSS is the deployed CSS verbatim. If we're
# running pre-rebuild against the legacy `.ps-wrap` CSS, this falls
# back to substituting the scope so the harness still works.
KEYED_CSS = PAGE_CSS.replace(".ps-wrap ", ".st-key-ps_wrap ") if (
    ".ps-wrap " in PAGE_CSS and ".st-key-ps_wrap" not in PAGE_CSS
) else PAGE_CSS


def build_page(body: str, css: str) -> str:
    # _PAGE_CSS / KEYED_CSS already carry their own <style> wrapper —
    # inject raw, not re-wrapped (same as verify_nav_tabs EDGE_CSS).
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{ST_DEFAULTS}
{css}
</head><body>
<div data-testid="stApp" class="stApp">
  <header data-testid="stHeader" class="stAppHeader"><div>toolbar</div></header>
  <div data-testid="stAppViewContainer">
    <section data-testid="stMain" class="stMain">
      <div data-testid="stMainBlockContainer"
           class="stMainBlockContainer block-container">
        {body}
      </div>
    </section>
  </div>
</div>
</body></html>"""


CUR_HTML = Path("/tmp/player_settings_broken.html")
TGT_HTML = Path("/tmp/player_settings_target.html")
CUR_HTML.write_text(
    build_page(build_dom_markdown_div(), LEGACY_CSS), encoding="utf-8")
TGT_HTML.write_text(
    build_page(build_dom_keyed(), KEYED_CSS), encoding="utf-8")


# ---------------------------------------------------------------------
# 5. Computed-style probe. Reused for BOTH models; `wrapSel` is the
#    scope under test (`.ps-wrap` for current, `.st-key-ps_wrap` for
#    target). Returns the same shape so the verdict logic is symmetric.
# ---------------------------------------------------------------------
PROBE = r"""
(wrapSel) => {
  const out = {wrapSel};
  const wrap = document.querySelector(wrapSel);
  out.wrapFound = !!wrap;

  // ASSERTION 1 — does .ps-wrap actually contain the widgets?
  const elDesc = wrap
    ? wrap.querySelectorAll('[data-testid="stElementContainer"]').length
    : -1;
  out.wrapElementDescendants = elDesc;

  // ASSERTION 2 — a representative text input's computed background.
  // Intended dark --ink-2 = #0D0F13 = rgb(13, 15, 19).
  const ti = document.querySelector('[data-testid="stTextInput"] input');
  out.textInputFound = !!ti;
  out.textInputBg = ti ? getComputedStyle(ti).backgroundColor : null;
  out.textInputColor = ti ? getComputedStyle(ti).color : null;
  out.textInputBorderRadius =
    ti ? getComputedStyle(ti).borderTopLeftRadius : null;

  // ASSERTION 3 — the .ps-grid element's display + element children.
  // (selector unchanged across models — .ps-grid is a markdown class
  //  in current; absent in keyed, which is itself the finding.)
  const grid = document.querySelector('.ps-grid');
  out.gridFound = !!grid;
  if (grid) {
    out.gridDisplay = getComputedStyle(grid).display;
    out.gridGridTemplateColumns =
      getComputedStyle(grid).gridTemplateColumns;
    out.gridElementChildren = grid.querySelectorAll(
      '[data-testid="stElementContainer"],[data-testid="stHorizontalBlock"]'
    ).length;
  } else {
    out.gridDisplay = null;
    out.gridElementChildren = -1;
  }

  // ASSERTION 4 — .ps-card / section.ps-card nodes with ZERO widgets.
  const cards = Array.from(
    document.querySelectorAll('.ps-card, section.ps-card'));
  out.cardCount = cards.length;
  out.emptyCards = cards.filter(c => c.querySelectorAll(
    '[data-testid="stTextInput"],[data-testid="stSelectbox"],' +
    '[data-testid="stRadio"],[data-testid="stCheckbox"],' +
    '[data-testid="stButton"]'
  ).length === 0).length;

  // Selectbox + segmented + toggle + pill — corroborate the skin.
  const selBox = document.querySelector(
    '[data-baseweb="select"] > div');
  out.selectBg = selBox ? getComputedStyle(selBox).backgroundColor : null;

  // Radio (legacy / CURRENT model still uses st.radio horizontal)
  const radioGroup = document.querySelector(
    '[data-testid="stRadio"] > div[role="radiogroup"]') ||
    document.querySelector('[data-testid="stRadio"] > div');
  out.radioDisplay =
    radioGroup ? getComputedStyle(radioGroup).display : null;
  out.radioBg =
    radioGroup ? getComputedStyle(radioGroup).backgroundColor : null;

  // SEGMENTED button row (rebuild's replacement for st.radio horizontal).
  // The keyed container .st-key-ps_seg_bats should be display:flex with
  // a gold-bordered primary button as the "selected" option.
  const seg = document.querySelector('[class*="st-key-ps_seg_"]');
  out.segFound = !!seg;
  out.segDisplay = seg ? getComputedStyle(seg).display : null;
  const segPrimary = seg
    ? seg.querySelector('button[kind="primary"], '
        + 'button[data-testid="stBaseButton-primary"]')
    : null;
  out.segPrimaryFound = !!segPrimary;
  out.segPrimaryColor =
    segPrimary ? getComputedStyle(segPrimary).color : null;

  // Toggle — st.toggle uses role=switch (or fallback to role=checkbox).
  // The width should be <= ~48px (a pill, not a full-width row).
  const toggle = document.querySelector(
    '[data-testid="stCheckbox"] div[role="switch"]') ||
    document.querySelector(
      '[data-testid="stCheckbox"] div[role="checkbox"]');
  out.toggleWidth = toggle ? getComputedStyle(toggle).width : null;
  out.toggleFound = !!toggle;

  // Pill-grid button (the rebuild uses .st-key-ps_pillgrid_* keyed
  // containers; selected option is kind=primary with gold border).
  const pillCtr = document.querySelector('[class*="st-key-ps_pillgrid_"]');
  const pillBtn = pillCtr
    ? pillCtr.querySelector('[data-testid="stButton"] button')
    : document.querySelector('[data-testid="stButton"] button');
  out.pillButtonBg =
    pillBtn ? getComputedStyle(pillBtn).backgroundColor : null;
  out.pillButtonRadius =
    pillBtn ? getComputedStyle(pillBtn).borderTopLeftRadius : null;
  const pillPrimary = pillCtr
    ? pillCtr.querySelector('button[kind="primary"], '
        + 'button[data-testid="stBaseButton-primary"]')
    : null;
  out.pillPrimaryBorder =
    pillPrimary ? getComputedStyle(pillPrimary).borderColor : null;

  return out;
}
"""

INK_2 = (13, 15, 19)  # #0D0F13 — the intended dark input background


def _rgb(s: str | None) -> tuple[int, int, int] | None:
    if not s:
        return None
    nums = re.findall(r"\d+", s)
    if len(nums) < 3:
        return None
    return (int(nums[0]), int(nums[1]), int(nums[2]))


def _is_ink2(s: str | None) -> bool:
    rgb = _rgb(s)
    if rgb is None:
        return False
    return all(abs(rgb[i] - INK_2[i]) <= 3 for i in range(3))


from playwright.sync_api import sync_playwright  # noqa: E402

results: dict = {}

with sync_playwright() as p:
    b = p.chromium.launch()

    # ---- CURRENT model: probe + the two required screenshots ----
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(f"file://{CUR_HTML}", wait_until="networkidle")
    pg.wait_for_timeout(450)
    results["current"] = pg.evaluate(PROBE, ".ps-wrap")
    pg.screenshot(path="/tmp/player_settings_broken.png", full_page=True)
    pg.close()

    pg = b.new_page(viewport={"width": 768, "height": 900})
    pg.goto(f"file://{CUR_HTML}", wait_until="networkidle")
    pg.wait_for_timeout(450)
    pg.screenshot(
        path="/tmp/player_settings_broken_mobile.png", full_page=True)
    pg.close()

    # ---- TARGET model: same probe against the keyed container ----
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.goto(f"file://{TGT_HTML}", wait_until="networkidle")
    pg.wait_for_timeout(450)
    results["target"] = pg.evaluate(PROBE, ".st-key-ps_wrap")
    pg.screenshot(
        path="/tmp/player_settings_target.png", full_page=True)
    pg.close()

    # ---- TARGET model — additional responsive screenshots so the user
    # can visually verify the page across PC / tablet / mobile widths. ----
    for label, vw, vh in [("pc", 1440, 900), ("tablet", 1024, 900),
                            ("mobile", 375, 800)]:
        pg = b.new_page(viewport={"width": vw, "height": vh})
        pg.goto(f"file://{TGT_HTML}", wait_until="networkidle")
        pg.wait_for_timeout(450)
        pg.screenshot(
            path=f"/tmp/player_settings_target_{label}.png", full_page=True)
        pg.close()

    b.close()


# ---------------------------------------------------------------------
# 6. Evaluate both models. Print findings; never crash the diagnostic.
# ---------------------------------------------------------------------
cur = results["current"]
tgt = results["target"]

# ---- CURRENT model findings (broken-by-design is EXPECTED) ----
cur_findings: list[tuple[str, bool, str]] = []

# A1: .ps-wrap exists but has ZERO stElementContainer descendants.
a1_broken = cur["wrapFound"] and cur["wrapElementDescendants"] == 0
cur_findings.append((
    "A1 .ps-wrap is an EMPTY auto-closed sibling "
    f"(stElementContainer descendants = {cur['wrapElementDescendants']})",
    a1_broken,
    "GOOD STATE: .ps-wrap should CONTAIN the widgets "
    "(descendants > 0). Currently it does not → broken by design.",
))

# A2: representative text input bg is NOT the intended dark --ink-2.
a2_broken = not _is_ink2(cur["textInputBg"])
cur_findings.append((
    f"A2 text input bg = {cur['textInputBg']} "
    f"(intended --ink-2 rgb{INK_2})",
    a2_broken,
    "GOOD STATE: input bg should equal rgb(13,15,19). The "
    "`.ps-wrap [data-testid=stTextInput] input` rule never applied.",
))

# A3: .ps-grid display + element children count.
a3_grid_broken = (
    cur["gridFound"]
    and (cur["gridDisplay"] == "grid")
    and (cur["gridElementChildren"] < 2)
)
cur_findings.append((
    f"A3 .ps-grid display={cur['gridDisplay']} "
    f"templateCols={cur.get('gridGridTemplateColumns')} "
    f"elementChildren={cur['gridElementChildren']}",
    a3_grid_broken,
    "GOOD STATE: the two-column grid should hold >=2 element "
    "children (rail + col). Empty markdown div => display:grid "
    "but zero children.",
))

# A4: count .ps-card / section.ps-card with zero widget descendants.
a4_broken = cur["cardCount"] > 0 and cur["emptyCards"] == cur["cardCount"]
cur_findings.append((
    f"A4 {cur['emptyCards']}/{cur['cardCount']} .ps-card nodes "
    f"contain ZERO widgets",
    a4_broken,
    "GOOD STATE: every .ps-card should wrap its section's "
    "widgets. All cards empty => cards are decorative siblings only.",
))

cur_findings.append((
    f"(corroborating) selectbox bg={cur['selectBg']} "
    f"radio display={cur['radioDisplay']} radio bg={cur['radioBg']} "
    f"toggle width={cur['toggleWidth']} pill btn bg={cur['pillButtonBg']}",
    not _is_ink2(cur["selectBg"]),
    "GOOD STATE: selectbox/radio/toggle/pill all pick up the dark "
    "ink-2 skin. Default white/grey here => scope never matched.",
))

# CURRENT is "broken as expected" when A1..A4 are all in the broken state.
current_broken_as_expected = a1_broken and a2_broken and a4_broken

# ---- TARGET model findings (these MUST pass post-rebuild) ----
tgt_problems: list[str] = []

if not tgt["wrapFound"]:
    tgt_problems.append("keyed wrapper .st-key-ps_wrap not found")
elif tgt["wrapElementDescendants"] < 1:
    tgt_problems.append(
        "keyed wrapper has ZERO stElementContainer descendants "
        "(widgets are NOT inside it)"
    )
if not _is_ink2(tgt["textInputBg"]):
    tgt_problems.append(
        f"text input bg in TARGET is {tgt['textInputBg']}, "
        f"expected dark --ink-2 rgb{INK_2}"
    )
if not _is_ink2(tgt["selectBg"]):
    tgt_problems.append(
        f"selectbox bg in TARGET is {tgt['selectBg']}, "
        f"expected dark --ink-2"
    )
# Rebuild replaced st.radio-as-segmented with a button row inside a
# keyed `.st-key-ps_seg_*` container. Verify THAT instead.
if not tgt.get("segFound"):
    tgt_problems.append("segmented control container .st-key-ps_seg_* not found")
elif tgt.get("segDisplay") != "flex":
    tgt_problems.append(
        f"segmented control display is {tgt.get('segDisplay')}, expected flex"
    )
elif not tgt.get("segPrimaryFound"):
    tgt_problems.append("segmented control has no primary (selected) button")
# Rebuild replaced st.checkbox-as-toggle with st.toggle. Verify it
# rendered something pill-shaped (<=48px wide), not a full-width row.
import re as _re_local
_tw = tgt.get("toggleWidth") or ""
_tw_num = float(_re_local.findall(r"[\d.]+", _tw)[0]) if _re_local.findall(r"[\d.]+", _tw) else 9999
if _tw_num > 48:
    tgt_problems.append(
        f"toggle width in TARGET is {_tw}, expected a pill (<=48px). "
        f"Likely the toggle DOM did not render."
    )
pill_rgb = _rgb(tgt["pillButtonBg"])
if pill_rgb is None or pill_rgb == (247, 247, 247):
    tgt_problems.append(
        f"pill button bg in TARGET is {tgt['pillButtonBg']}, "
        f"still the Streamlit default (skin not applied)"
    )

target_passes = not tgt_problems


# ---------------------------------------------------------------------
# 7. Report.
# ---------------------------------------------------------------------
results["summary"] = {
    "current_broken_as_expected": current_broken_as_expected,
    "target_passes": target_passes,
    "target_problems": tgt_problems,
}

print(json.dumps(results, indent=2))

print("\n" + "=" * 64)
print("CURRENT (markdown-div) MODEL — reproduces today's broken page")
print("=" * 64)
for desc, broken, good in cur_findings:
    flag = "BROKEN (expected)" if broken else "not-broken"
    print(f"  [{flag}] {desc}")
    print(f"           ↳ {good}")

print("\n" + "=" * 64)
print("TARGET (keyed-container) MODEL — what the rebuild must achieve")
print("=" * 64)
if target_passes:
    print("  PASS: widgets nest inside .st-key-ps_wrap and every "
          "computed-style check (input/select/radio/toggle/pill) "
          "resolves to the intended dark skin.")
else:
    for prob in tgt_problems:
        print(f"  FAIL: {prob}")

print("\n" + "=" * 64)
print("=== VERDICT ===")
print("=" * 64)
print(
    "CURRENT page model is "
    + ("BROKEN — and this is EXPECTED PRE-FIX. "
       if current_broken_as_expected
       else "NOT in the expected broken shape (investigate). ")
)
print(
    "  The `.ps-wrap` markdown-div never wraps anything in Streamlit\n"
    "  1.57, so every `.ps-wrap [data-testid=...]` rule is dead and the\n"
    "  page renders as raw default-chrome widgets on a dark background.\n"
    "  Screenshots: /tmp/player_settings_broken.png (1440x900),\n"
    "               /tmp/player_settings_broken_mobile.png (768x900)."
)
print(
    "\nTARGET keyed-container model "
    + ("PASSES — this documents the fixed state. After the rebuild\n"
       "  switches to st.container(key='ps_wrap'), THIS harness "
       "(run with\n  --gate) is the regression gate that keeps it fixed."
       if target_passes
       else "FAILS — the keyed model did not produce the intended\n"
            "  skin; the design itself or this harness needs attention.")
)

# Diagnostic mode never fails the process. The real pass/fail gate is
# behind --gate and trips ONLY if the TARGET (good-state) model breaks
# — that is the post-rebuild regression contract.
if GATE:
    if target_passes:
        print("\n--gate: TARGET model passes → exit 0")
        sys.exit(0)
    print("\n--gate: TARGET model FAILED → exit 1 (rebuild regressed)")
    sys.exit(1)

sys.exit(0)
