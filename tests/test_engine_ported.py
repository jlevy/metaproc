"""Direct tests for process engine functions — ported from example_workflow.

Tests the core engine functions as pure units — no CLI, no temp file setup.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from metaproc.engine.build_plan import merge_defaults
from metaproc.engine.code_handler import resolve_code_handler
from metaproc.engine.code_handler import resolve_templates as handler_resolve_templates
from metaproc.engine.discovery import normalize_item_fields
from metaproc.engine.placeholders import (
    TemplateResolutionError,
    check_unresolved_placeholders,
    resolve_runtime_config,
    resolve_templates,
)
from metaproc.models.authored import AdapterConfig, ForEach, ProcessDefaults, ProcessStep
from metaproc.models.runtime import StatusRecord

# ── resolve_templates ─────────────────────────────────────────────


def test_resolve_templates_basic():
    result = resolve_templates(
        "DATE={{DATE}} TICKER={{TICKER}}", {"DATE": "2026-03-24", "TICKER": "AAPL"}
    )
    assert result == "DATE=2026-03-24 TICKER=AAPL"


def test_resolve_templates_missing_vars_left_intact():
    result = resolve_templates("{{DATE}} {{UNKNOWN}}", {"DATE": "2026-03-24"})
    assert result == "2026-03-24 {{UNKNOWN}}"


def test_resolve_templates_nested_braces_not_confused():
    result = resolve_templates("prefix {literal} {{VAR}} suffix", {"VAR": "ok"})
    assert result == "prefix {literal} ok suffix"


def test_resolve_templates_empty_variables():
    result = resolve_templates("{{A}} {{B}}", {})
    assert result == "{{A}} {{B}}"


def test_resolve_templates_env_var_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unresolved placeholders fall back to os.environ.

    The fallback runs through ``resolve_runtime_runs_dir`` (added in
    plan-2026-05-20-earnings-run-settings-migration.md), which resolves
    ``RUNS_DIR`` to an absolute path; this test uses an absolute
    ``tmp_path`` so the assertion is environment-stable.
    """
    abs_runs = tmp_path / "runs" / "local" / "example-workflow"
    monkeypatch.setenv("RUNS_DIR", str(abs_runs))
    result = resolve_templates("{{run.parent_dir}}/{{run.id}}", {"RUN_ID": "test-123"})
    assert result == f"{abs_runs}/test-123"


def test_resolve_templates_explicit_var_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit variables dict takes priority over os.environ."""
    monkeypatch.setenv("RUNS_DIR", "from-env")
    result = resolve_templates("{{run.parent_dir}}", {"RUNS_DIR": "from-var"})
    assert result == "from-var"


def test_resolve_templates_env_var_unset_stays_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Placeholders not in variables or env stay as-is."""
    monkeypatch.delenv("MISSING_VAR", raising=False)
    result = resolve_templates("{{MISSING_VAR}}", {})
    assert result == "{{MISSING_VAR}}"


def test_resolve_templates_supports_dotted_run_namespace_aliases() -> None:
    result = resolve_templates(
        "{{run.parent_dir}}/{{run.id}}/{{run.variant}}",
        {
            "RUNS_DIR": "runs/local",
            "RUN_ID": "test-123",
            "VARIANT": "pi-glm-5",
        },
    )
    assert result == "runs/local/test-123/pi-glm-5"


