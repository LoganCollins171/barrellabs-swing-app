"""Product analytics (PostHog) — track the funnel so you can see what works.

Right now you can't tell how many people sign up, run a swing, or convert to
Pro. This wires up PostHog server-side event tracking to answer that.

To turn it on: add to `.streamlit/secrets.toml`:
    [posthog]
    api_key = "phc_..."          # Project API Key from posthog.com
    host    = "https://us.i.posthog.com"   # or https://eu.i.posthog.com

Until that's set, every call here is a complete no-op — zero overhead, never
raises, safe to ship now. Add the key later and events start flowing.

Usage:
    import analytics
    analytics.track("swing_analyzed", user_id, edge_score=82, bracket="13-14")
"""
from __future__ import annotations

import os

_client = None
_resolved = False

# Same kill switch as monitoring.py (kept independent/duplicated on purpose —
# this module has no other dependency on monitoring.py and shouldn't gain
# one just for this flag). See monitoring.py's comment for the string-vs-TOML-
# boolean note.
_TELEMETRY_DISABLE_FLAG = "BARRELLABS_DISABLE_TELEMETRY"


def _telemetry_disabled() -> bool:
    """BL-008C.24: same automatic pytest-detection fix as monitoring.py's
    identically-named function (see that module's comment for the full
    Sentry PYTHON-6 root-cause story) -- PostHog shares the exact same
    hardcoded-fallback-key risk shape, so the same fix applies here too,
    even though no PostHog leak was reported."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    return os.environ.get(_TELEMETRY_DISABLE_FLAG, "").strip().lower() in (
        "1", "true", "yes",
    )


def _get_client():
    """Lazily build the PostHog client from secrets/env, or None if unconfigured."""
    global _client, _resolved
    if _resolved:
        return _client
    _resolved = True

    if _telemetry_disabled():
        return None  # explicit kill switch — stay a no-op regardless of key

    api_key = None
    host = "https://us.i.posthog.com"
    try:
        import streamlit as st
        cfg = st.secrets.get("posthog", {}) or {}
        api_key = cfg.get("api_key")
        host = cfg.get("host") or host
    except Exception:
        pass
    # Fallback to the project's PUBLIC key so the deployed app tracks without
    # needing a Streamlit Cloud secret. PostHog project keys (phc_…) are
    # client-side by design — safe to ship in code. Override via secrets/env to
    # rotate without a code change.
    api_key = (api_key or os.environ.get("POSTHOG_API_KEY")
               or "phc_vjE5nPRGDPRJmBdywqi7iGgRWUPnbwGwa2mYhANguecT")
    host = os.environ.get("POSTHOG_HOST") or host
    if not api_key:
        return None
    try:
        from posthog import Posthog
        _client = Posthog(project_api_key=api_key, host=host)
    except Exception:
        _client = None
    return _client


def track(event: str, user_id: str | None, **properties) -> None:
    """Record a product event (best-effort). No-op if PostHog isn't configured
    or anything goes wrong — never breaks the app flow."""
    try:
        client = _get_client()
        if client is None:
            return
        client.capture(
            distinct_id=str(user_id) if user_id else "anonymous",
            event=event,
            properties={k: v for k, v in properties.items() if v is not None},
        )
    except Exception:
        pass
