"""BL-004B (B.5): guards against a real person's name ever being used as
example/placeholder content in public-facing code or in dev-tooling scripts
that live in this public repo.

Root cause this prevents from regressing: `auth_screen.py`'s signup form
used "Mario" / "Ricard" — Logan's best friend's real first and last name —
as the First name / Last name field placeholders, rendered to every visitor
who opens the public signup form. Several `scripts/visual_qa/*.py` dev
scripts (not run by the deployed app, but committed to the same public
GitHub repo) used the same real name as mock data for the same reason.
Both were fixed in BL-004B by swapping to the fictional "Jordan Ellis".

This test is intentionally broad (whole-repo, case-insensitive) rather than
scoped to the files fixed today, so a *new* file introducing the same real
name anywhere fails immediately instead of waiting for another audit.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Real name banned from ever appearing as example/placeholder content.
# Checked as two separate whole-word tokens (not a combined phrase) so a
# regression can't dodge the check by splitting the name across lines/args.
BANNED_NAME_PARTS = ["mario", "ricard"]

# Only scan source/doc file types that are either rendered to users or
# published in the public repo. Binary/image assets are out of scope.
SCANNED_SUFFIXES = {".py", ".md", ".html", ".toml", ".sql"}

# This test file itself legitimately contains the banned words (in its own
# docstring/strings, explaining the regression it guards against) — exclude
# only itself, nothing else.
SELF_PATH = Path(__file__).resolve()


def _iter_scanned_files():
    """Scan git-tracked files only. The scope of this guard is "could this
    reach the public GitHub repo as example/placeholder content" — an
    untracked local-only file (e.g. a private PM handoff doc that
    legitimately discusses the real user by name, per BL-004B's
    instruction to preserve such internal documentation) never reaches the
    repo at all, so it is correctly out of scope rather than exempted
    ad hoc."""
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    for rel in tracked:
        path = REPO_ROOT / rel
        if path.resolve() == SELF_PATH:
            continue
        if path.suffix in SCANNED_SUFFIXES and path.is_file():
            yield path


def test_real_name_never_appears_as_example_or_placeholder_content():
    offenders = []
    for path in _iter_scanned_files():
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for part in BANNED_NAME_PARTS:
            if re.search(rf"\b{part}\b", text, re.IGNORECASE):
                offenders.append((str(path.relative_to(REPO_ROOT)), part))

    assert not offenders, (
        "A real person's name part was found in a scanned file — this must "
        "never be used as example/placeholder/mock content in public-facing "
        "or publicly-committed code. Legitimate private references (e.g. an "
        "internal PM handoff doc discussing the real user by name) should "
        "live outside the scanned directories/suffixes, not be exempted "
        f"here. Offenders: {offenders}"
    )


def test_signup_form_placeholders_are_not_the_banned_name():
    """Narrower, file-specific pin on the actual bug site — fails loudly and
    specifically if auth_screen.py's signup placeholders regress, even if
    the broad repo-wide scan above were ever weakened."""
    auth_screen_src = (REPO_ROOT / "auth_screen.py").read_text()
    call_site = re.search(
        r'su_first = st\.text_input\(\s*"First name",\s*placeholder="([^"]*)"',
        auth_screen_src,
    )
    assert call_site, "could not find the signup First name text_input call"
    assert call_site.group(1).lower() != "mario"

    last_name_site = re.search(
        r'su_last = st\.text_input\(\s*"Last name",\s*placeholder="([^"]*)"',
        auth_screen_src,
    )
    assert last_name_site, "could not find the signup Last name text_input call"
    assert last_name_site.group(1).lower() != "ricard"
