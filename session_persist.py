"""Durable login — keep the user signed in across page reloads and new tabs.

The app's Supabase session normally lives only in st.session_state (in-memory),
so any full page reload (a refresh, or returning from Stripe Checkout in a new
tab) logs the user out. This module persists the Supabase *refresh token* in a
browser cookie so the session can be silently restored on the next load.

BL-008C.32 — why the original version never worked in production, and how
this one does:

* Reading. Streamlit Community Cloud's proxy strips almost all request cookies
  before they reach the app ("Streamlit Cloud filters some headers and almost
  all cookies in the proxy layer" — Streamlit staff, discuss.streamlit.io
  /t/80597), so `st.context.cookies` is empty there and the restore never ran.
  Production showed exactly that: five owner logins on 2026-10-22, zero
  refresh_token grants in Supabase's logs. With no proxy locally, every local
  test passed. The cookie is now read IN THE BROWSER by a small component
  (components/session_bridge) and handed to Python over the websocket, which
  the proxy does not filter. Reproduced and verified behind a local stand-in
  for that proxy (scripts/auth_e2e/cookie_stripping_proxy.py).

* Writing. Supabase refresh tokens are single-use: every refresh rotates the
  token, and every tab of the browser shares one cookie. A write is therefore
  compare-and-set: it carries the token it replaces (`expect`), and the browser
  applies it only if the cookie still holds that token, so a tab
  whose rotation is delivered late can never overwrite a newer token another
  tab wrote. A fresh login and an explicit logout are unconditional. Pending
  writes/clears are recorded in session_state and re-sent by bridge() until
  the browser acknowledges them, because Streamlit discards undelivered UI of a
  run cut short by st.rerun() (AppSession._clear_queue), which auth_screen does
  right after login.

SAFE BY DESIGN: every function swallows its own errors. If cookie persistence
fails for ANY reason, the app falls back to in-session-only behavior (the user
signs in again). Nothing here can break the existing auth.

Security note: the refresh token is stored in a JS-readable cookie. HttpOnly is
not achievable on this platform: Streamlit gives the app no control over HTTP
response headers (so the server can't set one), and Community Cloud's proxy
strips request cookies (so the server couldn't read one either). This is the
same trade-off the official supabase-js client makes (it persists the session
in localStorage). SameSite=Lax + Secure (on HTTPS) + host-only scope limit
where it is sent; the token is only useful to an attacker with an XSS hole in
this origin, and logout revokes it server-side (supabase_client.clear_session),
so a copied cookie stops working once the user signs out. The cookie holds
ONLY the refresh token — never the user's password or PII. The token is never
logged (see auth_diag) and never placed in a URL.
"""
from __future__ import annotations

import json
import os
import uuid

import streamlit as st
import streamlit.components.v1 as components

_COOKIE = "bl_session"
_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

_TARGET_KEY = "_bl_cookie_target"       # token the cookie should hold
_EXPECT_KEY = "_bl_cookie_expect"       # token it may replace (None = unconditional)
_CLEAR_KEY = "_bl_cookie_clear"         # a clear is pending
_OP_ID_KEY = "_bl_cookie_op_id"         # bumps on every write/clear request
_ACKED_KEY = "_bl_cookie_acked_op"      # last op the browser reported on
_BRIDGE_KEY = "_bl_session_bridge"      # component widget key
_BRIDGE_VALUE = "_bl_bridge_value"      # browser's latest report, this run
_NONCE_KEY = "_bl_bridge_nonce"         # per-browser-session render nonce
_SKIP_KEY = "_bl_restore_skipped"       # user chose "sign in instead"
_REJECTED_KEY = "_bl_rejected_token"    # last token Supabase refused
_RENDERED_OP_KEY = "_bl_rendered_op_id"  # op_id the bridge carried this run
_FORCED_OP_KEY = "_bl_forced_rerun_op"   # op_id we already reran once for
REAUTH_KEY = "_bl_reauth_needed"        # set by supabase_client on a dead session

# Bridge bookkeeping a local (non-logout) session reset keeps. The nonce and
# the last report are deliberately NOT kept: a fresh nonce makes the browser
# report the cookie again, so the gate decides on its CURRENT value (another
# tab may have rotated it) rather than a stale one -- and can't wait forever
# on a report the browser thinks it already sent.
BRIDGE_READ_KEYS = frozenset({
    _BRIDGE_KEY, _OP_ID_KEY, _ACKED_KEY, _REJECTED_KEY, _RENDERED_OP_KEY, _FORCED_OP_KEY,
})


