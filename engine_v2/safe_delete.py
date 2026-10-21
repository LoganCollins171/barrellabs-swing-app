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


# BL-008C.21: directory allowlisting alone is NOT sufficient proof of
# application ownership. APP_UPLOAD_DIR (uploads_streamlit/) is exclusive
# to this app -- nothing else on the machine writes there, and every file
# in it is saved by app.py itself with a uuid-stamped name
# (upload_paths.unique_upload_name) -- so trusting that whole directory
# is justified. tempfile.gettempdir(), by contrast, is SHARED by every
# process on the machine; "this path happens to resolve inside /tmp" is
# not evidence this app created it. A real file that a user, another
# program, or a careless test script copies into system temp would
# previously have passed the old directory-only check.
#
# The fix: within the shared OS temp directory, a path is only
# considered app-owned if it was explicitly registered as disposable at
# the moment THIS app created it (register_owned_path). Nothing is
# trusted there by location alone anymore. APP_UPLOAD_DIR keeps
# directory-level trust because that directory itself is the ownership
# proof -- it's exclusively this app's, not shared.
#
# In-memory, per-process registry -- deliberately not persisted to disk
# (no new architecture, no migration, no extra moving part). A process
# restart naturally drops stale entries, which is fine: an abandoned
# temp file left behind by a crashed process is handled separately by
# cleanup_utils.prune_stale_files's age-based sweep over APP_UPLOAD_DIR,
# which never touches the shared OS temp dir at all.
_OWNED_PATHS: set[str] = set()


def register_owned_path(path: str | os.PathLike) -> None:
    """Call this at the exact moment the app creates a disposable file
    outside APP_UPLOAD_DIR (e.g. a tempfile.mkstemp() scratch copy) --
    marks it as something safe_remove is later allowed to delete. A path
    that doesn't resolve is silently ignored (nothing to register)."""
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        return
    _OWNED_PATHS.add(str(resolved))


def _unregister_owned_path(resolved: Path) -> None:
    _OWNED_PATHS.discard(str(resolved))


def _resolved_owned_dirs() -> list[Path]:
    """Real, symlink-resolved app-owned directories it is ever safe to
    delete a file from BY LOCATION ALONE. Computed fresh each call (not
    cached at import time) so a freshly created APP_UPLOAD_DIR is picked
    up correctly. Deliberately does NOT include the OS temp dir anymore
    -- see the BL-008C.21 comment above _OWNED_PATHS."""
    dirs = []
    try:
        dirs.append(APP_UPLOAD_DIR.resolve())
    except OSError:
        pass
    return dirs


def is_app_owned_path(path: str | os.PathLike) -> bool:
    """True only if `path`, after resolving symlinks and relative
    components, is EITHER (a) a descendant of APP_UPLOAD_DIR (an
    exclusively app-owned directory -- trusted by location), OR (b) a
    path this app explicitly registered as disposable via
    register_owned_path (required for anything in the shared OS temp
    dir, which is not exclusively owned by this app) -- AND is not
    inside one of the explicitly protected personal directories either
    way. All checks must pass -- this is intentionally conservative; an
    ambiguous or unresolvable path is NOT safe."""
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

    return str(resolved) in _OWNED_PATHS


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
            f"refusing to delete {os.path.basename(str(path))!r}: its resolved path is neither inside "
            f"the app's exclusive upload directory ({str(APP_UPLOAD_DIR)!r}) nor explicitly registered "
            f"as a disposable file this app created (register_owned_path). Merely living inside the "
            f"shared system temp dir ({tempfile.gettempdir()!r}) is not, by itself, proof this app owns "
            f"it. This guard exists specifically because BL-008C.19 accidentally deleted a real original "
            f"recording this way -- see engine_v2/safe_delete.py."
        )
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        resolved = None
    os.remove(path)
    if resolved is not None:
        _unregister_owned_path(resolved)
