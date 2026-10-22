"""Streamlit Community Cloud feeds packages.txt to apt-get through xargs: no
comment support, and quotes are special. A commented packages.txt (9cae39c)
made every word an apt package name and the build failed on the next cold
start ("Error installing requirements", production, 2026-10-22 17:49 UTC:
"E: Unable to locate package #", "xargs: unmatched single quote"). Keep the
file to bare Debian package names, one per line."""
import re
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")   # Debian package-name syntax


def test_packages_txt_is_bare_package_names_only():
    text = (Path(__file__).resolve().parents[1] / "packages.txt").read_text()
    for n, line in enumerate(text.splitlines(), 1):
        assert line == line.strip(), f"line {n}: no leading/trailing whitespace"
        if line:
            assert _NAME.match(line), f"line {n} is not a bare package name: {line!r}"
