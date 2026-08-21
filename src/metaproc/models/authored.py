"""Authored process specification models.

These types represent the human-written ``.process.md`` specification
before plan resolution: steps, IO contracts, adapter config, fan-out.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import ClassVar, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from metaproc.models.lane import LaneMatrix
from metaproc.models.resource_budget import ResourceBudgetSpec

# ── Code-mode handler types ──────────────────────────────────────

#: Variable context passed to code-mode handlers — resolved step inputs.
StepContext = dict[str, str]

#: Signature for a code-mode handler function loaded from a .py file.
CodeHandler = Callable[["StepContext", "ProcessStep"], None]


# ── Adapter config ──────────────────────────────────────────────


class AdapterConfig(BaseModel):
    """Structured adapter configuration: type + freeform config dict."""

    type: str = "claude-code-cli"
    guarantee: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    config_by_variant: dict[str, dict[str, object]] = Field(default_factory=dict)


# ── Shared / nested ─────────────────────────────────────────────


class ProcessDefaults(BaseModel):
    """Process-level defaults inherited by all steps.

    All adapter configurations live in the ``adapters`` map keyed by name.
    ``default_adapter`` names which key to use when a step doesn't specify one.
    """

    adapters: dict[str, AdapterConfig] = Field(default_factory=dict)
    default_adapter: str = "default"
    default_execution_profile: str | None = None
    recommended_execution_profiles: list[str] = Field(default_factory=list)
    reuse_policy: str = "validated_outputs"
    retry: RetryPolicy | None = None

    @property
    def default_adapter_config(self) -> AdapterConfig:
        """Return the default adapter config from the adapters map."""
        if self.default_adapter in self.adapters:
            return self.adapters[self.default_adapter]
        if self.adapters:
            return next(iter(self.adapters.values()))
        return AdapterConfig()


class ParamDef(BaseModel):
    """Legacy helper kept for tests; authored specs now use ``inputs: ... param:``."""

    type: Literal["string"] = "string"
    required: bool = True


# ── Core Model value types ──────────────────────────────────────
#
# Closed value type set for process-level inputs/outputs (Phase 2A).
# Spec: docs/arch/arch-metaproc-core.md
# The authored surface accepts the short string form (e.g. ``list<map>``);
# ``ValueType.parse`` turns it into a structured tree the engine can consume.

_VALUE_TYPE_PRIMITIVES: frozenset[str] = frozenset({"string", "path"})


class ValueType(BaseModel):
    """A typed value declaration from the closed set ``{string, path, list<T>, map<K,V>}``."""

    kind: Literal["string", "path", "list", "map"]
    element: ValueType | None = None
    key: ValueType | None = None
    value: ValueType | None = None

    @classmethod
    def parse(cls, raw: str) -> ValueType:
        """Parse the authored short form (``string``, ``list<map>``, ``map<string, path>``)."""
        s = raw.strip()
        if not s:
            msg = "value type string is empty"
            raise ValueError(msg)
        if "<" not in s:
            if s in _VALUE_TYPE_PRIMITIVES:
                return cls(kind=s)  # pyright: ignore[reportArgumentType]
            if s == "list":
                return cls(kind="list")
            if s == "map":
                return cls(kind="map")
            msg = f"unknown value type: {raw!r}"
            raise ValueError(msg)
        if not s.endswith(">"):
            msg = f"malformed value type: {raw!r}"
            raise ValueError(msg)
        head, _, inner = s[:-1].partition("<")
        head = head.strip()
        if head == "list":
            return cls(kind="list", element=cls.parse(inner))
        if head == "map":
            key_raw, sep, value_raw = _split_top_level_comma(inner)
            if not sep:
                msg = f"map type must have two arguments, got: {raw!r}"
                raise ValueError(msg)
            return cls(kind="map", key=cls.parse(key_raw), value=cls.parse(value_raw))
        msg = f"unknown generic value type: {raw!r}"
        raise ValueError(msg)


def _split_top_level_comma(s: str) -> tuple[str, bool, str]:
    """Split on the first top-level comma (ignoring commas inside ``<>``)."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "," and depth == 0:
            return s[:i], True, s[i + 1 :]
    return s, False, ""


# ── Parse configuration ─────────────────────────────────────────


class ParseConfig(BaseModel):
    """How to turn a file's bytes into a value of the declared ``as:`` type."""

    format: Literal["yaml", "frontmatter-md"]
    extract: str | None = None


# ── Process-level I/O declarations ──────────────────────────────


