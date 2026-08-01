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


def test_validate_rejects_malformed_contract_id_without_traceback(tmp_path) -> None:
    artifact_path = tmp_path / "artifact.md"
    artifact_path.write_text("---\nvalue: test\n---\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "metaproc",
            "softschema",
            "validate",
            str(artifact_path),
            "--schema",
            "example.record.v1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Error: --schema must match [namespace:]Name[/version]" in result.stderr
    assert "Traceback" not in result.stderr


def test_compile_rejects_malformed_contract_id_without_traceback(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "metaproc",
            "softschema",
            "compile",
            "metaproc.models.authored:ProcessSpec",
            "--out",
            str(tmp_path / "process.schema.yaml"),
            "--contract",
            "example.record.v1",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Error: --contract must match [namespace:]Name[/version]" in result.stderr
    assert "Traceback" not in result.stderr
