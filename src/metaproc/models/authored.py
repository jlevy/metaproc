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


class StepContext(dict[str, str]):
    """Resolved code-handler inputs plus cooperative cancellation state.

    The mapping behavior preserves the original handler API. Long-running handlers can
    call :meth:`cancel_requested` at safe checkpoints and return promptly when the
    owning run is cancelled.
    """

    def __init__(
        self,
        values: dict[str, str],
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(values)
        self._cancel_requested = cancel_requested

    def cancel_requested(self) -> bool:
        """Return whether the owning run has requested cooperative cancellation."""
        return self._cancel_requested is not None and self._cancel_requested()


#: Signature for a code-mode handler function loaded from a .py file.
CodeHandler = Callable[[StepContext, "ProcessStep"], None]


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
# Spec: src/metaproc/docs/metaproc-design.md
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


InputChangePolicy = Literal["record", "new_run"]
"""What a launch that resolves a different value for an input does (``on_change:``).

``record`` records the change and continues; ``new_run`` refuses, because the scope's
durable state describes the value it was bound to.
"""


class ProcessInput(_ProcessIOBase):
    """A declared input on ``ProcessSpec.inputs``.

    Input sources from the Core Model:
    - operator-supplied parameter via ``param: <CLI_OR_ENV_NAME>``
    - parsed-from-file via ``path:`` + ``parse:``
    - literal (caller fills ``path:`` with an absolute or run-relative location)

    Every resolved input is recorded in ``run-config.yaml`` at launch. ``on_change``
    says what a later launch that resolves a different value does:

    - ``record`` (the default): the change is logged and recorded as a
      ``launch_config_change`` event, ``run-config.yaml`` then records the value the
      resume ran with, and what re-runs is decided by step fingerprints. A value that
      records how a run executed, such as the code revision that launched it, wants
      this.
    - ``new_run``: the scope holds one value for the input for its whole life, because
      its task state, results, and summaries all describe that value. The value is
      recorded in the scope's ``input-bindings.yaml`` when the scope is first entered,
      and every later entry compares against it before anything is written: a
      ``run-process`` resume, a composite child scope after ``with:`` resolution, and
      ``run-step`` and ``run-parallel``. A different value refuses, naming the recorded
      and the new value and the two ways out: the recorded value, or a new ``RUN_ID``.
      Declare it for a value whose change makes the run a different run, such as the
      date anchor or the roster a run covers, and sparingly: a bound value cannot be
      corrected under the same ``RUN_ID``.

    ``new_run`` needs a ``param``-backed input. The binding is the value the launch
    resolves, compared under the input's logical name whatever its ``param`` alias is
    called, so it is the path string of an ``as: path`` input, never the file's
    contents; reuse follows contents through step fingerprints. A file-backed input
    (``path:`` without ``param:``) has no launch value to bind and is rejected.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True, extra="forbid")

    path: str | None = None
    param: str | None = None
    parse: ParseConfig | None = None
    required: bool = True
    default: str | None = None
    on_change: InputChangePolicy = "record"

    @model_validator(mode="after")
    def _new_run_binds_a_param(self) -> ProcessInput:
        """A ``new_run`` binding is a launch value, so only a ``param``-backed input has one."""
        if self.on_change == "new_run" and self.param is None:
            msg = (
                "on_change: new_run needs a param-backed input: the binding is the value "
                "the launch resolves, and a file-backed input (path: without param:) has "
                "none; reuse follows a file's contents through step fingerprints"
            )
            raise ValueError(msg)
        return self


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
    """Artifact reference with optional kind, format, and contract.

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

    on_invalid: dict[str, Literal["fail", "retry", "fail_run"]] | None = None
    """What it costs when *this* output fails its contract, keyed most-specific-first.

    A key is an invariant name, a contract id, or an
    :class:`~metaproc.models.runtime.OutputFailureKind`, tried in that order.
    Omitted, a failure costs what it has always cost. A clause governs the output
    that declares it and no other, so two outputs of one step can answer
    differently for the same kind of failure.

    Distinct from a step's ``on_failure``, which says whether *this* step runs
    when an *upstream* one failed. That is the consumer's side of an edge; this
    is the producer's own output.

    The common case is a stochastic producer, where a second attempt genuinely
    may succeed where the first did not::

        on_invalid:
          semantic: retry

    ``retry`` is honoured wherever per-item retry exists, which is fan-out
    execution; a single-shot step has no attempt to schedule, so there a failure
    is terminal whatever it declares.

    ``fail_run`` is for a defect that will recur on every item rather than be
    specific to this one. It currently fails the item permanently, exactly as
    ``fail`` does — no execution path aborts a run on it yet. It is readable
    through :func:`~metaproc.engine.retry.requires_run_abort`, which is the seam
    that abort would attach to.
    """

    optional: bool = False
    template: str | None = None
    condition: str | None = None

    # Fan-in binding. `collect` names an upstream fan-out step whose per-item outcomes
    # this input receives as one manifest, so a consumer reads a typed collection
    # instead of rediscovering upstream state by walking directories.
    collect: str | None = None
    require: Literal["succeeded", "finished"] | None = Field(
        default=None,
        description=(
            "Which upstream outcomes satisfy this input. `succeeded` (the default when "
            "collecting) needs every item to have succeeded. `finished` accepts any "
            "terminal outcome, so a partially failed upstream still satisfies the edge "
            "and the consumer decides what a failure means. The two are named for the "
            "condition each states, because 'completed' reads as terminal in some "
            "contexts and as success in others."
        ),
    )

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


# ── Spend and dispatch policy ────────────────────────────────────


class StepSpend(BaseModel):
    """The list price a mapped step declares for one of its items.

    A run with a spend cap reads it before the step dispatches anything: the worst case
    is the actionable items times ``max_attempts`` times ``per_item_usd``, and the step
    refuses to start when that plus the spend already measured exceeds the cap. The
    price is dispatch policy, not step contract, so changing it never invalidates
    completed work.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    per_item_usd: float = Field(
        gt=0,
        allow_inf_nan=False,
        description=(
            "List cost of one item when no attempt is retried: for an agent fan-out one "
            "agent invocation, for a mapped composite one pass through the child scope."
        ),
    )
    source: str = Field(
        min_length=1,
        description="Where the price comes from, such as the measured runs it averages.",
    )
    max_attempts: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Most attempts one item can make. Omitted, it is one plus the largest "
            "resolved `retry.max_retries` among the agent steps the item runs."
        ),
    )


class DispatchBreaker(BaseModel):
    """Stop dispatching a mapped step's items once too many of them have failed.

    The rate counts items this invocation finished. Once at least ``min_finished`` have
    finished and more than ``max_failure_fraction`` of them failed, no further item
    starts; items already running finish, and the rest stay pending for a resume.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    max_failure_fraction: float = Field(gt=0, lt=1, allow_inf_nan=False)
    min_finished: int = Field(ge=1)


class SpendCapPolicy(BaseModel):
    """How a process treats the run-level spend cap (``run-process --max-spend-usd``)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    required: bool = Field(
        default=False,
        description="Refuse a launch that has no cap from the flag, the run record or a default.",
    )
    default_usd: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="The cap a launch without the flag and without a recorded cap runs with.",
    )


# ── Fan-out ──────────────────────────────────────────────────────


class ForEach(BaseModel):
    """Structured fan-out declaration replacing flat each/source/item_fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    over: str
    bind: str
    bind_fields: list[str] = Field(default_factory=list)
    batch_size: int = 10
    retry: RetryPolicy | None = None

    max_concurrency: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Ceiling on this step's items in flight, independent of any other step's. "
            "A run-wide cap and an execution profile both answer a different question: "
            "the first is the whole run's budget and the second is which adapter and "
            "model, so expressing a per-step ceiling through either conflates it with "
            "something else and leaves the limit invisible in the spec that describes "
            "the work. Omitted, the step is bounded only by the run-wide cap."
        ),
    )

    breaker: DispatchBreaker | None = Field(
        default=None,
        description=(
            "Stop dispatching new items once the failed fraction of finished items "
            "exceeds `max_failure_fraction` after `min_finished` have finished."
        ),
    )

    align: Literal["same_key"] | None = Field(
        default=None,
        description=(
            "Declares this step's `needs` edge item-scoped rather than step-scoped: "
            "this step's task for item k waits only on the upstream task for item k, "
            "so items flow through the chain independently instead of barriering at "
            "the step boundary. Valid only where the upstream also fans out over the "
            "same source, because alignment on unrelated rosters would join unrelated "
            "work. Absent, the edge stays step-scoped and execution is unchanged."
        ),
    )

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
    spend: StepSpend | None = None

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
            "steps (e.g. run-stats) that must emit on partly failed runs. "
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
        if self.spend is not None and self.for_each is None:
            msg = f"step '{self.id}': spend prices a mapped step's items and requires for_each"
            raise ValueError(msg)
        if self.spend is not None and self.mode not in {"agent", "code", "composite"}:
            msg = f"step '{self.id}': spend is not valid on a {self.mode} step"
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
    spend_cap: SpendCapPolicy | None = Field(
        default=None,
        description=(
            "Run-level spend cap policy. Read only when this process is the launched "
            "root; a composite child's declaration is ignored."
        ),
    )
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

    @property
    def new_run_inputs(self) -> dict[str, ProcessInput]:
        """Return the inputs declared ``on_change: new_run``, by logical name.

        A scope binds each of them to one value for its whole life
        (``engine.input_bindings``); the logical name is the binding's key, so a
        renamed ``param`` alias changes nothing.
        """
        return {name: decl for name, decl in self.inputs.items() if decl.on_change == "new_run"}

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
