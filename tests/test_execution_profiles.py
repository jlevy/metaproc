from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from metaproc.cli import app
from metaproc.commands.auth_check import _resolve_variant_target
from metaproc.engine.build_plan import build_plan
from metaproc.models.authored import ProcessSpec
from metaproc.models.execution_profile import ExecutionProfileRegistry

runner = CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip())


def test_registry_resolves_extends_and_source_precedence(tmp_path: Path) -> None:
    repo_registry = tmp_path / ".metaproc" / "execution-profiles.yaml"
    local_registry = tmp_path / ".metaproc" / "execution-profiles.local.yaml"
    explicit_registry = tmp_path / "operator-profiles.yaml"

    _write(
        repo_registry,
        """
profiles:
  base-claude:
    adapter: claude-code-cli
    status: tested
    capabilities:
      native_tools: [Read, Write]
      native_web_search: false
    resources:
      estimated_process_rss_mb: 1024
    config:
      output_format: stream-json
      model: sonnet
  experiment:
    extends: base-claude
    model: opus
    config:
      model: opus
""",
    )
    _write(
        local_registry,
        """
profiles:
  experiment:
    extends: base-claude
    status: experimental
    config:
      model: sonnet
      timeout_s: 300
""",
    )
    _write(
        explicit_registry,
        """
profiles:
  experiment:
    extends: base-claude
    status: tested
    resources:
      initial_memory_budget_fraction: 0.5
    config:
      model: opus
""",
    )

    registry = ExecutionProfileRegistry.load(
        repo_root=tmp_path,
        explicit_files=[explicit_registry],
        include_builtins=False,
    )

    resolved = registry.resolve("experiment")

    assert resolved.name == "experiment"
    assert resolved.source.kind == "explicit"
    assert resolved.source.path == str(explicit_registry)
    assert resolved.profile.adapter == "claude-code-cli"
    assert resolved.profile.status == "tested"
    assert resolved.profile.capabilities == {
        "native_tools": ["Read", "Write"],
        "native_web_search": False,
    }
    assert resolved.profile.resources.estimated_process_rss_mb == 1024
    assert resolved.profile.resources.initial_memory_budget_fraction == 0.5
    assert resolved.profile.config == {"output_format": "stream-json", "model": "opus"}


def test_registry_rejects_extends_cycles(tmp_path: Path) -> None:
    _write(
        tmp_path / ".metaproc" / "execution-profiles.yaml",
        """
profiles:
  a:
    extends: b
    adapter: claude-code-cli
  b:
    extends: a
    adapter: claude-code-cli
""",
    )

    registry = ExecutionProfileRegistry.load(repo_root=tmp_path, include_builtins=False)

    with pytest.raises(ValueError, match="cycle"):
        registry.resolve("a")


def test_registry_rejects_secret_shaped_keys_and_values(tmp_path: Path) -> None:
    _write(
        tmp_path / ".metaproc" / "execution-profiles.yaml",
        """
profiles:
  bad-key:
    adapter: claude-code-cli
    config:
      api_key: should-not-live-here
  bad-value:
    adapter: codex-cli
    config:
      model: sk-test-secret-token
""",
    )

    with pytest.raises(ValueError, match="secret-shaped key"):
        ExecutionProfileRegistry.load(repo_root=tmp_path, include_builtins=False)

    _write(
        tmp_path / ".metaproc" / "execution-profiles.yaml",
        """
profiles:
  bad-value:
    adapter: codex-cli
    config:
      model: sk-test-secret-token
""",
    )

    with pytest.raises(ValueError, match="secret-shaped value"):
        ExecutionProfileRegistry.load(repo_root=tmp_path, include_builtins=False)


def test_built_in_registry_contains_generic_profiles() -> None:
    registry = ExecutionProfileRegistry.load(include_builtins=True)

    codex = registry.resolve("codex-gpt55")
    claude = registry.resolve("claude-opus")
    pi = registry.resolve("pi-glm5")

    assert codex.profile.adapter == "codex-cli"
    assert claude.profile.adapter == "claude-code-cli"
    assert pi.profile.adapter == "pi-cli"


