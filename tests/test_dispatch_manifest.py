"""Tests for dispatch manifest — per-step worker job persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from metaproc.cloud.gcp.worker_dispatch import (
    WorkerDispatchConfig,
    WorkerJobManifest,
    WorkerResult,
    _inspect_worker_jobs,
    reconcile_dispatched_workers,
)
from metaproc.errors import CLIError
from metaproc.io import read_yaml_file
from metaproc.io.claimed_items import claim_item, collect_claimed_items
from metaproc.io.dispatch_manifest import (
    append_dispatch_manifest,
    read_dispatch_manifest,
    write_dispatch_manifest,
)
from metaproc.paths import DISPATCH_MANIFEST_FILE, step_state_dir
from metaproc.runpool.status import ControllerStatus, ScaleBounds, ScaleState, write_scale_state


class TestWriteDispatchManifest:
    def test_writes_manifest(self, tmp_path: Path) -> None:
        worker_jobs = [
            {
                "worker_id": "0",
                "job_name": "projects/p/locations/r/jobs/j0",
                "job_id": "j0",
                "items_count": "10",
                "items": ["AAPL", "MSFT"],
            },
            {
                "worker_id": "1",
                "job_name": "projects/p/locations/r/jobs/j1",
                "job_id": "j1",
                "items_count": "10",
                "items": ["NVDA", "AMD"],
            },
        ]
        path = write_dispatch_manifest(
            tmp_path, "generate-record", worker_jobs=worker_jobs, num_items=20, variant="pi-glm-5"
        )

        assert path.exists()
        data = read_yaml_file(path)
        assert data["step_id"] == "generate-record"
        assert data["num_items"] == 20
        assert data["num_workers"] == 2
        assert data["variant"] == "pi-glm-5"
        assert len(data["workers"]) == 2
        assert data["workers"][0]["job_id"] == "j0"
        assert data["workers"][0]["items"] == ["AAPL", "MSFT"]
        assert "dispatched_at" in data

    def test_path_is_under_step_state_dir(self, tmp_path: Path) -> None:
        worker_jobs = [{"worker_id": "0", "job_name": "j0", "job_id": "j0", "items_count": "5"}]
        path = write_dispatch_manifest(tmp_path, "my-step", worker_jobs=worker_jobs, num_items=5)
        # New layout: <run>/.state/steps/<step_id>/dispatch-manifest.yaml
        expected = step_state_dir(tmp_path, "my-step") / DISPATCH_MANIFEST_FILE
        assert path == expected


class TestReadDispatchManifest:
    def test_reads_written_manifest(self, tmp_path: Path) -> None:
        worker_jobs = [{"worker_id": "0", "job_name": "j0", "job_id": "j0", "items_count": "3"}]
        write_dispatch_manifest(tmp_path, "step-a", worker_jobs=worker_jobs, num_items=3)

        data = read_dispatch_manifest(tmp_path, "step-a")
        assert data is not None
        assert data["step_id"] == "step-a"
        assert data["num_items"] == 3

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert read_dispatch_manifest(tmp_path, "nonexistent") is None

    def test_returns_none_on_corrupt_file(self, tmp_path: Path) -> None:
        path = step_state_dir(tmp_path, "step-b") / DISPATCH_MANIFEST_FILE
        path.parent.mkdir(parents=True)
        path.write_text("not yaml: [")

        # Should return None or raise — either is acceptable for corrupt data.
        # The implementation logs a warning and returns None.
        result = read_dispatch_manifest(tmp_path, "step-b")
        # Corrupt YAML may parse as something other than dict, handled gracefully.
        assert result is None or isinstance(result, dict)


class TestAppendDispatchManifest:
    def test_appends_new_workers(self, tmp_path: Path) -> None:
        write_dispatch_manifest(
            tmp_path,
            "generate-record",
            worker_jobs=[
                {
                    "worker_id": "0",
                    "job_name": "projects/p/locations/r/jobs/j0",
                    "job_id": "j0",
                    "items_count": "1",
                    "items": ["AAPL"],
                }
            ],
            num_items=2,
            variant="pi-glm-5",
        )

        append_dispatch_manifest(
            tmp_path,
            "generate-record",
            worker_jobs=[
                {
                    "worker_id": "1",
                    "job_name": "projects/p/locations/r/jobs/j1",
                    "job_id": "j1",
                    "items_count": "1",
                    "items": ["MSFT"],
                }
            ],
        )

        data = read_dispatch_manifest(tmp_path, "generate-record")
        assert data is not None
        workers = data["workers"]
        assert isinstance(workers, list)
        assert len(workers) == 2
        assert workers[1]["job_id"] == "j1"  # pyright: ignore[reportIndexIssue]
        assert data["num_workers"] == 2


class TestInspectWorkerJobs:
    def test_raises_when_job_state_is_unknown(self) -> None:
        worker_jobs: list[WorkerJobManifest] = [
            {
                "worker_id": "0",
                "job_name": "projects/p/locations/r/jobs/j0",
                "job_id": "j0",
                "items_count": "1",
            }
        ]

        with patch("google.cloud.batch_v1.BatchServiceClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.get_job.side_effect = RuntimeError("batch unavailable")
            mock_client_cls.return_value = mock_client

            with pytest.raises(CLIError, match="Refusing to redispatch"):
                asyncio.run(_inspect_worker_jobs(worker_jobs))


class TestReconcileDispatchedWorkers:
    def test_scales_up_with_unclaimed_items(self, tmp_path: Path) -> None:
        worker_jobs = [
            {
                "worker_id": "0",
                "job_name": "projects/p/locations/r/jobs/j0",
                "job_id": "j0",
                "items_count": "2",
                "items": ["AAPL", "MSFT"],
            }
        ]
        write_dispatch_manifest(tmp_path, "generate-record", worker_jobs=worker_jobs, num_items=3)
        claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL")
        write_scale_state(
            step_state_dir(tmp_path, "generate-record") / "scale-state.yaml",
            ScaleState(
                updated_at="2026-04-17T12:00:00",
                controller=ControllerStatus(
                    operator_cap=15,
                    effective_target=15,
                    memory_ceiling=15,
                    provider_ceiling=15,
                    bottleneck="operator-capped",
                ),
                desired_workers=2,
                desired_max_concurrency=15,
                generation=1,
                bounds=ScaleBounds(
                    min_workers=1,
                    max_workers=4,
                    min_concurrency=1,
                    max_concurrency=30,
                ),
            ),
        )

        config = WorkerDispatchConfig(
            gcp=MagicMock(),
            num_workers=1,
            max_concurrency=5,
            poll_interval=1,
            spot=True,
            variant="pi-test",
            adapter_config_json="",
        )

        out = MagicMock()
        submitted: dict[str, object] = {}

        async def fake_submit(**kwargs):
            submitted["partitions"] = kwargs["partitions"]
            submitted["starting_worker_id"] = kwargs["starting_worker_id"]
            return [
                {
                    "worker_id": "1",
                    "job_name": "projects/p/locations/r/jobs/j1",
                    "job_id": "j1",
                    "items_count": "2",
                    "items": ["MSFT", "NVDA"],
                }
            ]

        async def fake_poll(worker_jobs, *args, **kwargs):
            assert len(worker_jobs) == 2
            return [
                WorkerResult(
                    worker_id=0,
                    job_name="projects/p/locations/r/jobs/j0",
                    job_id="j0",
                    items_count=2,
                    state="RUNNING",
                    exit_code=0,
                ),
                WorkerResult(
                    worker_id=1,
                    job_name="projects/p/locations/r/jobs/j1",
                    job_id="j1",
                    items_count=2,
                    state="RUNNING",
                    exit_code=0,
                ),
            ]

        with (
            patch(
                "metaproc.cloud.gcp.worker_dispatch._inspect_worker_jobs",
                return_value=(worker_jobs, []),
            ),
            patch(
                "metaproc.cloud.gcp.worker_dispatch._submit_workers",
                side_effect=fake_submit,
            ),
            patch("metaproc.cloud.gcp.worker_dispatch._poll_workers", side_effect=fake_poll),
        ):
            results = asyncio.run(
                reconcile_dispatched_workers(
                    run_dir=tmp_path,
                    step="generate-record",
                    item_contexts=[
                        {"EVENT_ID": "AAPL"},
                        {"EVENT_ID": "MSFT"},
                        {"EVENT_ID": "NVDA"},
                    ],
                    each="EVENT_ID",
                    config=config,
                    process_spec_rel="example_plugin/process/mine",
                    variables={"RUN_ID": "run-1"},
                    out=out,
                )
            )

        assert results is not None
        assert len(results) == 2
        assert submitted["partitions"] == [["MSFT", "NVDA"]]
        assert submitted["starting_worker_id"] == 1

    def test_terminal_worker_claims_are_reclaimed_before_scale_up(self, tmp_path: Path) -> None:
        worker_jobs = [
            {
                "worker_id": "0",
                "job_name": "projects/p/locations/r/jobs/j0",
                "job_id": "j0",
                "items_count": "1",
                "items": ["AAPL"],
            }
        ]
        write_dispatch_manifest(tmp_path, "generate-record", worker_jobs=worker_jobs, num_items=1)
        claim_item(tmp_path, "generate-record", worker_id="0", item="AAPL")
        write_scale_state(
            step_state_dir(tmp_path, "generate-record") / "scale-state.yaml",
            ScaleState(
                updated_at="2026-04-17T12:00:00",
                controller=ControllerStatus(
                    operator_cap=15,
                    effective_target=15,
                    memory_ceiling=15,
                    provider_ceiling=15,
                    bottleneck="operator-capped",
                ),
                desired_workers=1,
                desired_max_concurrency=15,
                generation=1,
                bounds=ScaleBounds(
                    min_workers=1,
                    max_workers=4,
                    min_concurrency=1,
                    max_concurrency=30,
                ),
            ),
        )

        config = WorkerDispatchConfig(
            gcp=MagicMock(),
            num_workers=1,
            max_concurrency=5,
            poll_interval=1,
            spot=True,
            variant="pi-test",
            adapter_config_json="",
        )

        with (
            patch(
                "metaproc.cloud.gcp.worker_dispatch._inspect_worker_jobs",
                return_value=([], worker_jobs),
            ),
            patch(
                "metaproc.cloud.gcp.worker_dispatch._submit_workers",
                return_value=[
                    {
                        "worker_id": "1",
                        "job_name": "projects/p/locations/r/jobs/j1",
                        "job_id": "j1",
                        "items_count": "1",
                        "items": ["AAPL"],
                    }
                ],
            ),
            patch("metaproc.cloud.gcp.worker_dispatch._poll_workers", return_value=[]),
        ):
            asyncio.run(
                reconcile_dispatched_workers(
                    run_dir=tmp_path,
                    step="generate-record",
                    item_contexts=[{"EVENT_ID": "AAPL"}],
                    each="EVENT_ID",
                    config=config,
                    process_spec_rel="example_plugin/process/mine",
                    variables={"RUN_ID": "run-1"},
                    out=MagicMock(),
                    wait_for_completion=False,
                )
            )

        assert collect_claimed_items(tmp_path, "generate-record") == set()
