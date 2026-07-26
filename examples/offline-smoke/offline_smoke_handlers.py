"""Code-mode handlers for the deterministic offline quickstart."""

from __future__ import annotations

from pathlib import Path

from metaproc.models.authored import ProcessStep


def _record_invocation(variables: dict[str, str], step_id: str) -> None:
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "invocations.log").open("a").write(f"{step_id}\n")


def step_one(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    _record_invocation(variables, "s1")
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    (run_dir / "s1.txt").write_text("s1 done\n")


def step_two(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    _record_invocation(variables, "s2")
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    (run_dir / "s2.txt").write_text("s2 done\n")


def step_three(variables: dict[str, str], step: ProcessStep) -> None:  # noqa: ARG001
    _record_invocation(variables, "s3")
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    (run_dir / "s3.txt").write_text("s3 done\n")
