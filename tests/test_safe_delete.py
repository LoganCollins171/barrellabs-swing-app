"""BL-008C.20 Deliverable 2 -- tests for engine_v2.safe_delete, the
NONNEGOTIABLE guard against repeating the BL-008C.19 incident (a test
script deleted a real original recording, ~/Downloads/IMG_8607.MOV, by
passing it into a function whose contract is "always delete your
input")."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from engine_v2.safe_delete import safe_remove, is_app_owned_path, UnsafeDeletionError, APP_UPLOAD_DIR


def test_refuses_to_delete_a_path_under_downloads():
    """The exact category of path that caused the BL-008C.19 incident."""
    fake_downloads_file = Path.home() / "Downloads" / "__bl008c20_test_do_not_create__.mov"
    assert not is_app_owned_path(fake_downloads_file)
    # Must raise even if (hypothetically) the file existed -- verified
    # separately below with a real file, in a throwaway temp HOME.


def test_refuses_to_delete_a_real_file_under_downloads(tmp_path, monkeypatch):
    """End-to-end: create a REAL file under a fake $HOME/Downloads and
    confirm safe_remove refuses to delete it and leaves it on disk."""
    fake_home = tmp_path / "fakehome"
    (fake_home / "Downloads").mkdir(parents=True)
    victim = fake_home / "Downloads" / "my_real_swing.mov"
    victim.write_bytes(b"not actually a video, just bytes that must survive")

    monkeypatch.setattr("engine_v2.safe_delete.Path.home", staticmethod(lambda: fake_home))
    # Recompute the module's protected-dirs list against the patched home.
    import engine_v2.safe_delete as sd
    monkeypatch.setattr(sd, "_NEVER_DELETE_FROM", [
        fake_home / "Downloads", fake_home / "Desktop", fake_home / "Documents",
        fake_home / "Pictures", fake_home / "Movies", fake_home / "Music",
    ])

    with pytest.raises(UnsafeDeletionError):
        safe_remove(victim)
    assert victim.exists(), "the original file must still exist after a refused deletion"
    assert victim.read_bytes() == b"not actually a video, just bytes that must survive"


def test_allows_deletion_inside_the_os_temp_dir():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
        f.write(b"disposable temp copy")
        path = f.name
    assert is_app_owned_path(path)
    safe_remove(path)
    assert not os.path.exists(path)


def test_allows_deletion_inside_the_app_upload_dir():
    APP_UPLOAD_DIR.mkdir(exist_ok=True)
    victim = APP_UPLOAD_DIR / "__bl008c20_test_disposable__.mp4"
    victim.write_bytes(b"disposable app-owned upload copy")
    try:
        assert is_app_owned_path(victim)
        safe_remove(victim)
        assert not victim.exists()
    finally:
        if victim.exists():
            victim.unlink()


def test_nonexistent_path_is_a_safe_noop():
    """Deleting a path that's already gone must not raise -- this is
    about preventing wrongful deletion, not asserting existence."""
    safe_remove("/this/path/definitely/does/not/exist/anywhere.mp4")


def test_symlink_escaping_the_owned_dir_is_refused():
    """A symlink INSIDE the app upload dir that points OUTSIDE it must
    not be followed into a false allow -- the guard resolves real paths,
    not just the literal argument. Uses a directory under the repo
    itself (never the OS temp dir, which pytest's own tmp_path fixture
    happens to live inside on macOS -- an earlier version of this test
    used tmp_path and got a false pass for exactly that reason)."""
    APP_UPLOAD_DIR.mkdir(exist_ok=True)
    outside_dir = APP_UPLOAD_DIR.parent / "__bl008c20_test_outside_dir__"
    outside_dir.mkdir(exist_ok=True)
    outside_target = outside_dir / "outside_real_file.mov"
    outside_target.write_bytes(b"a file genuinely outside any owned directory")
    link = APP_UPLOAD_DIR / "__bl008c20_test_symlink__.mov"
    if link.exists() or link.is_symlink():
        link.unlink()
    try:
        link.symlink_to(outside_target)
        assert not is_app_owned_path(link), (
            "a symlink inside the upload dir that resolves outside it must not be treated as owned"
        )
        with pytest.raises(UnsafeDeletionError):
            safe_remove(link)
        assert outside_target.exists()
    finally:
        if link.is_symlink() or link.exists():
            link.unlink()
        import shutil
        shutil.rmtree(outside_dir, ignore_errors=True)