class _ProcessIOBase(BaseModel):
    """Shared fields between ProcessInput, ProcessOutput, and ProcessDep.

    `path` is declared per-subclass because its required-ness differs:
    required on ProcessDep, optional on ProcessInput/ProcessOutput.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    description: str | None = None
    as_: ValueType = Field(alias="as")

    @field_validator("as_", mode="before")
    @classmethod
    def _coerce_as(cls, raw: object) -> object:
        if isinstance(raw, str):
            return ValueType.parse(raw)
        return raw


class ProcessInput(_ProcessIOBase):
    """A declared input on ``ProcessSpec.inputs``.

    Input sources from the Core Model:
    - operator-supplied parameter via ``param: <CLI_OR_ENV_NAME>``
    - parsed-from-file via ``path:`` + ``parse:``
    - literal (caller fills ``path:`` with an absolute or run-relative location)
    """

    path: str | None = None
    param: str | None = None
    parse: ParseConfig | None = None
    required: bool = True
    default: str | None = None


class ProcessDep(_ProcessIOBase):
    """A declared durable file dependency on ``ProcessSpec.deps``."""

    path: str
    parse: ParseConfig | None = None
    produced_by: str | None = None


class ProcessOutput(_ProcessIOBase):
    """A declared output on ``ProcessSpec.outputs``.

    An output may be a concrete path written by the process, or a re-export
    of a step output via ``ref: <step-id>.<output-name>``.

    Optional ``template:`` names a scaffold file (may contain ``{{vars}}``) that the
    agent fills in. Optional ``condition:`` is a predicate against resolved variables
    (e.g. ``"RUN_MODE == backtest"``) gating whether this output applies to the run.
    """

    path: str | None = None
    format: str | None = None
    ref: str | None = None
    template: str | None = None
    condition: str | None = None


class IOSpec(BaseModel):
    """Artifact reference with optional kind, format, and schema.

    Optional ``template:`` names a scaffold file (may contain ``{{vars}}``) the agent
    fills in. Optional ``condition:`` is a predicate against resolved variables (e.g.
    ``"RUN_MODE == backtest"``) gating whether this IO applies to the run.
    """

    path: str | None = None
    ref: str | None = None
    type: str | None = None
    kind: Literal["file", "directory", "stream"] | None = None
    format: str | None = None
    contract: str | None = Field(
        default=None,
        validation_alias=AliasChoices("contract", "schema"),
        serialization_alias="contract",
    )
    """Contract id this output must validate against, as ``namespace:Name/vN``.

    A contract id, not a path to a schema document. softschema draws that line
    in its own document metadata, where ``contract`` names the identity and
    ``schema`` optionally points at a generated JSON Schema file; the identity is
    what resolves to a model and does the validating. ``schema:`` is accepted as
    an alias so existing process specs keep working.
    """

    optional: bool = False
    template: str | None = None
    condition: str | None = None

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def validate_io_source(self) -> IOSpec:
        """Reject ambiguous authored declarations."""
        if self.ref and self.path:
            msg = "io spec must declare either 'path' or 'ref', not both"
            raise ValueError(msg)
        return self


StepStatus = Literal["pending", "running", "completed", "failed", "cached", "deferred"]
"""Aggregate step status.

