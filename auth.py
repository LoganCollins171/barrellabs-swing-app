"""
Authentication facade.

Wraps Supabase sign_up / sign_in / sign_out so the rest of the app
never has to know which backend it's talking to. Returns the same
shape of profile dict the old file-based player_storage used, so
the UI code only needs a one-line swap.

Public API
----------
    sign_up(name, email, password, handedness, height_in=None, weight_lb=None)
        -> profile dict (after creating the auth user + players row)
    sign_in(email, password)
        -> profile dict (after fetching the players row for that user)
    sign_out()
        -> None
    current_profile()
        -> profile dict or None
"""

from datetime import datetime
from typing import Optional

import streamlit as st

from supabase_client import (
    get_client,
    store_session,
    clear_session,
    get_current_user,
)


# --------------------------------------------------------------------
#  Internal helpers
# --------------------------------------------------------------------
def _fetch_player_row(user_id: str) -> Optional[dict]:
    """Pull the players row for the given auth user. None if missing."""
    sb = get_client()
    try:
        resp = (
            sb.table("players")
              .select("*")
              .eq("user_id", user_id)
              .is_("removed_at", "null")          # never restore a removed profile
              .order("created_at", desc=False)    # deterministic: oldest = the owner
              .limit(1)
              .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception as exc:
        st.error(f"Failed to load profile: {exc}")
        return None


def _profile_from_row(row: dict) -> dict:
    """
    Translate a Supabase players row into the legacy 'profile' dict
    shape the rest of the app already expects. The UI was built
    against this shape; keep it stable so we don't have to touch
    every read site.
    """
    if not row:
        return {}
    return {
        # Identity
        "id":          row.get("id"),           # players.id (uuid)
        "user_id":     row.get("user_id"),      # auth.users.id (uuid)
        "slug":        row.get("id"),           # back-compat alias used by the UI
        "name":        row.get("name"),
        "email":       row.get("email"),
        "handedness":  row.get("handedness"),

        # Body / metadata
        "height_in":   row.get("height_in"),
        "weight_lb":   row.get("weight_lb"),
        "birth_year":  row.get("birth_year"),
        "team":        row.get("team"),
        "position":    row.get("position"),
        "throws":      row.get("throws"),
        "level":       row.get("level"),
        "primary_goal":row.get("primary_goal"),
        "profile_pic_path": row.get("profile_pic_path"),

        # MLB comp lock — slug of the reference this player is locked to,
        # set on first auto-picked swing so subsequent swings keep
        # comparing to the same MLB hitter instead of bouncing between
        # players (Trout one week, Judge the next).
        "locked_mlb_slug": row.get("locked_mlb_slug"),

        # Bookkeeping
        "created_at":  row.get("created_at"),
    }


# --------------------------------------------------------------------
#  Household / multi-profile helpers
# --------------------------------------------------------------------
def _current_user_id() -> Optional[str]:
    """The logged-in auth user id, or None."""
    user = get_current_user()
    return user.id if user else None


def _query_household_rows(user_id: str) -> list[dict]:
    """All players rows for an auth user (incl. removed). Thin DB wrapper
    so tests can stub it."""
    sb = get_client()
    try:
        resp = (sb.table("players").select("*")
                  .eq("user_id", user_id)
                  .order("created_at", desc=False).execute())
        return resp.data or []
    except Exception as exc:
        st.error(f"Failed to load household: {exc}")
        return []


def list_household_players(user_id: str) -> list[dict]:
    """Active (non-removed) profile dicts for a household, in the app's
    legacy profile shape."""
    if not user_id:
        return []
    rows = [r for r in _query_household_rows(user_id) if not r.get("removed_at")]
    return [_profile_from_row(r) for r in rows]


def set_active_player(player_id: str) -> bool:
    """Set the active profile for this session. Returns False (and does
    nothing) if player_id isn't one of the caller's own non-removed
    profiles — IDOR guard."""
    uid = _current_user_id()
    if not uid or not player_id:
        return False
    for r in _query_household_rows(uid):
        if r.get("id") == player_id and not r.get("removed_at"):
            st.session_state["player"] = _profile_from_row(r)
            # Mark that the active profile was an explicit choice this
            # session, so app.py's picker gate lets it through (the gate
            # ignores a player that was only auto-restored from the
            # Supabase session for a multi-profile household).
            st.session_state["_profile_picked"] = True
            return True
    return False


def needs_profile_pick() -> bool:
    """True when the household has >1 active profile and none is selected
    yet. With exactly 1 profile, auto-selects it and returns False (solo
    users never see a picker)."""
    if st.session_state.get("player"):
        return False
    uid = _current_user_id()
    if not uid:
        return False
    actives = [r for r in _query_household_rows(uid) if not r.get("removed_at")]
    if len(actives) == 1:
        st.session_state["player"] = _profile_from_row(actives[0])
        return False
    return len(actives) > 1


def current_household_seats() -> int:
    """Seat cap for the logged-in household's plan (default 1)."""
    try:
        sb = get_client()
        resp = sb.table("v_my_plan").select("seats").limit(1).execute()
        rows = resp.data or []
        return int(rows[0].get("seats") or 1) if rows else 1
    except Exception:
        return 1


def create_household_player(name: str, handedness: str = "RIGHT",
                            position: Optional[str] = None,
                            is_minor: bool = True,
                            birth_year=None,
                            guardian_consent: bool = False) -> dict:
    """Create a new profile under the household via the seat-capped RPC.
    Returns {ok, player?, error?}.

    COPPA: adding a MINOR requires the account owner to affirm they are the
    child's parent/legal guardian and consent to data collection
    (`guardian_consent`). We refuse to create a minor without it, and stamp a
    consent record (who/when/method) on the new child row for the audit trail.
    """
    if not (name or "").strip():
        return {"ok": False, "error": "Enter a name."}
    import datetime as _dt
    from analyzer import parse_birth_year, is_under_coppa_age
    # COPPA consent is only legally required for an actual under-13. Teens
    # (13-17) are minors but not COPPA-covered, so adding them needs no extra
    # affirmation.
    #
    # BL-004C fail-closed fix: is_under_coppa_age() returns None when the
    # birth year is missing or unparseable, and its own docstring is
    # explicit that None means "can't tell", never "old enough". The
    # previous version here did `bool(is_under_coppa_age(birth_year))`,
    # which silently evaluates a None result to False — i.e. "no birth
    # year" silently became "no consent needed", exactly backwards. A
    # comment claimed a fallback to the generic `is_minor` flag "so we
    # don't silently skip consent for a kid", but that fallback was never
    # actually implemented. It is now: when the birth year can't tell us
    # the age, we trust `is_minor` (default True — fail closed) instead of
    # silently treating "unknown" as "adult". Only an explicit
    # `is_minor=False` (the caller affirmatively asserting this profile is
    # not a minor — e.g. a coach or the household owner adding themselves)
    # skips requiring a birth year at all, so this doesn't force
    # unnecessary birth-year collection on genuinely adult profiles.
    by = parse_birth_year(birth_year)
    _age_minor_status = is_under_coppa_age(by)  # True / False / None
    if _age_minor_status is None:
        if is_minor:
            return {"ok": False, "error": (
                "Please enter this player's birth year — it's required to "
                "tell whether parental consent is needed before the "
                "profile can be created."
            )}
        _needs_consent = False
    else:
        _needs_consent = _age_minor_status
    if _needs_consent and not guardian_consent:
        return {"ok": False, "error": (
            "To add a player under 13 you must confirm you're their parent or "
            "legal guardian and consent to their data being collected."
        )}
    try:
        sb = get_client()
        resp = sb.rpc("create_household_player", {
            "p_name": name.strip(),
            "p_handedness": handedness,
            "p_position": position,
            "p_is_minor": is_minor,
        }).execute()
        data = resp.data
        row = data[0] if isinstance(data, list) and data else data
        # The RPC has no birth_year / consent params; stamp them via an
        # owner-scoped update on the new row (RLS already restricts this to the
        # household owner). birth_year keeps the child's first swing
        # age-bracketed; the guardian_consent_* fields are the COPPA record.
        # (`by` was already parsed above, for the consent gate.)
        update_fields = {}
        if by is not None:
            update_fields["birth_year"] = by
        if guardian_consent:
            update_fields["guardian_consent_at"] = _dt.datetime.utcnow().isoformat() + "Z"
            update_fields["guardian_consent_by"] = _current_user_id()
            update_fields["guardian_consent_method"] = "authenticated_account"
        if isinstance(row, dict) and update_fields and row.get("id"):
            try:
                upd = (sb.table("players")
                         .update(update_fields)
                         .eq("id", row["id"])
                         .execute())
                urows = upd.data or []
                if urows:
                    row = urows[0]
            except Exception:
                # Non-fatal: the player exists; birth year can still be set in
                # their settings. Don't fail the whole add over it.
                pass
        return {"ok": True, "player": _profile_from_row(row) if row else None}
    except Exception as exc:
        msg = str(exc)
        if "slots are in use" in msg:
            return {"ok": False, "error": "Your household is full — every profile slot is in use."}
        return {"ok": False, "error": msg}


def remove_household_player(player_id: str) -> dict:
    """Soft-remove a profile (set removed_at). Owner-scoped by RLS."""
    if not player_id:
        return {"ok": False, "error": "Missing id."}
    import datetime as _dt
    try:
        sb = get_client()
        sb.table("players").update(
            {"removed_at": _dt.datetime.utcnow().isoformat() + "Z"}
        ).eq("id", player_id).execute()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# --------------------------------------------------------------------
#  Signup
# --------------------------------------------------------------------
def sign_up(
    name: str,
    email: str,
    password: str,
    handedness: str,
    height_in=None,
    weight_lb=None,
    birth_year=None,
    terms_agreed: bool = False,
) -> dict:
    """
    Create a Supabase auth user and the matching players row.
    Raises ValueError on validation / duplicate-email errors so the
    UI can surface a clean st.error message.

    COPPA: the account owner must be 13+ (verified from birth_year) and must
    affirm the Terms/Privacy + their age (`terms_agreed`). Under-13 self-signup
    is blocked here — they must be added as a household player by a consenting
    parent/guardian. This is the server-side enforcement; the form mirrors it.
    """
    from analyzer import parse_birth_year, is_under_coppa_age

    name = (name or "").strip()
    email = (email or "").strip().lower()
    password = password or ""

    if not name:
        raise ValueError("Please enter your name.")
    if not email or "@" not in email:
        raise ValueError("Please enter a valid email address.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if handedness not in {"RIGHT", "LEFT"}:
        raise ValueError("Please choose right- or left-handed.")

    # COPPA age gate — require a valid birth year, then block under-13.
    by = parse_birth_year(birth_year)
    if by is None:
        raise ValueError("Please enter your birth year (e.g. 2009).")
    if is_under_coppa_age(by):
        raise ValueError(
            "BarrelLabs accounts have to be created by someone 13 or older. "
            "If you're under 13, ask a parent or guardian to make the account "
            "and add you as a player from their household."
        )
    if not terms_agreed:
        raise ValueError(
            "Please confirm you're 13+ and agree to the Terms & Privacy Policy."
        )

    sb = get_client()

    # 1. Create the auth user.
    try:
        auth_resp = sb.auth.sign_up({"email": email, "password": password})
    except Exception as exc:
        msg = str(exc)
        if "already registered" in msg.lower() or "already exists" in msg.lower():
            raise ValueError("An account with this email already exists.") from exc
        raise ValueError(f"Signup failed: {msg}") from exc

    user = getattr(auth_resp, "user", None)
    session = getattr(auth_resp, "session", None)
    if not user:
        raise ValueError("Signup did not return a user. Try again in a minute.")

    # 2. Persist the session so the next DB call is authenticated.
    if session:
        store_session(session)

    # 3. Insert the players row. Re-fetch the client so the auth header
    #    picks up the new session above.
    import datetime as _dt
    sb = get_client()
    try:
        insert_resp = (
            sb.table("players")
              .insert({
                  "user_id":    user.id,
                  "name":       name,
                  "email":      email,
                  "handedness": handedness,
                  "height_in":  int(height_in) if height_in else None,
                  "weight_lb":  int(weight_lb) if weight_lb else None,
                  "birth_year": by,
                  # COPPA: record that this 13+ owner affirmed age + agreed to
                  # the Terms/Privacy at signup (audit trail).
                  "account_terms_agreed_at": _dt.datetime.utcnow().isoformat() + "Z",
              })
              .execute()
        )
    except Exception as exc:
        raise ValueError(
            "Auth account created but profile row failed: "
            f"{exc}. You can try logging in — the profile may be created on next sign-in."
        ) from exc

    rows = insert_resp.data or []
    if not rows:
        raise ValueError("Profile row was not returned after insert.")

    profile = _profile_from_row(rows[0])
    st.session_state["player"] = profile
    try:
        import analytics
        analytics.track("signed_up", profile.get("user_id") or profile.get("id"))
    except Exception:
        pass
    return profile


# --------------------------------------------------------------------
#  Login
# --------------------------------------------------------------------
def sign_in(email: str, password: str) -> dict:
    """
    Log in with email + password. Returns the legacy-shaped profile dict.
    Raises ValueError on bad credentials.
    """
    email = (email or "").strip().lower()
    password = password or ""

    if not email or not password:
        raise ValueError("Please enter your email and password.")

    sb = get_client()

    try:
        auth_resp = sb.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        msg = str(exc).lower()
        if "invalid" in msg or "credentials" in msg:
            raise ValueError("Incorrect email or password.") from exc
        raise ValueError(f"Login failed: {exc}") from exc

    user = getattr(auth_resp, "user", None)
    session = getattr(auth_resp, "session", None)
    if not user or not session:
        raise ValueError("Login did not return a session. Try again.")

    store_session(session)

    row = _fetch_player_row(user.id)
    if not row:
        # The auth account exists but no players row — auto-recover by
        # creating a minimal one. Name = local-part of email so they at
        # least see a sensible default until they fill in their profile.
        sb = get_client()
        fallback_name = email.split("@", 1)[0].replace(".", " ").title() or "Player"
        insert_resp = (
            sb.table("players")
              .insert({
                  "user_id":    user.id,
                  "name":       fallback_name,
                  "email":      email,
                  "handedness": "RIGHT",
              })
              .execute()
        )
        rows = insert_resp.data or []
        row = rows[0] if rows else None

    profile = _profile_from_row(row)
    st.session_state["player"] = profile
    # A fresh login must re-evaluate the household picker, even if a stale
    # _profile_picked lingered in this browser tab's session.
    st.session_state.pop("_profile_picked", None)
    return profile


# --------------------------------------------------------------------
#  Logout
# --------------------------------------------------------------------
def sign_out():
    clear_session()


# --------------------------------------------------------------------
#  Read current profile
# --------------------------------------------------------------------
def current_profile() -> Optional[dict]:
    """
    Return the cached profile dict if we have one, otherwise fetch
    it from the database. None if no user is logged in.
    """
    cached = st.session_state.get("player")
    if cached:
        return cached

    user = get_current_user()
    if not user:
        return None

    row = _fetch_player_row(user.id)
    if not row:
        return None

    profile = _profile_from_row(row)
    st.session_state["player"] = profile
    return profile


# --------------------------------------------------------------------
#  Update profile
# --------------------------------------------------------------------
ALLOWED_PROFILE_UPDATES = {
    "name", "handedness", "height_in", "weight_lb", "birth_year",
    "team", "position", "throws", "level", "primary_goal",
    "profile_pic_path",
    "locked_mlb_slug",
}

# Columns whose migration is apply-gated: the code ships before the SQL
# runs against prod, so an UPDATE that names them can be rejected wholesale
# by PostgREST ("could not find the 'X' column"). update_profile strips
# these and retries once so the rest of the profile still saves. Drop a
# name from this set once its migration is confirmed applied everywhere.
_APPLY_GATED_PROFILE_COLUMNS = {"birth_year"}


# --------------------------------------------------------------------
#  Password reset flow
# --------------------------------------------------------------------
def request_password_reset(email: str, redirect_to: Optional[str] = None) -> None:
    """
    Ask Supabase to send a password-reset email. The email contains
    a magic link that brings the user back to `redirect_to` with a
    recovery token in the URL.

    We always answer with a generic success message in the UI — even
    if the email doesn't exist — so attackers can't use this endpoint
    to enumerate registered emails. Errors that aren't "user not found"
    are surfaced.
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("Please enter a valid email address.")

    sb = get_client()
    try:
        kwargs = {}
        if redirect_to:
            kwargs["options"] = {"redirect_to": redirect_to}
        sb.auth.reset_password_for_email(email, **kwargs)
    except Exception as exc:
        msg = str(exc).lower()
        # Silently swallow "user not found" — see docstring.
        if "user not found" in msg or "not registered" in msg:
            return
        raise ValueError(f"Could not send reset email: {exc}") from exc


def update_password(new_password: str) -> None:
    """
    Set a new password for the user who just clicked a reset link (or, from
    Settings, the currently logged-in user).

    Implementation note — why a direct REST call instead of
    `sb.auth.update_user(...)`:

    The Supabase client is a process-wide cached singleton whose ambient
    session (`st.session_state["supabase_session"]`) is re-applied, refreshed,
    and `set_session()`-ed on by `get_client()` on EVERY rerun. During password
    recovery that ambient session gets clobbered between the user landing on the
    reset screen and submitting the form, so `update_user()` would run against a
    client that no longer cleanly authorized the change — the call silently
    no-op'd server-side (verified in the auth logs: no PUT /user) while the UI
    reported success. We instead PUT /auth/v1/user directly with the recovery
    access token captured at link-consume time, and surface real HTTP errors
    instead of swallowing them.
    """
    new_password = new_password or ""
    if len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters.")

    # Prefer the dedicated recovery token; fall back to the generic session so
    # the same function also serves the change-password-while-logged-in path.
    rec = st.session_state.get("_recovery_session") or {}
    access_token = rec.get("access_token")
    refresh_token = rec.get("refresh_token")
    if not access_token:
        sess = st.session_state.get("supabase_session") or {}
        access_token = sess.get("access_token")
        refresh_token = refresh_token or sess.get("refresh_token")
    if not access_token:
        raise ValueError(
            "Your reset session has expired. Request a new reset email and "
            "try again."
        )

    import json as _json
    import urllib.request as _rq
    import urllib.error as _er

    cfg = st.secrets["supabase"]
    base = str(cfg["url"]).rstrip("/")
    apikey = cfg.get("publishable_key") or cfg.get("anon_key")

    req = _rq.Request(
        f"{base}/auth/v1/user",
        method="PUT",
        data=_json.dumps({"password": new_password}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "apikey": apikey,
            "Content-Type": "application/json",
        },
    )
    try:
        with _rq.urlopen(req, timeout=15) as resp:
            if resp.status not in (200, 201):
                raise ValueError(
                    f"Password update failed (HTTP {resp.status})."
                )
    except _er.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        low = (detail or "").lower()
        if exc.code in (401, 403):
            raise ValueError(
                "Your reset link has expired or was already used. Request a "
                "new reset email and try again."
            ) from exc
        if "different from the old" in low or ("same" in low and "password" in low):
            raise ValueError(
                "That's already your password — choose a different one."
            ) from exc
        if "weak" in low or "at least" in low:
            raise ValueError(
                "That password is too weak — try a longer one."
            ) from exc
        raise ValueError(
            f"Could not update password: {detail or ('HTTP ' + str(exc.code))}"
        ) from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not update password: {exc}") from exc

    # Success — the change is committed server-side. Plant the (still-valid)
    # recovery session as the live session so the user is logged straight in,
    # and clear the one-time recovery tokens.
    try:
        if refresh_token:
            sb = get_client()
            sb.auth.set_session(access_token, refresh_token)
            st.session_state["supabase_session"] = {
                "access_token":  access_token,
                "refresh_token": refresh_token,
            }
            try:
                from supabase_client import store_session
                store_session(getattr(sb.auth.get_session(), "session", None)
                              or sb.auth.get_session())
            except Exception:
                pass
    except Exception:
        # Even if re-planting the session fails, the password IS changed — the
        # user can sign in with it. Don't raise.
        pass
    st.session_state.pop("_recovery_session", None)


def consume_recovery_url(access_token: str, refresh_token: str) -> bool:
    """
    Called when the app loads with a recovery token in the URL hash.
    Plants the tokens on the client so the next update_password() call
    is properly authenticated as the recovering user.
    """
    if not access_token or not refresh_token:
        return False
    try:
        sb = get_client()
        sb.auth.set_session(access_token, refresh_token)
        st.session_state["supabase_session"] = {
            "access_token":  access_token,
            "refresh_token": refresh_token,
        }
        # Stash the recovery tokens under a DEDICATED key that nothing else
        # reads, refreshes, or set_session()s on. update_password() uses these
        # for a direct GoTrue call so the password change can't be lost when the
        # shared client's ambient `supabase_session` is clobbered across reruns.
        st.session_state["_recovery_session"] = {
            "access_token":  access_token,
            "refresh_token": refresh_token,
        }
        return True
    except Exception:
        return False


def consume_recovery_token_hash(token_hash: str) -> bool:
    """
    Called when the app loads with `?token_hash=...&type=recovery` in
    the URL query string. This is the SSR-style recovery flow where the
    email link points directly at our app with a token_hash query
    param (cleaner than the implicit/hash-fragment flow because
    Streamlit can read query params natively, no JS shim needed).

    We call verify_otp to exchange the token_hash for a real session,
    then plant that session on the client.
    """
    if not token_hash:
        return False
    try:
        sb = get_client()
        # supabase-py accepts a dict for verify_otp; the dict shape
        # matches the JS SDK's VerifyTokenHashParams.
        resp = sb.auth.verify_otp({
            "token_hash": token_hash,
            "type":       "recovery",
        })
        sess = getattr(resp, "session", None)
        if not sess:
            return False
        at = getattr(sess, "access_token",  None)
        rt = getattr(sess, "refresh_token", None)
        if not at or not rt:
            return False
        sb.auth.set_session(at, rt)
        st.session_state["supabase_session"] = {
            "access_token":  at,
            "refresh_token": rt,
        }
        # Dedicated recovery tokens for update_password() — see the note in
        # consume_recovery_url() above.
        st.session_state["_recovery_session"] = {
            "access_token":  at,
            "refresh_token": rt,
        }
        return True
    except Exception:
        return False


def update_profile(player_id: str, **fields) -> Optional[dict]:
    """
    Patch the current player's profile. Only fields in the whitelist
    are written.
    """
    safe = {k: v for k, v in fields.items() if k in ALLOWED_PROFILE_UPDATES}
    if not safe:
        return st.session_state.get("player")

    safe["updated_at"] = datetime.utcnow().isoformat()

    sb = get_client()

    def _run(payload: dict):
        return (
            sb.table("players")
              .update(payload)
              .eq("id", player_id)
              .execute()
        )

    try:
        resp = _run(safe)
    except Exception as exc:
        # An apply-gated column missing from the live schema makes PostgREST
        # reject the entire UPDATE. Strip those columns and retry once so a
        # plain profile save still works before the migration has run.
        msg = str(exc).lower()
        retry = {k: v for k, v in safe.items()
                 if k not in _APPLY_GATED_PROFILE_COLUMNS}
        schema_err = any(s in msg for s in
                         ("does not exist", "schema cache", "could not find"))
        if schema_err and retry != safe:
            resp = _run(retry)
        else:
            raise
    rows = resp.data or []
    if not rows:
        return None
    profile = _profile_from_row(rows[0])
    st.session_state["player"] = profile
    return profile


# --------------------------------------------------------------------
#  Email change
# --------------------------------------------------------------------
def request_email_change(new_email: str) -> str:
    """Request a change of the logged-in user's email address.

    Supabase sends a confirmation link to the user's CURRENT email
    address (and, if double-opt-in is enabled, also to the new one).
    The change only takes effect after the user clicks that link, so
    we return a human-readable status string the UI can show.

    Raises ValueError on validation or backend errors.
    """
    new_email = (new_email or "").strip().lower()
    if not new_email or "@" not in new_email:
        raise ValueError("Please enter a valid email address.")

    current = current_profile() or {}
    if (current.get("email") or "").lower() == new_email:
        raise ValueError("That is already your account email.")

    sb = get_client()
    try:
        sb.auth.update_user({"email": new_email})
    except Exception as exc:
        msg = str(exc).lower()
        if "already" in msg and ("registered" in msg or "exists" in msg or "use" in msg):
            raise ValueError(
                "That email is already attached to another BarrelLabs account."
            ) from exc
        raise ValueError(f"Could not start email change: {exc}") from exc

    # We do NOT update public.players.email here — that mirror happens
    # after the user confirms the change via the link. Until then, the
    # auth-side email update is in a "pending" state and the next
    # auth.refresh will surface the new email; we'll reconcile then.
    return (
        f"Confirmation link sent to {new_email}. "
        "Your new email becomes active once you click the link "
        "(check both inboxes — old and new)."
    )


def sync_email_after_confirm() -> Optional[str]:
    """After the user has confirmed an email change at the auth provider,
    pull the canonical email back down and mirror it to public.players.

    Call this on login + on the settings page open so the mirror is
    always fresh. Returns the new email (lower-cased) if a change was
    detected, otherwise None.
    """
    user = get_current_user()
    if not user:
        return None
    auth_email = (getattr(user, "email", None) or "").strip().lower()
    if not auth_email:
        return None

    cached = st.session_state.get("player") or {}
    if (cached.get("email") or "").lower() == auth_email:
        return None  # already in sync

    player_id = cached.get("id") or cached.get("slug")
    if not player_id:
        return None

    sb = get_client()
    try:
        resp = (
            sb.table("players")
              .update({"email": auth_email,
                       "updated_at": datetime.utcnow().isoformat()})
              .eq("id", player_id)
              .execute()
        )
        rows = resp.data or []
        if rows:
            st.session_state["player"] = _profile_from_row(rows[0])
            return auth_email
    except Exception:
        # The mirror is best-effort; the auth.users email is canonical
        # so a transient failure here doesn't lock the user out.
        return None
    return None


# --------------------------------------------------------------------
#  Account deletion
# --------------------------------------------------------------------
def delete_account(*, cancel_stripe: bool = True) -> dict:
    """Permanently delete the logged-in player's BarrelLabs account.

    What this DOES (in order, each step soft-fails so a partial wipe
    still cleans the user's most-sensitive data first):

    1. Cancel any active Stripe subscription so we don't keep billing
       a deleted user. (Skipped if cancel_stripe=False or Stripe isn't
       configured.)
    2. Delete every row from public.swings owned by this player.
    3. Delete the public.players row itself (the player profile).
    4. Delete the auth.users row via the delete_own_auth_user() RPC
       (SECURITY DEFINER, self-only). Every public table referencing
       auth.users is ON DELETE CASCADE, so this also reaps anything the
       RLS deletes above missed. No more orphaned auth/PII row.
    5. Sign the user out (clears the session token).

    Returns a status dict the UI can use to show what happened:
        {
          "stripe_cancelled":  bool,
          "swings_deleted":    int,
          "player_deleted":    bool,
          "auth_user_deleted": bool,
          "signed_out":        bool,
          "errors":            [str, ...],   # non-fatal per-step issues
        }
    """
    status = {
        "stripe_cancelled": False,
        "swings_deleted": 0,
        "player_deleted": False,
        "auth_user_deleted": False,
        "signed_out": False,
        "errors": [],
    }

    profile = st.session_state.get("player") or current_profile() or {}
    player_id = profile.get("id") or profile.get("slug")
    user_id   = profile.get("user_id")
    if not player_id:
        status["errors"].append("No active player profile to delete.")
        return status

    # 1. Stripe — cancel the subscription so we don't keep billing.
    if cancel_stripe:
        try:
            from stripe_client import cancel_active_subscription
            cancelled = cancel_active_subscription(user_id or player_id)
            status["stripe_cancelled"] = bool(cancelled)
        except ImportError:
            # No stripe module / not configured — skip silently.
            pass
        except Exception as exc:
            status["errors"].append(f"Stripe cancellation failed: {exc}")

    sb = get_client()

    # 2. Wipe swing records.
    try:
        del_swings = (
            sb.table("swings")
              .delete()
              .eq("player_id", player_id)
              .execute()
        )
        status["swings_deleted"] = len(del_swings.data or [])
    except Exception as exc:
        status["errors"].append(f"Could not delete swing records: {exc}")

    # 3. Delete the player row itself.
    try:
        del_player = (
            sb.table("players")
              .delete()
              .eq("id", player_id)
              .execute()
        )
        status["player_deleted"] = bool(del_player.data)
    except Exception as exc:
        status["errors"].append(f"Could not delete player row: {exc}")

    # 3b. Delete the auth.users row itself (closes the orphaned-PII gap). The
    # RPC is SECURITY DEFINER + self-only (auth.uid()), and cascades to every
    # public table that references the user, so this is the real "account gone".
    try:
        sb.rpc("delete_own_auth_user").execute()
        status["auth_user_deleted"] = True
    except Exception as exc:
        # Soft-fail: if the RPC isn't deployed yet or errors, the app-row
        # deletes above still wiped the user's data; the auth row just lingers
        # (the prior behavior). Surface it as a non-fatal note.
        status["errors"].append(f"Could not delete auth user: {exc}")

    # 4. Sign out. We do this even if earlier steps failed so the user
    # is at least logged out on this device.
    try:
        sign_out()
        # Wipe the cached profile and any in-progress swing state.
        for _k in ("player", "user", "view_swing_record", "view_swing_path",
                   "view_swing_report_id", "view", "page"):
            st.session_state.pop(_k, None)
        status["signed_out"] = True
    except Exception as exc:
        status["errors"].append(f"Sign-out failed: {exc}")

    return status
