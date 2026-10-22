"""Captures the REAL streamlit module before any test module is imported.

Several older tests replace sys.modules["streamlit"] with a stub (some at
import time) and don't restore it. Tests that drive the real app through
Streamlit's AppTest use tests._stub_isolation to put this real module back
for their duration."""
import streamlit as REAL_STREAMLIT  # noqa: F401  (imported for its side effect of being captured first)
