"""Guardrail: Metaproc source must remain workflow-neutral and public-safe.

The release hygiene detector stores private vocabulary as hashes, so this test
can enforce the boundary without republishing the terms it rejects. A second
hash set protects identifiers moved to a consumer package during extraction.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from devtools.public_hygiene import find_hygiene_findings

REPO_ROOT = Path(__file__).resolve().parents[1]
METAPROC_SRC = REPO_ROOT / "src" / "metaproc"
IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")

FORBIDDEN_IDENTIFIER_HASHES: dict[str, str] = {
    "6c90bd06bcb4002ba7f90a17ba7e187c5c60c34d7afbffbf3fdba04fde5a0727": (
        "consumer run-root environment variable"
    ),
    "dc15f220cced8543e6e226b1327b9bbda9b9244ac487132e8ddf1bf6ac81cbf6": (
        "consumer run-root environment variable"
    ),
    "6383a16247733a23844bf3382e3ca017c78b83173db4af79feb80a3510fe2105": (
        "consumer run-root environment variable"
    ),
    "b20d64499301cebd41c41cf174cb3a18b0e8cf24208ee7ff03bc6efac132a07a": ("consumer workflow enum"),
    "0663ab1ac1242a2aa1b2bb668c53b301e0f10b4671aad23edb2425cc0b94ad97": ("consumer path resolver"),
    "5535b71f99baede084b23d212dc5f1604079bb4bda6e0caa5bd31133ed1c0a33": (
        "consumer variable resolver"
    ),
    "c7d430971f61630c51b3fdc3f9a6ca2d1b799bba6d3a5aee195cab55a881a93f": (
        "consumer environment registry"
    ),
    "33fee39b9377a58dddf35395b12fb672b365483f69e949f5ecc2a052fc18f875": ("consumer path resolver"),
}


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_metaproc_src_is_domain_neutral() -> None:
    """Reject private residue and identifiers owned by a consumer workflow."""
    offenders: list[str] = []
    for py_file in _python_files(METAPROC_SRC):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        source = py_file.read_text()
        offenders.extend(find_hygiene_findings(rel, source))
        for line_no, line in enumerate(source.splitlines(), start=1):
            for match in IDENTIFIER_PATTERN.finditer(line):
                digest = hashlib.sha256(match.group(0).encode()).hexdigest()
                label = FORBIDDEN_IDENTIFIER_HASHES.get(digest)
                if label is not None:
                    offenders.append(f"{rel}:{line_no}: {label}")

    if offenders:
        msg = (
            f"{len(offenders)} domain or public-hygiene violation(s) in metaproc/src/:\n"
            + "\n".join(f"  {offender}" for offender in sorted(set(offenders)))
            + "\n\nMove consumer policy to its plugin package or replace private provenance "
            + "with a durable public explanation."
        )
        raise AssertionError(msg)
