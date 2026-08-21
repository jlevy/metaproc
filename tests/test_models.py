"""Tests for metaproc.models — authored, plan, and runtime models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaproc.models import runtime as _rt
from metaproc.models.authored import (
    AdapterConfig,
    ForEach,
    IOSpec,
    ParseConfig,
    ProcessDefaults,
    ProcessDep,
    ProcessInput,
    ProcessOutput,
    ProcessSpec,
    ProcessStep,
    ValueType,
)
from metaproc.models.plan import FanOut, Plan, ResolvedAdapter, ResolvedStep
from metaproc.models.runtime import (
    MapItem,
    MapItemsFrontmatter,
    get_terminal_statuses,
    register_terminal_statuses,
)

# ── Authored models ─────────────────────────────────────────────


class TestAdapterConfig:
    def test_defaults(self):
        cfg = AdapterConfig()
        assert cfg.type == "claude-code-cli"
        assert cfg.guarantee is None
        assert cfg.config == {}

    def test_custom_type(self):
        cfg = AdapterConfig(type="gemini-cli", config={"model": "pro"})
        assert cfg.type == "gemini-cli"
        assert cfg.config["model"] == "pro"


class TestIOSpec:
    def test_basic(self):
        spec = IOSpec(path="output.md")
        assert spec.path == "output.md"
        assert spec.optional is False

    def test_ref_only(self):
        spec = IOSpec(ref="generate.record")
        assert spec.ref == "generate.record"
        assert spec.path is None

    def test_rejects_path_and_ref_together(self):
        with pytest.raises(ValidationError, match="either 'path' or 'ref'"):
            IOSpec(path="output.md", ref="generate.record")

    def test_contract_accepts_either_spelling(self):
        """The field holds a contract id; ``schema:`` stays valid for existing specs."""
        canonical = IOSpec(path="out.md", contract="earnings:Prediction/v1")
        legacy = IOSpec(path="out.md", contract="earnings:Prediction/v1")

        assert canonical.contract == "earnings:Prediction/v1"
        assert legacy.contract == "earnings:Prediction/v1"

    def test_contract_serializes_under_its_own_name(self):
        spec = IOSpec(path="out.md", contract="earnings:Prediction/v1")

        assert spec.model_dump(by_alias=True, exclude_none=True)["contract"] == (
            "earnings:Prediction/v1"
        )


class TestForEach:
    def test_over_bind_fields(self):
        data = {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker", "sector"]}
        fe = ForEach.model_validate(data)
        assert fe.over == "tickers"
        assert fe.bind == "ticker"

    def test_default_batch_size(self):
        fe = ForEach.model_validate({"over": "items", "bind": "x", "bind_fields": ["x"]})
        assert fe.batch_size == 10


class TestProcessStep:
    def test_agent_step(self):
        step = ProcessStep(id="s1", mode="agent", prompt_paths=["runbook.md"])
        assert step.mode == "agent"
        assert step.prompt_paths == ["runbook.md"]

    def test_output_root_round_trips(self):
        step = ProcessStep(id="s1", mode="agent", output_root="runs/test/step")
        assert step.output_root == "runs/test/step"

    def test_code_step_requires_handler_or_command(self):
        with pytest.raises(ValidationError, match="code mode requires"):
            ProcessStep(id="s1", mode="code")

    def test_code_step_with_handler(self):
        step = ProcessStep(id="s1", mode="code", handler="my_handler.py")
        assert step.handler == "my_handler.py"

    def test_composite_requires_uses(self):
        with pytest.raises(ValidationError, match="composite mode requires"):
            ProcessStep(id="s1", mode="composite")

    def test_composite_with_uses(self):
        step = ProcessStep(id="s1", mode="composite", uses="deps.child_process")
        assert step.uses == "deps.child_process"

    def test_for_each_bind_must_be_in_bind_fields(self):
        with pytest.raises(ValidationError, match="for_each.bind_fields must include"):
            ProcessStep(
                id="s1",
                mode="agent",
                for_each=ForEach.model_validate(
                    {"over": "items", "bind": "ticker", "bind_fields": ["sector"]}
                ),
            )

    def test_for_each_bind_fields_must_be_unique(self):
        with pytest.raises(ValidationError, match="must be unique"):
            ProcessStep(
                id="s1",
                mode="agent",
                for_each=ForEach.model_validate(
                    {"over": "items", "bind": "ticker", "bind_fields": ["ticker", "ticker"]}
                ),
            )

    def test_for_each_bind_fields_preserve_authored_case(self):
        step = ProcessStep(
            id="s1",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "items", "bind": "Ticker", "bind_fields": ["Ticker", "Sector"]}
            ),
        )
        assert step.for_each is not None
        assert step.for_each.bind == "Ticker"
        assert step.for_each.bind_fields == ["Ticker", "Sector"]

    def test_on_failure_defaults_to_block(self):
        step = ProcessStep(id="s1", mode="agent", prompt_paths=["r.md"])
        assert step.on_failure == "block"

    def test_on_failure_accepts_continue(self):
        step = ProcessStep(id="run-stats", mode="code", handler="h.py", on_failure="continue")
        assert step.on_failure == "continue"

    def test_on_failure_rejects_invalid_value(self):
        """Validate via model_validate so we exercise Pydantic at runtime
        without going through the static-typed constructor."""
        with pytest.raises(ValidationError):
            ProcessStep.model_validate(
                {
                    "id": "s1",
                    "mode": "code",
                    "handler": "h.py",
                    "on_failure": "ignore",
                }
            )

    def test_with_alias(self):
        data = {
            "id": "s1",
            "mode": "composite",
            "uses": "child.md",
            "with": {"param1": "val1"},
        }
        step = ProcessStep.model_validate(data)
        assert step.with_ == {"param1": "val1"}

    def test_env_field(self):
        step = ProcessStep(id="s1", mode="agent", env={"KEY": "val"})
        assert step.env == {"KEY": "val"}


class TestProcessSpec:
    def test_minimal(self):
        spec = ProcessSpec(name="test-process")
        assert spec.name == "test-process"
        assert spec.steps == []
        assert spec.inputs == {}
        assert spec.deps == {}
        assert spec.outputs == {}

    def test_with_steps(self):
        spec = ProcessSpec(
            name="test",
            steps=[ProcessStep(id="s1", mode="agent")],
        )
        assert len(spec.steps) == 1

    def test_deps_declared(self):
        spec = ProcessSpec(
            name="test",
            deps={
                "child_process": ProcessDep.model_validate(
                    {
                        "path": "process/child/process.md",
                        "as": "path",
                    }
                )
            },
        )
        assert "child_process" in spec.deps
        assert spec.deps["child_process"].path == "process/child/process.md"

    def test_inputs_default_empty(self):
        """Existing specs that don't declare inputs keep an empty dict."""
        spec = ProcessSpec(name="test")
        assert spec.inputs == {}

    def test_outputs_default_empty(self):
        """Existing specs that don't declare outputs keep an empty dict."""
        spec = ProcessSpec(name="test")
        assert spec.outputs == {}

    def test_inputs_declared(self):
        """Process-level inputs parse into ProcessInput entries."""
        spec = ProcessSpec.model_validate(
            {
                "name": "predict",
                "inputs": {
                    "tickers_param": {
                        "description": "Comma-separated tickers",
                        "param": "TICKERS",
                        "as": "string",
                    },
                    "tickers": {
                        "path": "{{run.dir}}/predict/tickers.md",
                        "parse": {"format": "frontmatter-md", "extract": "items"},
                        "as": "list<map>",
                    },
                },
            }
        )
        assert set(spec.inputs.keys()) == {"tickers_param", "tickers"}
        param_input = spec.inputs["tickers_param"]
        assert param_input.param == "TICKERS"
        assert param_input.as_.kind == "string"
        tickers_input = spec.inputs["tickers"]
        assert tickers_input.path == "{{run.dir}}/predict/tickers.md"
        assert tickers_input.parse is not None
        assert tickers_input.parse.format == "frontmatter-md"
        assert tickers_input.parse.extract == "items"
        assert tickers_input.as_.kind == "list"
        assert tickers_input.as_.element is not None
        assert tickers_input.as_.element.kind == "map"

    def test_outputs_declared(self):
        """Process-level outputs parse into ProcessOutput entries."""
        spec = ProcessSpec.model_validate(
            {
                "name": "predict",
                "outputs": {
                    "predictions": {
                        "path": "{{run.dir}}/predict/{{run.variant}}/*/prediction.md",
                        "as": "list<path>",
                        "format": "frontmatter-md",
                    },
                    "qa_summary": {
                        "path": "{{run.dir}}/predict/qa/qa-summary.md",
                        "as": "path",
                        "format": "frontmatter-md",
                    },
                },
            }
        )
        predictions = spec.outputs["predictions"]
        assert predictions.as_.kind == "list"
        assert predictions.as_.element is not None
        assert predictions.as_.element.kind == "path"
        assert predictions.format == "frontmatter-md"
        qa = spec.outputs["qa_summary"]
        assert qa.as_.kind == "path"


