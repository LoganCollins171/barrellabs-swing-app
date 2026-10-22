"""Shared test helper (BL-008C.36)."""

def drop_stub_bound_modules(monkeypatch):
    """Other tests import project modules while a fake `streamlit` is
    installed; those stub-bound copies linger in sys.modules. Drop every
    module whose `st` isn't the real Streamlit so the app under test imports
    real ones (monkeypatch restores the previous entries afterwards)."""
    import sys
    from tests.conftest import REAL_STREAMLIT as real_st
    if sys.modules.get("streamlit") is not real_st:
        monkeypatch.setitem(sys.modules, "streamlit", real_st)
    for name, mod in list(sys.modules.items()):
        st_attr = getattr(mod, "st", None)
        if st_attr is not None and st_attr is not real_st and not name.startswith("streamlit"):
            monkeypatch.delitem(sys.modules, name, raising=False)
