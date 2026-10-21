"""
BarrelLabs SwingAI — Subscription storage layer.

Thin DB-access layer for everything subscription / entitlements related.
This is the ONLY module that talks to Supabase for subscription state.
`entitlements.py` consumes the snapshots this module produces.

Public surface:
    load_my_plan()              -> dict | None   (reads v_my_plan)
    invalidate_my_plan_cache()  -> None          (drops Streamlit cache)
    increment_free_swing_count()-> int           (RPC)
    increment_free_swing_count_idempotent(key)
                                 -> int           (RPC, retry-safe)
    record_successful_swing_analysis(plan, key=None)
                                 -> bool          (the ONE call every analysis
                                                    pathway uses -- caller MUST
                                                    check the return value
                                                    before revealing results)
    redeem_beta_code(code)      -> dict          (RPC, raises ValueError)

Design rules:
  • Per-rerun caching via st.session_state so a single Streamlit page
    render doesn't fire N queries for the same plan info.
  • JWT-expiry errors flow through the same _flag_session_expired path
    the rest of the app uses, so the UI can render one clean
    "please log back in" banner.
  • RPC errors are translated into clean ValueError messages with
    user-facing copy — never raw Postgrest dumps to the screen.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from supabase_client import get_client


# Cache key used inside st.session_state. Lives for the life of the
# Streamlit session so we don't hit the DB on every widget interaction.
_CACHE_KEY = "_my_plan_snapshot"


# --------------------------------------------------------------------
#  Error helpers — mirror the player_storage.py conventions
# --------------------------------------------------------------------
def _is_jwt_expired_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("jwt expired" in msg) or ("pgrst303" in msg)


def _is_missing_function_error(exc: Exception) -> bool:
    """BL-008C.23: true only when the error means 'this RPC doesn't exist
    in this environment' (Postgres undefined_function 42883, or
    PostgREST's own 'no matching function' PGRST202) -- i.e. the
    increment_free_swing_usage_idempotent() migration hasn't been
    applied here yet. Used ONLY to decide whether falling back to the
    legacy, non-idempotent RPC is safe; never used for any other error,
    since a generic/transient error must NOT trigger a fallback call (a
    lost-ack retry falling back to a different, non-idempotent RPC would
    be exactly the double-charge bug this migration exists to prevent)."""
    msg = str(exc).lower()
    return ("pgrst202" in msg) or ("42883" in msg) or ("could not find the function" in msg)


def _flag_session_expired() -> None:
    try:
        st.session_state["_session_expired"] = True
    except Exception:
        pass


def _clean_rpc_error(exc: Exception) -> str:
    """
    Pull a human-readable message out of a Postgrest/Supabase RPC error.
    Falls back to a generic message so we never leak raw error noise.
    """
    msg = str(exc)
    # Postgrest wraps raised-by-Postgres exceptions with their message
    # in the body. Look for a couple of common shapes and surface them.
    for marker in ("message':", "message\":"):
        if marker in msg:
            try:
                tail = msg.split(marker, 1)[1].lstrip(" '\"")
                end = min(
                    (tail.find(ch) for ch in ("'", '"') if tail.find(ch) != -1),
                    default=len(tail),
                )
                clean = tail[:end].strip()
                if clean:
                    return clean
            except Exception:
                pass
    return msg.strip() or "Something went wrong. Please try again."


# --------------------------------------------------------------------
#  Plan snapshot — backed by the v_my_plan SQL view
# --------------------------------------------------------------------
def _query_my_plan() -> Optional[dict]:
    """
    Single-row read from the v_my_plan view. RLS scopes this to the
    current auth user, so this returns either one row or None.
    """
    sb = get_client()
    try:
        resp = sb.table("v_my_plan").select("*").limit(1).execute()
    except Exception as exc:
        if _is_jwt_expired_error(exc):
            _flag_session_expired()
            return None
        # Any other error: signal a READ FAILURE (not "no plan"). load_my_plan
        # keeps the last-known snapshot so a transient hiccup doesn't silently
        # downgrade a paying user mid-analyze. Surface once for visibility.
        try:
            st.warning(f"Could not load subscription info: {exc}")
        except Exception:
            pass
        return _QUERY_ERROR

    rows = resp.data or []
    if not rows:
        return None
    return rows[0]


def load_my_plan(force_refresh: bool = False) -> Optional[dict]:
    """
    Cached read of the current user's effective plan. Pass force_refresh=True
    after a state-changing operation (beta redemption, checkout completion,
    plan cancellation) to bypass the cache for one call.
    """
    if not force_refresh:
        cached = st.session_state.get(_CACHE_KEY, _SENTINEL)
        if cached is not _SENTINEL:
            return _apply_sponsorship(cached)

    snap = _query_my_plan()
    if snap is _QUERY_ERROR:
        # Transient read failure: keep the last-known plan rather than dropping
        # a payer to Free for this call. Falls through to Free only if we never
        # had a snapshot this session.
        cached = st.session_state.get(_CACHE_KEY, _SENTINEL)
        return _apply_sponsorship(cached if cached is not _SENTINEL else None)
    try:
        st.session_state[_CACHE_KEY] = snap
    except Exception:
        pass
    return _apply_sponsorship(snap)


def invalidate_my_plan_cache() -> None:
    """Drop the cached snapshot so the next load_my_plan() re-reads. Also drops
    the per-player facility-sponsorship cache so joining/leaving a facility
    re-resolves immediately."""
    try:
        if _CACHE_KEY in st.session_state:
            del st.session_state[_CACHE_KEY]
        for _k in [k for k in list(st.session_state.keys())
                   if str(k).startswith("_sponsored_")]:
            del st.session_state[_k]
    except Exception:
        pass


# Internal sentinel so we can distinguish "cached None" (logged-out user,
# no plan row) from "not cached yet". `None` is a valid cached value.
class _Sentinel:
    pass
_SENTINEL = _Sentinel()
# Distinct sentinel for "the plan read FAILED" (transient DB error), vs None
# which legitimately means "no active plan row -> Free". Lets load_my_plan keep
# the last-known plan on a transient error instead of dropping a payer to Free.
_QUERY_ERROR = _Sentinel()


# --------------------------------------------------------------------
#  Facility sponsorship overlay (Model B)
# --------------------------------------------------------------------
# A facility sponsors full Pro for every active rostered player. Sponsorship is
# per ACTIVE PLAYER (a family account may have one sponsored kid and one not),
# while the base plan snapshot is per ACCOUNT — so we resolve sponsorship on top
# of the (cached) account snapshot on every read, keyed to whoever is the active
# profile. The sponsorship boolean itself is cached per-player to avoid a DB hit
# on every can_X() check; invalidate_my_plan_cache() clears it (called after
# join/leave and on profile switch).
def _active_player_id():
    try:
        p = st.session_state.get("player") or st.session_state.get("user") or {}
        return p.get("id")
    except Exception:
        return None


def _is_active_player_sponsored() -> bool:
    pid = _active_player_id()
    if not pid:
        return False
    ck = f"_sponsored_{pid}"
    cached = st.session_state.get(ck, _SENTINEL)
    if cached is not _SENTINEL:
        return bool(cached)
    val = False
    try:
        import facility_storage
        val = bool(facility_storage.is_player_sponsored(pid))
    except Exception:
        val = False
    try:
        st.session_state[ck] = val
    except Exception:
        pass
    return val


def _apply_sponsorship(snap):
    """Layer the active player's facility sponsorship onto the account plan
    snapshot. Best-of own sub vs sponsored Pro (never downgrades a payer). Returns
    the snapshot unchanged when the player isn't sponsored."""
    try:
        if not _is_active_player_sponsored():
            return snap
        from entitlements import resolve_effective_plan
        eff = resolve_effective_plan(snap, sponsored=True)
        base = dict(snap or {})
        if base.get("plan_id") != eff:
            base["plan_id"] = eff
            base.setdefault("status", "active")
            base["sponsored"] = True
        return base
    except Exception:
        return snap


