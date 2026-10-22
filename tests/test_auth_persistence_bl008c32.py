"""BL-008C.32 -- persistent login: the contracts behind the real-browser fix.

Mocked-auth unit tests (no Supabase network, no real credentials). The
real-browser evidence for the same behavior lives in
scripts/auth_e2e/ (local Streamlit + Chrome, fake GoTrue backend)."""
from __future__ import annotations

import sys
import types


def _passthrough(*a, **k):
    if len(a) == 1 and callable(a[0]) and not k:
        return a[0]
    def inner(fn):
        return fn
    return inner


def _install(monkeypatch, emitted=None):
    ss = {}
    emitted = emitted if emitted is not None else []
    comps = types.SimpleNamespace(html=lambda markup, **k: emitted.append(markup))
    st_stub = types.SimpleNamespace(
        session_state=ss, cache_resource=_passthrough, cache_data=_passthrough,
        secrets={"supabase": {"url": "https://example.invalid", "publishable_key": "pk"}},
        context=types.SimpleNamespace(cookies={}),
        components=types.SimpleNamespace(v1=comps),
    )
    monkeypatch.setitem(sys.modules, "streamlit", st_stub)
    monkeypatch.setitem(sys.modules, "streamlit.components", st_stub.components)
    monkeypatch.setitem(sys.modules, "streamlit.components.v1", comps)
    for m in ("supabase_client", "session_persist"):
        sys.modules.pop(m, None)
    import supabase_client
    import session_persist
    return supabase_client, session_persist, ss, st_stub, emitted


# ------------------------------------------------- cookie bridge (re-apply + ack)

def _with_bridge(monkeypatch, sp, reports):
    """Fake the bridge component: records the args of every render and
    returns whatever the 'browser' last reported (reports[-1] or None)."""
    renders = []

    def _component(**kw):
        renders.append(kw)
        return reports[-1] if reports else None
    monkeypatch.setattr(sp, "_bridge_component", lambda: _component)
    return renders


def test_write_is_reapplied_every_run_until_the_browser_acks_it(monkeypatch):
    _, sp, ss, _, emitted = _install(monkeypatch)
    reports = []
    renders = _with_bridge(monkeypatch, sp, reports)
    sp.write_refresh_token("rt-1")            # emitted, then (say) lost to st.rerun()
    assert len(emitted) == 1 and "rt-1" in emitted[0]

    sp.bridge(); sp.bridge()                  # two later runs, no ack yet
    assert [r["op"] for r in renders] == ["write", "write"]
    assert all(r["target"] == "rt-1" for r in renders)

    nonce = ss[sp._NONCE_KEY]
    reports.append({"token": "rt-1", "op_id": ss[sp._OP_ID_KEY], "nonce": nonce})
    sp.bridge()                               # browser confirms -> acked
    assert ss[sp._ACKED_KEY] == ss[sp._OP_ID_KEY]
    sp.bridge()
    assert renders[-1]["op"] is None, "an acked write must never be re-sent (stale-tab overwrite)"


def test_latest_rotated_token_is_the_one_applied(monkeypatch):
    _, sp, ss, _, _ = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    sp.write_refresh_token("rt-1")
    sp.write_refresh_token("rt-2")
    sp.bridge()
    assert renders[-1]["op"] == "write" and renders[-1]["target"] == "rt-2"


def test_clear_supersedes_a_pending_write(monkeypatch):
    _, sp, ss, _, emitted = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    sp.write_refresh_token("rt-1")
    sp.clear_refresh_token()
    assert sp._TARGET_KEY not in ss and ss[sp._CLEAR_KEY] is True
    assert "m=0" in emitted[-1] and "e=null" in emitted[-1], "logout clear is unconditional"
    sp.bridge()
    assert renders[-1]["op"] == "clear" and renders[-1]["target"] is None
    assert renders[-1]["expect"] is None


def test_new_login_after_logout_cancels_pending_clear(monkeypatch):
    _, sp, ss, _, _ = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    sp.clear_refresh_token()
    sp.write_refresh_token("rt-new")
    sp.bridge()
    assert renders[-1]["op"] == "write" and renders[-1]["target"] == "rt-new"


