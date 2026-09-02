"""Enforce the safeproc import boundary and dependency rule from the repository root.

Importing ``safeproc`` and its brokerless surfaces in a fresh interpreter must not load
``metaproc``, and the distribution must declare no runtime dependency. The package tests
check the same thing; this gate runs it from the workspace root so a root-level change
cannot break it unnoticed.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "packages" / "safeproc" / "pyproject.toml"


def main() -> int:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = data.get("project", {}).get("dependencies", [])
    if dependencies:
        print(f"safeproc declares runtime dependencies: {dependencies}", file=sys.stderr)
        return 1
    code = (
        "import sys, safeproc, safeproc.cli, safeproc.monitor, safeproc.replay, safeproc._platform.base; "
        "bad = sorted(m for m in sys.modules if m == 'metaproc' or m.startswith('metaproc.')); "
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    print("Safeproc boundary checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
