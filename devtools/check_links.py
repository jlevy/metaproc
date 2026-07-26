"""Check local Markdown links without fetching external URLs."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".agents",
    ".claude",
    ".codex",
    ".git",
    ".tbd",
    ".venv",
    "dist",
    "node_modules",
    "__pycache__",
}
SKIP_ROOTS = (Path("tests/fixtures"),)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")


def _target_path(root: Path, source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    elif " " in target:
        target = target.split(" ", 1)[0]
    if not target or target.startswith(("#", "//")):
        return None
    split = urlsplit(target)
    if split.scheme or not split.path:
        return None
    path = Path(unquote(split.path))
    return root / str(path).lstrip("/") if path.is_absolute() else source.parent / path


def check_local_markdown_links(root: Path = ROOT) -> list[str]:
    """Return missing local link targets in human-authored Markdown files."""
    findings: list[str] = []
    resolved_root = root.resolve()
    for source in sorted(root.rglob("*.md")):
        relative = source.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts) or any(
            relative.is_relative_to(skip_root) for skip_root in SKIP_ROOTS
        ):
            continue
        text = source.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(text):
            target = _target_path(root, source, match.group(1))
            if target is None:
                continue
            line = text.count("\n", 0, match.start()) + 1
            try:
                target.resolve().relative_to(resolved_root)
            except ValueError:
                findings.append(
                    f"{relative}:{line}: local link escapes repository: {match.group(1)}"
                )
                continue
            if target.exists():
                continue
            findings.append(f"{relative}:{line}: missing local link target: {match.group(1)}")
    return findings


def main() -> int:
    findings = check_local_markdown_links()
    if findings:
        print("Local Markdown link check failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Local Markdown link checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
