"""Fail any relative link in a shipped doc that escapes the shipped directory.

``devtools/check_links.py`` resolves local markdown links against the repository
root, so a document in ``src/metaproc/docs/`` linking ``../../../docs/development.md``
passes: the target exists in a checkout. It does not exist in the wheel, and the
documents in this directory are read from the wheel — that is the point of shipping
them. Every gate in the repository is blind to that difference.

The rule this enforces: a relative link in ``src/metaproc/docs/*.md`` must resolve
to a file inside ``src/metaproc/``. Everything under the package directory ships, and
at the same relative offset, so such a link resolves in a checkout and in an installed
wheel alike. Anything outside the package is either linked absolutely or written
around.

Absolute URLs are not checked here (nothing in the repository checks them), which
is a reason to prefer rewriting a link away over converting it to an absolute one.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "src" / "metaproc"
SHIPPED_DIR = PACKAGE_DIR / "docs"

# Markdown inline links: [text](target). Matches check_links.py's pattern so the
# two gates agree on what counts as a link.
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _is_relative_link(target: str) -> bool:
    """Return True for a target that resolves against the containing file."""
    if target.startswith(("#", "mailto:")):
        return False
    return "://" not in target


def check_shipped_links(
    shipped_dir: Path = SHIPPED_DIR, package_dir: Path = PACKAGE_DIR
) -> list[str]:
    """Return one finding per relative link that escapes ``package_dir``."""
    findings: list[str] = []
    resolved_dir = package_dir.resolve()
    # Report paths relative to the repository root the package sits in, so findings
    # read the same under the real tree and under a test fixture.
    display_root = package_dir.resolve().parents[1]
    for source in sorted(shipped_dir.glob("*.md")):
        relative_source = source.resolve().relative_to(display_root)
        for line_number, line in enumerate(source.read_text("utf-8").splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                raw_target = match.group(1).strip()
                if not _is_relative_link(raw_target):
                    continue
                target = (source.parent / raw_target.split("#", 1)[0]).resolve()
                if not target.is_relative_to(resolved_dir):
                    findings.append(
                        f"{relative_source}:{line_number}: link escapes the package "
                        f"(dead in the wheel): {raw_target}"
                    )
                elif not target.exists():
                    findings.append(
                        f"{relative_source}:{line_number}: missing in-package link "
                        f"target: {raw_target}"
                    )
    return findings


def main() -> int:
    """Print findings and return a process exit code."""
    findings = check_shipped_links()
    if findings:
        sys.stderr.write("Shipped-doc link check failed:\n")
        for finding in findings:
            sys.stderr.write(f"- {finding}\n")
        return 1
    sys.stdout.write("Shipped-doc link checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
