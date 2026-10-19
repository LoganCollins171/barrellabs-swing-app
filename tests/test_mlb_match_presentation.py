"""BL-004C (4): the MLB-comparison percentage's underlying computation
(mlb_match.py's match_pro/movement_vector) is untouched — this covers only
the PRESENTATION fix: every page that shows `movement_match_pct` to a
user must use the same honest label/caveat, sourced from one place
(mlb_match.MATCH_LABEL / MATCH_CAVEAT / MATCH_CAVEAT_SHORT), so "experimental
style comparison, not a validated probability" reads consistently across
the full swing report, saved-reports/session history, the dashboard, and
the development tracker — the actual numeric diagnostic value is never
altered by any of this.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_shared_constants_exist_and_are_honest():
    from mlb_match import MATCH_LABEL, MATCH_CAVEAT, MATCH_CAVEAT_SHORT

    assert MATCH_LABEL
    assert "experimental" in MATCH_CAVEAT.lower()
    assert "experimental" in MATCH_CAVEAT_SHORT.lower()
    assert "probability" in MATCH_CAVEAT.lower()
    assert "probability" in MATCH_CAVEAT_SHORT.lower()


def _src(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text()


def test_full_swing_report_imports_shared_caveat():
    """swing_report_dashboard_preview.py — the actual production report
    renderer (per HANDOFF.md: swing_report_page.py delegates to it) — must
    use the shared constants, not a hand-rolled caveat that could drift."""
    src = _src("swing_report_dashboard_preview.py")
    assert "from mlb_match import MATCH_LABEL, MATCH_CAVEAT_SHORT" in src
    # The raw percentage itself must still render — this is a presentation
    # fix, not a removal of the diagnostic value.
    assert '{int(movement_pct)}%' in src


def test_saved_reports_history_imports_shared_caveat():
    """The Sessions/saved-reports history list — a separate page from the
    full report — must use the SAME shared constants, so the number reads
    consistently whether a user is looking at one report or their history."""
    src = _src("saved_reports_dashboard.py")
    assert "from mlb_match import MATCH_LABEL, MATCH_CAVEAT_SHORT" in src
    assert '{int(sim)}' in src  # raw number still shown


def test_dashboard_imports_shared_caveat_at_both_sites():
    """dashboard_v3.py has two real movement_match_pct displays (the hero
    narrative sentence and the MLB-card 'Match score' stat pill) — both
    must reference the shared caveat, not duplicate/diverging text."""
    src = _src("dashboard_v3.py")
    assert src.count("from mlb_match import MATCH_CAVEAT_SHORT") >= 2
    # The raw number is still substituted into both — not removed.
    assert "{match_pct}%" in src


def test_development_tracker_imports_shared_caveat():
    src = _src("development_tracker.py")
    assert "from mlb_match import MATCH_CAVEAT_SHORT" in src
    assert "{mlb}" in src  # raw number still rendered


def test_no_render_site_hardcodes_a_diverging_caveat_string():
    """Guards against the fix regressing into copy-pasted (and therefore
    driftable) caveat text instead of the shared constant. Every touched
    file must import from mlb_match, not inline its own wording."""
    touched_files = [
        "swing_report_dashboard_preview.py",
        "saved_reports_dashboard.py",
        "dashboard_v3.py",
        "development_tracker.py",
    ]
    for relpath in touched_files:
        src = _src(relpath)
        assert "from mlb_match import" in src, (
            f"{relpath} shows the MLB-match percentage but doesn't import "
            f"the shared presentation constants from mlb_match.py"
        )


def test_match_pro_numeric_output_is_unchanged():
    """The actual calculation (what BL-004C explicitly must NOT touch) —
    same inputs, same output, byte-for-byte, as before this task."""
    from mlb_match import match_pro

    z_vector = [0.1, -0.2, 0.3, 0.0, -0.1, 0.2, 0.05]
    stats = {
        "centroids": [[0.0] * 7, [1.0] * 7],
        "pros": [
            {"slug": "a", "name": "Player A", "cluster": 0,
             "z": [0.1, -0.2, 0.3, 0.0, -0.1, 0.2, 0.05]},
            {"slug": "b", "name": "Player B", "cluster": 0,
             "z": [0.0] * 7},
        ],
    }
    result = match_pro(z_vector, stats)
    assert result["slug"] == "a"
    assert result["movement_match_pct"] == 100  # exact match, distance 0
    assert result["cluster"] == 0
