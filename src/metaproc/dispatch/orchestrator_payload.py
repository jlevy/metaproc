"""Typed wrapper for the orchestrator-dispatch env-var cohort.

Carries the operator-CLI to orchestrator Batch job env-var cohort:
``METAPROC_PROCESS_SPEC``, ``_VARS``, ``_NUM_WORKERS``,
``_DEFAULT_NUM_WORKERS``, ``_MACHINE_TYPE``, ``_MAX_CONCURRENCY``,
``_INITIAL_CONCURRENCY``, ``_SPOT``, ``_VARIANT``, ``_ADAPTER_CONFIG``,
``_SKIP_STEPS``, ``_FROM_STEP``, ``_ONLY_STEP``, ``_FORCE``,
``_CONTINUE_ON_ERROR``.

The encoder mirrors the existing orchestrator_dispatch behavior: only
non-default fields are written to the env-var dict so a baseline call
emits nothing extra. The decoder mirrors orchestrator_entrypoint:
unset env vars decode to dataclass defaults (``""`` / ``None`` /
``False`` / ``()``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from metaproc.config.env_vars import MetaprocEnv


def _decode_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes")


def _split_csv(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class OrchestratorDispatchPayload:
    """Operator-CLI → orchestrator Batch dispatch payload.

    Empty/zero/False fields are treated as "unset" by the encoder.
    The orchestrator entrypoint reads each field with the matching
    default so a missing env var produces the same orchestrator
    behavior as a missing CLI flag (typer's defaults).
    """

    process_spec: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    num_workers: int | None = None
    machine_type: str = ""
    max_concurrency: int | None = None
    initial_concurrency: int | None = None
    spot: bool = True  # operators dispatch to spot by default
    variant: str = ""
    adapter_config: dict[str, str] = field(default_factory=dict)
    skip_steps: tuple[str, ...] = ()
    from_step: str = ""
    only_step: str = ""
    force: bool = False
    continue_on_error: bool = False

    ENV_PROCESS_SPEC: ClassVar[str] = MetaprocEnv.METAPROC_PROCESS_SPEC.name
    ENV_VARS: ClassVar[str] = MetaprocEnv.METAPROC_VARS.name
    ENV_NUM_WORKERS: ClassVar[str] = MetaprocEnv.METAPROC_NUM_WORKERS.name
    ENV_DEFAULT_NUM_WORKERS: ClassVar[str] = MetaprocEnv.METAPROC_DEFAULT_NUM_WORKERS.name
    ENV_MACHINE_TYPE: ClassVar[str] = MetaprocEnv.METAPROC_MACHINE_TYPE.name
    ENV_MAX_CONCURRENCY: ClassVar[str] = MetaprocEnv.METAPROC_MAX_CONCURRENCY.name
    ENV_INITIAL_CONCURRENCY: ClassVar[str] = MetaprocEnv.METAPROC_INITIAL_CONCURRENCY.name
    ENV_SPOT: ClassVar[str] = MetaprocEnv.METAPROC_SPOT.name
    ENV_VARIANT: ClassVar[str] = MetaprocEnv.METAPROC_VARIANT.name
    ENV_ADAPTER_CONFIG: ClassVar[str] = MetaprocEnv.METAPROC_ADAPTER_CONFIG.name
    ENV_SKIP_STEPS: ClassVar[str] = MetaprocEnv.METAPROC_SKIP_STEPS.name
    ENV_FROM_STEP: ClassVar[str] = MetaprocEnv.METAPROC_FROM_STEP.name
    ENV_ONLY_STEP: ClassVar[str] = MetaprocEnv.METAPROC_ONLY_STEP.name
    ENV_FORCE: ClassVar[str] = MetaprocEnv.METAPROC_FORCE.name
    ENV_CONTINUE_ON_ERROR: ClassVar[str] = MetaprocEnv.METAPROC_CONTINUE_ON_ERROR.name

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OrchestratorDispatchPayload:
        """Construct from process env (defaults to ``os.environ``)."""

        e: Mapping[str, str] = env if env is not None else os.environ
        num_workers_raw = e.get(cls.ENV_NUM_WORKERS, "").strip()
        max_conc_raw = e.get(cls.ENV_MAX_CONCURRENCY, "").strip()
        initial_conc_raw = e.get(cls.ENV_INITIAL_CONCURRENCY, "").strip()
        vars_raw = e.get(cls.ENV_VARS, "").strip()
        adapter_raw = e.get(cls.ENV_ADAPTER_CONFIG, "").strip()
        return cls(
            process_spec=e.get(cls.ENV_PROCESS_SPEC, ""),
            variables=json.loads(vars_raw) if vars_raw else {},
            num_workers=int(num_workers_raw) if num_workers_raw else None,
            machine_type=e.get(cls.ENV_MACHINE_TYPE, ""),
            max_concurrency=int(max_conc_raw) if max_conc_raw else None,
            initial_concurrency=int(initial_conc_raw) if initial_conc_raw else None,
            spot=_decode_bool(e.get(cls.ENV_SPOT, "true")),
            variant=e.get(cls.ENV_VARIANT, ""),
            adapter_config=json.loads(adapter_raw) if adapter_raw else {},
            skip_steps=_split_csv(e.get(cls.ENV_SKIP_STEPS, "")),
            from_step=e.get(cls.ENV_FROM_STEP, ""),
            only_step=e.get(cls.ENV_ONLY_STEP, ""),
            force=_decode_bool(e.get(cls.ENV_FORCE, "")),
            continue_on_error=_decode_bool(e.get(cls.ENV_CONTINUE_ON_ERROR, "")),
        )

    def to_env_vars(self) -> dict[str, str]:
        """Encode to a Batch job env_vars dict.

        Only non-default fields are written so a baseline dispatch
        produces no extra env vars beyond what's strictly required.
        ``num_workers`` is always written when set (alongside
        ``METAPROC_DEFAULT_NUM_WORKERS`` as a belt-and-suspenders
        fallback for code-version drift).
        """
        out: dict[str, str] = {}
        if self.process_spec:
            out[self.ENV_PROCESS_SPEC] = self.process_spec
        if self.variables:
            out[self.ENV_VARS] = json.dumps(self.variables)
        if self.num_workers is not None:
            out[self.ENV_NUM_WORKERS] = str(self.num_workers)
            out[self.ENV_DEFAULT_NUM_WORKERS] = str(self.num_workers)
        if self.machine_type:
            out[self.ENV_MACHINE_TYPE] = self.machine_type
        if self.max_concurrency is not None:
            out[self.ENV_MAX_CONCURRENCY] = str(self.max_concurrency)
        if self.initial_concurrency is not None:
            out[self.ENV_INITIAL_CONCURRENCY] = str(self.initial_concurrency)
        # Spot defaults True; only emit when the operator opted into
        # NON-spot dispatch (false). Matches the upstream entrypoint
        # default of "true".
        if not self.spot:
            out[self.ENV_SPOT] = "false"
        if self.variant:
            out[self.ENV_VARIANT] = self.variant
        if self.adapter_config:
            out[self.ENV_ADAPTER_CONFIG] = json.dumps(self.adapter_config)
        if self.skip_steps:
            out[self.ENV_SKIP_STEPS] = ",".join(self.skip_steps)
        if self.from_step:
            out[self.ENV_FROM_STEP] = self.from_step
        if self.only_step:
            out[self.ENV_ONLY_STEP] = self.only_step
        if self.force:
            out[self.ENV_FORCE] = "true"
        if self.continue_on_error:
            out[self.ENV_CONTINUE_ON_ERROR] = "true"
        return out


__all__ = ["OrchestratorDispatchPayload"]
