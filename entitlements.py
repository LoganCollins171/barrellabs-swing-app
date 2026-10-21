"""
BarrelLabs SwingAI — Entitlements

Single source of truth for every "can this user do X?" question. Every
feature gate in the app should call into this module so we never end
up with inconsistent paywalls across pages.

Design rules:
  • Plan-level capabilities live in PLAN_CAPS (a flat dict-of-dicts).
  • Per-user state (which plan, usage counters) comes from
    subscription_storage.load_my_plan() — this module never talks to
    Supabase directly.
  • Every public function returns either a bool or an EntitlementResult
    so call sites can render meaningful upgrade prompts.

Usage:
    from entitlements import can_analyze_swing, can_save_drill_plan
    ok, reason, upgrade_to = can_analyze_swing()
    if not ok:
        show_paywall(reason, upgrade_to)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


FREE_PLAN_ID    = "free"
SOLO_PLAN_ID    = "solo_pro"
FAMILY_PLAN_ID  = "family_pro"
COACH_PLAN_ID   = "coach_pro"

PRO_PLANS = {SOLO_PLAN_ID, FAMILY_PLAN_ID, COACH_PLAN_ID}

# How many lifetime swings a Free user can run before hitting the wall.
FREE_SWING_LIMIT = 3


# --------------------------------------------------------------------
#  PLAN CAPABILITIES — the canonical capability table
# --------------------------------------------------------------------
# Every gated feature has a key here. Adding a new gate = adding a new
# key here + the corresponding can_X() function below.
#
# Keep this dict POD (plain old data) — no callables. The gating logic
# lives in can_X() so it's testable in isolation.
PLAN_CAPS = {
    FREE_PLAN_ID: {
        "swing_limit_lifetime":   FREE_SWING_LIMIT,   # int or None for unlimited
        "drill_plan":             False,
        "video_storage":          False,
        "development_tracker":    False,
        "rewards":                False,
        "pdf_export":             False,
        "compare_swings":         False,
        "mlb_comp_library":       "basic",            # 'basic' | 'full'
        "support_priority":       "standard",
    },
    SOLO_PLAN_ID: {
        "swing_limit_lifetime":   None,
        "drill_plan":             True,
        "video_storage":          True,
        "development_tracker":    True,
        "rewards":                True,
        "pdf_export":             True,
        "compare_swings":         True,
        "mlb_comp_library":       "full",
        "support_priority":       "standard",
    },
}
# Family + Coach share Solo's caps (just different seat counts on the sub).
PLAN_CAPS[FAMILY_PLAN_ID] = dict(PLAN_CAPS[SOLO_PLAN_ID])
PLAN_CAPS[COACH_PLAN_ID]  = dict(PLAN_CAPS[SOLO_PLAN_ID])


# --------------------------------------------------------------------
#  FACILITY / ACADEMY TIERS — roster-size brackets (spec §6)
# --------------------------------------------------------------------
# A facility SPONSORS full Pro for every active rostered player. The tier
# only sets the roster ceiling (and price, in plan_pricing.py); the caps a
# sponsored player gets are SOLO's (full Pro). `coach_pro` is kept as a
# deprecated alias mapping onto the `academy` tier so old references resolve.
FACILITY_TIERS = {
    "team":          {"roster_ceiling": 25},
    "academy":       {"roster_ceiling": 100},
    "academy_plus":  {"roster_ceiling": 250},
    "facility":      {"roster_ceiling": 500},
    "facility_pro":  {"roster_ceiling": 1000},
}
# Deprecated 20-seat coach_pro → smallest facility bracket that covers it.
FACILITY_TIER_ALIASES = {"coach_pro": "academy"}


def facility_tier_for_roster(n_players: int) -> str:
    """Smallest facility tier whose ceiling covers n_players. Above 1000 we
    return 'facility_pro' (the checkout routes 1000+ to a custom quote)."""
    for tier in ("team", "academy", "academy_plus", "facility", "facility_pro"):
        if n_players <= FACILITY_TIERS[tier]["roster_ceiling"]:
            return tier
    return "facility_pro"


def facility_roster_ceiling(tier: str) -> int:
    """Roster ceiling for a facility tier (accepts the coach_pro alias)."""
    tier = FACILITY_TIER_ALIASES.get(tier, tier)
    return FACILITY_TIERS.get(tier, FACILITY_TIERS["academy"])["roster_ceiling"]


# --------------------------------------------------------------------
#  Result type
# --------------------------------------------------------------------
@dataclass
class EntitlementResult:
    """
    Returned from every can_X() check. The UI can use this directly
    to render either the feature or a paywall.

    allowed:        boolean — whether the user can use the feature
    reason:         human-readable reason if blocked (None if allowed)
    upgrade_to:     suggested plan id to upgrade to (None if no upgrade
                    helps, e.g. the user is already on the highest plan)
    remaining:      for limit-based gates, how many uses they have left
    """
    allowed:    bool
    reason:     Optional[str] = None
    upgrade_to: Optional[str] = None
    remaining:  Optional[int] = None

    def __bool__(self) -> bool:
        return self.allowed


# --------------------------------------------------------------------
#  Plan lookups
# --------------------------------------------------------------------
def _resolve_plan_id(plan_snapshot: Optional[dict]) -> str:
    """Pick a plan id out of a v_my_plan row, defaulting to FREE.

    Family-Pro propagation needs NO special handling here: the v_my_plan
    SQL view already resolves a member's plan THROUGH their
    subscription_seats seat (its my_seat CTE joins seats → subscriptions),
    so a seat on a family_pro subscription already arrives here as
    plan_id='family_pro'. (An earlier version added a redundant
    _resolve_plan_via_family fallback; removed — the view is the single
    source of truth.)
    """
    if not plan_snapshot:
        return FREE_PLAN_ID
    pid = plan_snapshot.get("plan_id")
    if pid in PLAN_CAPS:
        return pid
    return FREE_PLAN_ID


def caps_for(plan_id: str) -> dict:
    """Return the capability dict for a plan id (defaults to FREE)."""
    return PLAN_CAPS.get(plan_id) or PLAN_CAPS[FREE_PLAN_ID]


def resolve_effective_plan(
    plan_snapshot: Optional[dict], *, sponsored: bool = False
) -> str:
    """Best-of the player's own plan and any facility sponsorship.

    A facility sponsors full Pro for every active rostered player. So a
    sponsored player on a Free plan resolves to SOLO (full Pro caps). A
    player who already has a real paid sub keeps it — losing sponsorship
    must NEVER downgrade a paying customer (portability, spec §6). Every
    existing can_X() gate keeps working unchanged: it just receives the
    resolved plan_id.
    """
    own = _resolve_plan_id(plan_snapshot)
    if sponsored and own == FREE_PLAN_ID:
        return SOLO_PLAN_ID
    return own


def is_pro(plan_snapshot: Optional[dict]) -> bool:
    """True if the user is on any paid plan (or beta-comp Pro)."""
    return _resolve_plan_id(plan_snapshot) in PRO_PLANS


def status_is_active(plan_snapshot: Optional[dict]) -> bool:
    """Whether the user's sub is in a non-terminal state."""
    if not plan_snapshot:
        return False
    return plan_snapshot.get("status") in ("active", "trialing", "comp")


