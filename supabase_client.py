"""
Supabase client singleton + session helpers.

The Supabase client is created once and cached so every module shares
the same auth state. The auth tokens for the currently logged-in user
are kept in st.session_state["supabase_session"] and re-applied to the
client on every Streamlit rerun (Streamlit reloads the whole script on
every interaction, so this matters).

Usage
-----
    from supabase_client import get_client, get_current_user

    sb = get_client()
    user = get_current_user()
    if user:
        rows = sb.table("players").select("*").eq("user_id", user.id).execute()
"""

import time

import streamlit as st

try:
    from supabase import create_client, Client
except ImportError as exc:
    # Surface a useful error inside the app instead of a confusing
    # ModuleNotFoundError stack trace.
    raise RuntimeError(
        "supabase-py is not installed. Run:\n"
        "    ./venv/bin/pip install supabase\n"
        "and restart the app."
    ) from exc


# --------------------------------------------------------------------
#  Singleton client
# --------------------------------------------------------------------
_CLIENT_KEY = "_sb_client"


def _diag(name, **fields):
    try:
        import auth_diag
        auth_diag.event(name, **fields)
    except Exception:
        pass

RESTORE_ERROR_KEY = "_auth_restore_error"
REAUTH_KEY = "_bl_reauth_needed"

# Non-identity, non-private UI state that may outlive a logout (see
# clear_session). Everything else in st.session_state is dropped.
_SURVIVES_LOGOUT = frozenset({
    "page", "auth_mode", "sidebar_collapsed", "_legal_return_to",
    "use_dashboard_v2", "use_dashboard_v3", "_swr_css_loaded", "_fac_logo_h",
    "_pruned_stale_files",
})


def _new_client() -> "Client":
    cfg = st.secrets["supabase"]
    url = cfg["url"]
    # We support both the new "publishable_key" name and the legacy
    # "anon_key" name so existing deployments don't break.
    key = cfg.get("publishable_key") or cfg.get("anon_key")
    if not url or not key:
        raise RuntimeError(
            "Supabase credentials are missing. Add them to "
            ".streamlit/secrets.toml under [supabase]."
        )
    # BL-008C.32: auto_refresh_token=False. With it on (the SDK default),
    # every set_session()/sign-in arms a background Timer that rotates the
    # refresh token server-side ~1 min before the access token expires --
    # and re-arms itself forever -- without updating st.session_state or the
    # browser cookie, which are then holding a revoked token. This app
    # already refreshes explicitly (get_client -> _refresh_stored_session)
    # and records every rotation; the hidden timer only creates stale tokens.
    from supabase import ClientOptions
    return create_client(
        url, key,
        options=ClientOptions(auto_refresh_token=False, persist_session=False),
    )


def _build_client() -> "Client":
    """One Supabase client per browser session (BL-008C.32).

    Previously @st.cache_resource: ONE client shared by every user of the
    process, onto which each request planted its own tokens via set_session().
    Streamlit runs sessions on separate threads, so two users' reruns could
    interleave between one user's set_session() and their query (running it
    under the other user's JWT), and clear_session()'s sign_out() revoked
    whichever user's session happened to be planted last. A per-session
    client keeps each user's auth state in that user's own session."""
    client = st.session_state.get(_CLIENT_KEY)
    if client is None:
        client = _new_client()
        st.session_state[_CLIENT_KEY] = client
    return client


def _session_is_expired(session: dict, *, skew_seconds: int = 60) -> bool:
    """
    Return True if the cached session's access token is at/past its
    expiry (with a small skew so we refresh BEFORE Postgrest yells).
    A session without an expires_at falls through as "unknown" -> False
    so we don't refresh unnecessarily.
    """
    if not session:
        return False
    expires_at = session.get("expires_at")
    if expires_at is None:
        return False
    try:
        return time.time() >= (float(expires_at) - skew_seconds)
    except Exception:
        return False


