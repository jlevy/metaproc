from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.helpers import enrich_single_item
from metaproc.errors import CLIError
from metaproc.models.authored import ForEach, IOSpec, ProcessStep

RUNNER = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("RUNS_DIR", "RUN_ID", "VARIANT"):
        monkeypatch.delenv(key, raising=False)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _runs_var(tmp_path: Path) -> list[str]:
    return ["--var", f"RUNS_DIR={tmp_path / 'runs'}"]


# ── Existing tests ────────────────────────────────────────────────


def test_run_step_rejects_composite_step(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    child_path = tmp_path / "child/child.process.md"

    _write(
        process_path,
        """---
process:
  name: root
  deps:
    child_process:
      path: "./child/child.process.md"
      as: path
  steps:
    - id: child
      mode: composite
      uses: deps.child_process
---
# root
""",
    )
    _write(
        child_path,
        """---
process:
  name: child
---
# child
""",
    )

    result = RUNNER.invoke(
        app,
        ["run-step", str(process_path), "--step", "child", "--dry-run", *_runs_var(tmp_path)],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "mode:composite" in error_text
    assert "child/child.process.md" in error_text


def test_run_step_errors_on_unresolved_spec_placeholders(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"

    _write(
        process_path,
        """---
process:
  name: root
  defaults:
    default_adapter: claude-code-cli
    adapters:
      claude-code-cli:
        type: claude-code-cli
        config:
          permission_mode: bypassPermissions
  steps:
    - id: s1
      mode: agent
      prompt_prefix: Hello
      outputs:
        main:
          path: "{{MISSING_ROOT}}/out.md"
---
# root
""",
    )

    result = RUNNER.invoke(
        app,
        ["run-step", str(process_path), "--step", "s1", "--dry-run", *_runs_var(tmp_path)],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "unresolved placeholders in process spec" in error_text
    assert "MISSING_ROOT" in error_text


def test_run_step_executes_code_mode_and_resolves_environment(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write(
        process_path,
        """---
process:
  name: executable
  inputs:
    message: {param: MESSAGE, as: string}
  steps:
    - id: verify-env
      mode: code
      command: /bin/sh -c 'test "$VALUE" = resolved'
      env:
        VALUE: "{{MESSAGE}}"
---
# Executable
""",
    )

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "verify-env",
            "--var",
            "MESSAGE=resolved",
            "--var",
            "RUN_ID=test-run",
            *_runs_var(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "completed (code mode)" in result.output


# ── for_each --item tests ────────────────────────────────────────


_PROCESS_WITH_FOR_EACH = """\
---
process:
  name: test-predict
  defaults:
    default_adapter: claude-code-cli
    adapters:
      claude-code-cli:
        type: claude-code-cli
        config:
          permission_mode: bypassPermissions
          timeout_s: 60
  inputs:
    requested_run: {{ param: RUN_ID, as: string }}
    requested_date: {{ param: EARNINGS_DATE, as: string }}
    requested_mode: {{ param: RUN_MODE, as: string }}
  steps:
    - id: fan-out-step
      mode: agent
      inputs:
        tickers:
          path: "{source_path}"
          kind: file
          format: frontmatter-md
      for_each:
        over: tickers
        bind: ticker
        bind_fields: [ticker, sector, earnings_date]
      prompt_prefix: "Process {{{{ticker}}}} in {{{{sector}}}} sector for {{{{earnings_date}}}}"
      outputs:
        record:
          path: "{source_dir}/{{{{ticker}}}}/record.md"
---
# test
"""

_TICKERS_MD = """\
---
progress:
  earnings_date: '2026-01-29'
  process: predict
  items:
  - ticker: AAPL
    sector: technology
    earnings_date: '2026-01-29'
  - ticker: GOOG
    sector: communication_services
    earnings_date: '2026-01-29'
  - ticker: BZH
    sector: consumer_discretionary
    earnings_date: '2026-01-29'
---
# Tickers
"""


def test_run_step_item_flag_resolves_for_each_placeholders(tmp_path: Path) -> None:
    """--item enriches variables from source so placeholders resolve."""
    tickers_path = tmp_path / "tickers.md"
    _write(tickers_path, _TICKERS_MD)

    process_content = _PROCESS_WITH_FOR_EACH.format(
        source_path=str(tickers_path),
        source_dir="{{run.dir}}/outputs",
    )
    process_path = tmp_path / "test.process.md"
    _write(process_path, process_content)

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "fan-out-step",
            "--item",
            "AAPL",
            "--var",
            "RUN_ID=test-run",
            "--var",
            "EARNINGS_DATE=2026-01-29",
            "--var",
            "RUN_MODE=backtest",
            *_runs_var(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, f"Unexpected failure: {result.output}"
    # Verify placeholders were resolved in the prompt.
    assert "AAPL" in result.output
    assert "technology" in result.output
    assert "{{ticker}}" not in result.output
    assert "{{sector}}" not in result.output


def test_run_step_item_flag_not_found_in_source(tmp_path: Path) -> None:
    """--item errors clearly when the item is not in the source file."""
    tickers_path = tmp_path / "tickers.md"
    _write(tickers_path, _TICKERS_MD)

    process_content = _PROCESS_WITH_FOR_EACH.format(
        source_path=str(tickers_path),
        source_dir="{{run.dir}}/outputs",
    )
    process_path = tmp_path / "test.process.md"
    _write(process_path, process_content)

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "fan-out-step",
            "--item",
            "NONEXISTENT",
            "--var",
            "RUN_ID=test-run",
            "--var",
            "EARNINGS_DATE=2026-01-29",
            "--var",
            "RUN_MODE=backtest",
            *_runs_var(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "NONEXISTENT" in error_text
    assert "not found" in error_text


def test_run_step_item_flag_preserves_authored_case(tmp_path: Path) -> None:
    """--item enrichment uses the exact declared field names from for_each."""
    tickers_path = tmp_path / "progress.md"
    _write(
        tickers_path,
        """\
---
progress:
  process: test
  run_id: test-run
  items:
  - Ticker: AAPL
    Sector: technology
---
# Progress
""",
    )

    process_path = tmp_path / "test.process.md"
    _write(
        process_path,
        f"""---
process:
  name: test-predict
  defaults:
    default_adapter: claude-code-cli
    adapters:
      claude-code-cli:
        type: claude-code-cli
        config:
          permission_mode: bypassPermissions
          timeout_s: 60
  inputs:
    requested_run: {{ param: RUN_ID, as: string }}
  steps:
    - id: fan-out-step
      mode: agent
      inputs:
        tickers:
          path: "{tickers_path}"
          kind: file
          format: frontmatter-md
      for_each:
        over: tickers
        bind: Ticker
        bind_fields: [Ticker, Sector]
      prompt_prefix: "Process {{{{Ticker}}}} in {{{{Sector}}}}"
      outputs:
        record:
          path: "{{{{run.dir}}}}/outputs/{{{{Ticker}}}}/record.md"
---
# test
""",
    )

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "fan-out-step",
            "--item",
            "AAPL",
            "--var",
            "RUN_ID=test-run",
            *_runs_var(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, f"Unexpected failure: {result.output}"
    assert "AAPL" in result.output
    assert "technology" in result.output
    assert "{{Ticker}}" not in result.output
    assert "{{Sector}}" not in result.output


def test_run_step_for_each_without_item_flag_errors(tmp_path: Path) -> None:
    """for_each step without --item or --var fields gives a clear error."""
    tickers_path = tmp_path / "tickers.md"
    _write(tickers_path, _TICKERS_MD)

    process_content = _PROCESS_WITH_FOR_EACH.format(
        source_path=str(tickers_path),
        source_dir="{{run.dir}}/outputs",
    )
    process_path = tmp_path / "test.process.md"
    _write(process_path, process_content)

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "fan-out-step",
            "--var",
            "RUN_ID=test-run",
            "--var",
            "EARNINGS_DATE=2026-01-29",
            "--var",
            "RUN_MODE=backtest",
            *_runs_var(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "for_each" in error_text
    assert "run-parallel" in error_text


def test_run_step_for_each_with_var_fields_passes(tmp_path: Path) -> None:
    """for_each step works without --item if all fields provided via --var."""
    tickers_path = tmp_path / "tickers.md"
    _write(tickers_path, _TICKERS_MD)

    process_content = _PROCESS_WITH_FOR_EACH.format(
        source_path=str(tickers_path),
        source_dir="{{run.dir}}/outputs",
    )
    process_path = tmp_path / "test.process.md"
    _write(process_path, process_content)

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "fan-out-step",
            "--var",
            "RUN_ID=test-run",
            "--var",
            "EARNINGS_DATE=2026-01-29",
            "--var",
            "RUN_MODE=backtest",
            "--var",
            "ticker=AAPL",
            "--var",
            "sector=technology",
            "--var",
            "earnings_date=2026-01-29",
            *_runs_var(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, f"Unexpected failure: {result.output}"
    assert "AAPL" in result.output


def test_run_step_validates_process_inputs_by_default(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write(
        process_path,
        """---
process:
  name: process-inputs
  inputs:
    ticker:
      param: TICKER
      as: string
  steps:
    - id: s1
      mode: code
      command: echo hello
---
# process-inputs
""",
    )

    result = RUNNER.invoke(
        app,
        ["run-step", str(process_path), "--step", "s1", "--dry-run", *_runs_var(tmp_path)],
    )

    assert result.exit_code != 0
    error_text = result.output or str(result.exception)
    assert "process input validation failed" in error_text
    assert "TICKER" in error_text


def test_run_step_no_validate_skips_process_input_contract(tmp_path: Path) -> None:
    process_path = tmp_path / "test.process.md"
    _write(
        process_path,
        """---
process:
  name: process-inputs
  inputs:
    ticker:
      param: TICKER
      as: string
  steps:
    - id: s1
      mode: code
      command: echo hello
---
# process-inputs
""",
    )

    result = RUNNER.invoke(
        app,
        [
            "run-step",
            str(process_path),
            "--step",
            "s1",
            "--dry-run",
            "--no-validate",
            *_runs_var(tmp_path),
        ],
    )

    assert result.exit_code == 0, f"Unexpected failure: {result.output}"
    assert "=== DRY RUN ===" in result.output


# ── enrich_single_item unit tests ────────────────────────────────


class TestEnrichSingleItem:
    def _make_step_def(self, source: str) -> ProcessStep:
        return ProcessStep(
            id="test-step",
            mode="agent",
            inputs={
                "source": IOSpec.model_validate(
                    {"path": source, "kind": "file", "format": "frontmatter-md"}
                )
            },
            for_each=ForEach.model_validate(
                {
                    "over": "source",
                    "bind": "ticker",
                    "bind_fields": ["ticker", "sector", "earnings_date"],
                }
            ),
        )

    def test_enriches_from_source_file(self, tmp_path: Path) -> None:
        tickers_path = tmp_path / "tickers.md"
        _write(tickers_path, _TICKERS_MD)
        step_def = self._make_step_def(str(tickers_path))

        result = enrich_single_item("test-step", step_def, "GOOG", {})

        assert result["ticker"] == "GOOG"
        assert result["sector"] == "communication_services"
        assert result["earnings_date"] == "2026-01-29"

    def test_item_not_found_raises(self, tmp_path: Path) -> None:
        tickers_path = tmp_path / "tickers.md"
        _write(tickers_path, _TICKERS_MD)
        step_def = self._make_step_def(str(tickers_path))

        with pytest.raises(CLIError, match="not found"):
            enrich_single_item("test-step", step_def, "MISSING", {})

    def test_no_for_each_raises(self) -> None:
        step_def = ProcessStep(id="plain", mode="agent")
        with pytest.raises(CLIError, match="does not declare for_each"):
            enrich_single_item("plain", step_def, "AAPL", {})

    def test_no_extra_fields_returns_primary_key(self) -> None:
        step_def = ProcessStep(
            id="simple",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "source", "bind": "ticker", "bind_fields": ["ticker"]}
            ),
        )
        result = enrich_single_item("simple", step_def, "AAPL", {})
        assert result == {"ticker": "AAPL"}

    def test_no_source_raises(self) -> None:
        step_def = ProcessStep(
            id="no-src",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "source", "bind": "ticker", "bind_fields": ["ticker", "sector"]}
            ),
        )
        with pytest.raises(CLIError, match="no resolved for_each.over input is available"):
            enrich_single_item("no-src", step_def, "AAPL", {})
