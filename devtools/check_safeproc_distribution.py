"""Validate the safeproc source-free build and a clean-wheel installation.

The package must build with ``--no-sources``, carry the do-not-upload classifier and
the repository license, declare no runtime dependency, leak no repository-only files,
and import without loading Metaproc from an isolated environment.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

from devtools.public_hygiene import find_binary_findings, find_hygiene_findings

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "safeproc"
DIST = PACKAGE / "dist"
REPOSITORY_ONLY_PARTS = {
    ".agents",
    ".claude",
    ".codex",
    ".github",
    ".tbd",
    ".venv",
    "dist",
    "node_modules",
    "tests",
}
EXPECTED_METADATA = {
    "Classifier: Private :: Do Not Upload",
    "License-Expression: AGPL-3.0-or-later",
    "License-File: LICENSE",
}


def _single(pattern: str) -> Path:
    found = sorted(DIST.glob(pattern))
    if len(found) != 1:
        raise RuntimeError(f"expected one {pattern} in {DIST}, found {len(found)}")
    return found[0]


def _check_text_member(name: str, payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        findings = find_binary_findings(name, payload)
        if findings:
            raise RuntimeError(f"artifact hygiene failed: {findings[:10]}") from None
        return
    findings = find_hygiene_findings(name, text)
    if findings:
        raise RuntimeError(f"artifact hygiene failed: {findings[:10]}")


def _inspect_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for suffix in (
            "safeproc/__init__.py",
            "safeproc/cli.py",
            "safeproc/py.typed",
            "dist-info/licenses/LICENSE",
        ):
            if not any(name.endswith(suffix) for name in names):
                raise RuntimeError(f"wheel is missing {suffix}")
        leaked = [name for name in names if REPOSITORY_ONLY_PARTS.intersection(Path(name).parts)]
        if leaked:
            raise RuntimeError(f"wheel contains repository-only files: {leaked[:10]}")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"wheel must contain one METADATA file, found {metadata_names}")
        lines = archive.read(metadata_names[0]).decode("utf-8").splitlines()
        missing = sorted(EXPECTED_METADATA - set(lines))
        if missing:
            raise RuntimeError(f"wheel metadata is missing: {missing}")
        runtime = [
            line for line in lines if line.startswith("Requires-Dist:") and "extra ==" not in line
        ]
        if runtime:
            raise RuntimeError(f"safeproc must declare no runtime dependency: {runtime}")
        for name in names:
            _check_text_member(name, archive.read(name))


def _inspect_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        names = {member.name for member in members}
        for suffix in ("LICENSE", "README.md", "pyproject.toml", "src/safeproc/cli.py"):
            if not any(name.endswith(suffix) for name in names):
                raise RuntimeError(f"sdist is missing {suffix}")
        leaked = [
            name
            for name in names
            if REPOSITORY_ONLY_PARTS.intersection(Path(name).parts) - {"tests"}
        ]
        if leaked:
            raise RuntimeError(f"sdist contains repository-only files: {leaked[:10]}")
        for member in members:
            extracted = archive.extractfile(member)
            if extracted is not None:
                _check_text_member(member.name, extracted.read())


def _smoke_install(wheel: Path) -> None:
    env = os.environ.copy()
    env.setdefault("UV_EXCLUDE_NEWER", "14 days")
    uv = [
        "uv",
        "--config-file",
        str(ROOT / "uv.toml"),
        "run",
        "--isolated",
        "--no-project",
        "--with",
        str(wheel),
    ]
    check = (
        "import sys; from importlib.metadata import version; "
        "import safeproc, safeproc.cli, safeproc.monitor, safeproc.replay; "
        "assert not [m for m in sys.modules if m == 'metaproc' or m.startswith('metaproc.')]; "
        "print(version('safeproc'))"
    )
    result = subprocess.run(
        [*uv, "python", "-c", check], cwd=ROOT, env=env, check=True, capture_output=True, text=True
    )
    expected = result.stdout.strip()
    subprocess.run([*uv, "safeproc", "--help"], cwd=ROOT, env=env, check=True, capture_output=True)
    version_result = subprocess.run(
        [*uv, "safeproc", "--version"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if version_result.stdout.strip() != f"safeproc {expected}":
        raise RuntimeError(
            f"installed CLI version mismatch: {version_result.stdout.strip()!r} != {expected!r}"
        )
    print(f"Installed-wheel smoke passed for safeproc {expected}.")


def main() -> int:
    wheel = _single("safeproc-*.whl")
    sdist = _single("safeproc-*.tar.gz")
    _inspect_wheel(wheel)
    _inspect_sdist(sdist)
    _smoke_install(wheel)
    print(f"Safeproc distribution checks passed: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
