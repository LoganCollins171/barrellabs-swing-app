"""BL-008C.32 -- the Experimental Manual Review path end to end, through the
REAL page code (manual_swing_review.render_manual_swing_review) in Streamlit's
own headless AppTest runner, with `ffprobe` genuinely ABSENT from PATH -- the
production condition that raised `FileNotFoundError: ... 'ffprobe'`. Real
decode (OpenCV), real pose extraction (MediaPipe), real interval check, real
analysis. Only the billing backend is faked, so charges can be counted.

Footage: a disposable copy of the repo's assets/sample_swing.mp4 placed in the
app's own upload dir (exactly where the real upload flow puts it), plus a
.mov-container stream copy of it when ffmpeg is available to make one (no
re-encode). The protected original's hash is checked before and after.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "assets" / "sample_swing.mp4"
UPLOAD_DIR = ROOT / "uploads_streamlit"
NO_FFPROBE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

APP = '''
import streamlit as st
import manual_swing_review as msr
msr.render_manual_swing_review({"slug": "e2e-player", "email": "e2e@example.test"})
'''


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def original_hash():
    before = _sha(ORIGINAL)
    yield before
    assert _sha(ORIGINAL) == before, "the protected original footage must never change"


@pytest.fixture
def no_ffprobe(monkeypatch):
    monkeypatch.setenv("PATH", NO_FFPROBE_PATH)
    assert shutil.which("ffprobe") is None, "test precondition: ffprobe must be unreachable"


@pytest.fixture
def billing(monkeypatch):
    """Fake billing backend: a Free account with allowance left; records
    every charge attempt and its idempotency key."""
    import manual_swing_review as msr
    state = {"charges": [], "fail_next": 0}

    def _record(plan, idempotency_key=None):
        state["charges"].append(idempotency_key)
        if state["fail_next"]:
            state["fail_next"] -= 1
            return False
        return True

    from entitlements import EntitlementResult
    monkeypatch.setattr(msr, "load_my_plan", lambda force_refresh=False: {"plan": "free", "free_swings_used": 0})
    monkeypatch.setattr(msr, "can_analyze_swing_for_account", lambda plan, email: EntitlementResult(allowed=True))
    monkeypatch.setattr(msr, "record_successful_swing_analysis", _record)
    return state


def _staged_copy(suffix: str) -> Path:
    """A disposable copy in the app's own upload dir, like the real flow."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    dst = UPLOAD_DIR / f"bl008c32-test_{uuid.uuid4().hex}_sample{suffix}"
    if suffix == ".mp4":
        shutil.copyfile(ORIGINAL, dst)
        return dst
    ffmpeg = shutil.which("ffmpeg", path=os.environ.get("_REAL_PATH") or None) or "/usr/local/bin/ffmpeg"
    if not os.path.exists(ffmpeg):
        pytest.skip("ffmpeg not available to build a .mov container copy")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(ORIGINAL), "-c", "copy", str(dst)],
                   check=True, timeout=60)
    return dst


def _app(video: Path):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(APP, default_timeout=180)
    at.session_state["manual_review_video_path"] = str(video)
    return at


def _texts(at) -> str:
    parts = []
    for coll in (at.markdown, at.success, at.error, at.warning, at.info, at.subheader):
        parts += [getattr(x, "value", "") or "" for x in coll]
    return "\n".join(str(p) for p in parts)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _confirm(at):
    """BL-008C.37: the confirmation checkbox is keyed to the exact selection."""
    return next(c for c in at.checkbox if str(c.key or "").startswith("manual_review_confirm_"))