class TestValueType:
    @pytest.mark.parametrize("raw", ["string", "path"])
    def test_primitive(self, raw: str):
        t = ValueType.parse(raw)
        assert t.kind == raw
        assert t.element is None
        assert t.key is None
        assert t.value is None

    def test_list_of_map(self):
        t = ValueType.parse("list<map>")
        assert t.kind == "list"
        assert t.element is not None
        assert t.element.kind == "map"

    def test_list_of_path(self):
        t = ValueType.parse("list<path>")
        assert t.kind == "list"
        assert t.element is not None
        assert t.element.kind == "path"

    def test_map_bare(self):
        """Bare `map` means container-only validation, not typed entries."""
        t = ValueType.parse("map")
        assert t.kind == "map"
        assert t.key is None
        assert t.value is None

    def test_map_generic(self):
        t = ValueType.parse("map<string, path>")
        assert t.kind == "map"
        assert t.key is not None
        assert t.key.kind == "string"
        assert t.value is not None
        assert t.value.kind == "path"

    def test_unknown_primitive_rejected(self):
        with pytest.raises(ValueError):
            ValueType.parse("integer")

    def test_malformed_generic_rejected(self):
        with pytest.raises(ValueError):
            ValueType.parse("list<")


class TestParseConfig:
    def test_yaml(self):
        pc = ParseConfig(format="yaml")
        assert pc.format == "yaml"
        assert pc.extract is None

    def test_frontmatter_md_with_extract(self):
        pc = ParseConfig(format="frontmatter-md", extract="items")
        assert pc.format == "frontmatter-md"
        assert pc.extract == "items"

    def test_unknown_format_rejected(self):
        with pytest.raises(ValidationError):
            ParseConfig(format="csv")  # pyright: ignore[reportArgumentType]


