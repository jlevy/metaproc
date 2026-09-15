"""Expected executor errors remain durable across process and task boundaries."""

from __future__ import annotations

import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands import run_process as run_process_module
from metaproc.engine.fan_in import collect_item_outcomes
from metaproc.engine.input_validation import validate_process_outputs_detailed
from metaproc.engine.run_status import scan_run_status
from metaproc.io import read_yaml_file
from metaproc.io.state_io import read_attempt_history_at, read_status_at
from metaproc.models.authored import ProcessSpec


@pytest.mark.parametrize(
    "source,exception_type,detail",
    [
        (None, "FileNotFoundError", "handler file not found"),
        ("def other(context, step):\n    pass\n", "AttributeError", "function 'fail' not found"),
        ("def fail(\n", "SyntaxError", "was never closed"),
        (
            "raise ImportError('required binding is unavailable')\n",
            "ImportError",
            "required binding",
        ),
        (
            "raise RuntimeError('handler initialization refused')\n",
            "RuntimeError",
            "initialization refused",
        ),
    ],
)
def test_handler_resolution_failure_is_durable_and_does_not_abandon_siblings(
    tmp_path: Path, source: str | None, exception_type: str, detail: str
) -> None:
    if source is not None:
        (tmp_path / "broken.py").write_text(source)
    (tmp_path / "healthy.py").write_text(
        "from pathlib import Path\n"
        "def run(context, step):\n"
        f"    Path({str(tmp_path / 'healthy.txt')!r}).write_text('finished')\n"
    )
    process = tmp_path / "binding.process.md"
    process.write_text(
        "---\nprocess:\n  name: binding\n  steps:\n"
        "    - id: broken\n      mode: code\n      handler: broken.py:fail\n"
        "    - id: healthy\n      mode: code\n      handler: healthy.py:run\n---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=binding",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "binding"
    process_status = read_yaml_file(run / ".state" / "process-status.yaml")
    assert process_status["state"] == "failed"
    assert process_status["steps"]["healthy"]["state"] == "completed"
    assert (tmp_path / "healthy.txt").read_text() == "finished"
    error = process_status["steps"]["broken"]["error"]
    assert exception_type in error and detail in error
    task = run / ".state" / "tasks" / "broken"
    status = read_status_at(task)
    assert status is not None and status.state == "failed"
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error == error
    captured = next((run / ".logs" / "tasks" / "broken").glob("process_*.log"))
    assert str(captured.relative_to(run)) in error
    assert exception_type in captured.read_text() and detail in captured.read_text()


@pytest.mark.parametrize("verb", ["run-step", "run-parallel"])
def test_standalone_code_command_retains_raw_evidence_and_safe_rate_limit_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    secret = "fixture-private-value-" + "s" * 256
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "0.1")
    (tmp_path / "fail.py").write_text(
        "import os, sys\n"
        "print('ProviderError: HTTP 429 Too Many Requests', file=sys.stderr)\n"
        "print('credential=' + os.environ['PROVIDER_API_KEY'], file=sys.stderr)\n"
        "raise SystemExit(7)\n"
    )
    parallel = verb == "run-parallel"
    if parallel:
        (tmp_path / "items.md").write_text(
            "---\nprogress:\n  process: code\n  items:\n    - ticker: AAPL\n---\n"
        )
    process = tmp_path / "code.process.md"
    process.write_text(
        "---\nprocess:\n  name: code\n  steps:\n"
        "    - id: query\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n"
        + (
            "      inputs:\n        items:\n"
            f"          path: {tmp_path / 'items.md'}\n"
            "          kind: file\n          format: frontmatter-md\n"
            "      for_each:\n        over: items\n        bind: ticker\n"
            "        bind_fields: [ticker]\n        key: '{{ticker}}'\n"
            if parallel
            else ""
        )
        + "---\n"
    )
    args = [verb, str(process), "--step", "query"]
    if parallel:
        args.extend(["--items", "AAPL", "--no-retry"])
    args.extend(["--var", f"RUNS_DIR={tmp_path / 'runs'}", "--var", "RUN_ID=failed"])
    result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    run = tmp_path / "runs" / "failed"
    task = run / ".state" / "tasks" / "query"
    logs = run / ".logs" / "tasks" / "query"
    if parallel:
        task /= "AAPL"
        logs /= "AAPL"
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and "HTTP 429" in attempt.error
    assert attempt.failure_class == "rate_limited"
    assert len(attempt.error) < 2_000
    assert secret not in attempt.error and secret not in result.output
    assert "[redacted]" in attempt.error
    captured = next(logs.glob("process_*.log"))
    assert secret in captured.read_text()
    assert str(captured.relative_to(run)) in attempt.error


