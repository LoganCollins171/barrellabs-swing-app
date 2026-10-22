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
    """Structural regression test for the bug BL-008C.19 found live: the
    "is there a video to prepare" check must only run while frames aren't
    prepared yet, or every later rerun (any marker move, any button) would
    bail out even though the work is done. BL-008C.36 moved preparation
    into _prepare_clip(); it must be reached ONLY through the
    `"manual_review_frames" not in st.session_state` guard."""
    import manual_swing_review as mod
    body = ast.parse(inspect.getsource(mod._render_body)).body[0]
    guarded_calls = []
    for node in ast.walk(body):
        if isinstance(node, ast.If):
            test = ast.unparse(node.test)
            if "manual_review_frames" in test and "not in" in test:
                guarded_calls += [ast.unparse(n) for n in ast.walk(node) if isinstance(n, ast.Call)]
    assert any("_prepare_clip" in c for c in guarded_calls), \
        "_prepare_clip must only be called inside the frames-not-prepared guard"
    all_calls = [ast.unparse(n) for n in ast.walk(body) if isinstance(n, ast.Call)]
    assert sum("_prepare_clip" in c for c in all_calls) == sum("_prepare_clip" in c for c in guarded_calls)
    prep_src = inspect.getsource(mod._prepare_clip)
    assert "manual_review_video_path" in prep_src and "os.path.isfile" in prep_src


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