def test_nothing_recorded_means_read_only_render(monkeypatch):
    _, sp, ss, _, emitted = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    assert sp.bridge() is None
    assert renders[-1]["op"] is None and emitted == []


def test_browser_report_is_the_token_source_on_community_cloud(monkeypatch):
    """Community Cloud's proxy strips request cookies: st.context.cookies is
    empty there, so the token must come from the bridge's browser report."""
    _, sp, ss, st_stub, _ = _install(monkeypatch)
    reports = []
    _with_bridge(monkeypatch, sp, reports)
    assert st_stub.context.cookies == {}
    sp.bridge()
    assert sp.read_refresh_token() is None and sp.restore_pending() is True
    reports.append({"token": "rt-from-browser", "op_id": 0, "nonce": ss[sp._NONCE_KEY]})
    sp.bridge()
    assert sp.read_refresh_token() == "rt-from-browser"
    assert sp.restore_pending() is False


def test_report_from_a_previous_identity_is_ignored(monkeypatch):
    """After logout the session state (incl. nonce) is wiped; a widget value
    the frontend re-sends from before must not be trusted."""
    _, sp, ss, _, _ = _install(monkeypatch)
    reports = [{"token": "old-users-rt", "op_id": 3, "nonce": "stale-nonce"}]
    _with_bridge(monkeypatch, sp, reports)
    assert sp.bridge() is None
    assert sp.read_refresh_token() is None


def test_restore_not_pending_after_logout_or_skip(monkeypatch):
    _, sp, ss, _, _ = _install(monkeypatch)
    _with_bridge(monkeypatch, sp, [])
    sp.bridge()
    assert sp.restore_pending() is True
    sp.skip_restore()
    assert sp.restore_pending() is False
    ss.pop(sp._SKIP_KEY)
    sp.clear_refresh_token()
    assert sp.restore_pending() is False, "just logged out: go straight to login, no 'restoring'"


def test_token_cannot_break_out_of_script_tag(monkeypatch):
    _, sp, _, _, emitted = _install(monkeypatch)
    sp.write_refresh_token("abc</script><script>alert(1)</script>")
    assert "</script><script>" not in emitted[0]


# -------------------------------------------------------- per-session client

def test_each_browser_session_gets_its_own_client_with_autorefresh_off(monkeypatch):
    sc, _, ss, st_stub, _ = _install(monkeypatch)
    made = []

    def _fake_create(url, key, options=None):
        made.append(options)
        return types.SimpleNamespace(tag=len(made))
    monkeypatch.setattr(sc, "create_client", _fake_create)

    a = sc._build_client()
    assert sc._build_client() is a, "same session must reuse its client"
    assert made[0].auto_refresh_token is False, \
        "the SDK's background refresh timer rotates tokens behind the app's back"

    st_stub.session_state = {}  # a different user's browser session
    sc.st.session_state = st_stub.session_state
    b = sc._build_client()
    assert b is not a, "two users must never share one auth client"


# ------------------------------------------------------------------- restore

def _client_with_refresh(fn):
    return types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=fn))


def test_transient_network_failure_keeps_cookie_and_flags_error(monkeypatch):
    sc, sp, ss, st_stub, emitted = _install(monkeypatch)
    st_stub.context.cookies["bl_session"] = "valid-rt"

    def _down(_rt):
        raise ConnectionError("[Errno 8] nodename nor servname provided")
    monkeypatch.setattr(sc, "_build_client", lambda: _client_with_refresh(_down))

    assert sc.restore_session_from_cookie() is False
    assert ss.get(sc.RESTORE_ERROR_KEY) is True
    assert sp._CLEAR_KEY not in ss, "a network blip must not delete a valid saved login"
    assert not any("max-age=0" in m for m in emitted)