# --------------------------------------------------------------------
#  Free-swing usage counter
# --------------------------------------------------------------------
def increment_free_swing_count() -> int:
    """
    Atomically bump the user's free-swing counter and return the new total.
    Call this once per successful swing analysis for Free-tier users.
    The RPC is SECURITY DEFINER and uses auth.uid() server-side, so the
    user can't lie about whose counter to bump.

    Returns -1 on failure (so the caller can fail closed rather than
    granting unlimited swings on a transient DB hiccup).
    """
    sb = get_client()
    try:
        resp = sb.rpc("increment_free_swing_usage", {}).execute()
    except Exception as exc:
        if _is_jwt_expired_error(exc):
            _flag_session_expired()
            return -1
        try:
            st.warning(f"Could not update swing count: {exc}")
        except Exception:
            pass
        return -1

    val = resp.data
    # supabase-py returns the RPC return value as `data`. Our RPC returns
    # an int (the new counter value).
    if isinstance(val, int):
        new_count = val
    elif isinstance(val, list) and val and isinstance(val[0], (int, dict)):
        first = val[0]
        new_count = first if isinstance(first, int) else int(
            first.get("increment_free_swing_usage") or 0
        )
    else:
        try:
            new_count = int(val) if val is not None else 0
        except Exception:
            new_count = 0

    # Bust the cache so the next entitlement check sees the new counter.
    invalidate_my_plan_cache()
    return new_count


