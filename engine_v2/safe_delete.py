"""BL-008C.20 Deliverable 2 -- NONNEGOTIABLE file-safety guard.

Root cause of the BL-008C.19 incident: a test script passed a real,
original recording (~/Downloads/IMG_8607.MOV) into
engine_v2.user_guided_flow.decode_and_cleanup, whose contract is "I will
always delete whatever path you give me once I'm done reading it" --
correct and necessary for a real upload, but the test script violated
the contract by handing it a path that wasn't disposable. That function
itself had no way to know the difference.

This module is that missing check: a single, defense-in-depth gate that
EVERY destructive file operation in this codebase must pass through.
It does not trust the caller's intent -- it independently verifies the
resolved, symlink-following real path is inside an explicitly
app-owned, designated temp/upload directory before allowing deletion,
and REFUSES (never silently deletes) anything else, including a path
that merely LOOKS like a temp file but resolves outside those
directories.

This does not just move the risk around: a test that reuses the
original BL-008C.19 mistake (handing a real personal file to a
cleanup function) will now get a clear, loud refusal instead of a
silent deletion -- verified by tests/test_safe_delete.py and
tests/test_original_footage_protection.py.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# The app's own designated upload directory (matches app.py's UPLOAD_DIR
# exactly -- PROJECT_ROOT / "uploads_streamlit"). Resolved lazily so this
# module has no import-time dependency on app.py or a particular cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_UPLOAD_DIR = _PROJECT_ROOT / "uploads_streamlit"

# Personal/user directories that must NEVER be treated as safe to delete
# from, even if some future bug in the allowlist check above would
# otherwise permit it. This is the second, independent layer -- a
# deny-list checked in addition to (not instead of) the allow-list,
# specifically naming the directory the BL-008C.19 incident happened in.
_HOME = Path.home()
_NEVER_DELETE_FROM = [
    _HOME / "Downloads",
    _HOME / "Desktop",
    _HOME / "Documents",
    _HOME / "Pictures",
    _HOME / "Movies",
    _HOME / "Music",
]


class UnsafeDeletionError(Exception):
    """Raised when code asks to delete a path that is not inside an
    explicitly app-owned temp/upload directory. Never caught silently
    anywhere in this codebase -- a caller hitting this must fix its own
    path handling, not retry or swallow the error."""


def _resolved_owned_dirs() -> list[Path]:
    """Real, symlink-resolved app-owned directories it is ever safe to
    delete a file from. Computed fresh each call (not cached at import
    time) so a freshly created APP_UPLOAD_DIR is picked up correctly."""
    dirs = [Path(tempfile.gettempdir()).resolve()]
    try:
        dirs.append(APP_UPLOAD_DIR.resolve())
    except OSError:
        pass
    return dirs


def is_app_owned_path(path: str | os.PathLike) -> bool:
    """True only if `path`, after resolving symlinks and relative
    components, is a descendant of an app-owned temp/upload directory
    AND is not inside one of the explicitly protected personal
    directories. Both checks must pass -- this is intentionally
    conservative; an ambiguous or unresolvable path is NOT safe."""
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        return False

    for forbidden in _NEVER_DELETE_FROM:
        try:
            resolved.relative_to(forbidden.resolve())
            return False
        except ValueError:
            pass

    for owned in _resolved_owned_dirs():
        try:
            resolved.relative_to(owned)
            return True
        except ValueError:
            continue
    return False


def safe_remove(path: str | os.PathLike) -> None:
    """The ONLY function in this codebase allowed to call os.remove on a
    video/upload path. Deletes `path` if and only if
    `is_app_owned_path(path)` is True. Otherwise raises
    UnsafeDeletionError and deletes NOTHING -- never a silent no-op that
    could be mistaken for success, never a silent deletion of something
    outside the app's own directories.

    A path that simply doesn't exist is treated as already-safe (no-op,
    no error) -- this function's job is preventing wrongful deletion,
    not asserting the path existed."""
    if not os.path.exists(path):
        return
    if not is_app_owned_path(path):
        raise UnsafeDeletionError(
            f"refusing to delete {os.path.basename(str(path))!r}: its resolved path is not inside an "
            f"app-owned temp/upload directory (checked: {tempfile.gettempdir()!r} and "
            f"{str(APP_UPLOAD_DIR)!r}). This guard exists specifically because BL-008C.19 accidentally "
            f"deleted a real original recording this way -- see engine_v2/safe_delete.py."
        )
    os.remove(path)
