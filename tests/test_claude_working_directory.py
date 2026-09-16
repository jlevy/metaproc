"""The resolved Claude working directory reaches the actual launched subprocess."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.adapters.registry import get_adapter
from metaproc.cli import app


def test_selected_claude_profile_launches_in_resolved_step_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_path = tmp_path / "test.process.md"
    process_path.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: working-directory
          steps:
            - id: write
              mode: agent
              prompt_prefix: Record the working directory.
              adapter:
                type: claude-code-cli
                config:
                  working_directory: "{{run.dir}}"
              outputs:
                cwd:
                  path: "{{run.dir}}/cwd.txt"
                  kind: file
        ---
        """)
    )
    adapter = get_adapter("claude-code-cli")

    def command(
        _prompt_file: Path,
        _config: dict[str, object],
        _variables: dict[str, str],
    ) -> list[str]:
        # Only replace the provider executable. Planning, adapter configuration,
        # cwd resolution, and local subprocess supervision remain real.
        return [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import json; "
                "Path('cwd.txt').write_text(str(Path.cwd())); "
                "print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, "
                "'result': 'written', 'duration_ms': 1}))"
            ),
        ]

    monkeypatch.setattr(adapter, "build_command", command)
    monkeypatch.setattr(adapter, "preflight", lambda: None)
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process_path),
            "--variant",
            "claude-sonnet",
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=cwd",
        ],
    )
    assert result.exit_code == 0, result.output
    run_dir = tmp_path / "runs" / "cwd"
    assert (run_dir / "cwd.txt").read_text() == str(run_dir.resolve())
    assert not (tmp_path / "cwd.txt").exists()
