"""Tests for Plan data model and build_plan() — ported from example_workflow.

Validates the Plan/ResolvedStep/FanOut models and the build_plan() function.
CLI tests and domain-specific cleanup tests are skipped.
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from metaproc.engine.build_plan import build_plan
from metaproc.engine.process_scope import expand_process_vars
from metaproc.io.frontmatter import register_envelopes
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import FanOut, Plan, ResolvedStep
from metaproc.models.runtime import MapItem, register_terminal_statuses


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip())


# ── Model tests ──────────────────────────────────────────────────


def test_fan_out_model():

    fo = FanOut.model_validate(
        {
            "over": "tickers",
            "bind": "ticker",
            "source": "runs/predict/2026-03-24/progress.md",
            "bind_fields": ["ticker", "sector"],
            "batch_size": 6,
            "items": [{"ticker": "AAPL", "sector": "technology"}],
            "filtered_count": 2,
        }
    )
    assert fo.bind == "ticker"
    assert fo.over == "tickers"
    assert fo.batch_size == 6
    assert len(fo.items) == 1
    assert fo.filtered_count == 2


def test_resolved_step_model():

    step = ResolvedStep.model_validate(
        {
            "step_id": "scaffold-day",
            "mode": "agent",
            "description": "Pull calendar",
            "adapter": {"type": "claude-code-cli", "config": {"model": "sonnet"}},
            "prompt_prefix": "Do the thing DATE=2026-03-24",
            "prompt_paths": ["runbook.md"],
            "reuse_policy": "validated_outputs",
        }
    )
    assert step.step_id == "scaffold-day"
    assert step.adapter.type == "claude-code-cli"
    assert step.adapter.config["model"] == "sonnet"
    assert step.fan_out is None


def test_plan_model():

    plan = Plan.model_validate(
        {
            "schema": "metaproc:Plan/0.5",
            "generated_at": "2026-03-24T12:00:00",
            "process": "process/predict/predict.process.md",
            "params": {"DATE": "2026-03-24"},
            "steps": [
                {
                    "step_id": "s1",
                    "mode": "agent",
                    "adapter": {"type": "claude-code-cli", "config": {}},
                },
            ],
        }
    )
    assert plan.schema_ == "metaproc:Plan/0.5"
    assert plan.params["DATE"] == "2026-03-24"
    assert len(plan.steps) == 1


def test_plan_serializes_with_schema_alias():
    """Plan.model_dump(by_alias=True) should use 'schema' not 'schema_'."""

    plan = Plan(
        generated_at="2026-03-24T12:00:00",
        process="process.md",
        params={},
        steps=[],
    )
    dumped = plan.model_dump(by_alias=True)
    assert "schema" in dumped
    assert dumped["schema"] == "metaproc:Plan/0.5"


# ── build_plan tests ─────────────────────────────────────────────


def test_build_plan_resolves_variables():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n# test\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "inputs": {"requested_date": {"param": "DATE", "as": "string"}},
                "defaults": {
                    "default_adapter": "claude-code-cli",
                    "adapters": {
                        "claude-code-cli": {
                            "type": "claude-code-cli",
                            "config": {"model": "opus"},
                        }
                    },
                },
                "steps": [
                    {
                        "id": "s1",
                        "mode": "agent",
                        "prompt_prefix": "Run for DATE={{DATE}}",
                        "output_root": "{{run.dir}}/s1",
                    },
                ],
            }
        )
        params = expand_process_vars(spec, {"DATE": "2026-03-24"}, process_dir=process_path.parent)
        plan = build_plan(spec, params, process_path=process_path)
        assert plan.params["DATE"] == "2026-03-24"
        assert plan.params["requested_date"] == "2026-03-24"
        assert plan.steps[0].prompt_prefix == "Run for DATE=2026-03-24"
        assert plan.steps[0].adapter.type == "claude-code-cli"
        assert plan.steps[0].adapter.config["model"] == "opus"


def test_build_plan_preserves_explicit_empty_step_env():
    """An empty step env value must override an inherited credential."""

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {"default_adapter": "claude-code-cli"},
                "steps": [
                    {
                        "id": "s1",
                        "mode": "agent",
                        "prompt_prefix": "do thing",
                        "output_root": "{{run.dir}}/s1",
                        "env": {
                            "GOOGLE_API_KEY": "",
                            "GOOGLE_GENAI_USE_VERTEXAI": "true",
                        },
                    },
                ],
            }
        )

        plan = build_plan(spec, {}, process_path=process_path)

        assert plan.steps[0].env == {
            "GOOGLE_API_KEY": "",
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
        }


def test_build_plan_merges_adapter_config():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "defaults": {
                    "default_adapter": "claude-code-cli",
                    "adapters": {
                        "claude-code-cli": {
                            "type": "claude-code-cli",
                            "config": {"model": "opus", "timeout_s": 900, "tools": ["Read"]},
                        }
                    },
                },
                "steps": [
                    {
                        "id": "s1",
                        "mode": "agent",
                        "output_root": "{{run.dir}}/s1",
                        "adapter": {
                            "type": "claude-code-cli",
                            "config": {"model": "sonnet", "tools": ["Read", "Write"]},
                        },
                    },
                ],
            }
        )
        plan = build_plan(spec, {}, process_path=process_path)
        adapter = plan.steps[0].adapter
        # Step config overrides defaults
        assert adapter.config["model"] == "sonnet"
        assert adapter.config["tools"] == ["Read", "Write"]
        # Default config is inherited where step doesn't override
        assert adapter.config["timeout_s"] == 900


def test_build_plan_with_fan_out():

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tickers_path = tmp / "runs/2026-03-24-daily/predict/tickers.md"
        process_path = tmp / "process/predict/predict.process.md"

        _write(
            tickers_path,
            """\
            ---
            tickers:
              date: "2026-03-24"
              process: predict
              items:
                - ticker: AAPL
                  sector: technology
                  status: pending
                - ticker: NVDA
                  sector: technology
                  status: done
            ---
            # Tickers
            """,
        )
        _write(process_path, "---\nprocess:\n  name: predict\n---\n")

        # Register a "tickers" envelope so load_frontmatter_typed can parse it
        class TickersFrontmatter(BaseModel):
            date: str = ""
            process: str = ""
            items: list[MapItem] = Field(default_factory=list)
            model_config = {"extra": "allow"}

        class TickersEnvelope(BaseModel):
            tickers: TickersFrontmatter

        register_envelopes({"tickers": TickersEnvelope})

        # Also register "done" as a terminal status
        register_terminal_statuses(frozenset({"done", "skipped"}))

        spec = ProcessSpec.model_validate(
            {
                "name": "predict",
                "inputs": {"date": {"param": "DATE", "as": "string"}},
                "steps": [
                    {
                        "id": "predict-ticker",
                        "mode": "agent",
                        "inputs": {
                            "tickers": {
                                "path": str(tickers_path),
                                "kind": "file",
                                "format": "frontmatter-md",
                            },
                        },
                        "for_each": {
                            "over": "tickers",
                            "bind": "ticker",
                            "bind_fields": ["ticker", "sector", "status"],
                            "batch_size": 4,
                        },
                        "prompt_prefix": "ticker={{ticker}} DATE={{date}}",
                        "outputs": {
                            "prediction": {
                                "path": "{{run.dir}}/{{sector}}/{{ticker}}/pred.md",
                            },
                        },
                    },
                ],
            }
        )
        plan = build_plan(
            spec,
            {
                "DATE": "2026-03-24",
                "RUNS_DIR": str(tmp / "runs"),
                "RUN_ID": "predict-run",
            },
            process_path=process_path,
        )
        step = plan.steps[0]
        assert step.fan_out is not None
        assert step.fan_out.bind == "ticker"
        assert step.fan_out.batch_size == 4
        assert len(step.fan_out.items) == 1  # AAPL actionable, NVDA filtered
        assert step.fan_out.filtered_count == 1
        assert step.fan_out.items[0]["ticker"] == "AAPL"


def test_build_plan_rejects_case_mismatched_fan_out_placeholder():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "steps": [
                    {
                        "id": "fan-out-step",
                        "mode": "agent",
                        "inputs": {"tickers": {"path": "tickers.md"}},
                        "prompt_prefix": "Process {{TICKER}}",
                        "for_each": {
                            "over": "tickers",
                            "bind": "ticker",
                            "bind_fields": ["ticker"],
                        },
                    }
                ],
            }
        )

        with pytest.raises(ValueError, match="\\{\\{TICKER\\}\\}"):
            build_plan(spec, {}, process_path=process_path)


def test_build_plan_rejects_missing_required_params():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "inputs": {"requested_date": {"param": "DATE", "as": "string"}},
                "steps": [],
            }
        )
        with pytest.raises(ValueError, match="requested_date"):
            build_plan(spec, {}, process_path=process_path)


def test_build_plan_rejects_blank_required_params():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "inputs": {"requested_run": {"param": "RUN_ID", "as": "string"}},
                "steps": [],
            }
        )
        with pytest.raises(ValueError, match="blank required input: requested_run"):
            build_plan(spec, {"RUN_ID": "   "}, process_path=process_path)


def test_build_plan_resolves_symbolic_ref_and_output_root(tmp_path):

    process_path = tmp_path / "test.process.md"
    _write(process_path, "---\nprocess:\n  name: test\n---\n")

    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "steps": [
                {
                    "id": "generate",
                    "mode": "agent",
                    "prompt_prefix": "generate",
                    "output_root": "{{run.parent_dir}}/{{run.id}}/generate",
                    "outputs": {
                        "record": {
                            "path": "record.md",
                            "type": "report",
                            "kind": "file",
                        }
                    },
                },
                {
                    "id": "consume",
                    "mode": "agent",
                    "prompt_prefix": "consume",
                    "output_root": "{{run.dir}}/consume",
                    "inputs": {
                        "record": {
                            "ref": "generate.record",
                            "type": "report",
                            "kind": "file",
                        }
                    },
                },
            ],
        }
    )

    plan = build_plan(
        spec,
        {"RUNS_DIR": str(tmp_path / "runs"), "RUN_ID": "test-run"},
        process_path=process_path,
    )

    generate = next(step for step in plan.steps if step.step_id == "generate")
    consume = next(step for step in plan.steps if step.step_id == "consume")
    expected = str(tmp_path / "runs" / "test-run" / "generate" / "record.md")

    assert generate.outputs["record"].path == expected
    assert consume.inputs["record"].path == expected
    assert consume.needs == ["generate"]


def test_build_plan_rejects_dangling_symbolic_ref(tmp_path):

    process_path = tmp_path / "test.process.md"
    _write(process_path, "---\nprocess:\n  name: test\n---\n")

    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "steps": [
                {
                    "id": "consume",
                    "mode": "agent",
                    "prompt_prefix": "consume",
                    "output_root": "{{run.dir}}/consume",
                    "inputs": {"record": {"ref": "generate.record"}},
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="points to an unknown step"):
        build_plan(spec, {}, process_path=process_path)


def test_build_plan_rejects_symbolic_ref_type_mismatch(tmp_path):

    process_path = tmp_path / "test.process.md"
    _write(process_path, "---\nprocess:\n  name: test\n---\n")

    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "steps": [
                {
                    "id": "generate",
                    "mode": "agent",
                    "prompt_prefix": "generate",
                    "outputs": {"record": {"path": "record.md", "type": "report"}},
                },
                {
                    "id": "consume",
                    "mode": "agent",
                    "prompt_prefix": "consume",
                    "output_root": "{{run.dir}}/consume",
                    "inputs": {"record": {"ref": "generate.record", "type": "summary"}},
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="type mismatch"):
        build_plan(spec, {}, process_path=process_path)


def test_build_plan_rejects_process_param_item_scope_collision(tmp_path):

    process_path = tmp_path / "test.process.md"
    _write(process_path, "---\nprocess:\n  name: test\n---\n")

    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "inputs": {
                "earnings_date": {"param": "EARNINGS_DATE", "as": "string", "required": False}
            },
            "steps": [
                {
                    "id": "fan-out",
                    "mode": "agent",
                    "inputs": {"tickers": {"path": "tickers.md"}},
                    "for_each": {
                        "over": "tickers",
                        "bind": "ticker",
                        "bind_fields": ["ticker", "earnings_date"],
                    },
                    "prompt_prefix": "date={{earnings_date}}",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="scope collision.*earnings_date"):
        build_plan(spec, {}, process_path=process_path)


def test_build_plan_rejects_reserved_run_id_process_name():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "inputs": {"run_id": {"param": "RUN_ID", "as": "string", "required": False}},
                "steps": [{"id": "noop", "mode": "manual"}],
            }
        )

        with pytest.raises(ValueError, match=r"run_id.*\{\{run\.id\}\}"):
            build_plan(spec, {}, process_path=process_path)


def test_build_plan_rejects_fan_out_agent_outputs_outside_run_dir():

    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        spec = ProcessSpec.model_validate(
            {
                "name": "test",
                "steps": [
                    {
                        "id": "mine-adhoc",
                        "mode": "agent",
                        "prompt_prefix": "mine",
                        "inputs": {"tickers": {"path": str(Path(tmpdir) / "tickers.md")}},
                        "for_each": {
                            "over": "tickers",
                            "bind": "ticker",
                            "bind_fields": ["ticker"],
                        },
                        "outputs": {
                            "record": {
                                "path": "knowledge-base/tickers/{{ticker}}/",
                                "kind": "directory",
                            }
                        },
                    }
                ],
            }
        )

        with pytest.raises(
            ValueError, match=r"fan-out agent outputs must stay under \{\{run\.dir\}\}"
        ):
            build_plan(
                spec,
                {"RUNS_DIR": str(Path(tmpdir) / "runs"), "RUN_ID": "test-run"},
                process_path=process_path,
            )


# ── Adapter override config merging tests ───────────────────────


def _make_spec_with_pi_default_and_claude_declared():
    """Spec where default is pi-cli but claude-code-cli is also in adapters map."""

    return ProcessSpec.model_validate(
        {
            "name": "test-mine",
            "defaults": {
                "default_adapter": "pi-glm-5",
                "adapters": {
                    "pi-glm-5": {
                        "type": "pi-cli",
                        "config": {
                            "provider": "vertex-maas",
                            "model": "glm-5-maas",
                        },
                    },
                    "claude-code-cli": {
                        "type": "claude-code-cli",
                        "config": {
                            "model": "opus",
                            "permission_mode": "bypassPermissions",
                            "tools": ["Read", "Write", "Bash"],
                            "timeout_s": 900,
                        },
                    },
                },
            },
            "steps": [
                {
                    "id": "s1",
                    "mode": "agent",
                    "prompt_prefix": "do thing",
                    "output_root": "{{run.dir}}/s1",
                },
            ],
        }
    )


def _make_spec_with_pi_default_only():
    """Spec where default is pi-cli and claude-code-cli is NOT in adapters map."""

    return ProcessSpec.model_validate(
        {
            "name": "test-mine",
            "defaults": {
                "default_adapter": "pi-glm-5",
                "adapters": {
                    "pi-glm-5": {
                        "type": "pi-cli",
                        "config": {
                            "provider": "vertex-maas",
                            "model": "glm-5-maas",
                        },
                    },
                },
            },
            "steps": [
                {
                    "id": "s1",
                    "mode": "agent",
                    "prompt_prefix": "do thing",
                    "output_root": "{{run.dir}}/s1",
                },
            ],
        }
    )


def test_adapter_override_uses_declared_config():
    """When --adapter targets a type IN the adapters map, use its declared config."""

    spec = _make_spec_with_pi_default_and_claude_declared()
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        plan = build_plan(spec, {}, process_path=process_path, adapter_override="claude-code-cli")
        adapter = plan.steps[0].adapter
        assert adapter.type == "claude-code-cli"
        assert adapter.config["permission_mode"] == "bypassPermissions"
        assert adapter.config["tools"] == ["Read", "Write", "Bash"]
        assert adapter.config["model"] == "opus"
        assert adapter.config["timeout_s"] == 900
        # Should NOT inherit pi-cli config
        assert "provider" not in adapter.config


def test_adapter_override_undeclared_gets_empty_config():
    """When --adapter targets a type NOT in the adapters map and differs from
    the default adapter type, base_config should be empty — not inherited
    from the incompatible default adapter."""

    spec = _make_spec_with_pi_default_only()
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        plan = build_plan(spec, {}, process_path=process_path, adapter_override="claude-code-cli")
        adapter = plan.steps[0].adapter
        assert adapter.type == "claude-code-cli"
        # Must NOT inherit pi-cli config keys
        assert "provider" not in adapter.config
        assert adapter.config.get("model") != "glm-5-maas"


def test_adapter_override_matching_default_inherits_config():
    """When --adapter matches the default adapter type, inherit its config normally."""

    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "defaults": {
                "default_adapter": "claude-code-cli",
                "adapters": {
                    "claude-code-cli": {
                        "type": "claude-code-cli",
                        "config": {
                            "model": "opus",
                            "permission_mode": "bypassPermissions",
                            "timeout_s": 900,
                        },
                    },
                },
            },
            "steps": [
                {
                    "id": "s1",
                    "mode": "agent",
                    "prompt_prefix": "do thing",
                    "output_root": "{{run.dir}}/s1",
                },
            ],
        }
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        plan = build_plan(spec, {}, process_path=process_path, adapter_override="claude-code-cli")
        adapter = plan.steps[0].adapter
        assert adapter.type == "claude-code-cli"
        assert adapter.config["permission_mode"] == "bypassPermissions"
        assert adapter.config["model"] == "opus"
        assert adapter.config["timeout_s"] == 900


def test_adapter_override_unknown_type_raises():
    """build_plan rejects an adapter_override that is neither a named config
    nor a known adapter type."""

    spec = ProcessSpec.model_validate(
        {
            "name": "test",
            "defaults": {"default_adapter": "claude-code-cli"},
            "steps": [
                {
                    "id": "s1",
                    "mode": "agent",
                    "prompt_prefix": "do thing",
                    "output_root": "{{run.dir}}/s1",
                },
            ],
        }
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        process_path = Path(tmpdir) / "test.process.md"
        _write(process_path, "---\nprocess:\n  name: test\n---\n")

        with pytest.raises(ValueError, match="unknown adapter override"):
            build_plan(spec, {}, process_path=process_path, adapter_override="bogus-adapter")
