"""Smoke test: ``python -m metaproc --help`` runs without executing main logic."""

from __future__ import annotations

import subprocess
import sys


def test_module_help_exits_cleanly() -> None:
    """Verify the __main__.py guard works — --help should not trigger main()."""
    result = subprocess.run(
        [sys.executable, "-m", "metaproc", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "Usage" in result.stdout or "usage" in result.stdout.lower()
