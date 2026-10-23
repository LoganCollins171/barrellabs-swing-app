"""BL-008C.37 -- the review page's check / confirm / analyze flow, through
the REAL page code in Streamlit's AppTest, on synthetic pose clips injected
as an already-prepared session (no private footage; decode and pose are
covered by tests/test_experimental_path_integration.py). Billing is faked
so every charge attempt is counted."""
from __future__ import annotations

import pytest

from tests._synthetic_swings import make_clip, frame_at

APP = '''
import streamlit as st
import manual_swing_review as msr
msr.render_manual_swing_review({"slug": "e2e-player", "email": "e2e@example.test"})
'''


@pytest.fixture(autouse=True)
def _real_streamlit_modules(monkeypatch):
    import sys
    for name in ("supabase_client", "session_persist", "auth_diag"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    from tests._stub_isolation import drop_stub_bound_modules
    drop_stub_bound_modules(monkeypatch)


@pytest.fixture
def billing(monkeypatch):
    import manual_swing_review as msr
    from entitlements import EntitlementResult
    state = {"charges": [], "gate_calls": 0}

    def _gate(plan, email):
        state["gate_calls"] += 1
        return EntitlementResult(allowed=True)

    def _record(plan, idempotency_key=None):
        state["charges"].append(idempotency_key)
        return True

    monkeypatch.setattr(msr, "load_my_plan", lambda force_refresh=False: {"plan": "free", "free_swings_used": 0})
    monkeypatch.setattr(msr, "can_analyze_swing_for_account", _gate)
    monkeypatch.setattr(msr, "record_successful_swing_analysis", _record)
    return state


def _session(frames, sel, clip_id="synthetic-clip"):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(APP, default_timeout=60)
    pts = [f.pts_s for f in frames]
    at.session_state["manual_review_frames"] = frames
    at.session_state["manual_review_pts"] = pts
    at.session_state["manual_review_previews"] = []
    at.session_state["manual_review_fps"] = 30.0
    at.session_state["manual_review_clip"] = {"id": clip_id, "preview_size": (270, 480), "n": len(frames),
                                              "duration": pts[-1]}
    at.session_state["manual_review_selection"] = sel
    at.session_state["manual_review_analysis_session_id"] = "sess-1"
    return at


def _texts(at) -> str:
    parts = []
    for coll in (at.markdown, at.success, at.error, at.warning, at.info, at.caption):
        parts += [str(getattr(x, "value", "") or "") for x in coll]
    return "\n".join(parts)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _buttons(at, prefix):
    return [b for b in at.button if b.label.startswith(prefix)]


def _confirm(at):
    return next(c for c in at.checkbox if str(c.key or "").startswith("manual_review_confirm_"))


def test_single_swing_reaches_confirmation_and_charges_exactly_once(billing):
    clip = make_clip(5, 30, [1.5])
    sel = (frame_at(clip, 0.2), frame_at(clip, 2.0))   # stance + load + swing + follow-through
    at = _session(clip, sel).run()
    assert not at.exception, at.exception
    _button(at, "Check this selection").click().run()
    out = _texts(at)
    assert "Looks good: one continuous movement." in out
    assert "more than one distinct movement" not in out
    assert billing["charges"] == [] and billing["gate_calls"] == 0, "checking is free"
    assert _button(at, "Analyze this selection").disabled, "nothing analyzes without confirmation"

    _confirm(at).check().run()
    _button(at, "Analyze this selection").click().run()
    out = _texts(at)
    assert f"Analyzed exactly what you selected: frames {sel[0]}-{sel[1]}" in out
    assert len(billing["charges"]) == 1

    at.run()          # plain reruns never re-charge
    at.run()
    assert len(billing["charges"]) == 1
    assert "Lower confidence" not in _texts(at)


def test_two_separate_swings_block_with_one_click_recovery_and_no_charge(billing):
    clip = make_clip(6, 30, [1.2, 3.6])
    at = _session(clip, (0, len(clip) - 1)).run()
    _button(at, "Check this selection").click().run()
    out = _texts(at)
    assert "more than one separate fast movement" in out
    assert not [b for b in at.button if b.label == "Analyze this selection"]
    picks = _buttons(at, "Use movement ")
    assert len(picks) == 2, [b.label for b in at.button]

    picks[1].click().run()
    s, e = at.session_state["manual_review_selection"]
    assert clip[s].pts_s < 3.6 < clip[e].pts_s and clip[s].pts_s > 2.3, (s, e)
    assert "more than one separate fast movement" not in _texts(at), "the old result must not survive the change"

    _button(at, "Check this selection").click().run()
    assert "Looks good: one continuous movement." in _texts(at)
    assert billing["charges"] == [] and billing["gate_calls"] == 0


def test_confirmation_does_not_carry_over_to_a_changed_selection(billing):
    clip = make_clip(5, 30, [1.5])
    a = (frame_at(clip, 0.2), frame_at(clip, 2.0))
    b = (frame_at(clip, 0.5), frame_at(clip, 2.3))
    at = _session(clip, a).run()
    _button(at, "Check this selection").click().run()
    _confirm(at).check().run()
    assert not _button(at, "Analyze this selection").disabled

    at.slider(key="manual_review_slider").set_value(b).run()
    assert not [x for x in at.button if x.label == "Analyze this selection"], "a changed selection needs a new check"
    _button(at, "Check this selection").click().run()
    assert _confirm(at).value is False, "a tick for the old selection must not confirm the new one"
    assert _button(at, "Analyze this selection").disabled
    assert billing["charges"] == []


def test_uncertain_selection_can_continue_and_result_is_labelled_lower_confidence(billing):
    clip = make_clip(5, 30, [1.0, 2.7])          # second swing too close to call
    at = _session(clip, (0, len(clip) - 1)).run()
    _button(at, "Check this selection").click().run()
    out = _texts(at)
    assert "Not sure about this selection -- you can still continue." in out
    assert "second, separate movement" in out
    _confirm(at).check().run()
    _button(at, "Analyze this selection").click().run()
    out = _texts(at)
    assert "Analyzed exactly what you selected" in out
    assert "Lower confidence" in out
    assert len(billing["charges"]) == 1


def test_untracked_selection_is_blocked_and_free(billing):
    clip = make_clip(5, 30, [1.5], drop=(0.5, 3.5))
    at = _session(clip, (frame_at(clip, 0.8), frame_at(clip, 2.4))).run()
    _button(at, "Check this selection").click().run()
    out = _texts(at)
    assert "weren't tracked clearly enough" in out
    assert "Nothing was charged." in out
    assert not [b for b in at.button if b.label == "Analyze this selection"]
    assert billing["charges"] == []


def _tiny_jpeg() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (40, 40, 40)).save(buf, "JPEG")
    return buf.getvalue()


def test_server_set_markers_land_on_frames_the_player_can_show(billing):
    """Long clips only have a preview for every k-th frame and the player
    shows the nearest preview at or before a marker. Markers the SERVER sets
    (a 'Use movement' pick) must land on preview frames, or the player
    would show a different frame than the one checked (seen in the browser:
    server 159, player 158)."""
    clip = make_clip(6, 30, [1.2, 3.6])
    jpg = _tiny_jpeg()
    at = _session(clip, (0, len(clip) - 3))
    at.session_state["manual_review_previews"] = [(i, jpg) for i in range(0, len(clip), 3)]
    at.run()
    assert not at.exception, at.exception
    _button(at, "Check this selection").click().run()
    picks = _buttons(at, "Use movement ")
    assert len(picks) == 2
    for k in range(2):
        if k:
            at = _session(clip, (0, len(clip) - 3))
            at.session_state["manual_review_previews"] = [(i, jpg) for i in range(0, len(clip), 3)]
            at.run()
            _button(at, "Check this selection").click().run()
        _buttons(at, "Use movement ")[k].click().run()
        s, e = at.session_state["manual_review_selection"]
        assert s % 3 == 0 and e % 3 == 0, (k, s, e)
    assert billing["charges"] == []