@pytest.mark.parametrize("verb", ["run-step", "run-parallel"])
def test_standalone_handler_exception_keeps_traceback_without_leaking_env_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    secret = "fixture-handler-private-value-" + "s" * 256
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "0.1")
    (tmp_path / "fail.py").write_text(
        "import os\n"
        "def fail(context, step):\n"
        "    raise RuntimeError('handler refused: ' + os.environ['PROVIDER_API_KEY'])\n"
    )
    parallel = verb == "run-parallel"
    if parallel:
        (tmp_path / "items.md").write_text(
            "---\nprogress:\n  process: code\n  items:\n    - ticker: AAPL\n---\n"
        )
    process = tmp_path / "handler.process.md"
    process.write_text(
        "---\nprocess:\n  name: handler\n  steps:\n"
        "    - id: query\n      mode: code\n      handler: fail.py:fail\n"
        + (
            "      inputs:\n        items:\n"
            f"          path: {tmp_path / 'items.md'}\n"
            "          kind: file\n          format: frontmatter-md\n"
            "      for_each:\n        over: items\n        bind: ticker\n"
            "        bind_fields: [ticker]\n        key: '{{ticker}}'\n"
            if parallel
            else ""
        )
        + "---\n"
    )
    args = [verb, str(process), "--step", "query"]
    if parallel:
        args.extend(["--items", "AAPL", "--no-retry"])
    args.extend(["--var", f"RUNS_DIR={tmp_path / 'runs'}", "--var", "RUN_ID=failed"])
    result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    run = tmp_path / "runs" / "failed"
    task = run / ".state" / "tasks" / "query"
    logs = run / ".logs" / "tasks" / "query"
    if parallel:
        task /= "AAPL"
        logs /= "AAPL"
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and "RuntimeError: handler refused" in attempt.error
    assert len(attempt.error) < 2_000
    assert "[redacted]" in attempt.error
    assert secret not in attempt.error and secret not in result.output
    captured = next(logs.glob("process_*.log"))
    assert secret in captured.read_text()
    assert str(captured.relative_to(run)) in attempt.error