def _refresh_stored_session(client) -> bool:
    """
    Try to mint a fresh access token from the stored refresh token.
    On success, overwrite st.session_state["supabase_session"] with the
    new tokens/expiry and return True. On failure, clear the session
    locally and return False (the next interaction will route the user
    back through the auth gate).
    """
    session = st.session_state.get("supabase_session")
    refresh_token = session.get("refresh_token") if session else None
    if not refresh_token:
        return False

    def _try(rt):
        # supabase-py's API has shifted across versions. Try the most
        # common signatures in order.
        for attempt in (
            lambda: client.auth.refresh_session(rt),
            lambda: client.auth.refresh_session(refresh_token=rt),
        ):
            try:
                resp = attempt()
                cand = getattr(resp, "session", None) or resp
                if cand and getattr(cand, "access_token", None):
                    return cand
            except TypeError:
                continue
            except Exception:
                return None
        return None

    new_session = _try(refresh_token)
    if not new_session:
        # BL-008C.32: another tab of this browser may have rotated this
        # session's token since this tab stored it (Supabase refresh tokens
        # are single-use). The browser's cookie holds the newest one -- try
        # it, but ONLY if it still belongs to the same user, so a different
        # account signed in from another tab can never silently take over
        # this tab's (private) state.
        try:
            import session_persist
            alt = session_persist.browser_token()
        except Exception:
            alt = None
        if alt and alt != refresh_token:
            cand = _try(alt)
            cand_uid = getattr(getattr(cand, "user", None), "id", None)
            if cand and cand_uid and cand_uid == session.get("user_id"):
                new_session = cand
                refresh_token = alt

    if not new_session:
        _diag("token_refresh", ok=False)
        try:
            import session_persist
            session_persist.note_rejected(refresh_token)
        except Exception:
            pass
        st.session_state.pop("supabase_session", None)
        st.session_state.pop("auth_user", None)
        # BL-008C.32: re-run the auth gate at the top of the next run (not
        # mid-page): it re-reads the browser's saved login, which another tab
        # may have rotated to a newer valid token, and shows the login form
        # only if there is none.
        st.session_state[REAUTH_KEY] = True
        # Go there now rather than finishing this run on a dead session.
        # (A no-op inside callbacks; the flag is handled on the next run.)
        _rerun = getattr(st, "rerun", None)
        if callable(_rerun):
            _rerun()
        return False
    _diag("token_refresh", ok=True)

    user_obj = getattr(new_session, "user", None)
    st.session_state["supabase_session"] = {
        "access_token":  getattr(new_session, "access_token", None),
        "refresh_token": getattr(new_session, "refresh_token", refresh_token),
        "expires_at":    getattr(new_session, "expires_at", None),
        "user_id":       getattr(user_obj, "id", None) if user_obj else session.get("user_id"),
        "email":         getattr(user_obj, "email", None) if user_obj else session.get("email"),
    }
    # Durable login: keep the persisted cookie in sync with the rotated refresh
    # token (Supabase rotates it on every refresh; the old one is single-use).
    try:
        import session_persist
        session_persist.write_refresh_token(
            st.session_state["supabase_session"].get("refresh_token"),
            expect=refresh_token)
    except Exception:
        pass
    # Invalidate the cached auth_user so the next get_current_user()
    # call picks up the refreshed identity.
    st.session_state.pop("auth_user", None)
    return True


def get_client() -> "Client":
    """
    Return the cached client with the current user's session re-applied.

    Streamlit reruns the script on every interaction, so the cached
    client may forget the user's auth tokens between runs. We persist
    the session in st.session_state and reattach it here. If the cached
    access token has already expired (Supabase's default TTL is 1 hour),
    we proactively swap it for a fresh one using the refresh token so
    PostgREST never sees a `JWT expired` error in the first place.
    """
    client = _build_client()

    session = st.session_state.get("supabase_session")
    if not session or not session.get("access_token") or not session.get("refresh_token"):
        return client

    # If the access token is already expired (or about to be), refresh
    # BEFORE planting it on the client.
    if _session_is_expired(session):
        if not _refresh_stored_session(client):
            # Refresh failed — the user is effectively logged out. The
            # next call to get_current_user() will return None and the
            # app will route to the auth gate.
            return client
        session = st.session_state.get("supabase_session") or {}

    try:
        client.auth.set_session(
            session["access_token"],
            session["refresh_token"],
        )
    except Exception:
        # set_session can throw if the access token is malformed or the
        # refresh token has been revoked. Try one refresh and one retry
        # before giving up.
        if _refresh_stored_session(client):
            session = st.session_state.get("supabase_session") or {}
            try:
                client.auth.set_session(
                    session["access_token"],
                    session["refresh_token"],
                )
            except Exception:
                st.session_state.pop("supabase_session", None)
                st.session_state.pop("auth_user", None)
        else:
            st.session_state.pop("supabase_session", None)
            st.session_state.pop("auth_user", None)
    return client