class TestProcessInput:
    def test_param_source(self):
        """An operator-supplied parameter: param name, no path."""
        pi = ProcessInput.model_validate(
            {"description": "items file", "param": "TICKERS", "as": "string"}
        )
        assert pi.param == "TICKERS"
        assert pi.path is None
        assert pi.parse is None

    def test_file_source(self):
        """A parsed-from-file input: path + parse config."""
        pi = ProcessInput.model_validate(
            {
                "path": "knowledge-base/kb-index.yaml",
                "parse": {"format": "yaml"},
                "as": "map",
            }
        )
        assert pi.path == "knowledge-base/kb-index.yaml"
        assert pi.parse is not None
        assert pi.parse.format == "yaml"


class TestProcessOutput:
    def test_minimal(self):
        po = ProcessOutput.model_validate(
            {"path": "{{run.dir}}/out.md", "as": "path", "format": "frontmatter-md"}
        )
        assert po.path == "{{run.dir}}/out.md"
        assert po.format == "frontmatter-md"

    def test_list_path(self):
        po = ProcessOutput.model_validate(
            {"path": "{{run.dir}}/*/prediction.md", "as": "list<path>"}
        )
        assert po.as_.kind == "list"
        assert po.as_.element is not None
        assert po.as_.element.kind == "path"

    def test_template_and_condition_default_none(self):
        po = ProcessOutput.model_validate({"path": "{{run.dir}}/out.md", "as": "path"})
        assert po.template is None
        assert po.condition is None

    def test_template_and_condition_parse(self):
        po = ProcessOutput.model_validate(
            {
                "path": "{{run.dir}}/leakage-check.md",
                "as": "path",
                "template": "process/predict/{{form_version}}/leakage-check.template.md",
                "condition": "RUN_MODE == backtest",
            }
        )
        assert po.template == "process/predict/{{form_version}}/leakage-check.template.md"
        assert po.condition == "RUN_MODE == backtest"


