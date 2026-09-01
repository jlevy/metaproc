"""When a nonzero exit is overridden, and when it is not.

Some adapters write every declared output, emit a terminal success record, and then exit
nonzero while shutting down. Failing the step on that discards finished work, so the
harness overrides the exit code when the agent's own verdict and the artifacts on disk
both say the work is done.

The override is the interesting part, so these fix its edges: it must not swallow a kill
this harness itself ordered, it must ask the same question the ordinary completion path
asks, and when it fires the step must not look like an ordinary clean pass afterwards.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from metaproc.adapters.registry import ADAPTER_REGISTRY
from metaproc.commands.run_process import _execute_agent_step
from metaproc.engine.pathing import compute_task_state_dir
from metaproc.io.state_io import read_attempt_history_at
from metaproc.models.authored import IOSpec, ProcessDefaults, ProcessSpec, ProcessStep, RetryPolicy
from metaproc.models.plan import ResolvedAdapter, ResolvedStep

ADAPTER_TYPE = "exit-rescue-test"


class _Out:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.warnings: list[str] = []

    def progress(self, message: str) -> None:
        self.messages.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _StubAdapter:
    """Never actually launched: `_run_scalar_agent_subprocess` is replaced below.

    The seam matters. Faking the launch is what lets a pool kill and a plain nonzero exit
    differ by exactly one value, which is the distinction under test; driving a real
    RunPool to a kill would vary a dozen other things at the same time.
    """

    adapter_type = ADAPTER_TYPE
    short_name = ADAPTER_TYPE
    default_model = None
    slot_credential_filename = "credential.txt"
    compatible_fallback_adapters: list[str] = []  # noqa: RUF012

    def build_command(
        self, _prompt_file: Path, _merged_config: dict[str, object], _variables: dict[str, str]
    ) -> list[str]:
        return ["true"]

    def prepare_env(self, env: dict[str, str], _merged_config: dict[str, object]) -> dict[str, str]:
        return env

    def working_directory(self, _merged_config: dict[str, object]) -> Path | None:
        return None

    def parse_result_event(self, _line: str) -> dict[str, object] | None:
        return None

    def check_auth(self) -> object:
        return object()

    def auth_info(self) -> str:
        return ""

    def validate_config(self, _merged_config: dict[str, object]) -> list[object]:
        return []

    def bootstrap(self, _home: Path) -> None:
        return None


def _install_launch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_code: int,
    kill_reason: str | None,
    claim: str | None,
    writes: dict[Path, str],
) -> None:
    """Stand in for the agent launch: write what the agent would have written."""

    async def _fake_launch(*_args: Any, **kwargs: Any) -> tuple[int, str | None]:
        log_path = Path(kwargs["log_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ['{"type":"message","role":"assistant","content":"done"}']
        if claim is not None:
            lines.append(json.dumps({"type": "result", "status": claim}))
        log_path.write_text("\n".join(lines) + "\n")
        for path, text in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return exit_code, kill_reason

    monkeypatch.setattr("metaproc.commands.run_process._run_scalar_agent_subprocess", _fake_launch)


def _run(
    tmp_path: Path,
    *,
    outputs: dict[str, IOSpec],
    variant: str | None = None,
) -> tuple[bool, Path, _Out, dict[str, str]]:
    runs_dir = tmp_path / "runs"
    run_id = "exit-rescue-run"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    step_def = ProcessStep(
        id="scalar-agent", mode="agent", prompt_prefix="test", outputs=outputs, variant=variant
    )
    spec = ProcessSpec(
        name="exit-rescue",
        defaults=ProcessDefaults(retry=RetryPolicy(max_retries=0, initial_backoff_s=0)),
        steps=[step_def],
    )
    target = ResolvedStep(
        step_id=step_def.id,
        mode="agent",
        adapter=ResolvedAdapter(type=ADAPTER_TYPE),
        prompt_prefix="test",
        outputs=outputs,
        variant=variant,
        artifact_namespace=variant,
    )
    variables = {"RUNS_DIR": str(runs_dir), "RUN_ID": run_id}
    out = _Out()
    succeeded = asyncio.run(
        _execute_agent_step(
            spec=spec,
            step_def=step_def,
            target=target,
            variables=variables,
            process_dir=tmp_path,
            run_dir=run_dir,
            run_id=run_id,
            out=out,
        )
    )
    state_dir = compute_task_state_dir(run_dir, step_def, {**variables, "VARIANT": variant or ""})
    return succeeded, state_dir, out, variables


@pytest.fixture(autouse=True)
def _register_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ADAPTER_REGISTRY, ADAPTER_TYPE, _StubAdapter())


class TestARescuedExit:
    def test_a_nonzero_exit_is_overridden_when_the_claim_and_the_artifacts_agree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = tmp_path / "runs" / "exit-rescue-run" / "summary.md"
        _install_launch(
            monkeypatch,
            exit_code=1,
            kill_reason=None,
            claim="success",
            writes={summary: "# summary\n"},
        )
        succeeded, _state_dir, out, _vars = _run(
            tmp_path, outputs={"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
        )
        assert succeeded is True
        assert any("shutdown warning" in message for message in out.messages)

    def test_the_accepted_anomaly_survives_in_the_attempt_history(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Progress output is suppressible. If the anomaly lives only there, the one
        signal that this step passed on a relaxed rule is missing from the artifact an
        operator reads afterwards, and replay shows an ordinary clean success."""
        summary = tmp_path / "runs" / "exit-rescue-run" / "summary.md"
        _install_launch(
            monkeypatch,
            exit_code=1,
            kill_reason=None,
            claim="success",
            writes={summary: "# summary\n"},
        )
        _succeeded, state_dir, _out, _vars = _run(
            tmp_path, outputs={"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
        )
        history = read_attempt_history_at(state_dir)
        assert history, "a rescued attempt still has durable history"
        anomalies = [note for attempt in history for note in attempt.anomalies]
        assert anomalies, "the accepted exit code must be recorded, not only printed"
        assert any("exit code 1" in note for note in anomalies)

    def test_a_clean_pass_records_no_anomaly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards the field against becoming noise on every successful attempt."""
        summary = tmp_path / "runs" / "exit-rescue-run" / "summary.md"
        _install_launch(
            monkeypatch,
            exit_code=0,
            kill_reason=None,
            claim="success",
            writes={summary: "# summary\n"},
        )
        succeeded, state_dir, _out, _vars = _run(
            tmp_path, outputs={"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
        )
        assert succeeded is True
        assert [
            note for attempt in read_attempt_history_at(state_dir) for note in attempt.anomalies
        ] == []


class TestASupervisorKillIsNotRescuable:
    def test_a_pool_kill_fails_the_step_even_with_valid_outputs_and_a_success_claim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Identical to the rescued case in every respect except the kill reason.

        The evidence behind the override is about exit codes an adapter produces while
        shutting itself down. It says nothing about a process this harness decided to
        terminate, and a killed process that happened to write its outputs first is still
        a step the harness stopped on purpose.
        """
        summary = tmp_path / "runs" / "exit-rescue-run" / "summary.md"
        _install_launch(
            monkeypatch,
            exit_code=1,
            kill_reason="capacity ceiling breached",
            claim="success",
            writes={summary: "# summary\n"},
        )
        succeeded, _state_dir, out, _vars = _run(
            tmp_path, outputs={"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
        )
        assert succeeded is False
        assert not any("shutdown warning" in message for message in out.messages)


class TestTheCheckUsesTheStepsOwnBindings:
    def test_a_variant_bearing_output_path_is_resolved_the_way_the_step_writes_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only step_vars binds VARIANT.

        With the parent variables, `{{run.variant}}` renders as the literal token, so the
        precheck looks for an artifact at a path nothing writes, finds nothing, and
        declines a rescue the evidence supports. The agent here writes at the correct
        resolved path, so a passing rescue is proof the right bindings were used.
        """
        written = tmp_path / "runs" / "exit-rescue-run" / "alt-profile" / "summary.md"
        _install_launch(
            monkeypatch,
            exit_code=1,
            kill_reason=None,
            claim="success",
            writes={written: "# summary\n"},
        )
        succeeded, _state_dir, out, _vars = _run(
            tmp_path,
            outputs={"summary": IOSpec(path="{{run.dir}}/{{run.variant}}/summary.md", kind="file")},
            variant="alt-profile",
        )
        assert succeeded is True, (
            "the rescue must resolve the output path with the same bindings the step "
            "writes it under"
        )
        assert any("shutdown warning" in message for message in out.messages)


class TestClaimsWithoutArtifacts:
    def test_an_invalid_output_is_not_rescued(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The claim alone cannot carry it, or a lying adapter manufactures a pass."""
        _install_launch(monkeypatch, exit_code=1, kill_reason=None, claim="success", writes={})
        succeeded, _state_dir, out, _vars = _run(
            tmp_path, outputs={"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
        )
        assert succeeded is False
        assert not any("shutdown warning" in message for message in out.messages)

    def test_a_step_with_no_declared_outputs_is_never_rescued(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """There is nothing to check the claim against, so the exit code stands."""
        _install_launch(monkeypatch, exit_code=1, kill_reason=None, claim="success", writes={})
        succeeded, _state_dir, out, _vars = _run(tmp_path, outputs={})
        assert succeeded is False
        assert not any("shutdown warning" in message for message in out.messages)

    def test_an_agent_that_claims_failure_is_not_rescued(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        summary = tmp_path / "runs" / "exit-rescue-run" / "summary.md"
        _install_launch(
            monkeypatch,
            exit_code=1,
            kill_reason=None,
            claim="error",
            writes={summary: "# summary\n"},
        )
        succeeded, _state_dir, _out, _vars = _run(
            tmp_path, outputs={"summary": IOSpec(path="{{run.dir}}/summary.md", kind="file")}
        )
        assert succeeded is False