def _diag(name, **fields):
    try:
        import auth_diag
        auth_diag.event(name, **fields)
    except Exception:
        pass


# --------------------------------------------------------------- immediate path
# The same compare-and-set instruction, emitted inline in the run that made the
# change: it lands at once if that run isn't cut short (e.g. a rotation in an
# otherwise idle tab), while bridge() covers the case where it is.
_JS_ATTRS = ("'; max-age='+m+'; path=/; SameSite=Lax'"
             "+(location.protocol==='https:'?'; Secure':'')")
_JS_READ = ("var c=null,p=document.cookie?document.cookie.split('; '):[];"
            "for(var i=0;i<p.length;i++){var q=p[i].indexOf('=');"
            "if(p[i].substring(0,q)==='" + _COOKIE + "'){c=decodeURIComponent(p[i].substring(q+1))||null;}}")


def _js(value) -> str:
    # json.dumps → a safe JS literal; replace </ so a value can never break
    # out of the <script> tag (defense-in-depth — tokens are URL-safe).
    return json.dumps(value).replace("</", "<\\/")


def _emit_write(target: str, expect: str | None) -> None:
    components.html(
        "<script>(function(){var t=" + _js(target) + ",e=" + _js(expect)
        + ",m=" + str(_MAX_AGE) + ";" + _JS_READ
        + "if(e===null||c===e||c===t){document.cookie='" + _COOKIE
        + "='+encodeURIComponent(t)+" + _JS_ATTRS + ";}})();</script>",
        height=0, width=0,
    )


def _emit_clear(expect: str | None) -> None:
    components.html(
        "<script>(function(){var e=" + _js(expect) + ",m=0;" + _JS_READ
        + "if(e===null||c===e){document.cookie='" + _COOKIE + "='+" + _JS_ATTRS
        + ";}})();</script>",
        height=0, width=0,
    )


# ------------------------------------------------------------------ public API
def write_refresh_token(refresh_token: str | None, expect: str | None = None) -> None:
    """Persist the refresh token to the browser cookie (best-effort, invisible).

    expect: the token this one rotated from. The browser applies the write
    only if the cookie still holds `expect`. None = unconditional
    (a fresh login)."""
    if not refresh_token:
        return
    try:
        st.session_state[_TARGET_KEY] = refresh_token
        st.session_state[_EXPECT_KEY] = expect
        st.session_state.pop(_CLEAR_KEY, None)
        st.session_state.pop(_SKIP_KEY, None)
        _bump_op()
        _emit_write(refresh_token, expect)
        _diag("cookie_write_emitted", conditional=expect is not None)
    except Exception:
        pass


def clear_refresh_token(expect: str | None = None) -> None:
    """Delete the persisted cookie. expect=None (explicit logout) clears
    unconditionally; otherwise only if the cookie still holds `expect` (a
    token Supabase just rejected) — another tab may have written a newer one."""
    try:
        st.session_state.pop(_TARGET_KEY, None)
        st.session_state[_EXPECT_KEY] = expect
        st.session_state[_CLEAR_KEY] = True
        _bump_op()
        _emit_clear(expect)
        _diag("cookie_clear_emitted", conditional=expect is not None)
    except Exception:
        pass


def note_rejected(token: str | None) -> None:
    """Remember a token Supabase refused, so the gate doesn't retry it."""
    if token:
        st.session_state[_REJECTED_KEY] = token


# ------------------------------------------------------------------ the bridge
_component_fn = None


def _bridge_component():
    global _component_fn
    if _component_fn is None:
        _component_fn = components.declare_component(
            "bl_session_bridge",
            path=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "components", "session_bridge"),
        )
    return _component_fn


def _bump_op() -> None:
    st.session_state[_OP_ID_KEY] = int(st.session_state.get(_OP_ID_KEY, 0)) + 1


def _pending_op():
    """(op, target, expect) still awaiting a browser report, or (None,)*3."""
    op_id = int(st.session_state.get(_OP_ID_KEY, 0))
    if st.session_state.get(_ACKED_KEY) == op_id:
        return None, None, None
    expect = st.session_state.get(_EXPECT_KEY)
    if st.session_state.get(_CLEAR_KEY):
        return "clear", None, expect
    if st.session_state.get(_TARGET_KEY):
        return "write", st.session_state[_TARGET_KEY], expect
    return None, None, None