def increment_free_swing_count_idempotent(idempotency_key: str) -> int:
    """BL-008C.23 -- same contract as increment_free_swing_count() (bump
    the counter, return the new total, -1 on failure), but safe to call
    more than once for the SAME logical analysis attempt: the server-side
    ledger (swing_analysis_charges, see
    supabase/migrations/2026_10_21_idempotent_swing_usage.sql) guarantees
    a second call with the same idempotency_key is a no-op that returns
    the current (unchanged) count rather than incrementing again. This
    is what actually closes the "lost ack" gap a plain retry of
    increment_free_swing_count() cannot: that function has no way to
    tell a retried call apart from a brand-new one.

    Falls back to the legacy, non-idempotent RPC automatically if this
    environment hasn't had the migration applied yet (detected via
    _is_missing_function_error, never guessed) -- so this function is
    safe to call in every environment starting the moment this code
    ships, independent of when/whether the migration itself is applied.
    """
    sb = get_client()
    try:
        resp = sb.rpc("increment_free_swing_usage_idempotent", {"p_idempotency_key": idempotency_key}).execute()
    except Exception as exc:
        if _is_jwt_expired_error(exc):
            _flag_session_expired()
            return -1
        if _is_missing_function_error(exc):
            # Migration not applied here yet -- the legacy RPC has no
            # idempotency key, but it's still correct for the common,
            # non-retried case, and falling back here (BEFORE any charge
            # has been attempted under either name) cannot double-charge.
            return increment_free_swing_count()
        try:
            st.warning(f"Could not update swing count: {exc}")
        except Exception:
            pass
        return -1

    val = resp.data
    if isinstance(val, int):
        new_count = val
    elif isinstance(val, list) and val and isinstance(val[0], (int, dict)):
        first = val[0]
        new_count = first if isinstance(first, int) else int(
            first.get("increment_free_swing_usage_idempotent") or 0
        )
    else:
        try:
            new_count = int(val) if val is not None else 0
        except Exception:
            new_count = 0

    invalidate_my_plan_cache()
    return new_count


def record_successful_swing_analysis(plan_snapshot: Optional[dict], idempotency_key: Optional[str] = None) -> bool:
    """BL-008C.22/.23 -- the ONE shared post-success usage-recording call
    for EVERY swing-analysis pathway (the legacy automatic pipeline and
    the experimental manual-review pathway alike). Not a new counter
    system -- centralizes one call pattern so a second pathway reuses it
    instead of duplicating (or drifting from) it.

    BL-008C.23 CHANGE: this now returns a bool the caller MUST check
    before revealing/saving results -- True means "charged (or Pro,
    nothing to charge) -- safe to deliver the result", False means "the
    usage RPC failed -- do NOT deliver results for this attempt; show an
    error and let the user retry." The previous soft-fail-and-deliver-
    anyway behavior is exactly the bug BL-008C.23 was opened to close: it
    let a Free user get an uncounted analysis for free whenever the RPC
    hiccuped. Concealing a quota failure behind a warning while still
    delivering the result is no longer acceptable (explicit instruction).

    `idempotency_key`, when provided, routes through
    increment_free_swing_count_idempotent so a user-initiated retry of
    the SAME attempt (same key, e.g. re-clicking "Analyze" after a
    failure on the exact same selection) cannot double-charge even if an
    earlier attempt's success response was lost in transit. Callers that
    don't pass one fall back to the legacy, non-idempotent RPC -- still
    correct for a first attempt, just without retry protection.

    Call this ONLY once per attempt, from the code path that runs
    exactly once per click (inside the `if st.button(...)` block) --
    unchanged requirement from BL-008C.22.

    No-op (returns True) for Pro/unlimited accounts. Reports every
    failure to monitoring -- unlike before, a failure is no longer
    something the caller can silently proceed past."""
    from entitlements import is_pro
    if is_pro(plan_snapshot):
        return True
    try:
        if idempotency_key:
            bumped = increment_free_swing_count_idempotent(idempotency_key)
        else:
            bumped = increment_free_swing_count()
    except Exception as exc:
        try:
            from monitoring import capture
            capture(exc)
        except Exception:
            pass
        return False
    if bumped == -1:
        try:
            from monitoring import capture
            capture(RuntimeError("swing usage recording returned -1"))
        except Exception:
            pass
        return False
    return True


# --------------------------------------------------------------------
#  Beta code redemption
# --------------------------------------------------------------------
def redeem_beta_code(code: str) -> dict:
    """
    Redeem a beta/promo code for the currently logged-in user.

    Server-side validation (all enforced by the redeem_beta_code RPC):
      * code exists and is not expired
      * code hasn't hit its redemption cap
      * this user hasn't already redeemed this code
      * user doesn't have a conflicting active Stripe sub

    Returns the inserted/updated subscriptions row on success.
    Raises ValueError with a user-facing message on any validation failure.
    """
    code_norm = (code or "").strip()
    if not code_norm:
        raise ValueError("Please enter a beta code.")

    sb = get_client()
    try:
        resp = sb.rpc("redeem_beta_code", {"p_code": code_norm}).execute()
    except Exception as exc:
        if _is_jwt_expired_error(exc):
            _flag_session_expired()
            raise ValueError("Your session has expired — please sign in again.")
        raise ValueError(_clean_rpc_error(exc))

    payload = resp.data
    # The RPC returns a single subscriptions row (jsonb / record).
    if isinstance(payload, list):
        if not payload:
            raise ValueError("Code accepted but no subscription was returned.")
        row = payload[0]
    elif isinstance(payload, dict):
        row = payload
    else:
        raise ValueError("Unexpected response from beta code redemption.")

    invalidate_my_plan_cache()
    return row


__all__ = [
    "load_my_plan",
    "invalidate_my_plan_cache",
    "increment_free_swing_count",
    "increment_free_swing_count_idempotent",
    "record_successful_swing_analysis",
    "redeem_beta_code",
]
