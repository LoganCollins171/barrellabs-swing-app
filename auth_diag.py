"""BL-008C.32 -- non-secret auth lifecycle events for the server log.

One line per event: `auth_event=<name> sid=<random per-browser-session id>
k=v ...`. Field values are restricted to bool / int / None BY CONSTRUCTION,
so a token, email, or user id can never be passed through by mistake -- any
other type is silently dropped. Goes to the private server log (Streamlit
Cloud's app log), never to the page.

Events: restore_attempt, restore_result, session_stored, token_refresh,
cookie_write_emitted, cookie_clear_emitted, auth_gate, logout.
"""
from __future__ import annotations

import logging
import sys
import uuid

import streamlit as st

_log = logging.getLogger("barrellabs.auth")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False


def _safe(fields: dict) -> str:
    out = []
    for k, v in fields.items():
        if v is None or isinstance(v, (bool, int)):
            out.append(f"{k}={v}")
    return " ".join(out)


def event(name: str, **fields) -> None:
    try:
        sid = st.session_state.get("_auth_diag_sid")
        if not sid:
            sid = uuid.uuid4().hex[:8]
            st.session_state["_auth_diag_sid"] = sid
        _log.info("auth_event=%s sid=%s %s", name, sid, _safe(fields))
    except Exception:
        pass