def test_built_in_registry_contains_gemini_profiles() -> None:
    """Gemini profiles must remain in the packaged catalog.

    ``gemini-flash-36`` is versioned so existing ``gemini-flash`` dispatch
    rosters continue to resolve to their reviewed 3.5 model.

    ``gemini-flash``, ``gemini-pro``, and ``pi-gemini-flash`` must remain in the
    packaged profile catalog so operators can `--execution-profiles <name>`
    from EIA dispatch without custom YAML.
    """
    registry = ExecutionProfileRegistry.load(include_builtins=True)

    flash_36 = registry.resolve("gemini-flash-36")
    flash = registry.resolve("gemini-flash")
    pro = registry.resolve("gemini-pro")
    pi_flash = registry.resolve("pi-gemini-flash")

    assert flash_36.profile.adapter == "gemini-cli"
    assert flash_36.profile.model == "gemini-3.6-flash"
    assert flash.profile.adapter == "gemini-cli"
    assert flash.profile.model == "gemini-3.5-flash"
    assert pro.profile.adapter == "gemini-cli"
    assert pro.profile.model == "gemini-3.1-pro-preview"
    assert pi_flash.profile.adapter == "pi-cli"
    assert pi_flash.profile.model == "gemini-3.5-flash"


def test_build_plan_resolves_profile_and_artifact_namespace(tmp_path: Path) -> None:
    profile_file = tmp_path / "profiles.yaml"
    _write(
        profile_file,
        """
profiles:
  fast-codex:
    adapter: codex-cli
    status: tested
    resources:
      estimated_process_rss_mb: 4096
      initial_memory_budget_fraction: 0.5
    config:
      model: gpt-5.5
      effort: high
""",
    )
    spec = ProcessSpec.model_validate(
        {
            "name": "profile-test",
            "steps": [
                {
                    "id": "write",
                    "mode": "agent",
                    "prompt_prefix": "write",
                    "outputs": {
                        "out": {
                            "path": "{{run.dir}}/{{run.artifact_namespace}}/out.md",
                            "kind": "file",
                        }
                    },
                }
            ],
        }
    )

    plan = build_plan(
        spec,
        {"RUNS_DIR": str(tmp_path), "RUN_ID": "run-1"},
        process_path=tmp_path / "profile-test.process.md",
        adapter_override="fast-codex",
        profile_files=[profile_file],
    )

    assert plan.execution_profile == "fast-codex"
    assert plan.artifact_namespace == "fast-codex"
    assert plan.params["EXECUTION_PROFILE"] == "fast-codex"
    assert plan.params["ARTIFACT_NAMESPACE"] == "fast-codex"
    assert plan.params["VARIANT"] == "fast-codex"
    assert plan.steps[0].adapter.type == "codex-cli"
    assert plan.steps[0].adapter.config["model"] == "gpt-5.5"
    assert "estimated_process_rss_mb" not in plan.steps[0].adapter.config
    assert plan.steps[0].resources == {
        "estimated_process_rss_mb": 4096,
        "initial_memory_budget_fraction": 0.5,
    }
    assert plan.steps[0].execution_profile == "fast-codex"
    assert plan.steps[0].artifact_namespace == "fast-codex"
    output_path = plan.steps[0].outputs["out"].path
    assert output_path is not None
    assert output_path.endswith("/run-1/fast-codex/out.md")


def test_step_profile_override_preserves_run_artifact_namespace(tmp_path: Path) -> None:
    profile_file = tmp_path / "profiles.yaml"
    _write(
        profile_file,
        """
profiles:
  run-profile:
    adapter: claude-code-cli
    status: tested
    config:
      model: opus
  summary-profile:
    adapter: codex-cli
    status: tested
    config:
      model: gpt-5.5
""",
    )
    spec = ProcessSpec.model_validate(
        {
            "name": "profile-test",
            "steps": [
                {
                    "id": "summarize",
                    "mode": "agent",
                    "execution_profile": "summary-profile",
                    "prompt_prefix": "summarize",
                    "outputs": {
                        "out": {
                            "path": "{{run.dir}}/{{run.artifact_namespace}}/summary.md",
                            "kind": "file",
                        }
                    },
                }
            ],
        }
    )

    plan = build_plan(
        spec,
        {"RUNS_DIR": str(tmp_path), "RUN_ID": "run-1"},
        process_path=tmp_path / "profile-test.process.md",
        adapter_override="run-profile",
        artifact_namespace="shared-artifacts",
        profile_files=[profile_file],
    )

    assert plan.execution_profile == "run-profile"
    assert plan.artifact_namespace == "shared-artifacts"
    assert plan.steps[0].execution_profile == "summary-profile"
    assert plan.steps[0].artifact_namespace == "shared-artifacts"
    assert plan.steps[0].adapter.type == "codex-cli"
    assert plan.steps[0].adapter.config["model"] == "gpt-5.5"
    output_path = plan.steps[0].outputs["out"].path
    assert output_path is not None
    assert output_path.endswith("/run-1/shared-artifacts/summary.md")