def bridge() -> dict | None:
    """Render the bridge (call exactly once per run, at a fixed position near
    the top of the page, before the auth gate). Returns the browser's latest
    report {"token": str|None, "op_id": int, "nonce": str} — what the cookie
    holds AFTER applying any pending op — or None until the browser has
    reported in this browser session. Pending writes/clears are applied on
    every render; the browser only REPORTS while no user is signed in (or a
    re-auth is pending), so it never triggers a rerun mid-session."""
    try:
        nonce = st.session_state.get(_NONCE_KEY)
        if not nonce:
            nonce = uuid.uuid4().hex
            st.session_state[_NONCE_KEY] = nonce
        op_id = int(st.session_state.get(_OP_ID_KEY, 0))
        op, target, expect = _pending_op()
        st.session_state[_RENDERED_OP_KEY] = op_id
        # Ask the browser to report back only while the auth gate needs it.
        # A report triggers a rerun; mid-session that would cut short whatever
        # the user just started (found in the BL-008C.32 browser E2E: a token
        # rotation during "Analyze swing" lost the click after pose detection).
        report = ("user" not in st.session_state) or bool(st.session_state.get(REAUTH_KEY))
        val = _bridge_component()(
            nonce=nonce, op=op, target=target, expect=expect, op_id=op_id,
            max_age=_MAX_AGE, report=report, key=_BRIDGE_KEY, default=None,
        )
        if not isinstance(val, dict) or val.get("nonce") != nonce:
            val = None   # a report from before this session's state was reset
        st.session_state[_BRIDGE_VALUE] = val
        if val is not None and op and int(val.get("op_id", -1)) == op_id:
            st.session_state[_ACKED_KEY] = op_id
            tok = val.get("token")
            if op == "write":
                _diag("cookie_write_ack", landed=tok == target,
                      superseded=tok != target)
            else:
                _diag("cookie_clear_ack", landed=not tok, superseded=bool(tok))
                if tok:
                    # Another tab holds a newer login: nothing to clear after all.
                    st.session_state.pop(_CLEAR_KEY, None)
        return val
    except Exception:
        st.session_state[_BRIDGE_VALUE] = None
        return None


def browser_token() -> str | None:
    """Token the browser last reported through the bridge, if any."""
    val = st.session_state.get(_BRIDGE_VALUE)
    return (val or {}).get("token") or None


def read_refresh_token() -> str | None:
    """The saved refresh token for this browser, or None. Prefers what the
    bridge read in the browser (the only source that works on Community
    Cloud); falls back to the server-side request cookie, which works on hosts
    without that proxy (local dev). Never returns a token Supabase has already
    refused in this browser session."""
    tok = browser_token()
    if not tok:
        try:
            tok = st.context.cookies.get(_COOKIE) or None
        except Exception:
            tok = None
    if tok and tok == st.session_state.get(_REJECTED_KEY):
        return None
    return tok


def restore_pending() -> bool:
    """True while we can't yet tell whether this browser holds a usable saved
    login — the auth gate then shows a neutral 'restoring' state instead of
    flashing the login form (or app content)."""
    if st.session_state.get(_SKIP_KEY):
        return False
    op, _target, expect = _pending_op()
    if op == "clear":
        # Explicit logout: go straight to the login form. A conditional clear
        # (rejected token) waits for the browser to say what the cookie holds.
        return expect is not None
    if st.session_state.get(_BRIDGE_VALUE) is not None:
        return False
    try:
        if st.context.cookies.get(_COOKIE):
            return False
    except Exception:
        pass
    return True


def deliver_now() -> bool:
    """True (once per op) when a cookie op was recorded AFTER this run's bridge
    rendered — e.g. the gate's restore just rejected the saved token. The
    bridge only hears about it on the next run, and on the 'restoring' screen
    nothing else would trigger one, so the caller should st.rerun(). Guarded
    to one forced rerun per op, so a browser that never reports can't loop."""
    op_id = int(st.session_state.get(_OP_ID_KEY, 0))
    if op_id == st.session_state.get(_RENDERED_OP_KEY):
        return False
    if st.session_state.get(_FORCED_OP_KEY) == op_id:
        return False
    st.session_state[_FORCED_OP_KEY] = op_id
    return True


def skip_restore() -> None:
    st.session_state[_SKIP_KEY] = True
