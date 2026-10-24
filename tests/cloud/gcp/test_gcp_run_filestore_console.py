"""A gcp-run job's console goes to Filestore, and the gcp CLI reads its live view."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from metaproc.cloud.gcp import gcp_run_live
from metaproc.cloud.gcp.batch_backend import GCPBatchConfig
from metaproc.cloud.gcp.gcp_run_console import (
    CONSOLE_MODULE,
    CONSOLE_PATH_ENV,
    LIVE_GCS_ENV,
    LIVE_STATUS_NAME,
    LIVE_TAIL_NAME,
    WATCH_ENV,
)
from metaproc.cloud.gcp.gcp_run_dispatch import (
    RESERVED_ENV_KEYS,
    DispatchGcpRunOptions,
    build_gcp_run_job,
)
from metaproc.commands import gcp as cmd_gcp
from metaproc.commands import gcp_run as cmd_gcp_run


def _config(**overrides: Any) -> GCPBatchConfig:
    base = GCPBatchConfig(
        project="p",
        region="us-central1",
        container_image="us-central1-docker.pkg.dev/p/agent:latest",
        machine_type="e2-standard-4",
        service_account_email="user@example.invalid",
        max_run_duration_s=3600,
        filestore_server="10.0.0.2",
        filestore_mount_path="/mnt/disks/filestore",
    )
    return dataclasses.replace(base, **overrides) if overrides else base


def _env(job: Any) -> dict[str, str]:
    return dict(job.task_groups[0].task_spec.runnables[-1].environment.variables)


# ── dispatch ─────────────────────────────────────────────────


def test_filestore_console_wraps_the_command_and_names_its_file() -> None:
    job_id, job = build_gcp_run_job(
        ["bash", "worker.sh", "launch"],
        DispatchGcpRunOptions(
            config=_config(),
            job_name="batch-01-launch-01",
            console_on_filestore=True,
            live_gcs_uri="gs://bucket/gcp-run/batch-01-launch-01/live",
            watch_run_dirs=("/mnt/disks/filestore/runs/example/run-a",),
        ),
    )
    env = _env(job)
    assert json.loads(env["METAPROC_GCP_RUN_CMD"]) == [
        "python",
        "-m",
        CONSOLE_MODULE,
        "--",
        "bash",
        "worker.sh",
        "launch",
    ]
    assert env[CONSOLE_PATH_ENV] == (
        "/mnt/disks/filestore/logs/gcp-run/batch-01-launch-01/console.log"
    )
    assert env[LIVE_GCS_ENV] == "gs://bucket/gcp-run/batch-01-launch-01/live"
    assert env[WATCH_ENV] == "/mnt/disks/filestore/runs/example/run-a"
    assert job_id == "batch-01-launch-01"


def test_cloud_logging_console_leaves_the_command_alone() -> None:
    _, job = build_gcp_run_job(["echo", "hi"], DispatchGcpRunOptions(config=_config()))
    env = _env(job)
    assert json.loads(env["METAPROC_GCP_RUN_CMD"]) == ["echo", "hi"]
    assert CONSOLE_PATH_ENV not in env and LIVE_GCS_ENV not in env


def test_filestore_console_needs_a_mount_and_live_view_needs_the_console() -> None:
    with pytest.raises(ValueError, match="requires a Filestore mount"):
        build_gcp_run_job(
            ["true"],
            DispatchGcpRunOptions(config=_config(filestore_server=""), console_on_filestore=True),
        )
    with pytest.raises(ValueError, match="require console_on_filestore"):
        build_gcp_run_job(
            ["true"], DispatchGcpRunOptions(config=_config(), live_gcs_uri="gs://b/live")
        )
    with pytest.raises(ValueError, match="absolute, comma-free"):
        build_gcp_run_job(
            ["true"],
            DispatchGcpRunOptions(
                config=_config(), console_on_filestore=True, watch_run_dirs=("runs/a",)
            ),
        )


def test_console_env_is_dispatcher_owned() -> None:
    assert {CONSOLE_PATH_ENV, LIVE_GCS_ENV, WATCH_ENV} <= RESERVED_ENV_KEYS
    with pytest.raises(ValueError, match="dispatcher-owned"):
        build_gcp_run_job(
            ["true"],
            DispatchGcpRunOptions(config=_config(), extra_env={CONSOLE_PATH_ENV: "/tmp/x"}),
        )


# ── CLI console choice ───────────────────────────────────────


@pytest.mark.parametrize(
    ("console", "server", "no_wheel", "expected"),
    [
        ("auto", "10.0.0.2", False, True),
        ("auto", "10.0.0.2", True, False),
        ("auto", "", False, False),
        ("filestore", "10.0.0.2", True, True),
        ("cloud-logging", "10.0.0.2", False, False),
    ],
)
def test_console_choice(console: str, server: str, no_wheel: bool, expected: bool) -> None:
    config = _config(filestore_server=server)
    assert (
        cmd_gcp_run._resolve_console(console, config=config, no_wheel=no_wheel, watch_run=None)
        is expected
    )


def test_console_choice_refusals() -> None:
    with pytest.raises(typer.BadParameter, match="needs the Filestore mount"):
        cmd_gcp_run._resolve_console(
            "filestore", config=_config(filestore_server=""), no_wheel=False, watch_run=None
        )
    with pytest.raises(typer.BadParameter, match="needs the Filestore console"):
        cmd_gcp_run._resolve_console(
            "cloud-logging", config=_config(), no_wheel=False, watch_run=["/mnt/runs/a"]
        )
    with pytest.raises(typer.BadParameter, match="must be one of"):
        cmd_gcp_run._resolve_console("stdout", config=_config(), no_wheel=False, watch_run=None)


def test_run_command_dispatches_the_filestore_console_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METAPROC_GCP_PROJECT", "p")
    monkeypatch.setenv("METAPROC_GCP_CONTAINER_IMAGE", "us-central1-docker.pkg.dev/p/i:t")
    monkeypatch.setenv("METAPROC_GCS_BUCKET", "dispatch-bucket")
    monkeypatch.setenv("METAPROC_GCP_SERVICE_ACCOUNT", "batch@example.invalid")
    monkeypatch.setenv("METAPROC_GCP_FILESTORE_SERVER", "10.0.0.2")
    monkeypatch.setenv("METAPROC_GCP_FILESTORE_MOUNT_PATH", "/mnt/disks/filestore")
    app = typer.Typer()
    app.command("run")(cmd_gcp_run.run_command)
    captured: dict[str, DispatchGcpRunOptions] = {}

    def fake_dispatch(cmd: list[str], options: DispatchGcpRunOptions) -> str:
        del cmd
        captured["options"] = options
        return "projects/p/locations/us-central1/jobs/job-x"

    with (
        patch.object(cmd_gcp_run, "build_wheel"),
        patch.object(cmd_gcp_run, "file_sha256", side_effect=["1" * 64, "2" * 64]),
        patch.object(cmd_gcp_run, "upload_wheel_to_gcs", return_value="gs://b/w.whl"),
        patch.object(cmd_gcp_run, "package_workspace"),
        patch.object(cmd_gcp_run, "upload_workspace_to_gcs", return_value="gs://b/ws.tgz"),
        patch.object(cmd_gcp_run, "find_repo_root", return_value=tmp_path),
        patch.object(cmd_gcp_run, "dispatch_gcp_run", side_effect=fake_dispatch),
    ):
        result = CliRunner().invoke(
            app,
            [
                "--detach",
                "--job-name",
                "job-x",
                "--watch-run",
                "/mnt/disks/filestore/runs/example/run-a",
                "echo",
                "hi",
            ],
        )
    assert result.exit_code == 0, result.output
    options = captured["options"]
    assert options.console_on_filestore
    assert options.live_gcs_uri == "gs://dispatch-bucket/gcp-run/job-x/live"
    assert options.watch_run_dirs == ("/mnt/disks/filestore/runs/example/run-a",)
    assert "console: /mnt/disks/filestore/logs/gcp-run/job-x/console.log" in result.output
    assert "live view: gs://dispatch-bucket/gcp-run/job-x/live" in result.output


# ── live view reader ─────────────────────────────────────────


class FakeBlob:
    def __init__(self, name: str, objects: dict[str, str]) -> None:
        self.name = name
        self._objects = objects

    def exists(self) -> bool:
        return self.name in self._objects

    def download_as_text(self) -> str:
        return self._objects[self.name]


class FakeBucket:
    def __init__(self, objects: dict[str, str]) -> None:
        self.objects = objects

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self.objects)

    def list_blobs(self, prefix: str) -> list[FakeBlob]:
        return [FakeBlob(n, self.objects) for n in sorted(self.objects) if n.startswith(prefix)]


class FakeClient:
    def __init__(self, objects: dict[str, str]) -> None:
        self.bucket_obj = FakeBucket(objects)
        self.buckets: list[str] = []

    def bucket(self, name: str) -> FakeBucket:
        self.buckets.append(name)
        return self.bucket_obj


def test_live_view_reads_status_tail_and_run_statuses() -> None:
    objects = {
        f"gcp-run/j/live/{LIVE_STATUS_NAME}": json.dumps(
            {"state": "running", "console_lines": 12, "console_path": "/mnt/c.log"}
        ),
        f"gcp-run/j/live/{LIVE_TAIL_NAME}": "a\nb\n",
        "gcp-run/j/live/runs/run-a.txt": "Status: RUNNING\n",
    }
    client = FakeClient(objects)
    view = gcp_run_live.LiveView("gs://bkt/gcp-run/j/live", client_factory=lambda: client)
    assert view.status() == {"state": "running", "console_lines": 12, "console_path": "/mnt/c.log"}
    assert view.tail() == "a\nb\n"
    assert view.run_statuses() == {"run-a": "Status: RUNNING\n"}
    assert client.buckets[0] == "bkt"
    empty = gcp_run_live.LiveView("gs://bkt/other", client_factory=lambda: FakeClient({}))
    assert empty.status() is None and empty.tail() is None


def test_live_view_uri_comes_from_the_jobs_own_spec() -> None:
    _, job = build_gcp_run_job(
        ["true"],
        DispatchGcpRunOptions(
            config=_config(), console_on_filestore=True, live_gcs_uri="gs://b/gcp-run/j/live"
        ),
    )
    assert gcp_run_live.live_view_uri_of(job) == "gs://b/gcp-run/j/live"
    _, plain = build_gcp_run_job(["true"], DispatchGcpRunOptions(config=_config()))
    assert gcp_run_live.live_view_uri_of(plain) == ""


@pytest.mark.parametrize(
    ("tail", "previous", "total", "expected"),
    [
        ("a\nb\nc\n", 10, 10, ([], 0)),
        ("a\nb\nc\n", 10, 12, (["b", "c"], 0)),
        ("a\nb\nc\n", 10, 15, (["a", "b", "c"], 2)),
    ],
)
def test_new_tail_lines(
    tail: str, previous: int, total: int, expected: tuple[list[str], int]
) -> None:
    assert gcp_run_live.new_tail_lines(tail, previous, total) == expected


def test_summarize_status() -> None:
    line = gcp_run_live.summarize_status(
        {
            "state": "exited",
            "exit_code": 1,
            "console_lines": 1234,
            "console_path": "/mnt/c.log",
            "updated_at": "2026-10-24T08:00:00Z",
        }
    )
    assert line == "exited 1; console /mnt/c.log (1,234 lines); updated 2026-10-24T08:00:00Z"


# ── gcp logs from the live view ──────────────────────────────


class ScriptedView:
    """A live view whose status and tail advance one step per poll."""

    def __init__(self, steps: list[tuple[dict[str, Any], str]]) -> None:
        self._steps = steps
        self._index = 0

    def status(self) -> dict[str, Any]:
        return self._steps[self._index][0]

    def tail(self) -> str:
        step = self._steps[self._index]
        self._index = min(self._index + 1, len(self._steps) - 1)
        return step[1]


def test_print_live_logs_follows_until_the_job_exits() -> None:
    job = MagicMock()
    job.name = "projects/p/locations/us-central1/jobs/j"
    job.task_groups[0].task_spec.runnables = []
    view = ScriptedView(
        [
            ({"state": "running", "console_lines": 2, "console_path": "/c"}, "one\ntwo\n"),
            ({"state": "running", "console_lines": 3, "console_path": "/c"}, "two\nthree\n"),
            ({"state": "exited", "exit_code": 0, "console_lines": 4}, "three\nfour\n"),
        ]
    )
    out = MagicMock()
    cmd_gcp._print_live_logs(
        [job],
        out=out,
        limit=100,
        follow=True,
        view_factory=lambda _uri: view,
        sleep=lambda _s: None,
    )
    printed = [call.args[0] for call in out.data.call_args_list]
    assert printed == ["one", "two", "three", "four"]
    assert out.progress.call_args_list[-1].args[0] == "Every followed job has exited."