# --------------------------------------------------------------------
#  Session helpers
# --------------------------------------------------------------------
def store_session(session_obj, expect=None):
    """Persist a Supabase Session (returned from sign_in / sign_up)
    into st.session_state so subsequent reruns stay logged in."""
    if not session_obj:
        return
    st.session_state["supabase_session"] = {
        "access_token":  session_obj.access_token,
        "refresh_token": session_obj.refresh_token,
        "expires_at":    getattr(session_obj, "expires_at", None),
        "user_id":       getattr(session_obj.user, "id", None) if getattr(session_obj, "user", None) else None,
        "email":         getattr(session_obj.user, "email", None) if getattr(session_obj, "user", None) else None,
    }
    _diag("session_stored")
    # Durable login: persist the refresh token to a browser cookie so a reload
    # or a new tab (e.g. returning from Stripe) keeps the user signed in.
    # Best-effort — never let cookie persistence break the in-memory session.
    try:
        import session_persist
        # expect: the token this session was restored/rotated from (a
        # compare-and-set write, see session_persist); None for a fresh login.
        session_persist.write_refresh_token(getattr(session_obj, "refresh_token", None),
                                            expect=expect)
    except Exception:
        pass


def clear_session():
    """Sign the user out — drop their tokens AND every per-identity cache so a
    second login on a shared device (a family iPad) never inherits the prior
    user's profile, plan, or swing-usage snapshot."""
    # BL-008C.32: revoke THIS browser's session server-side first, using this
    # user's own access token (get_client() refreshes it if it has expired).
    # Once revoked, the refresh token in the cookie is dead even if the
    # cookie-clear instruction below never reaches the browser, so logout
    # can't be undone by a reload. scope="local" ends this device's session
    # only; the user's other devices stay signed in.
    _revoked = False
    try:
        if st.session_state.get("supabase_session"):
            _client = get_client()
            _at = (st.session_state.get("supabase_session") or {}).get("access_token")
            if _at:
                _client.auth.admin.sign_out(_at, "local")
                _revoked = True
    except Exception:
        pass
    _diag("logout", revoked=_revoked)
    # BL-008C.32: wipe EVERYTHING except an explicit allowlist of non-private
    # UI preferences. The old denylist (tokens, profile, _my_plan*) missed
    # private analysis state -- manual_review_frames / _final_result /
    # _check_result, view_swing_record, player_settings_extras, upload state
    # -- so the next person to sign in on the same tab inherited the previous
    # user's in-progress experimental review. An allowlist can't silently miss
    # a key added later.
    for _k in list(st.session_state.keys()):
        if str(_k) in _SURVIVES_LOGOUT:
            continue
        try:
            st.session_state.pop(_k, None)
        except Exception:
            pass
    # Durable login: drop the persisted cookie so the next load doesn't silently
    # sign them back in.
    try:
        import session_persist
        session_persist.clear_refresh_token()
    except Exception:
        pass




def reset_local_session():
    """BL-008C.32: forget this browser tab's identity and private state
    WITHOUT revoking anything or touching the saved-login cookie. For a
    session that died on its own (token rejected, revoked elsewhere) -- as
    opposed to an explicit logout (clear_session). The cookie may hold a
    NEWER valid login written by another tab; the auth gate re-tries it on
    the next run, and only that restore (which knows the cookie's own token
    was rejected) clears it."""
    try:
        from session_persist import BRIDGE_READ_KEYS as _keep
    except Exception:
        _keep = frozenset()
    for _k in list(st.session_state.keys()):
        if str(_k) in _SURVIVES_LOGOUT or str(_k) in _keep:
            continue
        try:
            st.session_state.pop(_k, None)
        except Exception:
            pass
    _diag("local_session_reset")


def _is_auth_error(exc: Exception) -> bool:
    """Best-effort: is this exception an actual auth failure (vs. a
    transient network / 5xx hiccup)?

    Auth failures → clear the session and bounce to login.
    Transient errors → leave session intact, return the cached user
    if we have one, so a flaky network during a long upload doesn't
    kick a logged-in user to login with no warning.
    """
    msg = str(exc).lower()
    return (
        "jwt" in msg
        or "invalid_grant" in msg
        or "invalid token" in msg
        or "invalid refresh" in msg
        or "refresh token" in msg
        or "unauthor" in msg            # "unauthorized"
        or "user not found" in msg
        or "user_not_found" in msg
        or "session_not_found" in msg
        or "auth session missing" in msg
    )


