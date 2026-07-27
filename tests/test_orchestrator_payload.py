"""Tests for OrchestratorDispatchPayload — operator-CLI → orchestrator cohort.

Phase 10 follow-up of the Vehicle A pool redesign (the original design,
this regression).
"""

from __future__ import annotations

import json

import pytest

from metaproc.dispatch.orchestrator_payload import OrchestratorDispatchPayload


class TestDefaults:
    def test_default_construction(self) -> None:
        p = OrchestratorDispatchPayload()
        assert p.process_spec == ""
        assert p.variables == {}
        assert p.num_workers is None
        assert p.machine_type == ""
        assert p.max_concurrency is None
        assert p.initial_concurrency is None
        # Spot dispatch is the operator default — running ON-DEMAND
        # workers requires explicit opt-out.
        assert p.spot is True
        assert p.variant == ""
        assert p.adapter_config == {}
        assert p.skip_steps == ()
        assert p.from_step == ""
        assert p.only_step == ""
        assert p.force is False
        assert p.continue_on_error is False


class TestToEnvVars:
    def test_default_emits_empty_dict(self) -> None:
        # No process_spec, no num_workers, default spot=True (silent),
        # everything else default-empty/False — the encoder writes
        # nothing.
        assert OrchestratorDispatchPayload().to_env_vars() == {}

    def test_full_payload(self) -> None:
        p = OrchestratorDispatchPayload(
            process_spec="path/to/spec.process.md",
            variables={"DATE": "2026-04-28"},
            num_workers=4,
            machine_type="n2-highmem-4",
            max_concurrency=20,
            initial_concurrency=3,
            spot=False,  # explicit opt-out so encoder writes "false"
            variant="gemini-3",
            adapter_config={"model": "gemini-3"},
            skip_steps=("predict-ticker",),
            from_step="curate",
            only_step="postprocess",
            force=True,
            continue_on_error=True,
        )
        env = p.to_env_vars()
        assert env["METAPROC_PROCESS_SPEC"] == "path/to/spec.process.md"
        assert json.loads(env["METAPROC_VARS"]) == {"DATE": "2026-04-28"}
        # num_workers always emits the belt-and-suspenders fallback too.
        assert env["METAPROC_NUM_WORKERS"] == "4"
        assert env["METAPROC_DEFAULT_NUM_WORKERS"] == "4"
        assert env["METAPROC_MACHINE_TYPE"] == "n2-highmem-4"
        assert env["METAPROC_MAX_CONCURRENCY"] == "20"
        assert env["METAPROC_INITIAL_CONCURRENCY"] == "3"
        assert env["METAPROC_SPOT"] == "false"
        assert env["METAPROC_VARIANT"] == "gemini-3"
        assert json.loads(env["METAPROC_ADAPTER_CONFIG"]) == {"model": "gemini-3"}
        assert env["METAPROC_SKIP_STEPS"] == "predict-ticker"
        assert env["METAPROC_FROM_STEP"] == "curate"
        assert env["METAPROC_ONLY_STEP"] == "postprocess"
        assert env["METAPROC_FORCE"] == "true"
        assert env["METAPROC_CONTINUE_ON_ERROR"] == "true"

    def test_default_spot_silent(self) -> None:
        # Default spot=True must NOT emit METAPROC_SPOT — that's the
        # entrypoint default, so writing "true" is redundant noise.
        env = OrchestratorDispatchPayload(process_spec="x").to_env_vars()
        assert "METAPROC_SPOT" not in env


class TestFromEnv:
    def test_empty_returns_default(self) -> None:
        # Empty env reads with spot=True (the entrypoint default).
        assert OrchestratorDispatchPayload.from_env({}) == OrchestratorDispatchPayload()

    def test_full_payload(self) -> None:
        p = OrchestratorDispatchPayload.from_env(
            {
                "METAPROC_PROCESS_SPEC": "x.process.md",
                "METAPROC_VARS": '{"DATE": "2026-04-28"}',
                "METAPROC_NUM_WORKERS": "4",
                "METAPROC_MACHINE_TYPE": "n2-highmem-4",
                "METAPROC_MAX_CONCURRENCY": "20",
                "METAPROC_INITIAL_CONCURRENCY": "3",
                "METAPROC_SPOT": "false",
                "METAPROC_VARIANT": "gemini-3",
                "METAPROC_ADAPTER_CONFIG": '{"model": "gemini-3"}',
                "METAPROC_SKIP_STEPS": "a,b,c",
                "METAPROC_FROM_STEP": "curate",
                "METAPROC_ONLY_STEP": "postprocess",
                "METAPROC_FORCE": "true",
                "METAPROC_CONTINUE_ON_ERROR": "true",
            }
        )
        assert p.process_spec == "x.process.md"
        assert p.variables == {"DATE": "2026-04-28"}
        assert p.num_workers == 4
        assert p.machine_type == "n2-highmem-4"
        assert p.max_concurrency == 20
        assert p.initial_concurrency == 3
        assert p.spot is False
        assert p.variant == "gemini-3"
        assert p.adapter_config == {"model": "gemini-3"}
        assert p.skip_steps == ("a", "b", "c")
        assert p.from_step == "curate"
        assert p.only_step == "postprocess"
        assert p.force is True
        assert p.continue_on_error is True

    def test_unset_spot_decodes_true(self) -> None:
        # Spot defaults True at the entrypoint; unset env reads as True.
        assert OrchestratorDispatchPayload.from_env({}).spot is True

    @pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no"])
    def test_falsy_spot_decodes_false(self, raw: str) -> None:
        assert OrchestratorDispatchPayload.from_env({"METAPROC_SPOT": raw}).spot is False


class TestRoundTrip:
    @pytest.mark.parametrize(
        "payload",
        [
            OrchestratorDispatchPayload(),
            OrchestratorDispatchPayload(process_spec="x.md", num_workers=2),
            OrchestratorDispatchPayload(
                process_spec="x.md",
                variables={"K": "V"},
                num_workers=4,
                initial_concurrency=2,
                spot=False,
                skip_steps=("a", "b"),
                force=True,
            ),
        ],
    )
    def test_round_trip(self, payload: OrchestratorDispatchPayload) -> None:
        # Default-spot (True) round-trips even when not emitted (the
        # missing env var decodes back to True).
        env = payload.to_env_vars()
        assert OrchestratorDispatchPayload.from_env(env) == payload