``deferred`` is a post-Phase-2c addition: a step whose retry_later
checkpoint was written but whose re-dispatch has not yet happened.
Downstream steps treat it as ``pending`` (waiting, not terminal)
rather than ``failed``, so ``metaproc status`` can tell the operator
"this run is waiting, not broken." See
plan-2026-04-21-auth-credential-pool.md P2c.6.
"""


class ProgressCounts(BaseModel):
    """Aggregated item counts from scanning .state/ directories."""

    total: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cached: int = 0
    retrying: int = 0


# ── Retry ────────────────────────────────────────────────────────


class RetryPolicy(BaseModel):
    """Retry-with-backoff policy for fan-out steps, framework-wide default ON.

    Retriable errors (see ``engine.retry.classify_error`` — rate limits,
    stream-idle / timeout, 5xx, connection drops, transient API failures) are
    retried across all fan-out steps. Permanent errors (OOM, quota, cancellation,
    permission denied) are never retried.

    Defaults give upstream infra enough time to recover from the realistic
    range of transient failures — from a burst of 429s (clears in seconds) to a
    full stream-reset or degraded-backend episode (can take many minutes).
    Short initial backoff (5s) recovers fast from brief hiccups; the ``1.5×``
    multiplier keeps ramp gentle so we don't overshoot a recoverable blip.
    Schedule with ``max_retries=12``, initial 5s, multiplier 1.5, cap 600s:
    5, 7.5, 11, 17, 25, 38, 57, 85, 128, 192, 288, 432s → ~21.5 min of
    accumulated wait across 12 retries before giving up. Many steps in this
    project run for long enough (hours) that a 20-min retry window is the
    proportionate response to "upstream briefly unavailable."

    Step-level ``retry:`` blocks in process.md remain the knob for per-step
    tuning; ``--no-retry`` and ``--max-retries N`` on the CLI override at
    dispatch time.
    """

    max_retries: int = 12
    initial_backoff_s: float = 5.0
    backoff_multiplier: float = 1.5
    max_backoff_s: float = 600.0


# ── Fan-out ──────────────────────────────────────────────────────


class ForEach(BaseModel):
    """Structured fan-out declaration replacing flat each/source/item_fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    over: str
    bind: str
    bind_fields: list[str] = Field(default_factory=list)
    batch_size: int = 10
    retry: RetryPolicy | None = None

    key: str | None = Field(
        default=None,
        description=(
            "Per-task item-key template, resolved with the loop variables. "
            "Addresses per-task state at <run>/.state/tasks/<step_id>/<key>/ "
            "and per-task logs at <run>/.logs/tasks/<step_id>/<key>/. Must "
            "resolve to a filesystem-safe string matching [A-Za-z0-9._-]+. "
            "Omitting it defaults to '{{<bind>}}' and emits a deprecation "
            "warning."
        ),
    )

    @field_validator("key")
    @classmethod
    def _validate_key_template(cls, v: str | None) -> str | None:
        """Validate the literal characters of the key template.

        We allow ``{{ ... }}`` placeholders plus the safe-filename charset
        ``[A-Za-z0-9._-]`` (and underscore). The resolved value at dispatch
        time is checked again against ``ITEM_KEY_RE`` in pathing helpers.
        """
        if v is None:
            return v
        stripped = re.sub(r"\{\{[^{}]+\}\}", "", v)
        if not stripped:
            return v
        if not re.fullmatch(r"[A-Za-z0-9._-]+", stripped):
            raise ValueError(
                f"for_each.key template must use only "
                f"[A-Za-z0-9._-] outside of {{{{...}}}} placeholders; got: {v!r}"
            )
        return v


# ── ProcessStep (unified) ───────────────────────────────────────


