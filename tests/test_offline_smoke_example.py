"""End-to-end contract for the public offline quickstart."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_offline_smoke_example_runs_without_external_services(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    process_spec = root / "examples" / "offline-smoke" / "offline-smoke.process.md"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "metaproc",
            "run-process",
            str(process_spec),
            "--var",
            f"RUNS_DIR={tmp_path}",
            "--var",
            "RUN_ID=quickstart",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    run_dir = tmp_path / "quickstart"
    assert (run_dir / "invocations.log").read_text() == "s1\ns2\ns3\n"
    assert (run_dir / "s1.txt").read_text() == "s1 done\n"
    assert (run_dir / "s2.txt").read_text() == "s2 done\n"
    assert (run_dir / "s3.txt").read_text() == "s3 done\n"
    assert "Process 'offline-smoke' completed" in result.stdout + result.stderr
