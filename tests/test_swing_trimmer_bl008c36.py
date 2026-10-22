"""BL-008C.36 -- the visual swing trimmer: Python payload, browser-side
safety properties, and the page's handling of committed selections
(including the stale-arguments race reproduced in the browser E2E)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "components" / "swing_trimmer" / "index.html").read_text()


# ------------------------------------------------------------------ payload
def test_payload_maps_each_preview_to_its_real_frame_index_and_timestamp(monkeypatch):
    import swing_trimmer as stm
    captured = {}
    monkeypatch.setattr(stm, "_component", lambda: (lambda **kw: captured.update(kw)))
    previews = [(0, b"\xff\xd8a"), (2, b"\xff\xd8b"), (4, b"\xff\xd8c")]
    pts = [0.0, 0.0167, 0.0333, 0.06, 0.0767]   # uneven timing on purpose
    stm.swing_trimmer(clip_id="c1", previews=previews, pts=pts, total_frames=5, start=0, end=4,
                      preview_size=(480, 276), fps_label="53.8", key="k", ack_seq=7)
    frames = captured["frames"]
    assert [f["i"] for f in frames] == [0, 2, 4]
    assert [f["t"] for f in frames] == [0.0, 0.0333, 0.0767], "times are the frames' own timestamps"
    assert all(f["u"].startswith("data:image/jpeg;base64,") for f in frames), "headless: inline"
    assert captured["ack_seq"] == 7 and captured["total_frames"] == 5 and captured["key"] == "k"


# ------------------------------------------------------- browser-side safety
def test_component_only_talks_to_its_same_origin_parent():
    assert 'postMessage(m, ORIGIN)' in HTML and '"*"' not in HTML
    assert "ev.origin !== ORIGIN || ev.source !== window.parent" in HTML
    assert not re.search(r"\bfetch\(|XMLHttpRequest|sendBeacon|localStorage|console\.", HTML)


def test_scrubbing_and_playback_never_commit():
    """Only marker release, Set START/END, or (debounced) marker keys commit."""
    assert "}, function () { commit(); });" in HTML                       # marker release
    assert "setStart(S.cur, true)" in HTML and "setEnd(S.cur, true)" in HTML  # Set START/END here
    assert "commitSoon();" in HTML                                         # debounced marker keys
    assert "onDrag(function (e) { seek(nearestK(fracAt(e))); }, null);   // scrubbing never commits" in HTML
    tick = HTML[HTML.index("function tick("):HTML.index("function play(")]
    assert "commit" not in tick, "playback must never commit"


def test_stale_render_cannot_undo_a_newer_commit():
    assert "a.ack_seq >= S.seq" in HTML
    assert "S.seq = typeof a.ack_seq === \"number\" ? a.ack_seq : 0;" in HTML


def test_layout_does_not_use_iframe_relative_viewport_units():
    css = HTML[HTML.index("<style>"):HTML.index("</style>")]
    assert "vh" not in css, "vh inside an auto-height iframe is circular"
    assert "[hidden] { display: none !important; }" in css


# ------------------------------------------------------------ page handling
APP = '''
import streamlit as st
import manual_swing_review as msr
msr.render_manual_swing_review({"slug": "e2e-player", "email": "e2e@example.test"})
'''


@pytest.fixture
def page(monkeypatch):
    """The real page with a fake prepared clip (no decode needed) and a
    capturing stand-in for the player."""
    from tests._stub_isolation import drop_stub_bound_modules
    drop_stub_bound_modules(monkeypatch)
    import manual_swing_review  # noqa: F401
    import swing_trimmer as stm
    from engine_v2.types import LandmarkFrame
    renders = []
    monkeypatch.setattr(stm, "swing_trimmer", lambda **kw: renders.append(kw))
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(APP, default_timeout=60)
    n = 40
    at.session_state["manual_review_frames"] = [
        LandmarkFrame(frame_index=i, pts_s=i / 60, landmarks=None, mean_visibility=0.0) for i in range(n)]
    at.session_state["manual_review_pts"] = [i / 60 for i in range(n)]
    import cv2
    import numpy as np
    jpg = cv2.imencode(".jpg", np.zeros((27, 48, 3), np.uint8))[1].tobytes()
    at.session_state["manual_review_previews"] = [(i, jpg) for i in range(0, n, 2)]
    at.session_state["manual_review_fps"] = 60.0
    at.session_state["manual_review_clip"] = {"id": "clipA", "preview_size": (480, 270), "n": n, "duration": (n - 1) / 60}
    at.session_state["manual_review_selection"] = (0, n - 1)
    at.session_state["manual_review_analysis_session_id"] = "s1"
    return at, renders


def _sel_text(at):
    md = "\n".join(m.value for m in at.markdown)
    return re.search(r"Your selection: frames <b>(\d+)–(\d+)</b>", md).groups()


def test_committed_selection_is_applied_before_the_player_renders(page):
    """Regression for the E2E race: the run triggered by a commit must send
    the player the NEW selection (and its seq as ack), not the old one."""
    at, renders = page
    at.run()
    at.session_state["manual_review_trimmer"] = {"clip_id": "clipA", "start": 10, "end": 30, "seq": 5}
    at.run()
    assert (renders[-1]["start"], renders[-1]["end"], renders[-1]["ack_seq"]) == (10, 30, 5)
    assert _sel_text(at) == ("10", "30")
    assert at.slider(key="manual_review_slider").value == (10, 30), "simple controls follow the player"


def test_a_commit_for_a_different_clip_is_ignored(page):
    at, renders = page
    at.session_state["manual_review_trimmer"] = {"clip_id": "OLD-clip", "start": 3, "end": 9, "seq": 1}
    at.run()
    assert _sel_text(at) == ("0", "39")


def test_invalid_committed_values_are_ignored(page):
    at, renders = page
    for bad in ({"clip_id": "clipA", "start": 30, "end": 10, "seq": 2},
                {"clip_id": "clipA", "start": -1, "end": 10, "seq": 3},
                {"clip_id": "clipA", "start": 0, "end": 999, "seq": 4},
                {"clip_id": "clipA", "start": "0", "end": 10, "seq": 5}):
        at.session_state["manual_review_trimmer"] = bad
        at.run()
        assert _sel_text(at) == ("0", "39"), bad


def test_simple_controls_change_the_selection_and_the_player_follows(page):
    at, renders = page
    at.run()
    at.slider(key="manual_review_slider").set_value((5, 25)).run()
    assert _sel_text(at) == ("5", "25")
    assert (renders[-1]["start"], renders[-1]["end"]) == (5, 25)


def test_check_results_are_scoped_to_the_clip(page):
    """A check result recorded for another clip with the same frame numbers
    must never show for this one."""
    at, renders = page
    from types import SimpleNamespace
    at.session_state["manual_review_checked_selection"] = ("OTHER-clip", 0, 39)
    at.session_state["manual_review_check_result"] = SimpleNamespace(
        status="needs_confirmation", validation_state="SINGLE_MOVEMENT_DETECTED", reason="")
    at.run()
    assert "manual_review_check_result" not in at.session_state
    assert not [c for c in at.checkbox if "reviewed the clip" in c.label]


def test_header_and_navigation(page):
    at, renders = page
    at.run()
    md = "\n".join(m.value for m in at.markdown)
    assert "Mark your swing" in md and "not an Edge Score" in md
    assert 'blx-step on">2 · Mark the swing' in md
    labels = [b.label for b in at.button]
    assert "Upload a different clip" in labels and "Back to dashboard" in labels
    next(b for b in at.button if b.label == "Upload a different clip").click().run()
    assert at.session_state["page"] == "upload"
    for k in ("manual_review_frames", "manual_review_selection", "manual_review_clip",
              "manual_review_previews", "manual_review_pts"):
        assert k not in at.session_state, f"{k} must be cleared"


def test_app_routes_review_page_without_the_upload_hero_and_clears_on_handoff():
    src = (ROOT / "app.py").read_text()
    hero_set = src[src.index("_pages_with_own_hero = {"):src.index("}", src.index("_pages_with_own_hero = {"))]
    assert '"manual_review"' in hero_set
    handoff = src[src.index('key="manual_review_btn"'):src.index('st.session_state["page"] = "manual_review"')]
    assert 'startswith("manual_review_")' in handoff, "a new clip must start a clean review"


def test_restoring_screen_has_its_own_dark_background():
    src = (ROOT / "app.py").read_text()
    block = src[src.index("Signing you back in"):][:10]  # sanity
    restoring = src[src.rindex("<style>[data-testid='stAppViewContainer']", 0, src.index("Signing you back in")):
                    src.index("Signing you back in")]
    assert "#0A0B0E" in restoring and block