# --------------------------------------------------------------------
#  Capability checks
# --------------------------------------------------------------------
def can_analyze_swing(plan_snapshot: Optional[dict]) -> EntitlementResult:
    """
    Free: hard-capped at FREE_SWING_LIMIT lifetime analyses.
    Any Pro plan: unlimited.
    """
    plan_id = _resolve_plan_id(plan_snapshot)
    caps    = caps_for(plan_id)
    limit   = caps.get("swing_limit_lifetime")

    if limit is None:
        return EntitlementResult(allowed=True)

    used = int((plan_snapshot or {}).get("free_swings_used") or 0)
    remaining = max(0, limit - used)
    if remaining <= 0:
        return EntitlementResult(
            allowed=False,
            reason=(
                f"You\u2019ve used all {limit} of your free swing analyses. "
                "Upgrade to Pro for unlimited swings, drill plans, "
                "video saving, and the full Development Tracker."
            ),
            upgrade_to=SOLO_PLAN_ID,
            remaining=0,
        )
    return EntitlementResult(allowed=True, remaining=remaining)


# --------------------------------------------------------------------
#  BL-008C.20 Deliverable 5 -- narrowly scoped pilot allowance
# --------------------------------------------------------------------
# A small, explicit set of account emails get a RAISED lifetime swing
# limit instead of unlimited/Pro access -- deliberately narrow, never a
# blanket bypass of the commercial Free-tier wall. Empty until the
# owner provides the specific account identifier(s) to approve; adding
# an email here is the ONLY step required to activate it for that
# account, and the effect is scoped to exactly swing_limit_lifetime for
# that one email, nothing else (no other Pro capability is granted).
#
# Deliberately NOT read from an environment variable or secrets.toml --
# this is reviewable, committed, auditable code, matching how every
# other entitlement rule in this file already works, not a hidden
# runtime toggle.
PILOT_SWING_LIMIT = 5  # predeclared margin for retries across 5 real recordings
PILOT_ALLOWLIST: frozenset[str] = frozenset({
    # "friend@example.com",  # <- owner adds the approved test account's email here
})