def restore_session_from_cookie() -> bool:
    """Durable login: if there's no in-memory session, try to rebuild one from
    the persisted refresh-token cookie. Returns True if a session was restored.

    The ordinary-refresh logout was NOT a failure in here: the cookie never
    reached the browser (see session_persist's _TARGET_KEY note), so this
    returned False at "no refresh token" on every reload. Production
    evidence: zero refresh_token grants in Supabase's logs across five
    owner logins on 2026-10-22. A rejected token (expired, revoked by
    logout, or already rotated by a concurrent tab past Supabase's reuse
    interval) still fails closed here: the dead cookie is cleared and the
    normal login screen shows.

    SAFE: any failure returns False and (for a rejected token) drops the cookie,
    so the app cleanly falls back to the auth gate. Never raises.
    """
    if st.session_state.get("supabase_session"):
        return True
    try:
        import session_persist
        refresh_token = session_persist.read_refresh_token()
    except Exception:
        return False
    _diag("restore_attempt", cookie_present=bool(refresh_token))
    if not refresh_token:
        return False
    st.session_state.pop(RESTORE_ERROR_KEY, None)
    try:
        client = _build_client()
        try:
            resp = client.auth.refresh_session(refresh_token)
        except TypeError:
            # supabase-py's API has shifted across versions (the same reason
            # _refresh_stored_session hedges); 2.30 takes it positionally.
            resp = client.auth.refresh_session(refresh_token=refresh_token)
        session = getattr(resp, "session", None)
        if not session or not getattr(session, "access_token", None):
            raise RuntimeError("invalid refresh token: no session returned")
    except Exception as exc:
        _diag("restore_result", restored=False, rejected=_is_auth_error(exc))
        if _is_auth_error(exc):
            # Expired / revoked / already-rotated token: permanently dead, so
            # drop it from the cookie -- but only if the cookie still holds
            # THIS token (another tab may have written a newer one), and never
            # retry it in this browser session.
            try:
                import session_persist
                session_persist.note_rejected(refresh_token)
                session_persist.clear_refresh_token(expect=refresh_token)
            except Exception:
                pass
        else:
            # Network / 5xx: the cookie may still be perfectly valid. Keep it,
            # and let the auth gate say sign-in couldn't be checked instead of
            # silently presenting an ordinary logged-out screen.
            st.session_state[RESTORE_ERROR_KEY] = True
        return False
    # store_session repopulates st.session_state AND records the rotated
    # refresh token for the cookie (the old one is now single-use-spent).
    store_session(session, expect=refresh_token)
    _diag("restore_result", restored=True)
    return True


def get_current_user():
    """
    Return the currently authenticated Supabase user object, or None.

    Pulls fresh user info from the API on first call per rerun, then
    caches it on st.session_state["auth_user"] for the rest of the run.

    Defensive: never clear the session on a transient error. A network
    blip during a 30-60s pose-detection upload was bouncing logged-in
    users straight back to login with no warning. Only clear when the
    error is unambiguously an auth failure.
    """
    if "auth_user" in st.session_state:
        cached = st.session_state["auth_user"]
        if cached:
            return cached
        # Cached value is None — fall through and try once more rather
        # than returning a stale "not logged in" answer.

    session = st.session_state.get("supabase_session")
    if not session:
        return None

    try:
        client = get_client()
        resp = client.auth.get_user()
        user = getattr(resp, "user", None)
        if user:
            # Only cache truthy users. Caching None creates a sticky
            # "logged out" state across reruns even if the very next
            # call would have succeeded.
            st.session_state["auth_user"] = user
        return user
    except Exception as exc:
        if _is_auth_error(exc):
            # Real auth failure — purge and bounce them back to the gate.
            # BL-008C.32: reset, not clear_session(): this session is already
            # dead server-side, and the saved-login cookie may now belong to a
            # newer login from another tab -- the gate decides.
            reset_local_session()
            return None
        # Transient (network/5xx/etc): keep the session intact and
        # return whatever's cached. Worst case the user sees one stale
        # rerun, then the next call succeeds.
        return st.session_state.get("auth_user")


def is_logged_in() -> bool:
    return get_current_user() is not None
