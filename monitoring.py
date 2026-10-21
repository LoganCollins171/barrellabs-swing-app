"""Error monitoring — initializes Sentry if a DSN is configured, else a no-op.

Today the app could crash silently in production and nobody knew until a user
hit it. This wires up Sentry so unhandled errors get reported automatically.

To turn it on: add a DSN under `[sentry] dsn = "https://..."` in
`.streamlit/secrets.toml` (or set the `SENTRY_DSN` env var). Until you do, this
is a complete no-op — zero overhead, never raises, safe to ship now.
"""
from __future__ import annotations

import os

_initialized = False

# Hard kill switch for telemetry, independent of whether a DSN/key is
# configured. Set BARRELLABS_DISABLE_TELEMETRY=1 (any of 1/true/yes, not the
# TOML boolean `true` — Streamlit only promotes string/int/float secrets to
# environment variables) to force this module to stay a no-op even though the
# code ships a working fallback DSN. Unset by default, so existing production
# behavior is unchanged. Added for the Streamlit Community Cloud test so a
# throwaway deploy can't report into the real Sentry project.
_TELEMETRY_DISABLE_FLAG = "BARRELLABS_DISABLE_TELEMETRY"


def _telemetry_disabled() -> bool:
    """BL-008C.24: Sentry PYTHON-6 ("increment_free_swing_count returned -1
    [simulated transient DB hiccup] ... tests.test_shared_swing_usage_re...")
    was a real test exception that reached real production Sentry. Root
    cause: this module's safety depended entirely on BARRELLABS_DISABLE_TELEMETRY
    being explicitly exported before running pytest, or every single test
    that exercises an exception path through capture() correctly mocking
    sentry_sdk -- a per-test discipline that one missed mock (exactly what
    happened: tests/test_shared_swing_usage_recording.py exercises
    record_successful_swing_analysis's genuine except-block -> capture()
    call with NO sentry_sdk mocking at all) silently defeats.

    Fix: detect "running under pytest" automatically and unconditionally,
    the same way as BARRELLABS_DISABLE_TELEMETRY, with no per-developer or
    per-CI-run setup required. PYTEST_CURRENT_TEST is set by pytest itself
    for the duration of every test (a well-established, zero-config pytest
    idiom -- not something this project has to maintain or keep in sync).
    This makes the failure class structurally impossible going forward:
    a NEW test that forgets to mock sentry_sdk can no longer leak, because
    telemetry is off for the entire pytest process regardless."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    return os.environ.get(_TELEMETRY_DISABLE_FLAG, "").strip().lower() in (
        "1", "true", "yes",
    )


def init_monitoring() -> None:
    """Initialize Sentry once per process if a DSN is configured. Safe no-op
    otherwise — never raises, never blocks app startup."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    if _telemetry_disabled():
        return  # explicit kill switch — stay a no-op regardless of DSN

    dsn = None
    try:
        import streamlit as st
        dsn = (st.secrets.get("sentry", {}) or {}).get("dsn")
    except Exception:
        dsn = None
    # Fall back to the project's DSN so the DEPLOYED app reports crashes without
    # needing a Streamlit Cloud secret. A Sentry DSN is write-only ingestion
    # (like a PostHog public key) — safe to ship in code. Override via the
    # [sentry] secret or SENTRY_DSN env var to point at a different project.
    dsn = (dsn or os.environ.get("SENTRY_DSN")
           or "https://733ec3a052bb0ea6b31aeb8ad911944a@o4511515808432128.ingest.us.sentry.io/4511515814395904")
    if not dsn:
        return  # not configured yet — stay a no-op

    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            # Errors only — no performance tracing (keeps cost + overhead at 0).
            traces_sample_rate=0.0,
            # Don't ship user PII (emails, IPs) to a third party by default.
            send_default_pii=False,
            # BL-003I: sentry-sdk captures local variables in every stack
            # frame of a reported exception by default. A function that ever
            # holds a secret (e.g. a Supabase/Stripe key) as a local variable
            # and then throws would otherwise ship that value into Sentry.
            # Off by default in this app's own code regardless of whether
            # any single frame actually touches a secret today — this is
            # defense in depth, not a response to a known leak.
            include_local_variables=False,
            environment=os.environ.get("SENTRY_ENV", "production"),
        )
    except Exception:
        # sentry-sdk missing or init failed — never let monitoring break the app.
        pass


def capture(exc: BaseException, **context) -> None:
    """Manually report a caught exception (best-effort). Use in except blocks
    where the error is swallowed but you still want visibility."""
    if _telemetry_disabled():
        return
    try:
        import sentry_sdk
        if context:
            with sentry_sdk.push_scope() as scope:
                for k, v in context.items():
                    scope.set_extra(k, v)
                sentry_sdk.capture_exception(exc)
        else:
            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
