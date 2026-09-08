"""Tests for osutils.resource_context."""

from __future__ import annotations

import json
import logging

import pytest

from metaproc.osutils.resource_context import (
    collect_resource_context,
    format_resource_context,
    log_resource_context,
)


def test_collect_populates_required_fields() -> None:
    ctx = collect_resource_context()
    assert ctx.pid > 0
    assert ctx.ppid >= 0
    # host.cpu_count may be None in extremely restricted envs but is usually set;
    # the test just asserts the schema holds.
    assert isinstance(ctx.host.cpu_count, (int, type(None)))
    assert ctx.runtime.python_version.startswith(("3.", "4."))
    assert isinstance(ctx.runtime.env, dict)


def test_tracked_env_vars_are_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPROC_MAX_CONCURRENCY", "15")
    monkeypatch.setenv("BATCH_TASK_INDEX", "0")
    ctx = collect_resource_context()
    assert ctx.runtime.env.get("METAPROC_MAX_CONCURRENCY") == "15"
    assert ctx.runtime.env.get("BATCH_TASK_INDEX") == "0"


def test_secrets_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPROC_SUPER_TOKEN", "sekret123")
    monkeypatch.setenv("METAPROC_API_KEY", "sekret456")
    ctx = collect_resource_context()
    tok = ctx.runtime.env["METAPROC_SUPER_TOKEN"]
    assert "sekret123" not in tok
    assert "redacted" in tok
    key = ctx.runtime.env["METAPROC_API_KEY"]
    assert "sekret456" not in key


def test_dispatched_secret_targets_are_redacted_regardless_of_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "METAPROC_GCP_SECRET_REFS_JSON",
        json.dumps({"METAPROC_OPAQUE": "projects/p/secrets/opaque/versions/1"}),
    )
    monkeypatch.setenv("METAPROC_OPAQUE", "hydrated-value")

    ctx = collect_resource_context()

    assert ctx.runtime.env["METAPROC_OPAQUE"] == "<redacted:14chars>"
    assert "opaque/versions/1" not in ctx.runtime.env["METAPROC_GCP_SECRET_REFS_JSON"]


def test_format_is_multiline_and_mentions_key_sections() -> None:
    ctx = collect_resource_context()
    out = format_resource_context(ctx)
    assert "[host]" in out
    assert "[cgroup" in out
    assert "[rlimits]" in out
    assert "[runtime]" in out
    # Pids line appears for at least one of the cgroup versions when present;
    # at minimum the header lines are all there and the output is multi-line.
    assert out.count("\n") > 10


def test_log_emits_records(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("metaproc.tests.resource_context")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        log_resource_context(logger, level=logging.DEBUG)
    # log_resource_context emits one record per output line; the format
    # produces well over a dozen lines.
    assert len(caplog.records) > 5
    rendered = "\n".join(r.message for r in caplog.records)
    assert "[host]" in rendered
    assert "[cgroup" in rendered
