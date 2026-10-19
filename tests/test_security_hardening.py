"""BL-003I hardening, pinned so neither regresses silently:

1. client.showErrorDetails = "none" in .streamlit/config.toml — uncaught
   exceptions must not render full tracebacks/messages to a public visitor.
2. monitoring.py's Sentry init passes include_local_variables=False — a
   reported exception must not ship local-variable values (which could hold
   a secret) to Sentry.
3. The BARRELLABS_DISABLE_TELEMETRY kill switch (BL-003C) still takes
   priority over both — disabled means sentry_sdk.init is never called at
   all, so these settings are moot in that path, which is the point.
"""
import tomllib
from pathlib import Path

import monitoring

CONFIG_PATH = Path(__file__).resolve().parent.parent / ".streamlit" / "config.toml"


def _reset_monitoring():
    monitoring._initialized = False


def test_show_error_details_is_none_in_config_toml():
    """Guards against the default ('full', which shows exception types,
    messages, and tracebacks to any visitor) ever silently coming back,
    e.g. from someone regenerating this file without the [client] section."""
    data = tomllib.loads(CONFIG_PATH.read_text())
    assert data.get("client", {}).get("showErrorDetails") == "none"


def test_sentry_init_disables_local_variable_capture(monkeypatch):
    monkeypatch.delenv("BARRELLABS_DISABLE_TELEMETRY", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _reset_monitoring()

    import sentry_sdk

    captured_kwargs = {}

    def _capture_init(*a, **kw):
        captured_kwargs.update(kw)

    monkeypatch.setattr(sentry_sdk, "init", _capture_init)
    monitoring.init_monitoring()

    assert captured_kwargs.get("include_local_variables") is False


def test_telemetry_kill_switch_still_short_circuits_before_sentry_init(monkeypatch):
    """Preserves BL-003C: disabled means sentry_sdk.init is never reached at
    all, regardless of the new include_local_variables kwarg existing."""
    monkeypatch.setenv("BARRELLABS_DISABLE_TELEMETRY", "1")
    _reset_monitoring()

    import sentry_sdk

    def _boom(*a, **kw):
        raise AssertionError("sentry_sdk.init must not be called when disabled")

    monkeypatch.setattr(sentry_sdk, "init", _boom)
    monitoring.init_monitoring()  # must return before this is ever reached
