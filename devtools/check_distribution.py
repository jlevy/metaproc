"""Validate built artifacts and a clean-wheel installation."""

from __future__ import annotations

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

from devtools.public_hygiene import find_binary_findings, find_hygiene_findings

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
REPOSITORY_ONLY_PARTS = {
    ".agents",
    ".claude",
    ".codex",
    ".github",
    ".tbd",
    ".venv",
    "dist",
    "node_modules",
}
REPOSITORY_ONLY_NAMES = {".copier-answers.yml", "AGENTS.md", "CLAUDE.md", "skills-lock.json"}
EXPECTED_LICENSE_METADATA = {
    "License-Expression: AGPL-3.0-or-later",
    "License-File: LICENSE",
    "License-File: NOTICE.md",
}


def _single_wheel() -> Path:
    wheels = sorted(DIST.glob("metaproc-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel in dist/, found {len(wheels)}")
    return wheels[0]


def _single_sdist() -> Path:
    sdists = sorted(DIST.glob("metaproc-*.tar.gz"))
    if len(sdists) != 1:
        raise RuntimeError(f"expected one sdist in dist/, found {len(sdists)}")
    return sdists[0]


def _check_text_member(name: str, payload: bytes) -> None:
    if name.endswith("devtools/public_hygiene.py"):
        return
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


def _check_project_metadata(payload: bytes) -> None:
    metadata_lines = set(payload.decode("utf-8").splitlines())
    missing = sorted(EXPECTED_LICENSE_METADATA - metadata_lines)
    if missing:
        raise RuntimeError(f"wheel metadata is missing license declarations: {missing}")


def _inspect_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        required_suffixes = {
            "metaproc/__init__.py",
            "metaproc/cli.py",
            "metaproc/data/execution-profiles.default.yaml",
            "metaproc/data/pi-models.default.json",
            "metaproc/docs/metaproc-operator-reference.md",
            "metaproc/metabrowser_plugin/plugin/elk.bundled.js",
            "metaproc/metabrowser_plugin/plugin/elkjs-license.txt",
            "metaproc/metabrowser_plugin/plugin/manifest.toml",
            "metaproc/py.typed",
            "metaproc/skill/baselines/metaproc.md",
            "dist-info/licenses/LICENSE",
            "dist-info/licenses/NOTICE.md",
        }
        for suffix in required_suffixes:
            if not any(name.endswith(suffix) for name in names):
                raise RuntimeError(f"wheel is missing {suffix}")
        forbidden_parts = REPOSITORY_ONLY_PARTS | {"tests", "devtools"}
        leaked = [name for name in names if forbidden_parts.intersection(Path(name).parts)]
        if leaked:
            raise RuntimeError(f"wheel contains repository-only files: {leaked[:10]}")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"wheel must contain one METADATA file, found {metadata_names}")
        _check_project_metadata(archive.read(metadata_names[0]))
        for name in names:
            _check_text_member(name, archive.read(name))


def _inspect_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        names = {member.name for member in members}
        required_suffixes = {
            "LICENSE",
            "NOTICE.md",
            "README.md",
            "pyproject.toml",
            "process/self-test/smoke-core.process.md",
            "src/metaproc/cli.py",
            "src/metaproc/data/execution-profiles.default.yaml",
            "src/metaproc/docs/metaproc-operator-reference.md",
            "src/metaproc/metabrowser_plugin/plugin/manifest.toml",
            "src/metaproc/skill/baselines/metaproc.md",
        }
        for suffix in required_suffixes:
            if not any(name.endswith(suffix) for name in names):
                raise RuntimeError(f"sdist is missing {suffix}")
        leaked = [
            name
            for name in names
            if REPOSITORY_ONLY_PARTS.intersection(Path(name).parts)
            or Path(name).name in REPOSITORY_ONLY_NAMES
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
    uv_command = ["uv", "--config-file", str(ROOT / "uv.toml")]
    python_command = [
        *uv_command,
        "run",
        "--isolated",
        "--no-project",
        "--with",
        str(wheel),
        "python",
        "-c",
        (
            "from importlib.metadata import entry_points, version; "
            "from importlib.resources import files; "
            "import metaproc; "
            "root = files('metaproc'); "
            "assert metaproc.__doc__; "
            "assert root.joinpath('data/execution-profiles.default.yaml').is_file(); "
            "assert root.joinpath('data/pi-models.default.json').is_file(); "
            "assert root.joinpath('docs/metaproc-operator-reference.md').is_file(); "
            "assert root.joinpath('metabrowser_plugin/plugin/manifest.toml').is_file(); "
            "assert root.joinpath('metabrowser_plugin/plugin/elkjs-license.txt').is_file(); "
            "assert root.joinpath('skill/baselines/metaproc.md').is_file(); "
            "assert {ep.name for ep in entry_points(group='metaproc.skills')} == {'metaproc'}; "
            "assert {ep.name for ep in entry_points(group='metabrowser.plugins')} == "
            "{'metaproc'}; "
            "print(version('metaproc'))"
        ),
    ]
    python_result = subprocess.run(
        python_command,
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    expected_version = python_result.stdout.strip()

    cli_command = [
        *uv_command,
        "run",
        "--isolated",
        "--no-project",
        "--with",
        str(wheel),
        "metaproc",
    ]
    subprocess.run([*cli_command, "--help"], cwd=ROOT, env=env, check=True)
    version_result = subprocess.run(
        [*cli_command, "--version"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if version_result.stdout.strip() != expected_version:
        raise RuntimeError(
            "installed-wheel CLI version does not match distribution metadata: "
            f"{version_result.stdout.strip()!r} != {expected_version!r}"
        )
    subprocess.run([*cli_command, "env", "--template"], cwd=ROOT, env=env, check=True)
    subprocess.run([*cli_command, "help"], cwd=ROOT, env=env, check=True)
    skill_result = subprocess.run(
        [*cli_command, "skill", "metaproc"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if "name: metaproc" not in skill_result.stdout:
        raise RuntimeError("installed-wheel skill output is missing Metaproc frontmatter")
    print(f"Installed-wheel smoke passed for metaproc {expected_version}.")


def main() -> int:
    wheel = _single_wheel()
    sdist = _single_sdist()
    _inspect_wheel(wheel)
    _inspect_sdist(sdist)
    _smoke_install(wheel)
    print(f"Distribution checks passed: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