def test_rejected_token_clears_cookie_without_error_flag(monkeypatch):
    sc, sp, ss, st_stub, emitted = _install(monkeypatch)
    st_stub.context.cookies["bl_session"] = "dead-rt"

    def _rejected(_rt):
        raise Exception("Invalid Refresh Token: Already Used")
    monkeypatch.setattr(sc, "_build_client", lambda: _client_with_refresh(_rejected))

    assert sc.restore_session_from_cookie() is False
    assert ss.get(sp._CLEAR_KEY) is True
    assert sc.RESTORE_ERROR_KEY not in ss


def test_successful_restore_records_rotated_token_for_cookie(monkeypatch):
    sc, sp, ss, st_stub, _ = _install(monkeypatch)
    st_stub.context.cookies["bl_session"] = "rt-old"
    new = types.SimpleNamespace(access_token="at", refresh_token="rt-rotated",
                                expires_at=None, user=None)
    monkeypatch.setattr(sc, "_build_client",
                        lambda: _client_with_refresh(lambda rt: types.SimpleNamespace(session=new)))

    assert sc.restore_session_from_cookie() is True
    assert ss["supabase_session"]["refresh_token"] == "rt-rotated"
    assert ss[sp._TARGET_KEY] == "rt-rotated", "the spent token must be replaced in the cookie"


# -------------------------------------------------------------------- logout

def test_logout_revokes_this_users_own_session_locally(monkeypatch):
    sc, sp, ss, _, _ = _install(monkeypatch)
    calls = []
    client = types.SimpleNamespace(auth=types.SimpleNamespace(
        set_session=lambda at, rt: None,
        admin=types.SimpleNamespace(sign_out=lambda jwt, scope: calls.append((jwt, scope)))))
    monkeypatch.setattr(sc, "_build_client", lambda: client)
    ss["supabase_session"] = {"access_token": "my-at", "refresh_token": "my-rt", "expires_at": None}

    sc.clear_session()
    assert calls == [("my-at", "local")]


def test_logout_wipes_private_analysis_state_but_keeps_ui_prefs(monkeypatch):
    sc, sp, ss, _, _ = _install(monkeypatch)
    monkeypatch.setattr(sc, "_build_client", lambda: types.SimpleNamespace(
        auth=types.SimpleNamespace(set_session=lambda *a: None,
                                   admin=types.SimpleNamespace(sign_out=lambda *a: None))))
    ss.update({
        "supabase_session": {"access_token": "a", "refresh_token": "r"},
        "manual_review_frames": ["private landmarks"],
        "manual_review_final_result": {"private": True},
        "manual_review_check_result": object(),
        "manual_review_analysis_session_id": "abc",
        "view_swing_record": {"id": 1},
        "player_settings_extras": {"x": 1},
        "some_future_private_key": 1,
        "_sb_client": object(),
        "page": "manual_review",
        "sidebar_collapsed": True,
    })
    sc.clear_session()
    leftover = {k for k in ss if k not in (sp._CLEAR_KEY, sp._OP_ID_KEY, sp._EXPECT_KEY)}
    assert leftover == {"page", "sidebar_collapsed"}
    assert ss[sp._CLEAR_KEY] is True


def test_logout_with_no_session_makes_no_network_call(monkeypatch):
    sc, _, ss, _, _ = _install(monkeypatch)
    monkeypatch.setattr(sc, "_build_client",
                        lambda: (_ for _ in ()).throw(AssertionError("no client needed")))
    sc.clear_session()  # must not raise


# ------------------------------------------------------- multi-tab / dead session

def test_refresh_falls_back_to_browser_cookie_token_of_same_user(monkeypatch):
    """Another tab rotated this session's token: the in-memory one is spent,
    the browser cookie holds the newest. Same user -> adopt it, no logout."""
    sc, sp, ss, _, _ = _install(monkeypatch)
    ss["supabase_session"] = {"access_token": "a", "refresh_token": "spent", "user_id": "u1"}
    ss[sp._BRIDGE_VALUE] = {"token": "newest", "op_id": 0, "nonce": "n"}
    new = types.SimpleNamespace(access_token="a2", refresh_token="newest-2", expires_at=None,
                                user=types.SimpleNamespace(id="u1", email=None))

    def _refresh(rt=None, refresh_token=None):
        rt = rt or refresh_token
        if rt == "spent":
            raise Exception("Invalid Refresh Token: Already Used")
        return types.SimpleNamespace(session=new)
    client = types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=_refresh))
    assert sc._refresh_stored_session(client) is True
    assert ss["supabase_session"]["refresh_token"] == "newest-2"