def can_analyze_swing_for_account(plan_snapshot: Optional[dict], account_email: Optional[str]) -> EntitlementResult:
    """Same contract as can_analyze_swing, but checks the narrow pilot
    allowlist first. Any email NOT in PILOT_ALLOWLIST gets EXACTLY the
    same behavior as can_analyze_swing -- this function changes nothing
    for anyone not explicitly approved. A Pro account's email in the
    allowlist is a no-op (Pro is already unlimited); this only ever
    raises a Free account's limit, never lowers anything."""
    normalized = (account_email or "").strip().lower()
    if normalized and normalized in PILOT_ALLOWLIST:
        plan_id = _resolve_plan_id(plan_snapshot)
        if plan_id in PRO_PLANS:
            return EntitlementResult(allowed=True)  # already unlimited -- allowlist is a no-op here
        used = int((plan_snapshot or {}).get("free_swings_used") or 0)
        remaining = max(0, PILOT_SWING_LIMIT - used)
        if remaining <= 0:
            return EntitlementResult(
                allowed=False,
                reason=f"You\u2019ve used all {PILOT_SWING_LIMIT} of your pilot swing analyses.",
                upgrade_to=SOLO_PLAN_ID,
                remaining=0,
            )
        return EntitlementResult(allowed=True, remaining=remaining)
    return can_analyze_swing(plan_snapshot)


def _gate_pro_only(
    plan_snapshot: Optional[dict],
    cap_key: str,
    feature_label: str,
) -> EntitlementResult:
    """Shared helper for boolean Pro-only feature gates."""
    plan_id = _resolve_plan_id(plan_snapshot)
    caps    = caps_for(plan_id)
    if caps.get(cap_key):
        return EntitlementResult(allowed=True)
    return EntitlementResult(
        allowed=False,
        reason=(
            f"{feature_label} is a Pro feature. "
            "Upgrade to unlock it (or redeem a beta code)."
        ),
        upgrade_to=SOLO_PLAN_ID,
    )


def can_save_drill_plan(plan_snapshot: Optional[dict]) -> EntitlementResult:
    return _gate_pro_only(plan_snapshot, "drill_plan", "Personalized drill plans")


def can_save_video(plan_snapshot: Optional[dict]) -> EntitlementResult:
    return _gate_pro_only(plan_snapshot, "video_storage", "Swing video saving")


def can_access_development_tracker(plan_snapshot: Optional[dict]) -> EntitlementResult:
    return _gate_pro_only(
        plan_snapshot, "development_tracker", "The Development Tracker",
    )


def can_export_pdf(plan_snapshot: Optional[dict]) -> EntitlementResult:
    # PDF export is free for everyone. It's a weak paywall (nobody upgrades just
    # to download a PDF) and a branded, shareable swing report is good
    # distribution. Monetization stays on the free-swing limit + drill tracking /
    # history / family / facility seats. (Was _gate_pro_only on "pdf_export".)
    return EntitlementResult(allowed=True)


def can_compare_swings(plan_snapshot: Optional[dict]) -> EntitlementResult:
    return _gate_pro_only(plan_snapshot, "compare_swings", "Swing comparisons")


def can_access_rewards(plan_snapshot: Optional[dict]) -> EntitlementResult:
    return _gate_pro_only(plan_snapshot, "rewards", "Streaks, XP, and rewards")


# --------------------------------------------------------------------
#  Display helpers — used by paywall / pricing screens
# --------------------------------------------------------------------
def plan_display_name(plan_id: str) -> str:
    return {
        FREE_PLAN_ID:    "Free",
        SOLO_PLAN_ID:    "Solo Pro",
        FAMILY_PLAN_ID:  "Family Pro",
        COACH_PLAN_ID:   "Coach Pro",
    }.get(plan_id, plan_id.title())


def plan_seat_count(plan_id: str) -> int:
    return {
        FREE_PLAN_ID:   1,
        SOLO_PLAN_ID:   1,
        FAMILY_PLAN_ID: 4,
        COACH_PLAN_ID:  20,
    }.get(plan_id, 1)


__all__ = [
    "FREE_PLAN_ID", "SOLO_PLAN_ID", "FAMILY_PLAN_ID", "COACH_PLAN_ID",
    "PRO_PLANS", "FREE_SWING_LIMIT",
    "PLAN_CAPS",
    "EntitlementResult",
    "caps_for", "is_pro", "status_is_active",
    "can_analyze_swing", "can_save_drill_plan", "can_save_video",
    "can_access_development_tracker", "can_export_pdf",
    "can_compare_swings", "can_access_rewards",
    "plan_display_name", "plan_seat_count",
    "resolve_effective_plan",
    "FACILITY_TIERS", "FACILITY_TIER_ALIASES",
    "facility_tier_for_roster", "facility_roster_ceiling",
]
