"""BARRELLABS_DISABLE_TELEMETRY must force monitoring.py and analytics.py to
stay complete no-ops — no Sentry init, no Sentry capture, no PostHog client,
no PostHog event — regardless of any DSN/key (configured or the built-in
fallback). Unset, existing behavior is unchanged (both modules already
no-op safely with no config; these tests pin that too so the kill switch
can't accidentally change the already-safe default path).

Each module's internal module-level cache (_initialized / _resolved) is reset
per test since both are "once per process" by design.
"""
import os

import analytics
import monitoring


def _reset_monitoring():
    monitoring._initialized = False


def _reset_analytics():
    analytics._resolved = False
    analytics._client = None


def test_flag_unset_defaults_to_false(monkeypatch):
    monkeypatch.delenv("BARRELLABS_DISABLE_TELEMETRY", raising=False)
    assert monitoring._telemetry_disabled() is False
    assert analytics._telemetry_disabled() is False


def test_flag_values_that_disable(monkeypatch):
    for value in ("1", "true", "True", "TRUE", "yes", "Yes"):
        monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", value)
        assert monitoring._telemetry_disabled() is True, value
        assert analytics._telemetry_disabled() is True, value


def test_flag_values_that_do_not_disable(monkeypatch):
    for value in ("0", "false", "no", "", "garbage"):
        monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", value)
        assert monitoring._telemetry_disabled() is False, value
        assert analytics._telemetry_disabled() is False, value


def test_init_monitoring_is_noop_when_disabled(monkeypatch):
    """With the flag set, init_monitoring() must never reach sentry_sdk.init —
    verified by making sentry_sdk.init raise if it's ever called."""
    monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", "1")
    _reset_monitoring()

    import sentry_sdk

    def _boom(*a, **kw):
        raise AssertionError("sentry_sdk.init must not be called when disabled")

    monkeypatch.setattr(sentry_sdk, "init", _boom)
    monitoring.init_monitoring()  # must return before touching sentry_sdk at all


def test_capture_is_noop_when_disabled(monkeypatch):
    """capture() must not call sentry_sdk.capture_exception when disabled,
    even if init_monitoring() was never called first."""
    monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", "1")
    _reset_monitoring()

    import sentry_sdk

    def _boom(*a, **kw):
        raise AssertionError(
            "sentry_sdk.capture_exception must not be called when disabled"
        )

    monkeypatch.setattr(sentry_sdk, "capture_exception", _boom)
    monitoring.capture(ValueError("test"))  # must no-op silently


def test_posthog_get_client_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_should_never_be_used")
    _reset_analytics()

    assert analytics._get_client() is None


def test_track_sends_no_event_when_disabled(monkeypatch):
    """track() must not construct a PostHog client (and therefore can't send
    an event) when disabled, even with a key available via env override."""
    monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", "1")
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_should_never_be_used")
    _reset_analytics()

    from posthog import Posthog

    def _boom(*a, **kw):
        raise AssertionError("Posthog(...) must not be constructed when disabled")

    monkeypatch.setattr("posthog.Posthog", _boom)
    analytics.track("swing_analyzed", "test-user")  # must no-op silently


def test_existing_noop_behavior_unaffected_when_flag_unset(monkeypatch):
    """Sanity check the kill switch didn't change the pre-existing safe
    default: with the flag unset, both modules still don't raise. Both
    modules fall back to a built-in real DSN/key when unset — that's
    existing, pre-this-change behavior, not something to re-verify here —
    so the real init/capture/track calls are mocked out purely to keep this
    test from making a real network call, not to assert on their behavior."""
    monkeypatch.delenv("BARRELLABS_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("POSTHOG_API_KEY", raising=False)
    _reset_monitoring()
    _reset_analytics()

    import sentry_sdk
    from posthog import Posthog

    monkeypatch.setattr(sentry_sdk, "init", lambda *a, **kw: None)
    monkeypatch.setattr(sentry_sdk, "capture_exception", lambda *a, **kw: None)
    monkeypatch.setattr("posthog.Posthog", lambda *a, **kw: None)

    monitoring.init_monitoring()
    monitoring.capture(ValueError("test"))
    analytics.track("swing_analyzed", "test-user")
