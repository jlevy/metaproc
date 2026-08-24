"""Tests for metaproc.engine — pathing, build_plan, validation, runtime.

Placeholder, discovery, code-handler, and merge-defaults tests live in
test_engine_ported.py / test_runtime_ported.py (more thorough coverage).
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
from pydantic import BaseModel
from softschema import Contract, Contracts, SchemaStatus

from metaproc.engine.build_plan import (
    _coerce_config_value,
    build_plan,
)
from metaproc.engine.pathing import (
    common_path,
    compute_log_filename,
    compute_logs_dir,
    compute_run_dir,
    find_item_dir,
    glob_resolve_path,
)
from metaproc.engine.placeholders import (
    collect_deferred_variables,
    resolve_output_paths,
    validate_spec_placeholders,
)
from metaproc.engine.process_scope import expand_process_vars
from metaproc.engine.runtime import prepare_step, resolve_batch_size, validate_step_inputs_exist
from metaproc.engine.validation import validate_fan_out_contracts, validate_item_outputs
from metaproc.models.authored import (
    ForEach,
    IOSpec,
    ProcessDep,
    ProcessInput,
    ProcessOutput,
    ProcessSpec,
    ProcessStep,
)

# ── Placeholders (non-duplicated) ────────────────────────────────


class TestResolveOutputPaths:
    def test_resolves_paths(self):
        outputs = {"main": IOSpec(path="runs/{{TAG}}/output.md")}
        result = resolve_output_paths(outputs, {"TAG": "v1"})
        assert result == {"main": "runs/v1/output.md"}

    def test_empty_outputs(self):
        result = resolve_output_paths({}, {})
        assert result == {}


class TestCollectDeferredVariables:
    def test_basic(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    for_each=ForEach.model_validate(
                        {"over": "source", "bind": "ticker", "bind_fields": ["ticker", "sector"]}
                    ),
                )
            ],
        )
        deferred = collect_deferred_variables(spec)
        assert "ticker" in deferred
        assert "sector" in deferred
        assert "tickers" in deferred  # plural form

    def test_no_for_each(self):
        spec = ProcessSpec(name="test", steps=[ProcessStep(id="s1", mode="agent")])
        deferred = collect_deferred_variables(spec)
        assert "step.outputs_list" in deferred  # internal deferred always present
        assert "run.variant" in deferred
        assert "step.prompt_path" in deferred


class TestValidateSpecPlaceholders:
    def test_unresolved_runs_dir_in_process_dep(self, monkeypatch):
        monkeypatch.delenv("RUNS_DIR", raising=False)
        spec = ProcessSpec(
            name="test",
            deps={
                "tickers": ProcessDep.model_validate(
                    {
                        "path": "{{run.parent_dir}}/{{run.id}}/progress.md",
                        "as": "list<path>",
                    }
                )
            },
        )
        errors = validate_spec_placeholders(spec, {"RUN_ID": "r1"})
        assert errors
        assert any("run.parent_dir" in e for e in errors)

    def test_resolved_via_env(self, monkeypatch):
        monkeypatch.setenv("RUNS_DIR", "runs")
        spec = ProcessSpec(
            name="test",
            outputs={
                "main": ProcessOutput.model_validate(
                    {"path": "{{run.parent_dir}}/out.md", "as": "path"}
                )
            },
        )
        assert validate_spec_placeholders(spec, {}) == []

    def test_resolved_via_variables(self):
        spec = ProcessSpec(
            name="test",
            outputs={
                "main": ProcessOutput.model_validate(
                    {"path": "{{run.parent_dir}}/out.md", "as": "path"}
                )
            },
        )
        assert validate_spec_placeholders(spec, {"RUNS_DIR": "runs"}) == []

    def test_deferred_for_each_fields_allowed(self, monkeypatch):
        monkeypatch.setenv("RUNS_DIR", "runs")
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    inputs={"source": IOSpec(path="src.md", kind="file", format="frontmatter-md")},
                    for_each=ForEach.model_validate(
                        {"over": "source", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                    outputs={"main": IOSpec(path="{{run.parent_dir}}/{{ticker}}/out.md")},
                )
            ],
        )
        assert validate_spec_placeholders(spec, {}) == []

    def test_unresolved_in_step_output(self, monkeypatch):
        monkeypatch.delenv("RUNS_DIR", raising=False)
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    outputs={"main": IOSpec(path="{{RUNS_DIR}}/out.md")},
                )
            ],
        )
        errors = validate_spec_placeholders(spec, {})
        assert errors
        assert any("s1" in e and "main" in e for e in errors)

    def test_dotted_deferred_runtime_vars_allowed(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    inputs={"source": IOSpec(path="src.md", kind="file", format="frontmatter-md")},
                    for_each=ForEach.model_validate(
                        {"over": "source", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                    output_root="{{run.parent_dir}}/{{run.id}}/{{run.variant}}",
                    outputs={"main": IOSpec(path="{{ticker}}/out.md")},
                )
            ],
        )
        assert validate_spec_placeholders(spec, {"RUNS_DIR": "runs", "RUN_ID": "r1"}) == []


class TestExpandProcessVars:
    def test_defaults_optional_input_from_literal_default(self, tmp_path: Path) -> None:
        spec = ProcessSpec(
            name="predict",
            inputs={
                "form_version": ProcessInput.model_validate(
                    {
                        "param": "FORM_VERSION",
                        "as": "string",
                        "required": False,
                        "default": "v11",
                    }
                )
            },
        )

        result = expand_process_vars(spec, {}, process_dir=tmp_path)

        assert result["form_version"] == "v11"
        assert result["FORM_VERSION"] == "v11"

    def test_explicit_value_overrides_default(self, tmp_path: Path) -> None:
        spec = ProcessSpec(
            name="predict",
            inputs={
                "form_version": ProcessInput.model_validate(
                    {
                        "param": "FORM_VERSION",
                        "as": "string",
                        "required": False,
                        "default": "v11",
                    }
                )
            },
        )

        result = expand_process_vars(spec, {"FORM_VERSION": "v10"}, process_dir=tmp_path)

        assert result["form_version"] == "v10"
        assert result["FORM_VERSION"] == "v10"


# ── Pathing ──────────────────────────────────────────────────────


class TestComputeLogsDir:
    def test_from_run_scoped_dep(self, tmp_path):
        """Logs dir is always <run_dir>/.logs/ — no longer walks into dep subdirs."""
        spec = ProcessSpec(
            name="test",
            deps={
                "tickers": ProcessDep.model_validate(
                    {
                        "path": "{{run.dir}}/predict/tickers.md",
                        "as": "list<map>",
                    }
                )
            },
        )
        result = compute_logs_dir(spec, {"RUNS_DIR": str(tmp_path / "runs"), "RUN_ID": "v1"})
        assert result == tmp_path / "runs" / "v1" / ".logs"

    def test_run_dir_fallback(self, tmp_path):
        spec = ProcessSpec(name="test")
        result = compute_logs_dir(spec, {"RUNS_DIR": str(tmp_path / "runs"), "RUN_ID": "r1"})
        assert result == tmp_path / "runs" / "r1" / ".logs"


class TestComputeLogFilename:
    def test_basic(self):
        step = ProcessStep(
            id="predict",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "source", "bind": "ticker", "bind_fields": ["ticker"]}
            ),
        )
        result = compute_log_filename(step, {"ticker": "AAPL"})
        assert result.startswith("predict_AAPL_")
        assert result.endswith(".jsonl")

    def test_no_for_each(self):
        step = ProcessStep(id="learn", mode="agent")
        result = compute_log_filename(step, {"DATE": "2025-01-01"})
        assert "2025-01-01" in result


class TestFindItemDir:
    def test_direct(self, tmp_path):
        item_dir = tmp_path / "AAPL"
        item_dir.mkdir()
        assert find_item_dir(tmp_path, "AAPL") == item_dir

    def test_sector_layout(self, tmp_path):
        item_dir = tmp_path / "tech" / "AAPL"
        item_dir.mkdir(parents=True)
        assert find_item_dir(tmp_path, "AAPL") == item_dir

    def test_variant_layout(self, tmp_path):
        """Variant-scoped layout: run_dir/variant/item."""
        item_dir = tmp_path / "claude-code-cli" / "AAPL"
        item_dir.mkdir(parents=True)
        assert find_item_dir(tmp_path, "AAPL") == item_dir

    def test_not_found(self, tmp_path):
        assert find_item_dir(tmp_path, "MISSING") is None


class TestComputeRunDir:
    """``compute_run_dir`` always returns ``<RUNS_DIR>/<RUN_ID>``.

    The process root is unambiguous; the artifact tree under it is
    whatever the spec templates declare.
    """

    def test_from_run_id_only(self, tmp_path):
        """The run dir is RUNS_DIR/RUN_ID, regardless of dep templates."""
        spec = ProcessSpec(
            name="test",
            deps={
                "tickers": ProcessDep.model_validate(
                    {
                        "path": "{{run.dir}}/predict/tickers.md",
                        "as": "list<map>",
                    }
                )
            },
        )
        variables = {"RUNS_DIR": str(tmp_path / "runs"), "RUN_ID": "my-run-id"}
        result = compute_run_dir(spec, variables)
        assert result == tmp_path / "runs" / "my-run-id"

    def test_no_deps_or_outputs(self, tmp_path):
        """No deps / outputs — still RUNS_DIR/RUN_ID."""
        spec = ProcessSpec(name="test")
        variables = {"RUNS_DIR": str(tmp_path / "runs"), "RUN_ID": "my-run-id"}
        result = compute_run_dir(spec, variables)
        assert result == tmp_path / "runs" / "my-run-id"

    def test_variant_in_output_path_does_not_shift_run_dir(self, tmp_path):
        """Variant or sub-prefix in an output template does NOT change the run dir."""
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="learn",
                    mode="manual",
                    outputs={
                        "progress": IOSpec(path="{{run.dir}}/learn/{{run.variant}}/progress.md")
                    },
                )
            ],
        )
        variables = {
            "RUNS_DIR": str(tmp_path / "runs"),
            "RUN_ID": "my-run",
            "VARIANT": "claude-code-cli",
        }
        result = compute_run_dir(spec, variables)
        assert result == tmp_path / "runs" / "my-run"


class TestCommonPath:
    """common_path is no longer used by compute_run_dir but remains as a utility.

    The common-ancestor walk over dep/output paths was removed when the new
    run-dir layout landed (see ``plan-2026-05-10-metaproc-run-dir-layout.md``).
    The helper itself is preserved for any code that still needs path-prefix
    intersection.
    """

    def test_two_dots_return_dot(self):
        assert common_path(Path(), Path()) == "."

    def test_equal_absolute_paths(self):
        assert common_path(Path("/a/b"), Path("/a/b")) == "/a/b"

    def test_equal_relative_paths(self):
        assert common_path(Path("x/y"), Path("x/y")) == "x/y"

    def test_shared_prefix(self):
        assert common_path(Path("/a/b/c"), Path("/a/b/d")) == "/a/b"

    def test_disjoint_relative_paths_still_raise(self):
        with pytest.raises(ValueError, match="no common prefix"):
            common_path(Path("a/b"), Path("c/d"))

    def test_empty_and_nonempty_raise(self):
        with pytest.raises(ValueError, match="no common prefix"):
            common_path(Path(), Path("a"))


class TestComputeRunDirUnresolved:
    """When RUN_ID/RUNS_DIR are absent, compute_run_dir returns the unresolved template.

    This is the validate/build-plan path: no live run identity is set, so
    {{run.dir}} stays as a literal. Callers that need a real dir must
    pre-populate RUN_ID / RUNS_DIR.
    """

    def test_no_run_id_returns_unresolved(self):
        spec = ProcessSpec(name="aggregator")
        variables: dict[str, str] = {}
        result = compute_run_dir(spec, variables)
        # The literal `{{run.dir}}` token is preserved by resolve_templates
        # when the variables are absent.
        assert "{{run.dir}}" in str(result)


class TestGlobResolvePath:
    def test_direct_resolve(self, tmp_path):
        f = tmp_path / "output.md"
        f.write_text("content")
        result = glob_resolve_path(tmp_path, "output.md", {})
        assert result == f

    def test_missing(self, tmp_path):
        assert glob_resolve_path(tmp_path, "missing.md", {}) is None


# ── Build Plan ───────────────────────────────────────────────────
# merge_defaults tests live in test_engine_ported.py (more thorough coverage).


class TestCoerceConfigValue:
    def test_int_fields(self):
        assert _coerce_config_value("timeout_s", "300") == 300

    def test_float_fields(self):
        assert _coerce_config_value("max_budget_usd", "1.5") == 1.5

    def test_bool_fields(self):
        assert _coerce_config_value("verbose", "true") is True
        assert _coerce_config_value("verbose", "false") is False

    def test_strict_mcp_config_bool_round_trip(self):
        # worker_entrypoint serializes adapter-config bools to "true"/"false"
        # over `--adapter-config`, and the receiver must coerce them back so
        # _build_claude_flags' identity check (`is True`) emits the flag.
        assert _coerce_config_value("strict_mcp_config", "true") is True
        assert _coerce_config_value("strict_mcp_config", "false") is False
        assert _coerce_config_value("no_session_persistence", "true") is True
        assert _coerce_config_value("worktree", "1") is True

    def test_tools_field(self):
        assert _coerce_config_value("tools", "Read,Write") == ["Read", "Write"]

    def test_string_passthrough(self):
        assert _coerce_config_value("model", "sonnet") == "sonnet"


class TestBuildPlan:
    def test_minimal_spec(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Do {{TASK}}",
                    output_root="{{run.dir}}/s1",
                )
            ],
        )
        plan = build_plan(spec, {"TASK": "something"}, process_path=Path("test/process.md"))
        assert len(plan.steps) == 1
        assert plan.steps[0].prompt_prefix == "Do something"
        assert plan.process == "test/process.md"

    def test_missing_required_param(self):
        spec = ProcessSpec(
            name="test",
            inputs={"tag": ProcessInput.model_validate({"param": "TAG", "as": "string"})},
            steps=[],
        )
        with pytest.raises(ValueError, match="missing required input: tag"):
            build_plan(spec, {}, process_path=Path("test.md"))

    def test_composite_with_for_each_resolves_child_and_items(self, tmp_path: Path):
        child_path = tmp_path / "child.process.md"
        child_path.write_text(
            "---\nprocess:\n  name: child\n  steps: []\n---\nchild\n",
        )
        roster_path = tmp_path / "roster.md"
        roster_path.write_text(
            "---\nprogress:\n  items:\n    - ticker: AAPL\n    - ticker: MSFT\n---\nroster\n",
        )

        spec = ProcessSpec(
            name="test",
            deps={
                "child": ProcessDep.model_validate({"path": str(child_path), "as": "path"}),
                "roster": ProcessDep.model_validate({"path": str(roster_path), "as": "path"}),
            },
            steps=[
                ProcessStep(
                    id="parent",
                    mode="composite",
                    uses="deps.child",
                    for_each=ForEach.model_validate(
                        {
                            "over": "deps.roster",
                            "bind": "ticker",
                            "bind_fields": ["ticker"],
                            "key": "{{ticker}}",
                        }
                    ),
                )
            ],
        )
        plan = build_plan(spec, {}, process_path=tmp_path / "test.process.md")

        assert plan.steps[0].uses_path == str(child_path)
        assert plan.steps[0].fan_out is not None
        assert plan.steps[0].fan_out.items == [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
        assert plan.steps[0].fan_out.retry is None

    def test_composite_with_for_each_rejects_whole_scope_retry(self, tmp_path: Path):
        child_path = tmp_path / "child.process.md"
        child_path.write_text("---\nprocess:\n  name: child\n  steps: []\n---\n")
        roster_path = tmp_path / "roster.md"
        roster_path.write_text("---\nprogress:\n  items: []\n---\n")
        spec = ProcessSpec(
            name="test",
            deps={
                "child": ProcessDep.model_validate({"path": str(child_path), "as": "path"}),
                "roster": ProcessDep.model_validate({"path": str(roster_path), "as": "path"}),
            },
            steps=[
                ProcessStep(
                    id="parent",
                    mode="composite",
                    uses="deps.child",
                    for_each=ForEach.model_validate(
                        {
                            "over": "deps.roster",
                            "bind": "ticker",
                            "bind_fields": ["ticker"],
                            "retry": {"max_retries": 1},
                        }
                    ),
                )
            ],
        )

        with pytest.raises(ValueError, match="does not support for_each.retry"):
            build_plan(spec, {}, process_path=tmp_path / "test.process.md")


# ── Runtime ──────────────────────────────────────────────────────


class TestResolveBatchSize:
    def test_default(self):
        step = ProcessStep(id="s1", mode="agent")
        assert resolve_batch_size(step) == 10

    def test_from_step(self):
        step = ProcessStep(
            id="s1",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "source", "bind": "ticker", "bind_fields": ["ticker"], "batch_size": 5}
            ),
        )
        assert resolve_batch_size(step) == 5

    def test_override(self):
        step = ProcessStep(
            id="s1",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "source", "bind": "ticker", "bind_fields": ["ticker"], "batch_size": 5}
            ),
        )
        assert resolve_batch_size(step, batch_size_override=20) == 20

    def test_invalid(self):
        step = ProcessStep(id="s1", mode="agent")
        with pytest.raises(ValueError, match="must be > 0"):
            resolve_batch_size(step, batch_size_override=0)


# ── Validation ───────────────────────────────────────────────────


class TestValidateFanOutContracts:
    def test_valid(self):
        spec = ProcessSpec(
            name="test",
            inputs={
                "tag": ProcessInput.model_validate(
                    {"param": "TAG", "as": "string", "required": False}
                )
            },
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Process {{ticker}} with {{tag}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        errors = validate_fan_out_contracts(spec, Path("test.md"))
        assert errors == []

    def test_case_mismatched_placeholder_rejected(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Process {{TICKER}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        errors = validate_fan_out_contracts(spec, Path("test.md"))
        assert len(errors) == 1
        assert "{{TICKER}}" in errors[0]

    def test_exact_case_placeholder_allowed(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Process {{TICKER}} in {{Sector}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "TICKER", "bind_fields": ["TICKER", "Sector"]}
                    ),
                )
            ],
        )
        assert validate_fan_out_contracts(spec, Path("test.md")) == []

    def test_undeclared_placeholder(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="{{UNDECLARED}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        errors = validate_fan_out_contracts(spec, Path("test.md"))
        assert len(errors) == 1
        assert "UNDECLARED" in errors[0]

    def test_framework_runtime_vars_allowed(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Write to {{run.parent_dir}}/{{run.variant}} for {{ticker}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        assert validate_fan_out_contracts(spec, Path("test.md")) == []

    def test_dotted_framework_runtime_vars_allowed(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Write to {{run.id}}/{{run.variant}} for {{ticker}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        assert validate_fan_out_contracts(spec, Path("test.md")) == []

    def test_common_param_names_are_not_implicit_framework_vars(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Write to {{RUN_ID}} for {{ticker}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        errors = validate_fan_out_contracts(spec, Path("test.md"))
        assert len(errors) == 1
        assert "{{RUN_ID}}" in errors[0]
        assert "not declared" in errors[0]

    def test_unknown_framework_namespace_rejected(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Process {{process.name}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        errors = validate_fan_out_contracts(spec, Path("test.md"))
        assert len(errors) == 1
        assert "unknown framework namespace" in errors[0]


class TestBuildPlanFanOutValidation:
    def test_rejects_case_mismatched_fan_out_placeholder(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Process {{TICKER}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "ticker", "bind_fields": ["ticker"]}
                    ),
                )
            ],
        )
        with pytest.raises(ValueError, match="\\{\\{TICKER\\}\\}"):
            build_plan(spec, {}, process_path=Path("test.md"))

    def test_accepts_exact_case_fan_out_placeholder(self):
        spec = ProcessSpec(
            name="test",
            steps=[
                ProcessStep(
                    id="s1",
                    mode="agent",
                    prompt_prefix="Process {{TICKER}}",
                    inputs={
                        "tickers": IOSpec(path="tickers.md", kind="file", format="frontmatter-md")
                    },
                    output_root="{{run.dir}}/s1",
                    for_each=ForEach.model_validate(
                        {"over": "tickers", "bind": "TICKER", "bind_fields": ["TICKER"]}
                    ),
                )
            ],
        )
        plan = build_plan(spec, {}, process_path=Path("test.md"))
        assert plan.steps[0].prompt_prefix == "Process {{TICKER}}"

    def test_rejects_unknown_framework_namespace_in_prompt(self):
        spec = ProcessSpec(
            name="test",
            steps=[ProcessStep(id="s1", mode="agent", prompt_prefix="Do {{foo.bar}}")],
        )
        with pytest.raises(ValueError, match="unknown framework namespace"):
            build_plan(spec, {}, process_path=Path("test.md"))

    def test_rejects_unknown_framework_member_in_prompt(self):
        spec = ProcessSpec(
            name="test",
            steps=[ProcessStep(id="s1", mode="agent", prompt_prefix="Do {{run.unknown}}")],
        )
        with pytest.raises(ValueError, match="unknown framework variable"):
            build_plan(spec, {}, process_path=Path("test.md"))


class TestValidateItemOutputs:
    def test_file_exists(self, tmp_path):
        (tmp_path / "output.md").write_text("content")
        outputs = {"main": IOSpec(path="output.md")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert errors == []

    def test_file_exists_accepts_gzipped_sibling(self, tmp_path):
        f = tmp_path / "output.json"
        f.write_text('{"ok": true}\n')
        with f.open("rb") as src, gzip.open(f.with_name(f.name + ".gz"), "wb") as dst:
            dst.write(src.read())
        f.unlink()
        outputs = {"main": IOSpec(path="output.json", format="json")}

        errors = validate_item_outputs(tmp_path, outputs)

        assert errors == []

    def test_file_missing(self, tmp_path):
        outputs = {"main": IOSpec(path="missing.md")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_directory_exists(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "record.md").write_text("content")
        outputs = {"main": IOSpec(path="artifacts", kind="directory")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert errors == []

    def test_directory_output_item_dir_exists(self, tmp_path):
        output_dir = tmp_path / "artifacts"
        output_dir.mkdir()
        (output_dir / "record.md").write_text("content")
        outputs = {"main": IOSpec(path="artifacts", kind="directory")}
        errors = validate_item_outputs(output_dir, outputs)
        assert errors == []

    def test_directory_empty_is_error(self, tmp_path):
        (tmp_path / "artifacts").mkdir()
        outputs = {"main": IOSpec(path="artifacts", kind="directory")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_directory_state_and_logs_ignored_for_content_check(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / ".state").mkdir()
        (artifacts / ".state" / "status.yaml").write_text("state: running\n")
        (artifacts / ".logs").mkdir()
        (artifacts / ".logs" / "run.log").write_text("log\n")
        outputs = {"main": IOSpec(path="artifacts", kind="directory")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert len(errors) == 1
        assert "empty" in errors[0]

    def test_directory_nested_content_passes(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        nested = artifacts / "tickers" / "IBM"
        nested.mkdir(parents=True)
        (nested / "IBM-2025Q1.md").write_text("record\n")
        outputs = {"main": IOSpec(path="artifacts", kind="directory")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert errors == []

    def test_file_output_multi_component_path(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        kb_dir = repo / "knowledge-base"
        kb_dir.mkdir(parents=True)
        (kb_dir / "kb-index.yaml").write_text("records: []\n")
        monkeypatch.chdir(repo)
        item_dir = tmp_path / "item"
        item_dir.mkdir()
        outputs = {"kb_index": IOSpec(path="knowledge-base/kb-index.yaml")}
        errors = validate_item_outputs(item_dir, outputs)
        assert errors == []

    def test_file_output_multi_parent_dirs(self, tmp_path, monkeypatch):
        """Outputs with distinct parent dirs each validate at their own path."""
        repo = tmp_path / "repo"
        kb_dir = repo / "knowledge-base"
        kb_dir.mkdir(parents=True)
        (kb_dir / "kb-index.yaml").write_text("records: []\n")
        run_dir = repo / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "publish-report.yaml").write_text("ok: true\n")
        monkeypatch.chdir(repo)
        item_dir = tmp_path / "item"
        item_dir.mkdir()
        outputs = {
            "kb_index": IOSpec(path="knowledge-base/kb-index.yaml"),
            "publish_report": IOSpec(
                path=str(run_dir / "publish-report.yaml"),
            ),
        }
        errors = validate_item_outputs(item_dir, outputs)
        assert errors == []

    def test_directory_missing(self, tmp_path):
        outputs = {"main": IOSpec(path="artifacts", kind="directory")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert len(errors) == 1
        assert errors[0] == "artifacts: directory not found"

    def test_frontmatter_validation(self, tmp_path):
        f = tmp_path / "output.md"
        f.write_text("---\nprocess:\n  name: test\n  steps: []\n---\nBody\n")
        outputs = {"main": IOSpec(path="output.md", format="frontmatter-md")}
        errors = validate_item_outputs(tmp_path, outputs)
        assert errors == []

    def test_frontmatter_schema_validation_uses_softschema_binding(self, tmp_path):
        class Sample(BaseModel):
            ticker: str
            score: int

        registry = Contracts()
        registry.register(
            Contract(
                id="sample:Sample/v1",
                model=Sample,
                envelope_key="sample",
                status=SchemaStatus.enforced,
            )
        )
        f = tmp_path / "output.md"
        f.write_text("---\nsample:\n  ticker: AAPL\n  score: 3\n---\nBody\n")
        outputs = {
            "main": IOSpec(path="output.md", format="frontmatter-md", contract="sample:Sample/v1")
        }

        errors = validate_item_outputs(tmp_path, outputs, softschema_registry=registry)

        assert errors == []

    def test_frontmatter_schema_validation_accepts_gzipped_sibling(self, tmp_path):
        class Sample(BaseModel):
            ticker: str
            score: int

        registry = Contracts()
        registry.register(
            Contract(
                id="sample:Sample/v1",
                model=Sample,
                envelope_key="sample",
                status=SchemaStatus.enforced,
            )
        )
        f = tmp_path / "output.md"
        f.write_text("---\nsample:\n  ticker: AAPL\n  score: 3\n---\nBody\n")
        with f.open("rb") as src, gzip.open(f.with_name(f.name + ".gz"), "wb") as dst:
            dst.write(src.read())
        f.unlink()
        outputs = {
            "main": IOSpec(path="output.md", format="frontmatter-md", contract="sample:Sample/v1")
        }

        errors = validate_item_outputs(tmp_path, outputs, softschema_registry=registry)

        assert errors == []

    def test_frontmatter_schema_validation_reports_envelope_mismatch(self, tmp_path):
        class Sample(BaseModel):
            ticker: str

        registry = Contracts()
        registry.register(Contract(id="sample:Sample/v1", model=Sample, envelope_key="sample"))
        f = tmp_path / "output.md"
        f.write_text("---\nwrong:\n  ticker: AAPL\n---\nBody\n")
        outputs = {
            "main": IOSpec(path="output.md", format="frontmatter-md", contract="sample:Sample/v1")
        }

        errors = validate_item_outputs(tmp_path, outputs, softschema_registry=registry)

        assert len(errors) == 1
        assert "envelope_mismatch" in errors[0]

    def test_run_dir_rendered_path_works_regardless_of_cwd(self, tmp_path, monkeypatch):
        """Regression for the fix.

        The pilot mine review-batch step failed validation with `review.md:
        file not found` even though the file was on disk: the output path
        rendered with a relative {{run.dir}}, then validate_item_outputs
        checked Path(rel).exists() against whatever cwd the parent process
        happened to have. With the fix the rendered run.dir is always
        absolute, so cwd no longer matters.

        This test reproduces the original failure mode (cwd != expected) and
        asserts it succeeds because the path resolves absolutely.
        """
        run_dir = tmp_path / "runs" / "test-run"
        item_dir = run_dir / "mine" / "claude-code-cli"
        item_dir.mkdir(parents=True)
        (item_dir / "review.md").write_text("---\n---\n# review\n")

        # Move cwd somewhere unrelated — what the pilot's parent process
        # was effectively doing relative to the rendered path.
        unrelated = tmp_path / "elsewhere"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)

        # Variables include RUN_ID + RUNS_DIR; resolve_templates renders
        # {{run.dir}} to an absolute path via Path.resolve().
        variables = {
            "RUNS_DIR": str(tmp_path / "runs"),
            "RUN_ID": "test-run",
        }
        outputs = {
            "review": IOSpec(
                path="{{run.dir}}/mine/claude-code-cli/review.md",
                format="markdown",
            )
        }
        errors = validate_item_outputs(item_dir, outputs, variables=variables)
        assert errors == []

    def test_run_variant_template_resolves_when_variant_in_variables(self, tmp_path):
        """Regression: non-fan-out agent steps need VARIANT in the validator's variables.

        Wed/Thu 2026-04-29/30 dispatch hit this: create-ops-review and
        create-prediction-summary are non-fan-out agent steps with output paths
        like {{run.dir}}/{{run.variant}}/ops-review.md. _execute_agent_step
        previously passed the run-level `variables` (without VARIANT) to
        validate_item_outputs, so {{run.variant}} rendered as the literal
        token and exists() returned False even though the agent had written
        the artifact at the correct path. Fix: pass step_vars (which sets
        VARIANT=effective_variant). This test asserts the validator works
        when VARIANT is supplied.
        """
        run_dir = tmp_path / "runs" / "test-run"
        item_dir = run_dir / "claude-cli"
        item_dir.mkdir(parents=True)
        (item_dir / "ops-review.md").write_text("# ops-review\n")

        variables_with_variant = {
            "RUNS_DIR": str(tmp_path / "runs"),
            "RUN_ID": "test-run",
            "VARIANT": "claude-cli",
        }
        outputs = {
            "ops_review": IOSpec(
                path="{{run.dir}}/{{run.variant}}/ops-review.md",
                format="markdown",
            )
        }
        errors = validate_item_outputs(item_dir, outputs, variables=variables_with_variant)
        assert errors == []

        # And without VARIANT the validator fails (this is the unfixed pre-Δ4 bug):
        variables_without_variant = {
            "RUNS_DIR": str(tmp_path / "runs"),
            "RUN_ID": "test-run",
        }
        errors = validate_item_outputs(item_dir, outputs, variables=variables_without_variant)
        assert errors  # non-empty — placeholder leaks through, exists() False
        assert "ops-review.md" in errors[0]


# ── prepare_step: instruction inlining ────────────────────────────


class TestPrepareStepInlinesInstructions:
    """prepare_step inlines prompt files into the prompt."""

    def test_inlines_runbook_content(self, tmp_path):
        """When prompt_paths reference a file, its content is inlined."""
        runbook = tmp_path / "my-runbook.md"
        runbook.write_text("# Runbook\n\nDo the thing.\n")

        spec = ProcessSpec(name="test")
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_prefix="Follow the runbook: {{step.prompt_path}}",
            prompt_paths=["my-runbook.md"],
        )
        variables: dict[str, str] = {"RUN_ID": "r1"}

        resolved_prompt, _logs_dir, _log_path = prepare_step(
            spec, step, variables, process_dir=tmp_path
        )

        assert "<prompt-file path=" in resolved_prompt
        assert "Do the thing." in resolved_prompt
        assert str(runbook) in resolved_prompt

    def test_sets_runbook_path_variable(self, tmp_path):
        """step.prompt_path is set even when content is inlined."""
        runbook = tmp_path / "my-runbook.md"
        runbook.write_text("# Runbook\n")

        spec = ProcessSpec(name="test")
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_prefix="path={{step.prompt_path}}",
            prompt_paths=["my-runbook.md"],
        )
        variables: dict[str, str] = {"RUN_ID": "r1"}

        resolved_prompt, _, _ = prepare_step(spec, step, variables, process_dir=tmp_path)

        # The path placeholder is resolved in the prompt
        assert f"path={tmp_path}/my-runbook.md" in resolved_prompt

    def test_no_instructions_no_inlining(self, tmp_path):
        """Without prompt_paths, prompt_prefix is returned as-is."""
        spec = ProcessSpec(name="test")
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_prefix="Just do it.",
        )
        variables: dict[str, str] = {"RUN_ID": "r1"}

        resolved_prompt, _, _ = prepare_step(spec, step, variables, process_dir=tmp_path)

        assert resolved_prompt.strip() == "Just do it."
        assert "<prompt-file" not in resolved_prompt

    def test_missing_instruction_file_no_crash(self, tmp_path):
        """Missing prompt files fail hard."""
        spec = ProcessSpec(name="test")
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_prefix="Follow: {{step.prompt_path}}",
            prompt_paths=["nonexistent.md"],
        )
        with pytest.raises(FileNotFoundError, match="prompt file not found"):
            prepare_step(spec, step, {"RUN_ID": "r1"}, process_dir=tmp_path)

    def test_multiple_instruction_files_raise(self, tmp_path):
        """Multiple prompt files are inlined in order."""
        (tmp_path / "one.md").write_text("# One\n")
        (tmp_path / "two.md").write_text("# Two\n")
        spec = ProcessSpec(name="test")
        step = ProcessStep(
            id="s1",
            mode="agent",
            prompt_paths=["one.md", "two.md"],
        )

        resolved_prompt, _, _ = prepare_step(spec, step, {"RUN_ID": "r1"}, process_dir=tmp_path)
        assert resolved_prompt.count("<prompt-file path=") == 2
        assert "# One" in resolved_prompt
        assert "# Two" in resolved_prompt


class TestValidateStepInputsExist:
    def test_file_input_exists(self, tmp_path):
        path = tmp_path / "input.md"
        path.write_text("ok")
        inputs = {"input": IOSpec(path=str(path), kind="file")}

        validate_step_inputs_exist(inputs, {}, context="step 's1'")

    def test_missing_required_input_raises(self, tmp_path):
        missing = tmp_path / "missing.md"
        inputs = {"input": IOSpec(path=str(missing), kind="file")}

        with pytest.raises(ValueError, match="input 'input' not found"):
            validate_step_inputs_exist(inputs, {}, context="step 's1'")

    def test_optional_missing_input_allowed(self, tmp_path):
        missing = tmp_path / "missing.md"
        inputs = {"input": IOSpec(path=str(missing), kind="file", optional=True)}

        validate_step_inputs_exist(inputs, {}, context="step 's1'")
