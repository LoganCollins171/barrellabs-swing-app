"""BL-007: regression coverage for the sign-in scroll-accessibility fix.

Root cause investigation (documented in full in the BL-007 report):
- Computed-style and scroll-API diagnostics against the LIVE deployed app
  found the overflow/height CSS rules in auth_screen.py already correct
  (overflow: visible, scrollHeight > clientHeight, no JS preventDefault on
  wheel events) -- no code-level overflow/height defect was found.
- auth_screen.py was, however, missing `overscroll-behavior-y: none` on
  html/body, which bl_edge_chrome.py (used on every OTHER authenticated
  page, none of which have this reported bug) already carries for the
  identical symptom family -- that file's own comment describes fixing a
  "mobile scroll-lock/jank... locks for a second, can't scroll back up."
- This test pins that the proven fix was actually applied, and that it
  wasn't applied by weakening the real overflow/height fix already in
  place (defense against a future edit removing one while "simplifying"
  the other).
"""
from __future__ import annotations

from pathlib import Path

AUTH_SCREEN_SRC = (
    Path(__file__).resolve().parent.parent / "auth_screen.py"
).read_text()


def test_overscroll_behavior_fix_present():
    assert "overscroll-behavior-y: none" in AUTH_SCREEN_SRC


def test_overscroll_fix_applies_to_html_and_body():
    assert "html, body { overscroll-behavior-y: none; }" in AUTH_SCREEN_SRC


def test_original_overflow_fix_still_present_unweakened():
    """The overscroll fix is additive -- it must not have replaced or
    weakened the existing overflow:visible / height:auto fix for
    stApp/stAppViewContainer/stMain."""
    assert '[data-testid="stApp"]' in AUTH_SCREEN_SRC
    assert '[data-testid="stAppViewContainer"]' in AUTH_SCREEN_SRC
    assert '[data-testid="stMain"]' in AUTH_SCREEN_SRC
    assert "overflow: visible !important;" in AUTH_SCREEN_SRC
    assert "height: auto !important;" in AUTH_SCREEN_SRC


def test_bl_edge_chrome_has_the_same_fix_for_comparison():
    """Confirms the precedent this fix is based on is real and still
    present -- if bl_edge_chrome.py's own fix is ever removed, this test
    fails loudly rather than leaving auth_screen.py's fix looking
    arbitrary/unexplained."""
    bl_edge_chrome_src = (
        Path(__file__).resolve().parent.parent / "bl_edge_chrome.py"
    ).read_text()
    assert "overscroll-behavior-y: none" in bl_edge_chrome_src
