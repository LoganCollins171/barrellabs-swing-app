"""
BarrelLabs SwingAI — Subscription storage layer.

Thin DB-access layer for everything subscription / entitlements related.
This is the ONLY module that talks to Supabase for subscription state.
`entitlements.py` consumes the snapshots this module produces.

Public surface:
    load_my_plan()              -> dict | None   (reads v_my_plan)
    invalidate_my_plan_cache()  -> None          (drops Streamlit cache)
    increment_free_swing_count()-> int           (RPC)
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


def record_successful_swing_analysis(plan_snapshot: Optional[dict]) -> None:
    """BL-008C.22 -- the ONE shared post-success usage-recording call for
    EVERY swing-analysis pathway (the legacy automatic pipeline and the
    experimental manual-review pathway alike). Not a new counter system --
    this just centralizes the exact soft-fail call pattern app.py already
    used inline, so a second pathway can reuse it instead of duplicating
    it (or, worse, drifting from it).

    Call this ONLY once, and ONLY from the code path that runs exactly
    once per successful analysis click (i.e. inside the `if
    st.button(...)` block, never from a plain rerun or a page-load check)
    -- callers are responsible for that placement; this function itself
    has no rerun/idempotency guard of its own, matching the existing
    increment_free_swing_count() contract it wraps.

    No-op for Pro/unlimited accounts (increment_free_swing_usage would be
    meaningless for them and the RPC's own counter is Free-tier-scoped
    anyway). Never raises -- a transient DB hiccup here must not turn a
    successful analysis into a visible error for the user; the RPC's own
    -1-return + capture() path already reports failures to ops."""
    from entitlements import is_pro
    if is_pro(plan_snapshot):
        return
    try:
        bumped = increment_free_swing_count()
        if bumped == -1:
            try:
                from monitoring import capture
                capture(RuntimeError("increment_free_swing_count returned -1"))
            except Exception:
                pass
    except Exception as exc:
        try:
            from monitoring import capture
            capture(exc)
        except Exception:
            pass


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
    "redeem_beta_code",
]