class ProcessStep(BaseModel):
    """A single step in a process — supports all four modes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="forbid")

    id: str
    mode: Literal["agent", "code", "manual", "composite"]
    description: str = ""
    needs: list[str] = Field(
        default_factory=list,
        description=(
            "Declares logical dependencies between steps. "
            "Validated at plan-build time (dangling refs, cycles). "
            "Enforced by `run-process` for DAG execution ordering. "
            "Not enforced by `run-step` or `run-parallel`."
        ),
    )

    # composite mode
    uses: str | None = None
    with_: dict[str, str] | None = Field(default=None, alias="with")

    # agent mode
    prompt_paths: list[str] = Field(default_factory=list)
    prompt_prefix: str | None = None

    # code mode
    handler: str | None = None
    command: str | None = None

    # fan-out
    for_each: ForEach | None = None

    # IO contracts
    inputs: dict[str, IOSpec] = Field(default_factory=dict)
    outputs: dict[str, IOSpec] = Field(default_factory=dict)
    output_root: str | None = None

    # per-step adapter override
    adapter: AdapterConfig | None = None

    # explicit variant override — decouples directory from adapter/model
    variant: str | None = None

    # execution profile and artifact namespace overrides
    execution_profile: str | None = None
    artifact_namespace: str | None = None
    tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    timeout_s: int | None = None

    # environment variables
    env: dict[str, str] = Field(default_factory=dict)

    # budget
    max_budget_usd: float | None = None
    token_budget: int | None = None

    # reuse policy
    reuse_policy: str | None = None

    # failure propagation
    on_failure: Literal["block", "continue"] = Field(
        default="block",
        description=(
            "How this step reacts to an upstream failure in its `needs:` chain. "
            "Default `block`: the step is skipped (state=blocked) when any direct "
            "dependency failed — current behavior. `continue`: the step runs even "
            "if its upstream failed. Used for end-of-run rollup / post-mortem "
            "steps (e.g. run-stats) that must emit on partial-failure cohorts. "
            "Note: this only affects the step's direct gating; transitive "
            "dependents through a continue-marked step still follow normal "
            "blocking through other (default) needs."
        ),
    )

    @model_validator(mode="after")
    def validate_step_contract(self) -> ProcessStep:
        """Validate mode-specific field requirements."""
        if self.mode == "composite" and not self.uses:
            msg = f"step '{self.id}': composite mode requires 'uses'"
            raise ValueError(msg)
        if self.mode == "code" and not self.handler and not self.command:
            msg = f"step '{self.id}': code mode requires 'handler' or 'command'"
            raise ValueError(msg)
        if self.for_each is not None:
            fe = self.for_each
            item_name = fe.bind.strip()
            field_names = [str(field).strip() for field in fe.bind_fields]
            if item_name not in field_names:
                msg = f"step '{self.id}': for_each.bind_fields must include bind field '{fe.bind}'"
                raise ValueError(msg)
            if len(set(field_names)) != len(field_names):
                msg = f"step '{self.id}': for_each.bind_fields must be unique"
                raise ValueError(msg)
        return self

    @field_validator("inputs", mode="before")
    @classmethod
    def _coerce_input_refs(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        coerced: dict[str, object] = {}
        for key, value in raw.items():
            if isinstance(value, str):
                coerced[str(key)] = {"ref": value}
            else:
                coerced[str(key)] = value
        return coerced


# ── Top-level document models ───────────────────────────────────


class ProcessSpec(BaseModel):
    """Unified recursive process specification (rev2)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="forbid")

    schema_: str = Field(default="metaproc:ProcessSpec/0.1", alias="schema")
    name: str
    description: str = ""
    defaults: ProcessDefaults = Field(default_factory=ProcessDefaults)
    inputs: dict[str, ProcessInput] = Field(default_factory=dict)
    deps: dict[str, ProcessDep] = Field(default_factory=dict)
    outputs: dict[str, ProcessOutput] = Field(default_factory=dict)
    resource_budgets: list[ResourceBudgetSpec] = Field(default_factory=list)
    steps: list[ProcessStep] = Field(default_factory=list)
    lane_matrix: LaneMatrix | None = Field(
        default=None,
        description=(
            "Optional domain-neutral lane matrix. When set, the planner expands "
            "the matrix into ``execution_lanes`` and fans each domain item across "
            "every lane. When omitted, the process runs as a single degenerate "
            "lane derived from the run-level execution profile. See "
            "``metaproc.models.lane.LaneMatrix``."
        ),
    )

    @field_validator("resource_budgets")
    @classmethod
    def _validate_resource_budget_ids(
        cls,
        budgets: list[ResourceBudgetSpec],
    ) -> list[ResourceBudgetSpec]:
        ids = [budget.budget_id for budget in budgets]
        if len(ids) != len(set(ids)):
            raise ValueError("process resource budget IDs must be unique")
        return budgets

    @property
    def param_inputs(self) -> dict[str, ProcessInput]:
        """Return logical process inputs backed by operator-supplied params."""
        return {name: decl for name, decl in self.inputs.items() if decl.param is not None}

    @property
    def optional_input_names(self) -> set[str]:
        """Return optional logical input names plus their backing param names."""
        names: set[str] = set()
        for name, decl in self.inputs.items():
            if decl.required:
                continue
            names.add(name)
            if decl.param is not None:
                names.add(decl.param)
        return names

    def expand_param_aliases(self, variables: dict[str, str]) -> dict[str, str]:
        """Mirror declared ``param:`` values onto logical input names and vice versa."""
        expanded = dict(variables)
        for name, decl in self.param_inputs.items():
            if name in expanded:
                value = expanded[name]
            elif decl.param is not None and decl.param in expanded:
                value = expanded[decl.param]
            else:
                continue
            expanded[name] = value
            if decl.param is not None:
                expanded.setdefault(decl.param, value)
        return expanded

    def requires_param(self, param_name: str) -> bool:
        """Return whether any declared process input is backed by *param_name*."""
        return any(decl.param == param_name for decl in self.inputs.values())

    @field_validator("inputs", mode="before")
    @classmethod
    def _normalize_input_keys(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        return {str(key): value for key, value in raw.items()}

    @field_validator("deps", mode="before")
    @classmethod
    def _normalize_dep_keys(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        return {str(key): value for key, value in raw.items()}

    @field_validator("outputs", mode="before")
    @classmethod
    def _normalize_output_keys(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        return {str(key): value for key, value in raw.items()}
