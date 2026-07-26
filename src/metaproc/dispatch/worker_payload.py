"""Typed wrapper for the worker-dispatch env-var cohort.

Carries the orchestrator to worker Batch job env-var cohort, one
payload per worker partition.

Note: the cohort intentionally excludes the auth-pool sub-cohort and
the secret-refs / repo-sync / GCP-SDK env vars. Callers compose the
worker payload alongside :class:`AuthPoolFlags`,
:class:`SecretRefSet`, and :class:`RepoSyncPayload` — the dispatch
chain merges all of them into the final Batch ``env_vars`` dict.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar

from metaproc.config.env_vars import MetaprocEnv


def _split_csv(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class WorkerDispatchPayload:
    """One worker partition's Batch dispatch payload.

    ``adapter_config_json`` and ``pi_models_json`` are pre-encoded
    upstream (the producers already serialize them) so this dataclass
    keeps the strings as-is rather than re-decoding/encoding. Same
    for ``item_contexts_inline``: the upstream caller already
    json.dumps'd the contexts dict and gated the inline-vs-file
    choice on size; the dataclass only declares the env-var binding.
    """

    worker_id: int = 0
    items: tuple[str, ...] = ()
    process_spec: str = ""
    step: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    max_concurrency: int = 0
    initial_concurrency: int = 0
    variant: str = ""
    max_retries: int | None = None
    adapter_config_json: str = ""
    item_contexts_inline: str = ""
    item_contexts_file: str = ""
    pi_models_json: str = ""

    ENV_WORKER_ID: ClassVar[str] = MetaprocEnv.METAPROC_WORKER_ID.name
    ENV_WORKER_ITEMS: ClassVar[str] = MetaprocEnv.METAPROC_WORKER_ITEMS.name
    ENV_PROCESS_SPEC: ClassVar[str] = MetaprocEnv.METAPROC_PROCESS_SPEC.name
    ENV_STEP: ClassVar[str] = MetaprocEnv.METAPROC_STEP.name
    ENV_VARS: ClassVar[str] = MetaprocEnv.METAPROC_VARS.name
    ENV_MAX_CONCURRENCY: ClassVar[str] = MetaprocEnv.METAPROC_MAX_CONCURRENCY.name
    ENV_INITIAL_CONCURRENCY: ClassVar[str] = MetaprocEnv.METAPROC_INITIAL_CONCURRENCY.name
    ENV_VARIANT: ClassVar[str] = MetaprocEnv.METAPROC_VARIANT.name
    ENV_MAX_RETRIES: ClassVar[str] = MetaprocEnv.METAPROC_MAX_RETRIES.name
    ENV_ADAPTER_CONFIG: ClassVar[str] = MetaprocEnv.METAPROC_ADAPTER_CONFIG.name
    ENV_ITEM_CONTEXTS: ClassVar[str] = MetaprocEnv.METAPROC_ITEM_CONTEXTS.name
    ENV_ITEM_CONTEXTS_FILE: ClassVar[str] = MetaprocEnv.METAPROC_ITEM_CONTEXTS_FILE.name
    ENV_PI_MODELS_JSON: ClassVar[str] = MetaprocEnv.METAPROC_PI_MODELS_JSON.name

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerDispatchPayload:
        """Construct from process env (defaults to ``os.environ``)."""

        e: Mapping[str, str] = env if env is not None else os.environ
        worker_id_raw = e.get(cls.ENV_WORKER_ID, "").strip()
        max_conc_raw = e.get(cls.ENV_MAX_CONCURRENCY, "").strip()
        initial_conc_raw = e.get(cls.ENV_INITIAL_CONCURRENCY, "").strip()
        max_retries_raw = e.get(cls.ENV_MAX_RETRIES, "").strip()
        vars_raw = e.get(cls.ENV_VARS, "").strip()
        return cls(
            worker_id=int(worker_id_raw) if worker_id_raw else 0,
            items=_split_csv(e.get(cls.ENV_WORKER_ITEMS, "")),
            process_spec=e.get(cls.ENV_PROCESS_SPEC, ""),
            step=e.get(cls.ENV_STEP, ""),
            variables=json.loads(vars_raw) if vars_raw else {},
            max_concurrency=int(max_conc_raw) if max_conc_raw else 0,
            initial_concurrency=int(initial_conc_raw) if initial_conc_raw else 0,
            variant=e.get(cls.ENV_VARIANT, ""),
            max_retries=int(max_retries_raw) if max_retries_raw else None,
            adapter_config_json=e.get(cls.ENV_ADAPTER_CONFIG, ""),
            item_contexts_inline=e.get(cls.ENV_ITEM_CONTEXTS, ""),
            item_contexts_file=e.get(cls.ENV_ITEM_CONTEXTS_FILE, ""),
            pi_models_json=e.get(cls.ENV_PI_MODELS_JSON, ""),
        )

    def to_env_vars(self) -> dict[str, str]:
        """Encode to a Batch job env_vars dict.

        Always emits ``METAPROC_WORKER_ID`` (worker identity is
        load-bearing). Inline vs file item-contexts is mutually
        exclusive — only the populated one is written.
        """
        out: dict[str, str] = {self.ENV_WORKER_ID: str(self.worker_id)}
        if self.items:
            out[self.ENV_WORKER_ITEMS] = ",".join(self.items)
        if self.process_spec:
            out[self.ENV_PROCESS_SPEC] = self.process_spec
        if self.step:
            out[self.ENV_STEP] = self.step
        if self.variables:
            out[self.ENV_VARS] = json.dumps(self.variables)
        if self.max_concurrency:
            out[self.ENV_MAX_CONCURRENCY] = str(self.max_concurrency)
        if self.initial_concurrency:
            out[self.ENV_INITIAL_CONCURRENCY] = str(self.initial_concurrency)
        if self.variant:
            out[self.ENV_VARIANT] = self.variant
        if self.max_retries is not None:
            out[self.ENV_MAX_RETRIES] = str(self.max_retries)
        if self.adapter_config_json:
            out[self.ENV_ADAPTER_CONFIG] = self.adapter_config_json
        if self.item_contexts_file:
            out[self.ENV_ITEM_CONTEXTS_FILE] = self.item_contexts_file
        elif self.item_contexts_inline:
            out[self.ENV_ITEM_CONTEXTS] = self.item_contexts_inline
        if self.pi_models_json:
            out[self.ENV_PI_MODELS_JSON] = self.pi_models_json
        return out


__all__ = ["WorkerDispatchPayload"]
