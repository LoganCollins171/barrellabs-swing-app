"""BL-004C (3): regression coverage for the Coach Pro purchase safeguard.

Coach Pro is sold at $79.99/mo with roster/player-viewing features that
have no implementation anywhere in this codebase (verified in BL-004B:
the only seat infrastructure that exists, family_storage.py, checks
exclusively for is_family_pro_member). `pricing.py` was given a narrowly
scoped, reversible flag (`_COACH_PRO_SALES_PAUSED`) that disables the
Coach Pro checkout CTA specifically, while leaving Solo and Family
completely untouched and never affecting an existing Coach Pro
subscriber's "Current plan" state.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


@pytest.fixture
def pricing_module(monkeypatch):
    """Import pricing.py fresh under a minimal streamlit stub that records
    every st.button() call (label, kwargs) so tests can assert on exactly
    what was rendered without a real Streamlit runtime."""
    calls = []

    def _button(label, **kwargs):
        calls.append({"label": label, **kwargs})
        return False  # never "clicked" in these tests

    def _passthrough_decorator(*dargs, **dkwargs):
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        return lambda fn: fn

    st_stub = types.SimpleNamespace(
        button=_button,
        markdown=lambda *a, **k: None,
        error=lambda *a, **k: calls.append({"error": a[0] if a else None}),
        columns=lambda *a, **k: [],
        session_state={},
        rerun=lambda: None,
        cache_resource=_passthrough_decorator,
        cache_data=_passthrough_decorator,
    )
    monkeypatch.setitem(sys.modules, "streamlit", st_stub)
    sys.modules.pop("pricing", None)
    import pricing
    return pricing, calls


def _stub_plan(pricing_mod, monkeypatch, *, current_plan_id, on_pro, has_price_id=True):
    monkeypatch.setattr(pricing_mod, "load_my_plan", lambda: {"plan_id": current_plan_id})
    monkeypatch.setattr(pricing_mod, "_resolve_plan_id", lambda snap: current_plan_id)
    monkeypatch.setattr(pricing_mod, "is_pro", lambda snap: on_pro)
    monkeypatch.setattr(
        pricing_mod, "stripe_price_id",
        lambda plan_id, interval: ("price_abc123" if has_price_id else None),
    )


class TestCoachProCheckoutPaused:
    def test_free_user_sees_coach_pro_disabled_with_honest_copy(
            self, pricing_module, monkeypatch):
        pricing, calls = pricing_module
        _stub_plan(pricing, monkeypatch, current_plan_id="free", on_pro=False)

        pricing._render_upgrade_button("coach_pro", "monthly", featured=False)

        assert len(calls) == 1
        assert calls[0]["label"] == "Signups paused"
        assert calls[0]["disabled"] is True
        assert "roster" in calls[0]["help"].lower()
        assert "paused" in calls[0]["help"].lower()
        # Must not claim the plan is simply unbuilt/"coming soon" (which
        # implies normal pre-launch unavailability) — it must be clear
        # this is a deliberate pause due to incomplete features, and that
        # existing subscribers are unaffected.
        assert "already on coach pro" in calls[0]["help"].lower()

    def test_solo_pro_user_can_still_upgrade_to_family(
            self, pricing_module, monkeypatch):
        """The pause must not leak onto other plans."""
        pricing, calls = pricing_module
        _stub_plan(pricing, monkeypatch, current_plan_id="solo_pro", on_pro=True)

        pricing._render_upgrade_button("family_pro", "monthly", featured=True)

        assert len(calls) == 1
        assert calls[0]["label"] != "Signups paused"
        assert calls[0].get("disabled") is not True
        assert "Family" in calls[0]["label"] or "family_pro" in calls[0]["label"].lower()

    def test_free_user_can_still_start_solo(self, pricing_module, monkeypatch):
        pricing, calls = pricing_module
        _stub_plan(pricing, monkeypatch, current_plan_id="free", on_pro=False)

        pricing._render_upgrade_button("solo_pro", "monthly", featured=False)

        assert calls[0]["label"] == "Start with Solo"
        assert calls[0].get("disabled") is not True

    def test_existing_coach_pro_subscriber_sees_current_plan_not_paused(
            self, pricing_module, monkeypatch):
        """An existing Coach Pro subscriber must never see the pause
        message — they already have it, this isn't a new purchase."""
        pricing, calls = pricing_module
        _stub_plan(pricing, monkeypatch, current_plan_id="coach_pro", on_pro=True)

        pricing._render_upgrade_button("coach_pro", "monthly", featured=False)

        assert calls[0]["label"] == "✓ Current plan"
        assert calls[0]["disabled"] is True

    def test_switching_from_family_to_coach_pro_is_also_paused(
            self, pricing_module, monkeypatch):
        """Not just a fresh/free signup — an existing Family Pro user
        trying to SWITCH to Coach Pro must also be blocked, since that
        still newly grants the broken entitlement."""
        pricing, calls = pricing_module
        _stub_plan(pricing, monkeypatch, current_plan_id="family_pro", on_pro=True)

        pricing._render_upgrade_button("coach_pro", "annual", featured=False)

        assert calls[0]["label"] == "Signups paused"
        assert calls[0]["disabled"] is True

    def test_start_checkout_refuses_coach_pro_even_if_called_directly(
            self, pricing_module, monkeypatch):
        """Defense-in-depth: _start_checkout itself refuses coach_pro,
        independent of the button-level gate, in case any future caller
        reaches it another way."""
        pricing, calls = pricing_module

        def _boom(*a, **k):
            raise AssertionError(
                "create_checkout_session must not be reached for a paused plan"
            )
        monkeypatch.setitem(
            sys.modules, "stripe_client",
            types.SimpleNamespace(create_checkout_session=_boom),
        )

        pricing._start_checkout("coach_pro", "monthly")

        assert any("error" in c for c in calls)

    def test_start_checkout_still_works_for_solo(self, pricing_module, monkeypatch):
        """The defense-in-depth guard must be plan-specific, not a
        blanket block on checkout."""
        pricing, calls = pricing_module
        reached = {}

        def _fake_create(**kwargs):
            reached.update(kwargs)
            return "https://checkout.stripe.com/fake-session"

        monkeypatch.setitem(
            sys.modules, "stripe_client",
            types.SimpleNamespace(create_checkout_session=_fake_create),
        )
        monkeypatch.setattr(pricing, "_streamlit_base_url", lambda: "https://app.example.com")

        pricing._start_checkout("solo_pro", "monthly")

        assert reached.get("plan_id") == "solo_pro"


class TestMintBetaCodeCoachProGuard:
    """Companion guard: the local admin CLI that mints redeemable beta
    codes must also refuse to mint new coach_pro codes while sales are
    paused — this is a separate code path (writes straight to Supabase
    with the service-role key) that the pricing-page pause doesn't touch
    at all."""

    def test_coach_pro_plan_is_rejected(self):
        import mint_beta_code
        rc = mint_beta_code.main(["--plan", "coach_pro", "--dry-run"])
        assert rc == 2

    def test_solo_pro_plan_still_passes_validation(self):
        """Must not block other plans. --dry-run never calls _make_client()
        (confirmed by reading main()'s control flow), so this exercises
        real validation logic with no Supabase I/O at all."""
        import mint_beta_code
        rc = mint_beta_code.main(["--plan", "solo_pro", "--dry-run"])
        assert rc == 0
