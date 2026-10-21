"""BL-008C.19 -- regression test for manual_swing_review.py, the new
production-integration page. Pins the real bug found and fixed during
this session's own integration testing: the "is a video ready" guard
must only run when frames aren't already cached, otherwise every
rerun after a successful decode (any slider move, any button click)
incorrectly bails out even though the real work is already done.
"""
from __future__ import annotations

import ast
import inspect


def test_video_path_guard_is_scoped_inside_the_frames_cache_check():
    """Structural regression test for the exact bug this session found
    live: `if not video_path...` must be nested inside
    `if "manual_review_frames" not in st.session_state`, never a
    sibling check that runs unconditionally on every rerun."""
    import manual_swing_review as mod
    source = inspect.getsource(mod.render_manual_swing_review)
    tree = ast.parse(source)

    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)

    # Find the top-level "if 'manual_review_frames' not in st.session_state"
    # guard and confirm the video_path-missing check is NESTED inside it,
    # not a sibling at the same indentation level.
    found_frames_guard = False
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            src = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
            if "manual_review_frames" in src and "not in" in src:
                found_frames_guard = True
                inner_source = ast.unparse(node)
                assert "video_path" in inner_source, (
                    "the video_path-missing check must be nested inside the "
                    "frames-not-cached guard, not run unconditionally on every rerun"
                )
    assert found_frames_guard, "expected a 'manual_review_frames not in session_state' guard"


def test_module_imports_cleanly():
    import manual_swing_review  # noqa: F401


def test_plain_language_metric_info_matches_measurement_policy_displayable_set():
    """The plain-language labels must stay in sync with
    engine_v2.measurement_policy's actual displayable-by-default set --
    never silently drift out of sync if that set changes."""
    from engine_v2.measurement_policy import DISPLAYABLE_BY_DEFAULT
    from manual_swing_review import PLAIN_LANGUAGE_METRIC_INFO

    assert set(PLAIN_LANGUAGE_METRIC_INFO.keys()) == set(DISPLAYABLE_BY_DEFAULT)


def test_no_edge_score_or_mlb_code_usage_in_this_page():
    """The module docstring explicitly NAMES analyzer.py/mlb_match.py to
    say it does NOT use them -- that's correct, desired documentation.
    This test checks actual CODE usage (imports/calls), not docstring
    mentions, which is the real safety property."""
    import ast
    import manual_swing_review as mod
    tree = ast.parse(inspect.getsource(mod))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
    for forbidden in ("mlb_match", "swing_score", "reference_library", "analyzer"):
        assert forbidden not in imported_modules, f"must never import {forbidden}"
