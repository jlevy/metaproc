"""Repository policy tests for shared-filesystem locking primitives."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCAN_PATHS = (
    Path("src"),
    Path("docs"),
    Path("TODO.md"),
)

ALLOWED_POLICY_REFERENCES = {
    # Whole-file allowlist: these files either define the mkdir-lock policy or
    # contain explicit negative guidance. metaproc/tests is intentionally not
    # scanned so this test's own forbidden-term literals do not self-trip.
    Path("src/metaproc/docs/arch-runpool.md"),
    Path("src/metaproc/dispatch/credential_pool.py"),
    Path("src/metaproc/io/mkdir_lock.py"),
    Path("src/metaproc/runpool/README.md"),
}

FORBIDDEN_LOCK_TERMS = (
    "flock",
    "fcntl",
    "filelock",
    "portalocker",
)


def _iter_scanned_files() -> list[Path]:
    paths: list[Path] = []
    for rel_path in SCAN_PATHS:
        path = REPO_ROOT / rel_path
        if path.is_file():
            paths.append(rel_path)
            continue
        paths.extend(
            child.relative_to(REPO_ROOT)
            for child in path.rglob("*")
            if child.is_file() and child.suffix in {".md", ".py"}
        )
    return sorted(paths)


def test_no_forbidden_file_locking_guidance_or_usage() -> None:
    violations: list[str] = []
    for rel_path in _iter_scanned_files():
        if rel_path in ALLOWED_POLICY_REFERENCES:
            continue
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="ignore").lower()
        matched = sorted(term for term in FORBIDDEN_LOCK_TERMS if term in text)
        if matched:
            violations.append(f"{rel_path}: {', '.join(matched)}")

    assert not violations, (
        "Use metaproc.io.mkdir_lock / mkdir-based leases for shared-filesystem "
        "mutual exclusion. Forbidden lock primitive references found:\n" + "\n".join(violations)
    )