class TestProcessDefaults:
    def test_defaults(self):
        d = ProcessDefaults()
        assert d.adapters == {}
        assert d.default_adapter == "default"
        assert d.default_adapter_config.type == "claude-code-cli"
        assert d.reuse_policy == "validated_outputs"

    def test_default_adapter_config_from_map(self):
        d = ProcessDefaults(
            default_adapter="pi-cli",
            adapters={"pi-cli": AdapterConfig(type="pi-cli", config={"model": "glm-5"})},
        )
        assert d.default_adapter_config.type == "pi-cli"
        assert d.default_adapter_config.config["model"] == "glm-5"

    def test_default_adapter_config_fallback_to_first(self):
        d = ProcessDefaults(
            adapters={"custom": AdapterConfig(type="custom")},
        )
        assert d.default_adapter_config.type == "custom"


# ── Plan models ─────────────────────────────────────────────────


class TestFanOut:
    def test_basic(self):
        fo = FanOut(over="tickers", bind="ticker", source="tickers.md", bind_fields=["ticker"])
        assert fo.bind == "ticker"
        assert fo.items == []
        assert fo.filtered_count == 0


class TestResolvedStep:
    def test_basic(self):
        step = ResolvedStep(step_id="s1", mode="agent")
        assert step.adapter == ResolvedAdapter(type="claude-code-cli")
        assert step.env == {}


class TestPlan:
    def test_defaults(self):
        plan = Plan()
        assert plan.schema_ == "metaproc:Plan/0.6"
        assert plan.steps == []
        assert plan.lane_matrix is None
        assert plan.execution_lanes == []

    def test_schema_alias(self):
        data = {"schema": "metaproc:Plan/0.6", "process": "test", "steps": []}
        plan = Plan.model_validate(data)
        assert plan.schema_ == "metaproc:Plan/0.6"
        assert plan.process == "test"

    def test_historical_schema_token_still_resolvable(self):
        """Plans written under historical tokens keep parsing."""
        for token in ("metaproc:Plan/0.4", "metaproc:Plan/0.5"):
            data = {"schema": token, "process": "legacy", "steps": []}
            plan = Plan.model_validate(data)
            assert plan.process == "legacy"
            assert plan.lane_matrix is None
            assert plan.execution_lanes == []
            assert plan.resource_budgets == []


# ── Runtime models ──────────────────────────────────────────────


class TestMapItem:
    def test_extra_fields_allowed(self):
        item = MapItem.model_validate({"ticker": "AAPL", "sector": "tech"})
        assert item.ticker == "AAPL"  # pyright: ignore[reportAttributeAccessIssue]
        assert item.sector == "tech"  # pyright: ignore[reportAttributeAccessIssue]


class TestMapItemsFrontmatter:
    def test_with_items(self):
        data = {"items": [{"ticker": "AAPL"}, {"ticker": "GOOG"}]}
        fm = MapItemsFrontmatter.model_validate(data)
        assert len(fm.items) == 2

    def test_extra_fields_allowed(self):
        data = {"items": [], "extra_key": "val"}
        fm = MapItemsFrontmatter.model_validate(data)
        assert fm.extra_key == "val"  # pyright: ignore[reportAttributeAccessIssue]


class TestTerminalStatuses:
    def test_default_statuses(self):
        statuses = get_terminal_statuses()
        assert "completed" in statuses
        assert "cached" in statuses

    def test_register_additional(self):

        saved = set(_rt._TERMINAL_STATUSES)
        try:
            register_terminal_statuses(frozenset({"custom_done"}))
            statuses = get_terminal_statuses()
            assert "custom_done" in statuses
        finally:
            _rt._TERMINAL_STATUSES.clear()
            _rt._TERMINAL_STATUSES.update(saved)
