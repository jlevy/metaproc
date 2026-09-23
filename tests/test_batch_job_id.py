"""Direct coverage for ``build_job_id``, the GCP Batch job-id budget.

The orchestrator and worker dispatch tests both go through this helper, but each
exercises only the branch its own prefix and suffix lengths happen to select. The
truncation rule is the part that matters — a Batch job id is capped at 63
characters, and the uniqueness suffix must survive the cap — so it is pinned here
rather than inferred from a dispatch assertion.
"""

from __future__ import annotations

from metaproc.cloud.gcp.batch_backend import build_job_id

MAX_LEN = 63
PREFIX = "mp-orch"
SUFFIX = "1776328315-abc123"
# Two joining hyphens plus the prefix and suffix are unavailable to the run id.
AVAILABLE = MAX_LEN - len(PREFIX) - len(SUFFIX) - 2


def test_run_id_at_the_budget_is_carried_whole() -> None:
    run_id = "e" * AVAILABLE

    job_id = build_job_id(prefix=PREFIX, run_id=run_id, suffix=SUFFIX)

    assert job_id == f"{PREFIX}-{run_id}-{SUFFIX}"
    assert len(job_id) == MAX_LEN


def test_run_id_over_the_budget_is_truncated_and_the_suffix_survives() -> None:
    run_id = "e" * AVAILABLE + "-tail-segment"

    job_id = build_job_id(prefix=PREFIX, run_id=run_id, suffix=SUFFIX)

    assert "tail" not in job_id
    assert job_id == f"{PREFIX}-{'e' * AVAILABLE}-{SUFFIX}"
    assert len(job_id) == MAX_LEN


def test_truncation_never_leaves_a_trailing_hyphen() -> None:
    # Cutting at the budget lands on the hyphen, which must not end the run part.
    run_id = "e" * (AVAILABLE - 1) + "-tail"

    job_id = build_job_id(prefix=PREFIX, run_id=run_id, suffix=SUFFIX)

    assert job_id == f"{PREFIX}-{'e' * (AVAILABLE - 1)}-{SUFFIX}"
    assert "--" not in job_id


def test_run_id_is_dropped_when_prefix_and_suffix_fill_the_budget() -> None:
    suffix = "s" * (MAX_LEN - len(PREFIX) - 2)

    job_id = build_job_id(prefix=PREFIX, run_id="some-run-id", suffix=suffix)

    assert job_id == f"{PREFIX}-{suffix}"
    assert len(job_id) <= MAX_LEN


def test_empty_run_id_and_suffix_collapse_to_the_prefix() -> None:
    assert build_job_id(prefix=PREFIX, run_id="", suffix="") == PREFIX


def test_illegal_characters_are_sanitized_before_the_budget_is_applied() -> None:
    job_id = build_job_id(prefix="MP_Orch", run_id="Run_ID/42", suffix="x1")

    assert job_id == "mp-orch-run-id-42-x1"


def test_a_leading_nonalpha_gets_an_alphabetic_prefix() -> None:
    job_id = build_job_id(prefix="9pool", run_id="run", suffix="x1")

    assert job_id == "a9pool-run-x1"
    assert job_id[0].isalpha()


def test_everything_empty_falls_back_to_a_valid_id() -> None:
    assert build_job_id(prefix="", run_id="", suffix="") == "job"