def test_resolve_templates_derives_run_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`{{run.dir}}` resolves to an absolute path (internal-reference).

    Previously this returned the joined relative string verbatim, which
    broke code-mode `command:` subprocesses that ran with their own cwd.
    Anchoring at the operator's cwd at substitution time makes the rendered
    command work regardless of where the subprocess is invoked from.
    """
    runs_dir = tmp_path / "runs" / "local"
    runs_dir.mkdir(parents=True)
    result = resolve_templates(
        "{{run.dir}}/predict",
        {
            "RUNS_DIR": str(runs_dir),
            "RUN_ID": "test-123",
        },
    )
    expected = str((runs_dir / "test-123" / "predict").resolve())
    assert result == expected
    assert result.startswith("/")


def test_resolve_templates_raises_on_empty_string_substitution() -> None:
    """A placeholder explicitly set to empty must raise (internal-reference).

    Silent empty substitution produces malformed paths like `mine//<event>`
    where one component disappears. The guard fires before the path lands
    on disk.
    """

    with pytest.raises(TemplateResolutionError, match="empty string"):
        resolve_templates("mine/{{VARIANT}}/event-1", {"VARIANT": ""})


def test_resolve_templates_raises_on_whitespace_only_substitution() -> None:
    """All-whitespace is not a valid path component either."""

    with pytest.raises(TemplateResolutionError, match="empty string"):
        resolve_templates("mine/{{VARIANT}}/event-1", {"VARIANT": "   "})


def test_resolve_templates_leaves_unknown_placeholders_visible() -> None:
    """Missing key (None) is not the same as empty string; leave the token visible."""
    result = resolve_templates("mine/{{UNKNOWN}}/event-1", {})
    assert result == "mine/{{UNKNOWN}}/event-1"


def test_resolve_templates_supports_dotted_step_aliases() -> None:
    result = resolve_templates(
        "{{step.prompt_path}} -> {{step.outputs_list}}",
        {
            "step.prompt_path": "/tmp/runbook.md",
            "step.outputs_list": "/tmp/out.md",
        },
    )
    assert result == "/tmp/runbook.md -> /tmp/out.md"


def test_resolve_templates_rejects_list_interpolation() -> None:
    with pytest.raises(ValueError, match="cannot interpolate list value"):
        resolve_templates(
            "{{step.prompt_paths}}", {"step.prompt_paths": ["/tmp/a.md", "/tmp/b.md"]}
        )


def test_resolve_runtime_config_recurses() -> None:
    config = {
        "append_system_prompt": "Ticker={{TICKER}}",
        "tools": ["Read", "{{TOOL_NAME}}"],
        "nested": {"path": "runs/{{DATE}}/{{TICKER}}"},
    }
    resolved = resolve_runtime_config(
        config,
        {"DATE": "2026-03-24", "TICKER": "AAPL", "TOOL_NAME": "Write"},
    )
    assert resolved == {
        "append_system_prompt": "Ticker=AAPL",
        "tools": ["Read", "Write"],
        "nested": {"path": "runs/2026-03-24/AAPL"},
    }


# ── merge_defaults ────────────────────────────────────────────────


def test_merge_defaults_default_adapter():
    step = ProcessStep(id="s1", mode="agent")
    defaults = ProcessDefaults(
        default_adapter="claude-code-cli",
        adapters={
            "claude-code-cli": AdapterConfig(type="claude-code-cli", config={"model": "sonnet"})
        },
    )
    merged = merge_defaults(step, defaults)
    assert merged["adapter_type"] == "claude-code-cli"
    assert merged["model"] == "sonnet"


def test_merge_defaults_step_adapter_override():
    step = ProcessStep(
        id="s1",
        mode="agent",
        adapter=AdapterConfig(type="custom-adapter", config={"model": "opus"}),
    )
    defaults = ProcessDefaults(
        default_adapter="claude-code-cli",
        adapters={
            "claude-code-cli": AdapterConfig(
                type="claude-code-cli",
                config={"model": "sonnet", "timeout_s": 300},
            )
        },
    )
    merged = merge_defaults(step, defaults)
    assert merged["adapter_type"] == "custom-adapter"
    assert merged["model"] == "opus"
    # timeout_s mapped to timeout
    assert merged["timeout"] == 300


def test_merge_defaults_budget_inheritance():
    step = ProcessStep(id="s1", mode="agent", max_budget_usd=5.0, token_budget=100000)
    defaults = ProcessDefaults()
    merged = merge_defaults(step, defaults)
    assert merged["max_budget_usd"] == 5.0
    assert merged["token_budget"] == 100000


def test_merge_defaults_reuse_policy_fallback():
    step = ProcessStep(id="s1", mode="agent")
    defaults = ProcessDefaults(reuse_policy="always_rerun")
    merged = merge_defaults(step, defaults)
    assert merged["reuse_policy"] == "always_rerun"


def test_merge_defaults_reuse_policy_step_override():
    step = ProcessStep(id="s1", mode="agent", reuse_policy="skip_all")
    defaults = ProcessDefaults(reuse_policy="always_rerun")
    merged = merge_defaults(step, defaults)
    assert merged["reuse_policy"] == "skip_all"


# ── validate_step_contract ────────────────────────────────────────


def test_validate_step_contract_composite_without_uses():
    with pytest.raises(ValidationError, match="composite mode requires"):
        ProcessStep(id="s1", mode="composite")


def test_validate_step_contract_for_each_item_not_in_fields():
    with pytest.raises(ValidationError, match=r"for_each\.bind_fields must include bind field"):
        ProcessStep(
            id="s1",
            mode="agent",
            for_each=ForEach.model_validate(
                {"over": "progress", "bind": "ticker", "bind_fields": ["sector"]}
            ),
        )


def test_validate_step_contract_duplicate_fields():
    with pytest.raises(ValidationError, match=r"for_each\.bind_fields must be unique"):
        ProcessStep(
            id="s1",
            mode="agent",
            for_each=ForEach.model_validate(
                {
                    "over": "progress",
                    "bind": "ticker",
                    "bind_fields": ["ticker", "ticker", "sector"],
                }
            ),
        )


def test_validate_step_contract_preserves_authored_field_case():
    step = ProcessStep(
        id="s1",
        mode="agent",
        for_each=ForEach.model_validate(
            {"over": "progress", "bind": "Ticker", "bind_fields": ["Ticker", "Sector"]}
        ),
    )
    assert step.for_each is not None
    assert step.for_each.bind_fields == ["Ticker", "Sector"]


def test_validate_step_contract_valid_agent_step():
    step = ProcessStep(
        id="s1",
        mode="agent",
        for_each=ForEach.model_validate(
            {"over": "progress", "bind": "ticker", "bind_fields": ["ticker", "sector"]}
        ),
    )
    assert step.id == "s1"


# ── normalize_item_fields ─────────────────────────────────────────


def test_normalize_item_fields_preserves_authored_case():
    step_def = ProcessStep(
        id="s1",
        mode="agent",
        for_each=ForEach.model_validate(
            {"over": "source", "bind": "Ticker", "bind_fields": ["Ticker", "Sector"]}
        ),
    )
    assert normalize_item_fields(step_def) == ["Ticker", "Sector"]


def test_normalize_item_fields_deduplicates_item():
    step_def = ProcessStep(
        id="s1",
        mode="agent",
        for_each=ForEach.model_validate(
            {"over": "source", "bind": "ticker", "bind_fields": ["ticker", "sector"]}
        ),
    )
    result = normalize_item_fields(step_def)
    assert result == ["ticker", "sector"]
    assert result.count("ticker") == 1


def test_normalize_item_fields_inserts_item_if_missing():
    step_def = ProcessStep(
        id="s1",
        mode="agent",
        for_each=ForEach.model_validate(
            {"over": "source", "bind": "ticker", "bind_fields": ["ticker", "sector"]}
        ),
    )
    result = normalize_item_fields(step_def)
    assert result[0] == "ticker"
    assert "sector" in result


def test_normalize_item_fields_no_for_each():
    step_def = ProcessStep(id="s1", mode="agent")
    assert normalize_item_fields(step_def) == []


# ── check_unresolved_placeholders ────────────────────────────────


def test_check_unresolved_finds_unresolved():
    errors = check_unresolved_placeholders("{{DATE}} {{TICKER}} done")
    assert len(errors) == 2
    assert "DATE" in errors[0]
    assert "TICKER" in errors[1]


def test_check_unresolved_allows_specified_set():
    errors = check_unresolved_placeholders(
        "{{DATE}} {{TICKER}} done",
        allowed={"DATE", "TICKER"},
    )
    assert errors == []


def test_check_unresolved_partial_allowed():
    errors = check_unresolved_placeholders(
        "{{DATE}} {{TICKER}}",
        allowed={"DATE"},
    )
    assert len(errors) == 1
    assert "TICKER" in errors[0]


def test_check_unresolved_includes_context():
    errors = check_unresolved_placeholders("{{FOO}}", context="step 'bar'")
    assert "step 'bar'" in errors[0]


def test_check_unresolved_clean_text():
    errors = check_unresolved_placeholders("no placeholders here")
    assert errors == []


def test_check_unresolved_rejects_unknown_framework_namespace():
    errors = check_unresolved_placeholders("{{process.name}}")
    assert len(errors) == 1
    assert "unknown framework namespace" in errors[0]


def test_check_unresolved_rejects_unknown_framework_member():
    errors = check_unresolved_placeholders("{{run.unknown}}")
    assert len(errors) == 1
    assert "unknown framework variable" in errors[0]


# ── Negative model validation ─────────────────────────────────────


def test_process_step_invalid_mode():
    with pytest.raises(ValidationError):
        ProcessStep.model_validate({"id": "s1", "mode": "invalid_mode"})


def test_for_each_without_source():
    with pytest.raises(ValidationError):
        ForEach.model_validate({"bind": "ticker", "bind_fields": ["ticker"]})


def test_process_step_code_mode_requires_handler():
    """code mode without handler or command is rejected."""
    with pytest.raises(ValidationError, match="code mode requires"):
        ProcessStep.model_validate({"id": "s1", "mode": "code"})


def test_process_step_code_mode_with_handler():
    step = ProcessStep.model_validate({"id": "s1", "mode": "code", "handler": "foo.py:bar"})
    assert step.handler == "foo.py:bar"


def test_process_step_code_mode_with_command():
    step = ProcessStep.model_validate({"id": "s1", "mode": "code", "command": "echo hello"})
    assert step.command == "echo hello"


# ── resolve_code_handler ─────────────────────────────────────────


def test_resolve_code_handler_missing_colon():
    """Handler ref without ':' is rejected."""

    with pytest.raises(ValueError, match="must be"):
        resolve_code_handler("no_colon_here", Path())


def test_resolve_code_handler_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="handler file not found"):
        resolve_code_handler("missing.py:func", tmp_path)


def test_resolve_code_handler_function_not_found(tmp_path):
    handler_file = tmp_path / "handler.py"
    handler_file.write_text("def other_func(ctx, step): pass\n")
    with pytest.raises(AttributeError, match="not found"):
        resolve_code_handler("handler.py:missing_func", tmp_path)


def test_resolve_code_handler_wrong_signature(tmp_path):
    handler_file = tmp_path / "handler.py"
    handler_file.write_text("def bad_func(): pass\n")
    with pytest.raises(TypeError, match="must accept at least 2"):
        resolve_code_handler("handler.py:bad_func", tmp_path)


def test_resolve_code_handler_success(tmp_path):
    handler_file = tmp_path / "handler.py"
    handler_file.write_text("def good_func(ctx, step): pass\n")
    fn = resolve_code_handler("handler.py:good_func", tmp_path)
    assert callable(fn)
    assert fn.__name__ == "good_func"


def test_resolve_code_handler_bootstraps_repo_imports(tmp_path):
    repo_root = tmp_path / "repo"
    process_dir = repo_root / "process" / "predict"
    helper_pkg = repo_root / "demo_pkg"
    process_dir.mkdir(parents=True)
    helper_pkg.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n")
    (helper_pkg / "__init__.py").write_text("VALUE = 'ok'\n")
    handler_file = process_dir / "handler.py"
    handler_file.write_text(
        "from demo_pkg import VALUE\ndef good_func(ctx, step):\n    return VALUE\n"
    )

    fn = resolve_code_handler("handler.py:good_func", process_dir)

    assert callable(fn)
    assert fn({}, cast("ProcessStep", cast("object", None))) == "ok"


def test_resolve_code_handler_syntax_error(tmp_path):
    handler_file = tmp_path / "handler.py"
    handler_file.write_text("def broken(\n")
    with pytest.raises(SyntaxError):
        resolve_code_handler("handler.py:broken", tmp_path)


def test_resolve_code_handler_future_annotations_with_dataclass(tmp_path):
    """Handler using both `from __future__ import annotations` and @dataclass loads OK.

    Regression test for internal-reference: the dynamic loader did not register the
    synthetic module in ``sys.modules``, so ``dataclasses._is_type`` could not
    resolve stringised annotations and raised ``AttributeError``.
    """
    handler_file = tmp_path / "dc_handler.py"
    handler_file.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class Result:\n"
        "    value: int\n"
        "    label: str\n"
        "\n"
        "def h(context, step):\n"
        "    return Result(value=42, label='ok')\n"
    )
    fn = resolve_code_handler("dc_handler.py:h", tmp_path)
    assert callable(fn)
    result = fn({}, cast("ProcessStep", cast("object", None)))
    assert result.value == 42  # pyright: ignore[reportAttributeAccessIssue]
    assert result.label == "ok"  # pyright: ignore[reportAttributeAccessIssue]


def test_code_handler_re_exports_resolve_templates():
    assert handler_resolve_templates("{{NAME}}", {"NAME": "world"}) == "world"


# ── StatusRecord invalid state ────────────────────────────────────


def test_status_record_invalid_state():

    with pytest.raises(ValidationError):
        StatusRecord.model_validate(
            {
                "run_id": "test",
                "step_id": "s1",
                "item": {"ticker": "AAPL"},
                "state": "invalid_state",
                "attempt": 1,
                "started_at": "2026-03-24T12:00:00",
            }
        )
