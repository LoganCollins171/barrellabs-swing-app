"""Render the REAL masthead HTML + swing report + sessions as ONE
scrolling page so the nav/spacing/seamlessness can be visually QA'd
without auth. The masthead is now pure HTML, so this is faithful.

    .venv/bin/python scripts/visual_qa/render_full_app_static.py
Writes /tmp/full_app_preview.html (+ auto-opens unless PREVIEW_NO_OPEN=1).
"""
from __future__ import annotations

import os
import sys
import types
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_CAP: list[str] = []


def _stub_streamlit():
    st = types.ModuleType("streamlit")

    class _SS(dict):
        def __getattr__(s, k):
            try:
                return s[k]
            except KeyError as e:
                raise AttributeError(k) from e

        def __setattr__(s, k, v):
            s[k] = v

    st.session_state = _SS()

    class _QP(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    st.query_params = _QP()

    class _C:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

    st.markdown = lambda s, **k: _CAP.append(str(s))
    st.columns = lambda n, **k: [_C() for _ in range(n if isinstance(n, int) else len(n))]
    st.container = lambda *a, **k: _C()
    st.button = lambda *a, **k: False
    st.download_button = lambda *a, **k: False
    st.selectbox = lambda *a, **k: (a[1][0] if len(a) > 1 and a[1] else None)
    st.text_input = lambda *a, **k: ""
    for n in ("write", "error", "warning", "info", "caption", "image",
              "rerun", "stop", "toast", "divider"):
        setattr(st, n, lambda *a, **k: None)
    c1 = types.ModuleType("streamlit.components.v1")
    c1.html = lambda *a, **k: None
    c0 = types.ModuleType("streamlit.components")
    c0.v1 = c1
    st.components = c0
    sys.modules["streamlit"] = st
    sys.modules["streamlit.components"] = c0
    sys.modules["streamlit.components.v1"] = c1
    st.cache_data = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    st.cache_resource = st.cache_data
    return st


def build() -> str:
    _stub_streamlit()
    import importlib
    bec = importlib.import_module("bl_edge_chrome")

    _CAP.clear()
    bec.render_edge_masthead(
        {"name": "Jordan Ellis", "gamification": {"current_streak_days": 7}},
        active_page="saved_reports",
    )
    masthead_html = "\n".join(_CAP)

    from swing_report_dashboard_preview import (
        SAMPLE_RECORD, SAMPLE_HISTORY, build_dashboard_preview_html,
    )
    report_html = build_dashboard_preview_html(
        SAMPLE_RECORD, history=SAMPLE_HISTORY, is_sample=True
    )

    # A faux "dashboard band" approximating the iframe's top so we can
    # judge masthead↔content seamlessness (same ink, flush).
    dash_band = """
<div style="background:#0A0B0E;color:#F4EFE6;font-family:'Geist',system-ui,sans-serif;
            max-width:1560px;margin:0 auto;padding:34px 40px 60px;">
  <div style="font-family:'Geist Mono',monospace;font-size:11px;letter-spacing:.26em;
              text-transform:uppercase;color:#E64530;">§ 01 · This week's headline</div>
  <div style="font-family:'Instrument Serif',Georgia,serif;font-style:italic;
              font-size:3.4rem;line-height:1.05;margin:.5rem 0 0;">
    Your separation hit <span style="color:#E8C170;">+27.7°</span> — MLB territory.</div>
  <div style="color:rgba(244,239,230,.7);max-width:60ch;margin-top:1rem;line-height:1.6;">
    This band stands in for the editorial dashboard iframe — it shares the exact
    ink (#0A0B0E) so you can judge whether the masthead sits flush and seamless
    above the content with no black box or top gap.</div>
</div>
"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Full App — masthead + pages QA</title>
<style>
  html,body{{margin:0;padding:0;background:#0A0B0E;overflow-x:hidden;}}
  .qa-tag{{font-family:'Geist Mono',monospace;font-size:10px;letter-spacing:.18em;
    text-transform:uppercase;color:rgba(232,193,112,.7);
    max-width:1560px;margin:0 auto;padding:14px 40px 0;}}
</style></head><body>
{masthead_html}
{dash_band}
<div class="qa-tag">↓ Swing Report page (same masthead scrolls above)</div>
{report_html}
</body></html>"""


if __name__ == "__main__":
    out = Path(os.environ.get("FULL_PREVIEW_OUT", "/tmp/full_app_preview.html"))
    out.write_text(build(), encoding="utf-8")
    print(f"Wrote: {out}  ({out.stat().st_size:,} bytes)")
    if os.environ.get("PREVIEW_NO_OPEN") != "1":
        webbrowser.open(f"file://{out.resolve()}")