def test_artifact_namespace_param_overrides_execution_profile_default(
    tmp_path: Path,
) -> None:
    profile_file = tmp_path / "profiles.yaml"
    _write(
        profile_file,
        """
profiles:
  run-profile:
    adapter: codex-cli
    config:
      model: gpt-5.5
""",
    )
    spec = ProcessSpec.model_validate(
        {
            "name": "profile-test",
            "inputs": {
                "artifact_namespace": {
                    "param": "ARTIFACT_NAMESPACE",
                    "as": "string",
                    "required": False,
                    "default": "workflow-v1",
                }
            },
            "steps": [
                {
                    "id": "write",
                    "mode": "agent",
                    "artifact_namespace": "{{artifact_namespace}}",
                    "prompt_prefix": "write",
                    "outputs": {
                        "out": {
                            "path": "{{run.dir}}/{{run.artifact_namespace}}/out.md",
                            "kind": "file",
                        }
                    },
                }
            ],
        }
    )

    plan = build_plan(
        spec,
        {
            "RUNS_DIR": str(tmp_path),
            "RUN_ID": "run-1",
            "artifact_namespace": "workflow-v1",
            "ARTIFACT_NAMESPACE": "workflow-v1",
        },
        process_path=tmp_path / "profile-test.process.md",
        adapter_override="run-profile",
        profile_files=[profile_file],
    )

    assert plan.execution_profile == "run-profile"
    assert plan.artifact_namespace == "workflow-v1"
    assert plan.params["ARTIFACT_NAMESPACE"] == "workflow-v1"
    assert plan.params["VARIANT"] == "workflow-v1"
    assert plan.steps[0].artifact_namespace == "workflow-v1"
    output_path = plan.steps[0].outputs["out"].path
    assert output_path is not None
    assert output_path.endswith("/run-1/workflow-v1/out.md")


def test_build_plan_rejects_unknown_authored_execution_profiles(tmp_path: Path) -> None:
    base_step = {
        "id": "write",
        "mode": "agent",
        "prompt_prefix": "write",
        "outputs": {"out": {"path": "{{run.dir}}/out.md", "kind": "file"}},
    }
    vars_ = {"RUNS_DIR": str(tmp_path), "RUN_ID": "run-1"}

    default_spec = ProcessSpec.model_validate(
        {
            "name": "profile-test",
            "defaults": {"default_execution_profile": "missing-default"},
            "steps": [base_step],
        }
    )
    with pytest.raises(ValueError, match="defaults.default_execution_profile"):
        build_plan(default_spec, vars_, process_path=tmp_path / "profile-test.process.md")

    step_spec = ProcessSpec.model_validate(
        {
            "name": "profile-test",
            "steps": [{**base_step, "execution_profile": "missing-step"}],
        }
    )
    with pytest.raises(ValueError, match="step 'write' execution_profile"):
        build_plan(step_spec, vars_, process_path=tmp_path / "profile-test.process.md")

    override_spec = ProcessSpec.model_validate({"name": "profile-test", "steps": [base_step]})
    with pytest.raises(ValueError, match="--step-variant write"):
        build_plan(
            override_spec,
            vars_,
            process_path=tmp_path / "profile-test.process.md",
            step_profile_overrides={"write": "missing-override"},
        )


def test_variants_command_lists_profiles_as_json(tmp_path: Path) -> None:
    profile_file = tmp_path / "profiles.yaml"
    process_file = tmp_path / "test.process.md"
    _write(
        profile_file,
        """
profiles:
  local-codex:
    adapter: codex-cli
    status: tested
    config:
      model: gpt-5.5
""",
    )
    _write(
        process_file,
        """
---
process:
  name: test
  steps:
    - id: write
      mode: agent
      tools: [Read]
      optional_tools: [WebSearch]
      prompt_prefix: write
---
""",
    )

    result = runner.invoke(
        app,
        [
            "variants",
            str(process_file),
            "--profile-file",
            str(profile_file),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert '"profile": "local-codex"' in result.output
    assert '"adapter": "codex-cli"' in result.output


def test_auth_check_variant_resolution_uses_registry_without_prefix_fallback(
    tmp_path: Path,
) -> None:
    profile_file = tmp_path / "profiles.yaml"
    _write(
        profile_file,
        """
profiles:
  local-codex:
    adapter: codex-cli
    provider: openai
    model: gpt-5.5
    config:
      model: gpt-5.5
""",
    )

    assert _resolve_variant_target("local-codex", profile_files=[profile_file]) == (
        "codex-cli",
        "gpt-5.5",
        "openai",
    )

    with pytest.raises(Exception, match="unknown execution profile"):
        _resolve_variant_target("pi-cli-glm-5-maas", profile_files=[profile_file])
