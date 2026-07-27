"""Enforce that every `# noqa: PLC0415` comment carries a written justification.

Why this is its own check:

PLC0415 ("import should be at the top-level of a file") is a judgment call: a
bare `# noqa: PLC0415` silences the linter without explaining whether the
import is a deliberate optional-heavy-dep guard, a CLI startup-perf hoist, a
plugin loader, or a Typer/Click registration -- vs. just a reflex local
import that should have been moved to the top. The guideline in
`docs/general/guidelines/python-structural-quality-guidelines.md` (Static vs
Dynamic Imports) says every suppression must have a reason.

This script enforces that. The expected shape is:

    from foo import bar  # noqa: PLC0415 -- <reason>

Anything beyond the bare ` -- ` separator counts as a reason. We don't try to
classify the reason -- the point is to make the author write it down so the
reviewer can sanity-check it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match a `noqa:` directive that contains PLC0415. We want to know whether the
# tail of that segment (after `PLC0415`) carries justification text.
#
# Accepted forms (justification text present after PLC0415):
#   # noqa: PLC0415 -- optional heavy dep
#   # noqa: PLC0415 -- ...
#   # noqa: PLC0415  # optional heavy dep
#   # noqa: F401, PLC0415 -- optional heavy dep
#
# Rejected (no justification after PLC0415):
#   # noqa: PLC0415
#   # noqa: PLC0415,F401
#
# Strategy: find the substring starting at "PLC0415" up to end of line; require
# that what follows contains a " -- " or a secondary "#" comment with text.

NOQA_RE = re.compile(r"#\s*noqa:[^\n]*\bPLC0415\b[^\n]*$", re.MULTILINE)
JUSTIFIED_RE = re.compile(
    r"#\s*noqa:[^\n]*\bPLC0415\b[^\n#\-–—]*"
    r"(?:--\s*\S|[–—]\s*\S|#\s*\S)",  # "-- text" or "— text" or "# text"
)


def scan_file(path: Path) -> list[tuple[int, str]]:
    failures: list[tuple[int, str]] = []
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return failures
    all_lines = text.splitlines()
    for i, line in enumerate(all_lines, start=1):
        if "PLC0415" not in line or "noqa" not in line:
            continue
        # Skip lines where the directive is prose, not a Ruff suppression.
        # A real suppression sits on an import line or a closing `)` of one.
        prefix = line.split("# noqa", 1)[0]
        if not (
            "import " in prefix
            or prefix.rstrip().endswith((")", ","))
            or prefix.rstrip().endswith("import")
        ):
            continue
        if not NOQA_RE.search(line):
            continue
        if JUSTIFIED_RE.search(line):
            continue
        # Accept an explanatory comment on the immediately preceding line.
        # (Useful when the justification is long enough to want its own line.)
        if i >= 2:
            prev = all_lines[i - 2].strip()
            if prev.startswith("#") and "noqa" not in prev and len(prev) > 2:
                continue
        failures.append((i, line.rstrip()))
    return failures


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_plc0415_justifications.py <path>...", file=sys.stderr)
        return 2
    total = 0
    for arg in sys.argv[1:]:
        root = Path(arg)
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for f in files:
            fails = scan_file(f)
            for lineno, line in fails:
                print(
                    f"{f}:{lineno}: unjustified `# noqa: PLC0415` -- "
                    f"expected ` -- <reason>` or trailing `# <reason>`",
                    file=sys.stderr,
                )
                print(f"    {line}", file=sys.stderr)
                total += 1
    if total:
        print(
            f"\n{total} unjustified PLC0415 suppression(s). "
            "Add a reason after `# noqa: PLC0415` -- see "
            "docs/general/guidelines/python-structural-quality-guidelines.md "
            "(Static vs Dynamic Imports).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
