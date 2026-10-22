"""BL-008C.32 -- regression tests for the reported "logged out whenever
refreshing the page" bug. The real cookie read/write plumbing was verified
separately with a real local browser round-trip (write -> full page reload
-> server-side st.context.cookies read); these tests isolate the higher
layer: restore_session_from_cookie()'s handling of the Supabase
refresh_session() call, including the already-rotated/single-use-token
race this task's investigation identified as the most likely real-world
cause (two sessions presenting the same stored cookie value -- two open
tabs, or a reload racing the previous rotation's async cookie write)."""
from __future__ import annotations

import sys
import types


def _passthrough(*a, **k):
    if len(a) == 1 and callable(a[0]) and not k:
        return a[0]
    def inner(fn):
        return fn
    return inner


def _install(monkeypatch):
    ss = {}
    st_stub = types.SimpleNamespace(
        session_state=ss,
        cache_resource=_passthrough,
        cache_data=_passthrough,
        secrets={},
    )
    monkeypatch.setitem(sys.modules, "streamlit", st_stub)
    sys.modules.pop("supabase_client", None)
    import supabase_client
    return supabase_client, ss


class _FakeSession:
    def __init__(self, access_token="new-at", refresh_token="new-rt", user=None):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = None
        self.user = user


class _FakeResp:
    def __init__(self, session):
        self.session = session


def _fake_session_persist(monkeypatch, sc, stored_token="stored-rt"):
    sp_stub = types.SimpleNamespace(
        read_refresh_token=lambda: stored_token,
        write_refresh_token=lambda _t: None,
        clear_refresh_token=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "session_persist", sp_stub)
    return sp_stub


def test_already_in_memory_session_short_circuits(monkeypatch):
    """If st.session_state already has a session, never touch the network
    or the cookie -- this is what makes repeated reruns within one browser
    session cheap and avoids re-triggering rotation needlessly."""
    sc, ss = _install(monkeypatch)
    ss["supabase_session"] = {"access_token": "x", "refresh_token": "y"}
    calls = []
    monkeypatch.setattr(sc, "_build_client", lambda: calls.append("built") or None)

    assert sc.restore_session_from_cookie() is True
    assert calls == [], "must not build a client when a session is already cached"


def test_no_cookie_returns_false_without_network_call(monkeypatch):
    sc, ss = _install(monkeypatch)
    _fake_session_persist(monkeypatch, sc, stored_token=None)
    assert sc.restore_session_from_cookie() is False
    assert "supabase_session" not in ss


def test_successful_refresh_restores_session_and_rewrites_cookie(monkeypatch):
    sc, ss = _install(monkeypatch)
    _fake_session_persist(monkeypatch, sc)
    fake_session = _FakeSession()
    client = types.SimpleNamespace(
        auth=types.SimpleNamespace(
            refresh_session=lambda refresh_token: _FakeResp(fake_session)))
    monkeypatch.setattr(sc, "_build_client", lambda: client)

    assert sc.restore_session_from_cookie() is True
    assert ss["supabase_session"]["access_token"] == "new-at"
    assert ss["supabase_session"]["refresh_token"] == "new-rt"


def test_already_rotated_single_use_token_fails_closed_to_login(monkeypatch):
    """BL-008C.32 root cause: a stored refresh token that was already
    consumed by a different session's rotation (e.g. a second open tab, or
    a reload that beat the prior rotation's cookie write) must be rejected
    -- Supabase itself raises for a reused single-use token. This must NOT
    raise out of restore_session_from_cookie(); it must cleanly fail
    closed so the app shows the ordinary login screen, not a crash."""
    sc, ss = _install(monkeypatch)
    sp_stub = _fake_session_persist(monkeypatch, sc, stored_token="already-rotated-rt")
    cleared = []
    sp_stub.clear_refresh_token = lambda: cleared.append(True)

    def _boom(refresh_token):
        raise Exception("invalid_grant: refresh_token_already_used")
    client = types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=_boom))
    monkeypatch.setattr(sc, "_build_client", lambda: client)

    assert sc.restore_session_from_cookie() is False
    assert "supabase_session" not in ss
    assert cleared == [True], "the dead cookie must be dropped, not retried forever"


def test_keyword_signature_fallback_is_tried(monkeypatch):
    """Some supabase-py versions reject the positional call; the function
    must fall back to the keyword form rather than giving up on the first
    TypeError."""
    sc, ss = _install(monkeypatch)
    _fake_session_persist(monkeypatch, sc)
    fake_session = _FakeSession()
    calls = []

    def _refresh(*a, **k):
        calls.append((a, k))
        if a:  # positional call -- simulate this version rejecting it
            raise TypeError("refresh_session() takes 1 positional argument")
        return _FakeResp(fake_session)

    client = types.SimpleNamespace(auth=types.SimpleNamespace(refresh_session=_refresh))
    monkeypatch.setattr(sc, "_build_client", lambda: client)

    assert sc.restore_session_from_cookie() is True
    assert len(calls) == 2, "must have tried the positional form, then the keyword form"


def test_malformed_response_with_no_usable_session_fails_closed(monkeypatch):
    sc, ss = _install(monkeypatch)
    sp_stub = _fake_session_persist(monkeypatch, sc)
    cleared = []
    sp_stub.clear_refresh_token = lambda: cleared.append(True)
    client = types.SimpleNamespace(
        auth=types.SimpleNamespace(refresh_session=lambda refresh_token: _FakeResp(None)))
    monkeypatch.setattr(sc, "_build_client", lambda: client)

    assert sc.restore_session_from_cookie() is False
    assert "supabase_session" not in ss