def test_refresh_never_adopts_a_different_users_cookie(monkeypatch):
    sc, sp, ss, _, _ = _install(monkeypatch)
    ss["supabase_session"] = {"access_token": "a", "refresh_token": "spent", "user_id": "alpha"}
    ss[sp._BRIDGE_VALUE] = {"token": "bravo-rt", "op_id": 0, "nonce": "n"}
    bravo = types.SimpleNamespace(access_token="b", refresh_token="b2", expires_at=None,
                                  user=types.SimpleNamespace(id="bravo", email=None))

    def _refresh(rt=None, refresh_token=None):
        rt = rt or refresh_token
        if rt == "spent":
            raise Exception("Invalid Refresh Token: Already Used")
        return types.SimpleNamespace(session=bravo)
    client = types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=_refresh))
    assert sc._refresh_stored_session(client) is False
    assert "supabase_session" not in ss


def test_dead_session_reset_keeps_the_cookie_and_bridge(monkeypatch):
    """A session that died on its own must not clear the shared cookie (it
    may hold another tab's newer login) and must keep the bridge's read
    state so the gate can re-restore immediately -- but must drop private
    data, since the cookie may belong to a different account."""
    sc, sp, ss, _, emitted = _install(monkeypatch)
    ss.update({"supabase_session": {"x": 1}, "user": {"id": "alpha"},
               "manual_review_frames": ["private"], "page": "dashboard",
               sp._BRIDGE_VALUE: {"token": "t", "op_id": 0, "nonce": "n"},
               sp._NONCE_KEY: "n", sp._TARGET_KEY: "stale-rt"})
    sc.reset_local_session()
    assert "user" not in ss and "manual_review_frames" not in ss and "supabase_session" not in ss
    assert sp._BRIDGE_VALUE not in ss and sp._NONCE_KEY not in ss, \
        "a fresh nonce makes the browser report the CURRENT cookie (another tab may have rotated it)"
    assert sp._TARGET_KEY not in ss, "a stale target must not be re-applied over a newer cookie"
    assert sp._CLEAR_KEY not in ss and emitted == []


def test_diag_events_cannot_carry_strings(monkeypatch):
    _install(monkeypatch)
    sys.modules.pop("auth_diag", None)
    import auth_diag
    assert auth_diag._safe({"token": "secret-value", "email": "a@b.c", "ok": True, "n": 3,
                            "x": None}) == "ok=True n=3 x=None"


# --------------------------------------------------------- compare-and-set

def test_rotation_writes_are_conditional_on_the_token_they_replace(monkeypatch):
    sc, sp, ss, st_stub, emitted = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    st_stub.context.cookies["bl_session"] = "rt-old"
    new = types.SimpleNamespace(access_token="at", refresh_token="rt-new",
                                expires_at=None, user=None)
    monkeypatch.setattr(sc, "_build_client",
                        lambda: _client_with_refresh(lambda rt: types.SimpleNamespace(session=new)))
    assert sc.restore_session_from_cookie() is True
    assert 'e="rt-old"' in emitted[-1] and 'var t="rt-new"' in emitted[-1]
    sp.bridge()
    assert renders[-1]["op"] == "write" and renders[-1]["expect"] == "rt-old"


def test_fresh_login_write_is_unconditional(monkeypatch):
    sc, sp, ss, _, emitted = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    sc.store_session(types.SimpleNamespace(access_token="a", refresh_token="login-rt",
                                           expires_at=None, user=None))
    sp.bridge()
    assert renders[-1]["expect"] is None and "e=null" in emitted[-1]