@pytest.mark.parametrize("stream", ["stderr", "stdout"])
def test_command_diagnostic_reaches_attempt_mapped_item_and_root_failure(
    tmp_path: Path, stream: str
) -> None:
    diagnostic = 'RuntimeError: query terms must not contain commas: "Example, Inc."'
    (tmp_path / "fail.py").write_text(
        "import sys\n"
        "print('provider request started')\n"
        f"print({diagnostic!r}, file=sys.{stream})\n"
        "raise SystemExit(7)\n"
    )
    (tmp_path / "child.process.md").write_text(
        "---\nprocess:\n  name: child\n  steps:\n"
        "    - id: query\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n---\n"
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    process = tmp_path / "parent.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: parent
          deps:
            child: {path: ./child.process.md, as: path}
            items: {path: ./items.md, as: path}
          steps:
            - id: child
              mode: composite
              uses: deps.child
              for_each:
                over: deps.items
                bind: item
                bind_fields: [item]
                key: "{{item}}"
        ---
        """)
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=command",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "command"
    leaf = run / "child" / "alfa" / ".state" / "tasks" / "query"
    attempt = read_attempt_history_at(leaf)[0]
    assert attempt.error and diagnostic in attempt.error
    assert "command exit code 7" in attempt.error
    assert diagnostic in collect_item_outcomes(run, "child")[0]["error"]
    root = scan_run_status(run, include_system=False)
    assert root.process_error and diagnostic in root.process_error
    assert "command exit code 7" in root.process_error


@pytest.mark.parametrize("mapped", [False, True])
def test_command_diagnostic_is_bounded_redacted_and_prefers_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapped: bool
) -> None:
    secret = "fixture-private-value-" + "s" * 300
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    diagnostic = "ProviderError: HTTP 429 Too Many Requests; Retry-After: 17"
    (tmp_path / "fail.py").write_text(
        "import os, sys\n"
        'print(\'{"type":"result","status":"success"}\')\n'
        "print('unrelated progress\\n' * 500, file=sys.stderr)\n"
        f"print({diagnostic!r}, file=sys.stderr)\n"
        "print('credential=' + os.environ['PROVIDER_API_KEY'], file=sys.stderr)\n"
        "print('unlabeled ' + os.environ['PROVIDER_API_KEY'].replace('private', '\\x1b[31mprivate\\x1b[0m'), file=sys.stderr)\n"
        "print('Authorization: Bearer fixture-bearer-token', file=sys.stderr)\n"
        "raise SystemExit(9)\n"
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    process = tmp_path / "command.process.md"
    process.write_text(
        "---\nprocess:\n  name: command\n"
        "  deps:\n    items: {path: ./items.md, as: path}\n"
        "  steps:\n    - id: query\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n"
        + (
            "      for_each:\n        over: deps.items\n        bind: item\n"
            "        bind_fields: [item]\n        key: '{{item}}'\n"
            if mapped
            else ""
        )
        + "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=redacted",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "redacted"
    task = run / ".state" / "tasks" / "query"
    if mapped:
        task /= "alfa"
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and diagnostic in attempt.error
    assert "command exit code 9" in attempt.error
    assert attempt.failure_class == "rate_limited"
    assert len(attempt.error) < 2_000
    assert "[redacted]" in attempt.error
    root = scan_run_status(run, include_system=False)
    assert root.process_error and diagnostic in root.process_error
    assert "fixture-private-value" not in root.process_error
    assert "fixture-bearer-token" not in root.process_error
    assert '"status":"success"' not in root.process_error
    logs = run / ".logs" / "tasks" / "query"
    if mapped:
        logs /= "alfa"
    captured = next(logs.glob("process_*.log")).read_text()
    assert secret in captured  # Source evidence is retained; the durable summary is redacted.
    assert "unrelated progress" in captured


@pytest.mark.parametrize("step_id,second", [("quota-check", 29), ("query", 29), ("query", 3)])
def test_command_failure_class_ignores_step_names_and_log_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step_id: str, second: int
) -> None:
    clock = Mock(wraps=datetime)
    clock.now.return_value = datetime(2026, 9, 13, 0, 44 if second == 29 else 5, second, tzinfo=UTC)
    monkeypatch.setattr(run_process_module, "datetime", clock)
    (tmp_path / "fail.py").write_text(
        "import sys\nprint('ValueError: invalid input', file=sys.stderr)\nraise SystemExit(1)\n"
    )
    process = tmp_path / "command.process.md"
    process.write_text(
        "---\nprocess:\n  name: command\n  steps:\n"
        f"    - id: {step_id}\n      mode: code\n"
        f"      command: '{sys.executable} fail.py'\n---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=classification",
        ],
    )
    assert result.exit_code == 1, result.output
    task = tmp_path / "runs" / "classification" / ".state" / "tasks" / step_id
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and "ValueError: invalid input" in attempt.error
    assert attempt.failure_class == "crash"


_CLEANUP_LINES = "\\n".join(f"cleanup line {index}" for index in range(13))


@pytest.mark.parametrize(
    "step_id,handler,diagnostic,attempt_fraction,attempts,failure_class",
    [
        # A permanent word in the step id and log path must not decide retry policy.
        (
            "quota-check",
            False,
            "ProviderError: HTTP 503 Service Unavailable",
            None,
            2,
            "server_error",
        ),
        # Classification reads the rate-limit line that display clipping later drops.
        ("query", True, f"HTTP 429 Too Many Requests\\n{_CLEANUP_LINES}", None, 2, "rate_limited"),
        # A status code inside the attempt id's timestamp fraction must not invent a retry.
        ("query", True, "bad input", "4290000000", 1, "unknown"),
    ],
    ids=["permanent-word-in-step-id", "clipped-rate-limit", "status-code-in-attempt-id"],
)
def test_run_parallel_retry_policy_uses_the_unclipped_path_free_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    step_id: str,
    handler: bool,
    diagnostic: str,
    attempt_fraction: str | None,
    attempts: int,
    failure_class: str,
) -> None:
    monkeypatch.setenv("METAPROC_PREFLIGHT_MIN_DISK_GB", "0.1")
    if attempt_fraction is not None:
        attempt_ids = (
            f"att-20260913T000000Z.{attempt_fraction}.fixture{index}" for index in range(1, 10)
        )
        monkeypatch.setattr(
            "metaproc.io.state_io.new_timestamped_typed_id", lambda _prefix: next(attempt_ids)
        )
    if handler:
        (tmp_path / "fail.py").write_text(
            f"def fail(context, step):\n    raise RuntimeError('{diagnostic}')\n"
        )
        runner = "      handler: fail.py:fail\n"
    else:
        (tmp_path / "fail.py").write_text(
            f"import sys\nprint({diagnostic!r}, file=sys.stderr)\nraise SystemExit(1)\n"
        )
        runner = f"      command: '{sys.executable} fail.py'\n"
    (tmp_path / "items.md").write_text(
        "---\nprogress:\n  process: code\n  items:\n    - ticker: AAPL\n---\n"
    )
    process = tmp_path / "code.process.md"
    process.write_text(
        "---\nprocess:\n  name: code\n  steps:\n"
        f"    - id: {step_id}\n      mode: code\n" + runner + "      inputs:\n        items:\n"
        f"          path: {tmp_path / 'items.md'}\n"
        "          kind: file\n          format: frontmatter-md\n"
        "      for_each:\n        over: items\n        bind: ticker\n"
        "        bind_fields: [ticker]\n        key: '{{ticker}}'\n"
        "        retry: {max_retries: 1, initial_backoff_s: 0}\n"
        "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-parallel",
            str(process),
            "--step",
            step_id,
            "--items",
            "AAPL",
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=classification",
        ],
    )
    assert result.exit_code != 0
    task = tmp_path / "runs" / "classification" / ".state" / "tasks" / step_id / "AAPL"
    history = read_attempt_history_at(task)
    assert [attempt.failure_class for attempt in history] == [failure_class] * attempts
    assert history[-1].disposition == ("retryable" if attempts > 1 else "permanent")
    status = read_status_at(task)
    assert status is not None and status.failure_class == failure_class


@pytest.mark.parametrize("handler", [False, True])
def test_identical_mapped_failures_count_as_one_cause_despite_per_item_evidence(
    tmp_path: Path, handler: bool
) -> None:
    diagnostic = "ProviderError: HTTP 503 Service Unavailable"
    if handler:
        (tmp_path / "fail.py").write_text(
            f"def fail(context, step):\n    raise RuntimeError({diagnostic!r})\n"
        )
        runner = "      handler: fail.py:fail\n"
        cause = f"RuntimeError: {diagnostic}"
    else:
        (tmp_path / "fail.py").write_text(
            f"import sys\nprint({diagnostic!r}, file=sys.stderr)\nraise SystemExit(1)\n"
        )
        runner = f"      command: '{sys.executable} fail.py'\n"
        cause = f"command exit code 1 (stderr: {diagnostic})"
    (tmp_path / "items.md").write_text(
        "---\nprogress:\n  items:\n    - item: alfa\n    - item: brvo\n    - item: chrl\n---\n"
    )
    process = tmp_path / "mapped.process.md"
    process.write_text(
        "---\nprocess:\n  name: mapped\n"
        "  deps:\n    items: {path: ./items.md, as: path}\n"
        "  steps:\n    - id: query\n      mode: code\n"
        + runner
        + "      for_each:\n        over: deps.items\n        bind: item\n"
        "        bind_fields: [item]\n        key: '{{item}}'\n"
        "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=mapped",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "mapped"
    expected = f"3 of 3 items failed (3 x {cause})"
    process_status = read_yaml_file(run / ".state" / "process-status.yaml")
    assert process_status["steps"]["query"]["error"] == expected
    assert scan_run_status(run, include_system=False).process_error == f"query: {expected}"
    for outcome in collect_item_outcomes(run, "query"):
        # Each item keeps the pointer to its own retained evidence.
        assert f".logs/tasks/query/{outcome['key']}/process_" in outcome["error"]


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("terminator", [None, "\x1b\\", "\x07"])
def test_handler_subprocess_failure_keeps_full_evidence_and_safe_durable_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapped: bool, terminator: str | None
) -> None:
    secret = "opaque-private-value"
    monkeypatch.setenv("PROVIDER_API_KEY", secret)
    decorated_secret = (
        "opaque-\x1b[31mprivate\x1b[0m-value"
        if terminator is None
        else f"opaque-\x1b]8;;https://example.invalid{terminator}private\x1b]8;;{terminator}-value"
    )
    diagnostic = (
        "HTTP 429 Too Many Requests\n"
        + "cleanup completed " * 100
        + "\ncleanup completed\n" * 20
        + f"unlabeled {decorated_secret}\n"
        + "Authorization: Bearer fixture-bearer-token\n"
        + "request refused; Retry-After: 17\n"
    )
    (tmp_path / "provider.py").write_text(
        f"import sys\nsys.stderr.write({diagnostic!r})\nraise SystemExit(7)\n"
    )
    (tmp_path / "handler.py").write_text(
        "import subprocess, sys\nfrom pathlib import Path\n"
        "def fail(_context, _step):\n"
        "    result = subprocess.run([sys.executable, str(Path(__file__).with_name('provider.py'))], "
        "capture_output=True, text=True, check=False)\n"
        "    raise RuntimeError(f'provider subprocess exited {result.returncode}: {result.stderr}')\n"
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    process = tmp_path / "handler.process.md"
    process.write_text(
        "---\nprocess:\n  name: handler\n"
        "  deps:\n    items: {path: ./items.md, as: path}\n"
        "  steps:\n    - id: query\n      mode: code\n"
        "      handler: handler.py:fail\n"
        + (
            "      for_each:\n        over: deps.items\n        bind: item\n"
            "        bind_fields: [item]\n        key: '{{item}}'\n"
            if mapped
            else ""
        )
        + "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=handler-evidence",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "handler-evidence"
    task = run / ".state" / "tasks" / "query"
    logs = run / ".logs" / "tasks" / "query"
    if mapped:
        task /= "alfa"
        logs /= "alfa"
    attempt = read_attempt_history_at(task)[0]
    assert attempt.error and attempt.error.startswith("RuntimeError:")
    assert "request refused; Retry-After: 17" in attempt.error
    assert attempt.failure_class == "rate_limited"
    assert len(attempt.error) < 1_600
    assert "[truncated]" in attempt.error
    assert "HTTP 429" not in attempt.error  # Classification precedes display clipping.
    assert "command exit code" not in attempt.error
    root = scan_run_status(run, include_system=False)
    assert root.process_error and "request refused" in root.process_error
    assert "[redacted]" in root.process_error
    for value in ("opaque-", "private", "fixture-bearer-token", "\x1b"):
        assert value not in root.process_error
    log_file = next(logs.glob("process_*.log"))
    assert str(log_file.relative_to(run)) in attempt.error
    captured = log_file.read_text()
    assert diagnostic in captured
    assert "RuntimeError: provider subprocess exited 7:" in captured


@pytest.mark.parametrize("mapped", [False, True])
def test_composite_output_failure_retains_path_message_and_failed_scope(
    tmp_path: Path,
    mapped: bool,
) -> None:
    (tmp_path / "child.process.md").write_text(
        textwrap.dedent("""\
        ---
        process:
          name: child
          outputs:
            report: {path: "{{run.dir}}/missing.md", as: path}
          steps:
            - {id: noop, mode: code, command: "true"}
        ---
        """)
    )
    (tmp_path / "items.md").write_text("---\nprogress:\n  items:\n    - item: alfa\n---\n")
    for_each = (
        """\
      for_each:
        over: deps.items
        bind: item
        bind_fields: [item]
        key: "{{item}}"
"""
        if mapped
        else ""
    )
    process = tmp_path / "parent.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: parent
          deps:
            child: {path: ./child.process.md, as: path}
            items: {path: ./items.md, as: path}
          steps:
            - id: child
              mode: composite
              uses: deps.child
        """)
        + for_each
        + "---\n"
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=boundary",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "boundary"
    child = run / "child" / "alfa" if mapped else run / "child"
    expected_path = str(child / "missing.md")
    child_status = read_yaml_file(child / ".state" / "process-status.yaml")
    assert child_status["state"] == "failed"
    assert expected_path in child_status["error"]
    parent_status = scan_run_status(run, include_system=False)
    assert parent_status.process_error and expected_path in parent_status.process_error
    if mapped:
        failure = collect_item_outcomes(run, "child")[0]["output_failures"][0]
    else:
        failure = read_yaml_file(run / ".state" / "process-status.yaml")["steps"]["child"][
            "output_failures"
        ][0]
    assert failure["output"] == "report"
    assert failure["path"] == expected_path
    assert failure["kind"] == "missing"
    assert failure["message"] == f"path does not exist: {expected_path}"