@pytest.fixture(autouse=True)
def _real_streamlit_auth_modules(monkeypatch):
    """Other (stub-based) auth tests re-import these modules while a fake
    `streamlit` is installed, and the stub-bound copies stay in sys.modules.
    Drop them so this test's app imports the real ones; monkeypatch puts the
    previous entries back afterwards."""
    import sys
    for name in ("supabase_client", "session_persist", "auth_diag"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    from tests._stub_isolation import drop_stub_bound_modules
    drop_stub_bound_modules(monkeypatch)


@pytest.fixture(scope="module", autouse=True)
def _remember_real_path():
    os.environ["_REAL_PATH"] = os.environ.get("PATH", "")
    yield


@pytest.mark.parametrize("suffix", [".mp4", ".mov"])
def test_full_experimental_flow_without_ffprobe(suffix, original_hash, no_ffprobe, billing):
    video = _staged_copy(suffix)
    at = _app(video).run()

    # Decode succeeded with no ffprobe (the production failure), temp copy cleaned.
    assert not at.exception, at.exception
    assert "Could not process this video" not in _texts(at)
    assert "manual_review_frames" in at.session_state
    assert not video.exists(), "the decoded temp copy must be removed"

    # Honest timing: this clip runs ~54 fps by its own timestamps, not 30.
    fps = at.session_state["manual_review_fps"]
    assert fps is not None and 50 < fps < 61, fps

    at.slider(key="manual_review_slider").set_value((24, 56)).run()
    _button(at, "Check this selection").click().run()
    assert billing["charges"] == [], "checking a selection is free"
    _confirm(at).check().run()
    _button(at, "Analyze this selection").click().run()

    out = _texts(at)
    assert "Analyzed exactly what you selected: frames 24-56" in out
    # BL-008C.36: labels are each frame's REAL timestamp (this clip has uneven
    # frame timing, so frame/avg-fps would be off), never an assumed 30 fps.
    pts = at.session_state["manual_review_pts"]
    assert f"~{pts[24]:.2f}s to ~{pts[56]:.2f}s" in out
    assert "~0.83s" not in out, "never the fabricated-30-fps label"
    assert "EXPERIMENTAL" in out and "not automatically verified" in out
    assert len(billing["charges"]) == 1, "a successful experimental analysis charges exactly once"

    at.run()  # an unrelated rerun
    at.slider(key="manual_review_slider").set_value((24, 56)).run()
    assert len(billing["charges"]) == 1, "reruns must never charge again"


def test_decode_failure_shows_error_and_never_charges(original_hash, no_ffprobe, billing):
    UPLOAD_DIR.mkdir(exist_ok=True)
    bad = UPLOAD_DIR / f"bl008c32-test_{uuid.uuid4().hex}_corrupt.mov"
    bad.write_bytes(b"\x00\x00\x00\x18ftypqt  " + os.urandom(4096))
    at = _app(bad).run()

    assert not at.exception, at.exception
    assert "Could not process this video" in _texts(at)
    assert "manual_review_frames" not in at.session_state
    assert "ffprobe" not in _texts(at), "a missing ffprobe must never be the reported failure"
    assert billing["charges"] == []
    assert not bad.exists(), "the failed temp copy must be removed too"


def test_failed_charge_hides_result_and_retry_is_double_charge_safe(original_hash, no_ffprobe, billing):
    video = _staged_copy(".mp4")
    at = _app(video).run()
    at.slider(key="manual_review_slider").set_value((24, 56)).run()
    _button(at, "Check this selection").click().run()
    _confirm(at).check().run()

    billing["fail_next"] = 1
    _button(at, "Analyze this selection").click().run()
    assert "Analyzed exactly what you selected" not in _texts(at), \
        "results must not be revealed when the charge could not be recorded"
    assert "you will not be double-charged" in _texts(at).lower()

    _button(at, "Analyze this selection").click().run()
    assert "Analyzed exactly what you selected" in _texts(at)
    assert len(billing["charges"]) == 2 and billing["charges"][0] == billing["charges"][1], \
        "the retry of a failed charge must reuse the same idempotency key"


def test_whole_clip_without_a_clear_swing_warns_but_does_not_block(original_hash, no_ffprobe, billing):
    """BL-008C.37: sample_swing.mp4 actually shows a batter in his stance with
    no swing (reviewed frame by frame). The old check hard-blocked the whole
    clip as "more than one distinct movement"; the new one says it isn't
    sure, explains why, and lets an informed user continue. Checking is free
    and Analyze stays disabled until the user confirms."""
    video = _staged_copy(".mp4")
    at = _app(video).run()
    _button(at, "Check this selection").click().run()   # default = whole clip
    out = _texts(at)
    assert "more than one distinct movement" not in out
    assert "Not sure about this selection" in out
    assert _button(at, "Analyze this selection").disabled
    assert billing["charges"] == []


APP_WITH_AUTH_EVENTS = '''
import streamlit as st
import supabase_client
if st.session_state.pop("_e2e_logout", False):
    supabase_client.clear_session()
if st.session_state.pop("_e2e_dead_session", False):
    supabase_client.reset_local_session()
import manual_swing_review as msr
msr.render_manual_swing_review({"slug": "e2e-player", "email": "e2e@example.test"})
'''


@pytest.mark.parametrize("event", ["_e2e_logout", "_e2e_dead_session"])
def test_logout_or_dead_session_wipes_experimental_state_without_charging(
        event, original_hash, no_ffprobe, billing, monkeypatch):
    """Workstream C: a private experimental result must not survive a logout
    or a dead-session reset (the next person on this tab may be someone else),
    and neither event may charge anything."""
    import types
    import supabase_client
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(supabase_client, "_build_client", lambda: types.SimpleNamespace(
        auth=types.SimpleNamespace(set_session=lambda *a: None,
                                   admin=types.SimpleNamespace(sign_out=lambda *a: None))))
    video = _staged_copy(".mp4")
    at = AppTest.from_string(APP_WITH_AUTH_EVENTS, default_timeout=180)
    at.session_state["manual_review_video_path"] = str(video)
    at.session_state["supabase_session"] = {"access_token": "a", "refresh_token": "r", "user_id": "u1"}
    at.run()
    at.slider(key="manual_review_slider").set_value((24, 56)).run()
    _button(at, "Check this selection").click().run()
    _confirm(at).check().run()
    _button(at, "Analyze this selection").click().run()
    assert "Analyzed exactly what you selected" in _texts(at)
    assert len(billing["charges"]) == 1

    at.session_state[event] = True
    at.run()
    for private in ("manual_review_frames", "manual_review_final_result",
                    "manual_review_check_result", "manual_review_analysis_session_id",
                    "supabase_session"):
        assert private not in at.session_state, f"{private} survived {event}"
    assert "Analyzed exactly what you selected" not in _texts(at)
    assert "No video is ready" in "\n".join(w.value for w in at.warning)
    assert len(billing["charges"]) == 1, "auth events must never charge"