def test_rejected_token_clear_is_conditional_and_never_retried(monkeypatch):
    sc, sp, ss, st_stub, emitted = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    st_stub.context.cookies["bl_session"] = "dead-rt"
    calls = []

    def _rejected(rt):
        calls.append(rt)
        raise Exception("Invalid Refresh Token: Already Used")
    monkeypatch.setattr(sc, "_build_client", lambda: _client_with_refresh(_rejected))
    assert sc.restore_session_from_cookie() is False
    sp.bridge()
    assert renders[-1]["op"] == "clear" and renders[-1]["expect"] == "dead-rt"
    assert sp.restore_pending() is True, "wait to hear what the cookie holds; don't flash login"
    assert sp.read_refresh_token() is None
    assert sc.restore_session_from_cookie() is False and calls == ["dead-rt"], \
        "a refused token must not be retried every run"


def test_conditional_clear_superseded_by_newer_cookie_allows_restore(monkeypatch):
    """Another tab wrote a newer valid token: the browser declines the clear,
    reports the newer token, and the gate restores from it."""
    sc, sp, ss, st_stub, _ = _install(monkeypatch)
    reports = []
    _with_bridge(monkeypatch, sp, reports)
    sp.bridge()
    nonce = ss[sp._NONCE_KEY]
    reports.append({"token": "dead-rt", "op_id": 0, "nonce": nonce})
    sp.bridge()
    sp.note_rejected("dead-rt")
    sp.clear_refresh_token(expect="dead-rt")
    reports.append({"token": "newer-rt", "op_id": ss[sp._OP_ID_KEY], "nonce": nonce})
    sp.bridge()
    assert sp._CLEAR_KEY not in ss
    assert sp.read_refresh_token() == "newer-rt"
    assert sp.restore_pending() is False


def test_failed_in_session_refresh_requests_reauth_without_clearing_cookie(monkeypatch):
    sc, sp, ss, _, emitted = _install(monkeypatch)
    ss["supabase_session"] = {"access_token": "a", "refresh_token": "spent", "user_id": "u1"}

    def _rejected(rt=None, refresh_token=None):
        raise Exception("Invalid Refresh Token: Already Used")
    client = types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=_rejected))
    assert sc._refresh_stored_session(client) is False
    assert ss.get(sc.REAUTH_KEY) is True
    assert ss.get(sp._REJECTED_KEY) == "spent"
    assert sp._CLEAR_KEY not in ss and emitted == []


def test_op_recorded_after_bridge_rendered_forces_exactly_one_rerun(monkeypatch):
    """Browser E2E found the 'restoring' screen stuck after a rejected cookie:
    the conditional clear was recorded after the bridge rendered, so the
    browser never got to report. deliver_now() asks for one rerun per op."""
    _, sp, ss, _, _ = _install(monkeypatch)
    _with_bridge(monkeypatch, sp, [])
    sp.bridge()                                   # renders op_id 0
    assert sp.deliver_now() is False
    sp.clear_refresh_token(expect="dead")          # op recorded after render
    assert sp.deliver_now() is True
    assert sp.deliver_now() is False, "never loop if the browser doesn't report"
    sp.bridge()                                   # next run carries the op
    assert sp.deliver_now() is False


def test_bridge_only_asks_for_a_report_while_the_gate_needs_one(monkeypatch):
    """A browser report triggers a rerun; mid-session that cut short an
    in-flight 'Analyze swing' in the browser E2E. Report only while no user
    is signed in or a re-auth is pending."""
    _, sp, ss, _, _ = _install(monkeypatch)
    renders = _with_bridge(monkeypatch, sp, [])
    sp.bridge()
    assert renders[-1]["report"] is True          # signed out: gate needs it
    ss["user"] = {"id": "u1"}
    sp.write_refresh_token("rotated", expect="old")
    sp.bridge()
    assert renders[-1]["report"] is False         # signed in: never trigger reruns
    assert renders[-1]["op"] == "write"           # ...but still apply the write
    ss[sp.REAUTH_KEY] = True
    sp.bridge()
    assert renders[-1]["report"] is True          # dead session: gate needs it again