@pytest.mark.parametrize("missing_input", [False, True])
def test_manual_refusal_finishes_step_with_the_actual_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_input: bool,
) -> None:
    process = tmp_path / "manual.process.md"
    inputs = (
        "      inputs:\n        prerequisite: {path: '{{run.dir}}/missing-input.txt'}\n"
        if missing_input
        else ""
    )
    process.write_text(
        "---\nprocess:\n  name: manual\n  steps:\n    - id: approve\n      mode: manual\n"
        + inputs
        + "---\n"
    )
    monkeypatch.setattr("metaproc.commands.run_process.MANUAL_ACK_TIMEOUT_S", 0)
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=timeout",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "timeout"
    state = read_status_at(run / ".state" / "tasks" / "approve")
    if missing_input:
        assert state is None  # Input rejection happens before an attempt starts.
        error = "missing-input.txt"
    else:
        assert state is not None and state.state == "failed"
        assert state.error and "manual acknowledgment not received" in state.error
        error = state.error
    status = scan_run_status(run, include_system=False)
    assert status.process_execution_state == "failed"
    assert status.process_error and error in status.process_error


def test_root_output_refusal_is_a_failed_process_even_when_all_steps_completed(
    tmp_path: Path,
) -> None:
    process = tmp_path / "root.process.md"
    process.write_text(
        textwrap.dedent("""\
        ---
        process:
          name: root
          outputs:
            report: {path: "{{run.dir}}/missing.md", as: path}
          steps:
            - {id: noop, mode: code, command: "true"}
        ---
        """)
    )
    result = CliRunner().invoke(
        app,
        [
            "run-process",
            str(process),
            "--var",
            f"RUNS_DIR={tmp_path / 'runs'}",
            "--var",
            "RUN_ID=root",
        ],
    )
    assert result.exit_code == 1, result.output
    run = tmp_path / "runs" / "root"
    raw = read_yaml_file(run / ".state" / "process-status.yaml")
    assert raw["steps"]["noop"]["state"] == "completed"
    assert raw["state"] == "failed"
    assert raw["output_failures"][0]["path"] == str(run / "missing.md")
    status = scan_run_status(run, include_system=False)
    assert status.process_error and "missing.md" in status.process_error


def test_process_output_read_error_retains_evidence_without_inventing_unresolved_paths(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory.md"
    directory.mkdir()
    spec = ProcessSpec.model_validate(
        {
            "name": "output-evidence",
            "outputs": {
                "unreadable": {"path": str(directory), "as": "path", "format": "frontmatter-md"},
                "unresolved": {"ref": "unknown.output", "as": "path"},
            },
        }
    )
    result = validate_process_outputs_detailed(spec, {}, tmp_path)
    assert len(result.errors) == 2
    assert len(result.output_failures) == 1
    failure = result.output_failures[0]
    assert failure.path == str(directory)
    assert failure.kind == "unreadable"
    assert "could not read output" in failure.message
    assert "plan is required" in result.errors[1]
