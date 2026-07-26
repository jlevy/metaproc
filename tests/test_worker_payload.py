"""Tests for WorkerDispatchPayload — orchestrator → worker cohort.

Phase 10 follow-up of the Vehicle A pool redesign (epic internal-reference,
bead internal-reference).
"""

from __future__ import annotations

import json

import pytest

from metaproc.dispatch.worker_payload import WorkerDispatchPayload


class TestDefaults:
    def test_default_construction(self) -> None:
        p = WorkerDispatchPayload()
        assert p.worker_id == 0
        assert p.items == ()
        assert p.process_spec == ""
        assert p.step == ""
        assert p.variables == {}
        assert p.max_concurrency == 0
        assert p.initial_concurrency == 0
        assert p.variant == ""
        assert p.max_retries is None
        assert p.adapter_config_json == ""
        assert p.item_contexts_inline == ""
        assert p.item_contexts_file == ""
        assert p.pi_models_json == ""


class TestToEnvVars:
    def test_default_emits_only_worker_id(self) -> None:
        # Worker identity is load-bearing — even a defaulted payload
        # must emit the worker ID so the worker subprocess can scope
        # state per-worker. Everything else is silent on default.
        env = WorkerDispatchPayload().to_env_vars()
        assert env == {"METAPROC_WORKER_ID": "0"}

    def test_full_inline_contexts(self) -> None:
        p = WorkerDispatchPayload(
            worker_id=2,
            items=("AAPL", "MSFT"),
            process_spec="x.process.md",
            step="predict-ticker",
            variables={"DATE": "2026-04-28"},
            max_concurrency=8,
            initial_concurrency=2,
            variant="claude",
            max_retries=3,
            adapter_config_json='{"model": "opus"}',
            item_contexts_inline='{"AAPL": {}}',
            pi_models_json='{"models": {}}',
        )
        env = p.to_env_vars()
        assert env["METAPROC_WORKER_ID"] == "2"
        assert env["METAPROC_WORKER_ITEMS"] == "AAPL,MSFT"
        assert env["METAPROC_PROCESS_SPEC"] == "x.process.md"
        assert env["METAPROC_STEP"] == "predict-ticker"
        assert json.loads(env["METAPROC_VARS"]) == {"DATE": "2026-04-28"}
        assert env["METAPROC_MAX_CONCURRENCY"] == "8"
        assert env["METAPROC_INITIAL_CONCURRENCY"] == "2"
        assert env["METAPROC_VARIANT"] == "claude"
        assert env["METAPROC_MAX_RETRIES"] == "3"
        assert env["METAPROC_ADAPTER_CONFIG"] == '{"model": "opus"}'
        assert env["METAPROC_ITEM_CONTEXTS"] == '{"AAPL": {}}'
        assert "METAPROC_ITEM_CONTEXTS_FILE" not in env
        assert env["METAPROC_PI_MODELS_JSON"] == '{"models": {}}'

    def test_file_contexts_takes_precedence_over_inline(self) -> None:
        # When both are populated (shouldn't happen in practice but
        # the encoder must be deterministic), the file path wins —
        # mirrors how upstream callers gate the choice on size.
        p = WorkerDispatchPayload(
            worker_id=1,
            item_contexts_inline='{"x": 1}',
            item_contexts_file="/tmp/spill.json",
        )
        env = p.to_env_vars()
        assert env["METAPROC_ITEM_CONTEXTS_FILE"] == "/tmp/spill.json"
        assert "METAPROC_ITEM_CONTEXTS" not in env


class TestFromEnv:
    def test_full_payload(self) -> None:
        p = WorkerDispatchPayload.from_env(
            {
                "METAPROC_WORKER_ID": "2",
                "METAPROC_WORKER_ITEMS": "AAPL,MSFT",
                "METAPROC_PROCESS_SPEC": "x.process.md",
                "METAPROC_STEP": "predict-ticker",
                "METAPROC_VARS": '{"DATE": "2026-04-28"}',
                "METAPROC_MAX_CONCURRENCY": "8",
                "METAPROC_INITIAL_CONCURRENCY": "2",
                "METAPROC_VARIANT": "claude",
                "METAPROC_MAX_RETRIES": "3",
                "METAPROC_ADAPTER_CONFIG": '{"model": "opus"}',
                "METAPROC_ITEM_CONTEXTS": '{"AAPL": {}}',
                "METAPROC_PI_MODELS_JSON": '{"models": {}}',
            }
        )
        assert p.worker_id == 2
        assert p.items == ("AAPL", "MSFT")
        assert p.process_spec == "x.process.md"
        assert p.step == "predict-ticker"
        assert p.variables == {"DATE": "2026-04-28"}
        assert p.max_concurrency == 8
        assert p.initial_concurrency == 2
        assert p.variant == "claude"
        assert p.max_retries == 3
        assert p.adapter_config_json == '{"model": "opus"}'
        assert p.item_contexts_inline == '{"AAPL": {}}'
        assert p.item_contexts_file == ""
        assert p.pi_models_json == '{"models": {}}'

    def test_file_contexts_decoded(self) -> None:
        p = WorkerDispatchPayload.from_env(
            {
                "METAPROC_WORKER_ID": "1",
                "METAPROC_ITEM_CONTEXTS_FILE": "/tmp/spill.json",
            }
        )
        assert p.item_contexts_file == "/tmp/spill.json"
        assert p.item_contexts_inline == ""


class TestRoundTrip:
    @pytest.mark.parametrize(
        "payload",
        [
            WorkerDispatchPayload(),
            WorkerDispatchPayload(worker_id=3, items=("A", "B"), process_spec="x.md"),
            WorkerDispatchPayload(
                worker_id=5,
                items=("X",),
                process_spec="x.md",
                step="s",
                variables={"K": "V"},
                max_concurrency=4,
                initial_concurrency=2,
                variant="claude",
                max_retries=2,
                adapter_config_json='{"a": 1}',
                item_contexts_file="/tmp/p.json",
                pi_models_json='{"m": 1}',
            ),
        ],
    )
    def test_round_trip(self, payload: WorkerDispatchPayload) -> None:
        env = payload.to_env_vars()
        assert WorkerDispatchPayload.from_env(env) == payload
