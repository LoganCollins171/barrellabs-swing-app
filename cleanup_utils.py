"""Prune stale local files so a long-running single-process server doesn't fill
its disk. Uploaded videos + the per-swing analysis artifacts (fingerprint,
phase chart, etc.) are written to local disk; the durable copies live in
Supabase, so the local ones are safe to drop once they're not part of an
in-flight analysis.
"""
from __future__ import annotations

import time
from pathlib import Path

# Generated per-swing artifacts (named from the upload stem). Suffix-scoped so
# committed files (e.g. mlb_match_stats.json, references/*.json) are never hit.
_ARTIFACT_SUFFIXES = (
    "_fingerprint.json", "_phases.png", "_metrics.csv",
    "_signals.json", "_phases_debug.json", "_debug.json",
)

# BL-008C.20 Deliverable 2: the project_root sweep below is already
# suffix-restricted to _ARTIFACT_SUFFIXES (generated data files, never a
# raw video), which structurally cannot match an original recording --
# but this second, independent check means that stays true even if
# _ARTIFACT_SUFFIXES is ever misconfigured to something broader in the
# future. Belt-and-suspenders on top of an already-safe design, not a
# sign this specific function was unsafe before.
_NEVER_DELETE_EXTENSIONS = (".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm")


def prune_stale_files(upload_dir, project_root, max_age_hours: float = 2.0) -> int:
    """Delete stale upload videos (everything in ``upload_dir``) and generated
    artifacts (suffix-matched in ``project_root``) older than ``max_age_hours``.
    Files newer than the cutoff are left alone so an in-flight analysis is never
    disturbed. Returns the number removed. Never raises.

    ``upload_dir`` deletions additionally go through
    engine_v2.safe_delete.safe_remove, which independently verifies the
    resolved path is inside an app-owned temp/upload directory before
    deleting anything (see that module's docstring for why -- the
    BL-008C.19 incident)."""
    from engine_v2.safe_delete import safe_remove, UnsafeDeletionError

    cutoff = time.time() - max_age_hours * 3600.0
    removed = 0

    def _sweep(directory: Path, match, *, use_safe_remove: bool):
        nonlocal removed
        try:
            for p in directory.glob("*"):
                try:
                    if p.suffix.lower() in _NEVER_DELETE_EXTENSIONS and not use_safe_remove:
                        continue  # the artifact sweep must never touch a video file
                    if p.is_file() and match(p) and p.stat().st_mtime < cutoff:
                        if use_safe_remove:
                            try:
                                safe_remove(p)
                            except UnsafeDeletionError:
                                continue  # refuse, don't delete, don't raise out of a best-effort sweep
                        else:
                            p.unlink()
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass

    _sweep(Path(upload_dir), lambda p: True, use_safe_remove=True)
    _sweep(Path(project_root), lambda p: p.name.endswith(_ARTIFACT_SUFFIXES), use_safe_remove=False)
    return removed
