"""Importing Net-Razor must be silent, and must not touch the home directory.

A stdio MCP server speaks the protocol over stdout, so a single stray print at
import time corrupts the stream and the host refuses to start with no useful
explanation. Nothing else in the suite would notice, because every other test
imports the package into a process that is already writing to a captured stdout.

The home directory check is here for the same reason ``stub_settings`` exists:
importing must not read or create the operator's real ``~/.net-razor``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# The MCP entry point matters most -- it is what a host launches -- but the
# package and the app are on the same import path and each is worth naming, so a
# failure says which one started writing.
IMPORTS = ["net_razor", "net_razor.app", "net_razor.mcp.server"]


def _import_in_a_clean_process(module: str, home) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        # A bare environment: no .env to find, no PATH to inherit, and a home
        # directory that starts empty so anything written to it is visible.
        env={"HOME": str(home), "PATH": ""},
        check=False,
    )


@pytest.mark.parametrize("module", IMPORTS)
def test_importing_writes_nothing_to_stdout_or_stderr(module, tmp_path):
    result = _import_in_a_clean_process(module, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"{module} wrote to stdout, which carries the protocol"
    assert result.stderr == "", f"{module} wrote to stderr at import time"


@pytest.mark.parametrize("module", IMPORTS)
def test_importing_creates_nothing_in_the_home_directory(module, tmp_path):
    _import_in_a_clean_process(module, tmp_path)

    assert list(tmp_path.iterdir()) == [], f"{module} wrote to the home directory"
