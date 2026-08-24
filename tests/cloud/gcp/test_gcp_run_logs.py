"""Tests for cloud/gcp/gcp_run_logs.py — log tail + exit-code propagation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from metaproc.cloud.gcp import gcp_run_logs


def _job(state: str, uid: str = "uid-123") -> Any:
    """Build a mock Batch Job object whose status.state stringifies to ``state``."""
    job = MagicMock()
    state_enum = MagicMock()
    state_enum.name = state
    state_enum.configure_mock(**{"__str__.return_value": f"State.{state}"})
    job.status.state = state_enum
    job.uid = uid
    return job


def _log_entry(
    message: str,
    insert_id: str = "id-1",
    timestamp: object = "2026-04-19T00:00:00Z",
) -> Any:
    entry = MagicMock()
    entry.payload = message
    entry.insert_id = insert_id
    entry.timestamp = timestamp
    return entry


class TestLogFetch:
    def test_datetime_watermark_is_fixed_precision_utc(self) -> None:
        assert gcp_run_logs._rfc3339_watermark(datetime(2026, 8, 23, 23, 9, 49)) == (
            "2026-08-23T23:09:49.000000Z"
        )
        pacific = timezone(-timedelta(hours=7))
        assert (
            gcp_run_logs._rfc3339_watermark(
                datetime(2026, 8, 23, 16, 9, 49, 310208, tzinfo=pacific)
            )
            == "2026-08-23T23:09:49.310208Z"
        )

    def test_fetch_uses_required_shared_client(self) -> None:
        client = MagicMock()
        client.list_entries.return_value = ["entry"]

        entries = gcp_run_logs._fetch_log_entries(
            client=client,
            job_uid="uid-1",
            job_id="job-1",
        )

        assert entries == ["entry"]
        client.list_entries.assert_called_once()


# ── _state_to_exit_code ───────────────────────────────────────


class TestStateToExitCode:
    def test_succeeded_zero(self):
        assert gcp_run_logs._state_to_exit_code("SUCCEEDED") == 0

    def test_failed_one(self):
        assert gcp_run_logs._state_to_exit_code("FAILED") == 1

    def test_cancelled_one_thirty(self):
        assert gcp_run_logs._state_to_exit_code("CANCELLED") == 130

    def test_deletion_in_progress_treated_as_cancel(self):
        # GCP Batch surfaces user-cancellation as DELETION_IN_PROGRESS.
        assert gcp_run_logs._state_to_exit_code("DELETION_IN_PROGRESS") == 130

    def test_unknown_state_falls_back_to_one(self):
        assert gcp_run_logs._state_to_exit_code("STATE_UNSPECIFIED") == 1


# ── tail_gcp_run_logs ─────────────────────────────────────────


class TestTailGcpRunLogs:
    @pytest.fixture(autouse=True)
    def _logging_client(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        client = MagicMock()
        monkeypatch.setattr(gcp_run_logs, "_get_logging_client", lambda _project: client)
        return client

    def test_returns_zero_on_succeeded_and_prints_log_lines(self, monkeypatch: pytest.MonkeyPatch):
        # Job goes RUNNING → SUCCEEDED.
        states = iter(["RUNNING", "SUCCEEDED"])

        client = MagicMock()
        client.get_job.side_effect = lambda req: _job(next(states))
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)

        # Logging client returns one entry on the first poll, none on the second.
        log_calls = iter([[_log_entry("hello world", insert_id="i1")], []])
        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", lambda **kw: next(log_calls))

        printed: list[str] = []
        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=printed.append,
            sleep=lambda _s: None,
        )
        assert rc == 0
        assert any("hello world" in line for line in printed)
        assert all(line.startswith("[gcp-run] ") for line in printed)
        assert "[gcp-run] state=RUNNING" in printed
        assert "[gcp-run] state=SUCCEEDED" in printed

    def test_reuses_and_closes_one_logging_client(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        states = iter(["SCHEDULED", "RUNNING", "SUCCEEDED"])
        batch_client = MagicMock()
        batch_client.get_job.side_effect = lambda req: _job(next(states))
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = batch_client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)

        logging_client = MagicMock()
        get_logging_client = MagicMock(return_value=logging_client)
        monkeypatch.setattr(gcp_run_logs, "_get_logging_client", get_logging_client)
        observed_clients: list[Any] = []

        def fake_fetch(**kwargs: Any) -> list[Any]:
            observed_clients.append(kwargs["client"])
            return []

        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", fake_fetch)

        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=lambda _s: None,
            sleep=lambda _s: None,
        )

        assert rc == 0
        get_logging_client.assert_called_once_with("p")
        assert observed_clients == [logging_client, logging_client, logging_client]
        logging_client.close.assert_called_once_with()
        batch_client.close.assert_called_once_with()

    def test_returns_one_on_failed(self, monkeypatch: pytest.MonkeyPatch):
        client = MagicMock()
        client.get_job.return_value = _job("FAILED")
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)
        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", lambda **kw: [])

        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=lambda _s: None,
            sleep=lambda _s: None,
        )
        assert rc == 1

    def test_returns_one_thirty_on_cancellation(self, monkeypatch: pytest.MonkeyPatch):
        client = MagicMock()
        client.get_job.return_value = _job("DELETION_IN_PROGRESS")
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)
        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", lambda **kw: [])

        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=lambda _s: None,
            sleep=lambda _s: None,
        )
        assert rc == 130

    def test_dedupes_log_entries_across_polls(self, monkeypatch: pytest.MonkeyPatch):
        # Two polls — RUNNING then SUCCEEDED. First poll returns id1+id2, second
        # returns id2+id3; we should only print id1, id2, id3 (id2 deduped).
        states = iter(["RUNNING", "SUCCEEDED"])
        client = MagicMock()
        client.get_job.side_effect = lambda req: _job(next(states))
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)

        log_calls = iter(
            [
                [
                    _log_entry("line1", insert_id="i1"),
                    _log_entry("line2", insert_id="i2"),
                ],
                [
                    _log_entry("line2-dup", insert_id="i2"),
                    _log_entry("line3", insert_id="i3"),
                ],
            ]
        )
        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", lambda **kw: next(log_calls))

        printed: list[str] = []
        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=printed.append,
            sleep=lambda _s: None,
        )
        assert rc == 0
        assert any("line1" in line for line in printed)
        assert any("line3" in line for line in printed)
        # The duplicate (i2) should only appear once.
        line2_count = sum(1 for line in printed if "line2" in line)
        assert line2_count == 1

    def test_passes_watermark_to_subsequent_fetches(self, monkeypatch: pytest.MonkeyPatch):
        # Three polls: RUNNING, RUNNING, SUCCEEDED. First poll returns an
        # entry; the subsequent fetches must carry a ``since`` watermark
        # equal to that entry's timestamp so we're not re-scanning old logs.
        states = iter(["RUNNING", "RUNNING", "SUCCEEDED"])
        client = MagicMock()
        client.get_job.side_effect = lambda req: _job(next(states))
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)

        first_ts = "2026-04-19T00:00:10Z"
        log_calls = iter(
            [
                [_log_entry("first", insert_id="i1", timestamp=first_ts)],
                [],
                [],
            ]
        )
        observed_since: list[str] = []

        def fake_fetch(**kw):
            observed_since.append(kw.get("since", ""))
            return next(log_calls)

        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", fake_fetch)

        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=lambda _s: None,
            sleep=lambda _s: None,
        )
        assert rc == 0
        # First fetch has no watermark, later fetches carry the first entry's ts.
        assert observed_since[0] == ""
        assert observed_since[1] == first_ts
        assert observed_since[2] == first_ts

    def test_normalizes_datetime_watermark_to_rfc3339(self, monkeypatch: pytest.MonkeyPatch):
        states = iter(["RUNNING", "SUCCEEDED"])
        client = MagicMock()
        client.get_job.side_effect = lambda req: _job(next(states))
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)

        first_ts = datetime(2026, 8, 23, 23, 9, 49, 310208, tzinfo=UTC)
        log_calls = iter(
            [
                [_log_entry("first", insert_id="i1", timestamp=first_ts)],
                [],
            ]
        )
        observed_since: list[str] = []

        def fake_fetch(**kw):
            observed_since.append(kw.get("since", ""))
            return next(log_calls)

        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", fake_fetch)

        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=lambda _s: None,
            sleep=lambda _s: None,
        )

        assert rc == 0
        assert observed_since == ["", "2026-08-23T23:09:49.310208Z"]

    def test_bounded_retry_on_persistent_get_job_failures(self, monkeypatch: pytest.MonkeyPatch):
        client = MagicMock()
        client.get_job.side_effect = RuntimeError("boom")
        fake_batch_v1 = MagicMock()
        fake_batch_v1.BatchServiceClient.return_value = client
        fake_batch_v1.GetJobRequest = MagicMock(side_effect=lambda **kw: kw)
        monkeypatch.setattr(gcp_run_logs, "_get_batch_v1", lambda: fake_batch_v1)
        monkeypatch.setattr(gcp_run_logs, "_fetch_log_entries", lambda **kw: [])

        rc = gcp_run_logs.tail_gcp_run_logs(
            job_resource_name="projects/p/locations/r/jobs/j",
            project="p",
            poll_interval_s=0.0,
            out=lambda _s: None,
            sleep=lambda _s: None,
        )
        assert rc == 1
        # Exactly MAX_CONSECUTIVE_ERRORS attempts were made — not an unbounded loop.
        assert client.get_job.call_count == gcp_run_logs.MAX_CONSECUTIVE_ERRORS


# ── log URL helper ────────────────────────────────────────────


class TestBuildLogUrl:
    def test_url_has_project_and_job_id(self):
        url = gcp_run_logs.build_log_url(
            "projects/exampletool/locations/us-central1/jobs/gcprun-foo"
        )
        assert "exampletool" in url
        assert "gcprun-foo" in url
        assert url.startswith("https://console.cloud.google.com/")
