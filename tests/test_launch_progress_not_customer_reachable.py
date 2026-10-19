"""BL-004B (C.4): regression coverage for removing the internal Launch
Progress page from customer-accessible routing.

`app.py` is a top-to-bottom Streamlit script with heavy side effects on
import (auth gate, Supabase client, page rendering) — the existing test
suite (see `tests/test_nav_routing_smoke.py`, `tests/test_pose_init.py`)
does not import it directly for this reason, and pins behavior by
asserting against the actual source text at the real call sites instead.
This test follows that same established pattern.

Root cause this guards against regressing: `?page=launch_progress` was
reachable by ANY logged-in user (no admin/role check existed anywhere in
this codebase) and rendered an internal pre-launch checklist that included
compliance/legal TODOs not meant for customer eyes.
"""
from __future__ import annotations

import re
from pathlib import Path

APP_SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text()


def test_launch_progress_not_in_allowed_url_pages():
    """The only way `st.session_state["page"]` could ever become
    "launch_progress" was via the `?page=` URL bridge consulting
    `_ALLOWED_PAGES_FROM_URL`. It must not be in that set."""
    match = re.search(
        r"_ALLOWED_PAGES_FROM_URL\s*=\s*\{(.*?)\}",
        APP_SRC,
        re.DOTALL,
    )
    assert match, "could not find _ALLOWED_PAGES_FROM_URL definition"
    allowed_pages_block = match.group(1)
    # Checked as a set-member literal (quote, comma) rather than a bare
    # substring so this doesn't false-positive on the explanatory comment
    # left in place of the removed entry, which mentions the string in
    # prose.
    assert '"launch_progress",' not in allowed_pages_block


def test_launch_progress_render_site_fails_closed_not_open():
    """Defense-in-depth: even if `page` session state ever ends up as
    "launch_progress" through some other path, the render site must
    redirect away rather than call the internal-roadmap renderer."""
    # Anchor on the BL-004B comment unique to the real render-site guard —
    # the string "launch_progress" also appears in an unrelated `elif`
    # (the active-nav-key mapping for highlighting the old nav tab), which
    # a looser anchor would match instead.
    match = re.search(
        r'if st\.session_state\.get\("page"\) == "launch_progress":\n'
        r"    # BL-004B \(C\.2\): fail closed(.*?)\n\n\n",
        APP_SRC,
        re.DOTALL,
    )
    assert match, "could not find the launch_progress render-site guard"
    guard_body = match.group(1)
    assert "_render_launch_progress()" not in guard_body, (
        "the internal roadmap renderer must not be callable from this "
        "guard — it was removed from the URL allowlist specifically "
        "because it had no access control"
    )
    assert "st.rerun()" in guard_body, (
        "expected the guard to redirect (set page + st.rerun()) instead "
        "of rendering"
    )
