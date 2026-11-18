"""A resume continues a run across a catalog change of the release it runs on.

A release can move an adapter's default model, drop a built-in execution profile, or
drop a model ID. A rerun against the same ``RUN_ID`` keeps the run as it was created
where the record allows: its derived artifact namespace, and a removed profile rebuilt
from its former definition. It records and warns about what moves, a step's default
model among them, and refuses only a removed profile whose model would not be served,
naming the retirement and the override that continues the run.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
from strif import atomic_write_text
from typer.testing import CliRunner, Result

from metaproc.adapters.registry import (
    ADAPTER_REGISTRY,
    UNRECORDED_NAMESPACE_DEFAULT_MODELS,
    current_default_models,
    derive_variant,
    recorded_namespace_default_models,
)
from metaproc.cli import app
from metaproc.commands.helpers import load_process_spec
from metaproc.commands.run_process import (
    _default_model_change_warnings,
    _LaunchConfig,
    _plan_default_models,
    _resume_plan_record,
    _validate_run_config,
)
from metaproc.config.model_catalog import (
    MODEL_CATALOG,
    REMOVED_MODELS,
    known_model_ids,
    removed_model_note,
    resolve_model,
)
from metaproc.engine.build_plan import build_plan
from metaproc.io import iter_jsonl_objects, read_yaml_file, to_yaml_string
from metaproc.models.execution_profile import (
    REMOVED_EXECUTION_PROFILES,
    ExecutionProfileRegistry,
    recorded_execution_profiles,
    removed_profile_note,
)
from metaproc.paths import RUN_CONFIG_FILE, STATE_DIR, dispatch_config_changes_log

_HANDLERS = '''\
"""Code handlers that log each invocation."""

from __future__ import annotations

from pathlib import Path


def _run_dir(variables: dict[str, str]) -> Path:
    run_dir = Path(variables["RUNS_DIR"]) / variables["RUN_ID"]
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def first(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    run_dir = _run_dir(variables)
    with (run_dir / "invocations.log").open("a", encoding="utf-8") as fh:
        fh.write("first\\n")
    (run_dir / "first.txt").write_text(variables["VARIANT"] + "\\n", encoding="utf-8")


def second(variables: dict[str, str], step: object) -> None:  # noqa: ARG001
    run_dir = _run_dir(variables)
    with (run_dir / "invocations.log").open("a", encoding="utf-8") as fh:
        fh.write("second\\n")
    (run_dir / "second.txt").write_text(variables["VARIANT"] + "\\n", encoding="utf-8")
'''


def _write_process(process_dir: Path) -> Path:
    """Write a two-step code process; each step logs its run and writes one output."""
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "handlers.py").write_text(_HANDLERS, encoding="utf-8")
    path = process_dir / "catalog-moves.process.md"
    path.write_text(
        textwrap.dedent(
            """\
            ---
            process:
              name: catalog-moves
              steps:
                - id: first
                  mode: code
                  handler: "handlers.py:first"
                  outputs:
                    out:
                      path: "{{run.dir}}/first.txt"
                      kind: file
                - id: second
                  mode: code
                  handler: "handlers.py:second"
                  needs: [first]
                  outputs:
                    out:
                      path: "{{run.dir}}/second.txt"
                      kind: file
            ---
            Catalog moves.
            """
        ),
        encoding="utf-8",
    )
    return path


def _run(process_path: Path, runs_dir: Path, run_id: str, *options: str) -> Result:
    args = [
        "run-process",
        str(process_path),
        "--var",
        f"RUNS_DIR={runs_dir}",
        "--var",
        f"RUN_ID={run_id}",
        "--backend",
        "local",
        *options,
    ]
    return CliRunner().invoke(app, args)


def _message(result: Result) -> str:
    return f"{result.output}\n{result.exception}"


def _invocations(run_dir: Path) -> list[str]:
    return (run_dir / "invocations.log").read_text(encoding="utf-8").splitlines()


def _config_path(run_dir: Path) -> Path:
    return run_dir / STATE_DIR / RUN_CONFIG_FILE


def _config(run_dir: Path) -> dict[str, Any]:
    return read_yaml_file(_config_path(run_dir))


def _forget(run_dir: Path, *fields: str) -> None:
    """Drop *fields* from run-config.yaml, as a release that did not write them left it."""
    config = _config(run_dir)
    for field in fields:
        config.pop(field, None)
    atomic_write_text(_config_path(run_dir), to_yaml_string(config))


def _events(run_dir: Path) -> list[dict[str, Any]]:
    path = dispatch_config_changes_log(run_dir)
    return list(iter_jsonl_objects(path)) if path.is_file() else []


def _step_states(run_dir: Path) -> dict[str, str]:
    status = CliRunner().invoke(app, ["status", str(run_dir), "--steps", "--format", "json"])
    assert status.exit_code == 0, _message(status)
    return {step["step_id"]: step["state"] for step in json.loads(status.output)["steps"]}


# ── H1: a moved adapter default ───────────────────────────────────


_FORMER_CODEX_DEFAULT = UNRECORDED_NAMESPACE_DEFAULT_MODELS["codex-cli"]
_CURRENT_CODEX_DEFAULT = MODEL_CATALOG.defaults["codex-cli"]


def test_the_former_codex_default_is_not_the_current_one() -> None:
    """The fixtures below move the codex default; they need it to have moved."""
    assert _FORMER_CODEX_DEFAULT != _CURRENT_CODEX_DEFAULT
    assert ADAPTER_REGISTRY["codex-cli"].default_model == _CURRENT_CODEX_DEFAULT


def _launch_under_former_codex_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *options: str
) -> tuple[Path, Path, str]:
    process_path = _write_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = "moved-default"
    with monkeypatch.context() as patch:
        patch.setattr(ADAPTER_REGISTRY["codex-cli"], "default_model", _FORMER_CODEX_DEFAULT)
        launched = _run(process_path, runs_dir, run_id, "--variant", "codex-cli", *options)
    assert launched.exit_code == 0, _message(launched)
    assert _config(runs_dir / run_id)["artifact_namespace"] == "codex-cli"
    assert sorted(_invocations(runs_dir / run_id)) == ["first", "second"]
    return process_path, runs_dir, run_id


def test_a_run_from_before_the_default_move_keeps_its_namespace_and_reuses_its_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review repro: an explicit former-default model derived the bare adapter name."""
    model = ("--adapter-config", f"model={_FORMER_CODEX_DEFAULT}")
    process_path, runs_dir, run_id = _launch_under_former_codex_default(
        tmp_path, monkeypatch, *model
    )
    run_dir = runs_dir / run_id
    # A run-config.yaml written before the release recorded the namespace defaults.
    _forget(run_dir, "namespace_default_models", "default_models")

    resumed = _run(process_path, runs_dir, run_id, "--variant", "codex-cli", *model)

    assert resumed.exit_code == 0, _message(resumed)
    config = _config(run_dir)
    assert (config["artifact_namespace"], config["variant"]) == ("codex-cli", "codex-cli")
    assert sorted(_invocations(run_dir)) == ["first", "second"]
    assert (run_dir / "first.txt").read_text(encoding="utf-8") == "codex-cli\n"
    assert "Artifact namespace stays 'codex-cli'" in resumed.output
    assert f"codex-cli default model was {_FORMER_CODEX_DEFAULT}" in resumed.output
    assert f"would use 'codex-cli-{_FORMER_CODEX_DEFAULT}'" in resumed.output
    assert "Resume changes artifact_namespace" not in resumed.output


def test_a_run_records_the_defaults_its_namespace_derives_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run created now pins its own defaults, so a later default move keeps it too."""
    model = ("--adapter-config", f"model={_FORMER_CODEX_DEFAULT}")
    process_path, runs_dir, run_id = _launch_under_former_codex_default(
        tmp_path, monkeypatch, *model
    )
    run_dir = runs_dir / run_id
    recorded = _config(run_dir)["namespace_default_models"]
    assert recorded["codex-cli"] == _FORMER_CODEX_DEFAULT
    assert set(recorded) == set(ADAPTER_REGISTRY)

    resumed = _run(process_path, runs_dir, run_id, "--variant", "codex-cli", *model)

    assert resumed.exit_code == 0, _message(resumed)
    assert _config(run_dir)["artifact_namespace"] == "codex-cli"
    assert _config(run_dir)["namespace_default_models"] == recorded
    assert sorted(_invocations(run_dir)) == ["first", "second"]
    assert _events(run_dir) == [] or all(
        not change["field"].startswith(("artifact_namespace", "variant"))
        for event in _events(run_dir)
        for change in event["changes"]
    )


def test_a_deliberate_model_change_on_resume_still_moves_the_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinning the derivation follows the model; it does not freeze the namespace."""
    process_path, runs_dir, run_id = _launch_under_former_codex_default(tmp_path, monkeypatch)
    run_dir = runs_dir / run_id
    _forget(run_dir, "namespace_default_models", "default_models")

    resumed = _run(
        process_path,
        runs_dir,
        run_id,
        "--variant",
        "codex-cli",
        "--adapter-config",
        f"model={_CURRENT_CODEX_DEFAULT}",
    )

    assert resumed.exit_code == 0, _message(resumed)
    expected = derive_variant("codex-cli", {"model": _CURRENT_CODEX_DEFAULT}, default_models={})
    assert expected == "codex-cli"  # the current default derives the bare name for a new run
    # Against the run's former default, the new model is a non-default selection.
    assert _config(run_dir)["artifact_namespace"] == f"codex-cli-{_CURRENT_CODEX_DEFAULT}"
    assert (
        f"Warning: Resume changes artifact_namespace: 'codex-cli' -> "
        f"'codex-cli-{_CURRENT_CODEX_DEFAULT}'" in resumed.output
    )
    # The namespace is in both fingerprints, so content decides: both steps re-run.
    assert sorted(_invocations(run_dir)) == ["first", "first", "second", "second"]


def test_derive_variant_compares_against_the_given_defaults() -> None:
    former = {"codex-cli": _FORMER_CODEX_DEFAULT}
    assert derive_variant("codex-cli", {"model": _FORMER_CODEX_DEFAULT}) == (
        f"codex-cli-{_FORMER_CODEX_DEFAULT}"
    )
    assert derive_variant("codex-cli", {"model": _FORMER_CODEX_DEFAULT}, default_models=former) == (
        "codex-cli"
    )
    assert derive_variant("codex-cli", {}, default_models=former) == "codex-cli"
    assert derive_variant("codex-cli", {"model": "gpt-6.2-sol"}, default_models=former) == (
        "codex-cli-gpt-6.2-sol"
    )


def test_recorded_namespace_defaults_fill_from_the_current_and_former_defaults() -> None:
    current = current_default_models()
    assert recorded_namespace_default_models(None) == {
        **current,
        **UNRECORDED_NAMESPACE_DEFAULT_MODELS,
    }
    assert recorded_namespace_default_models({"codex-cli": "gpt-x"}) == {
        **current,
        "codex-cli": "gpt-x",
    }
    assert recorded_namespace_default_models(current) == current


_AGENT_PROCESS = """\
---
process:
  name: default-models
  defaults:
    adapter:
      type: codex-cli
  steps:
    - id: draft
      mode: agent
      prompt_prefix: draft
    - id: judge
      mode: agent
      prompt_prefix: judge
      adapter:
        type: codex-cli
        config:
          model: gpt-6.2-sol
    - id: stamp
      mode: code
      command: /bin/true
---
Default models.
"""


def test_a_plan_records_the_default_model_of_agent_steps_that_set_none(tmp_path: Path) -> None:
    process_path = tmp_path / "default-models.process.md"
    process_path.write_text(_AGENT_PROCESS, encoding="utf-8")
    spec = load_process_spec(process_path)
    plan = build_plan(spec, {}, process_path=process_path, validate_required_inputs=False)

    # `judge` names its model and `stamp` runs no model, so only `draft` is recorded.
    assert _plan_default_models(plan) == {"codex-cli": _CURRENT_CODEX_DEFAULT}


def _launch(default_models: dict[str, str]) -> _LaunchConfig:
    return _LaunchConfig(
        variables={"RUN_ID": "r"},
        step_variants={},
        variant=None,
        execution_profile=None,
        artifact_namespace=None,
        resolved_profiles=[],
        backend="local",
        git_sha="abc",
        default_models=default_models,
    )


def _write_config(run_dir: Path, **fields: object) -> Path:
    config = {
        "process": "default-models",
        "run_id": "r",
        "run_dir": str(run_dir),
        "variables": {"RUN_ID": "r"},
        "backend": "local",
        "git_sha": "abc",
        **fields,
    }
    path = _config_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, to_yaml_string(config))
    return path


def test_a_moved_default_model_is_recorded_as_a_launch_config_change(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    path = _write_config(run_dir, default_models={"codex-cli": _FORMER_CODEX_DEFAULT})
    launch = _launch({"codex-cli": _CURRENT_CODEX_DEFAULT})

    changes = _validate_run_config(
        path, process_name="default-models", run_dir=run_dir, launch=launch
    )

    assert changes == {"default_models.codex-cli": (_FORMER_CODEX_DEFAULT, _CURRENT_CODEX_DEFAULT)}
    record = _resume_plan_record(run_dir)
    (warning,) = _default_model_change_warnings(changes, record)
    assert f"ran {_FORMER_CODEX_DEFAULT} and are reused" in warning
    assert f"steps this resume runs use {_CURRENT_CODEX_DEFAULT}" in warning


def test_an_unrecorded_default_model_names_the_former_default(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    path = _write_config(run_dir)
    launch = _launch({"codex-cli": _CURRENT_CODEX_DEFAULT})

    changes = _validate_run_config(
        path, process_name="default-models", run_dir=run_dir, launch=launch
    )

    assert changes == {"default_models.codex-cli": (None, _CURRENT_CODEX_DEFAULT)}
    record = _resume_plan_record(run_dir)
    assert record.default_models is None
    assert record.namespace_default_models["codex-cli"] == _FORMER_CODEX_DEFAULT
    (warning,) = _default_model_change_warnings(changes, record)
    assert f"most likely ran {_FORMER_CODEX_DEFAULT}" in warning


def test_an_unchanged_default_model_records_nothing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    path = _write_config(run_dir, default_models={"codex-cli": _CURRENT_CODEX_DEFAULT})

    changes = _validate_run_config(
        path,
        process_name="default-models",
        run_dir=run_dir,
        launch=_launch({"codex-cli": _CURRENT_CODEX_DEFAULT}),
    )

    assert changes == {}


# ── H2: a removed built-in execution profile ──────────────────────


def _former_profile_file(tmp_path: Path, name: str) -> Path:
    """Write *name*'s former built-in definition as an operator profile file."""
    definition = REMOVED_EXECUTION_PROFILES[name].definition
    path = tmp_path / "former-profiles.yaml"
    path.write_text(
        json.dumps(
            {
                "profiles": {
                    name: definition.model_dump(mode="json", by_alias=True, exclude_none=True)
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _launch_on_former_profile(tmp_path: Path, name: str) -> tuple[Path, Path, str]:
    """Launch on a removed profile, defined as the release that shipped it defined it."""
    process_path = _write_process(tmp_path / "proc")
    runs_dir = tmp_path / "runs"
    run_id = f"former-{name}"
    profile_file = _former_profile_file(tmp_path, name)
    launched = _run(
        process_path, runs_dir, run_id, "--profile-file", str(profile_file), "--variant", name
    )
    assert launched.exit_code == 0, _message(launched)
    assert sorted(_invocations(runs_dir / run_id)) == ["first", "second"]
    return process_path, runs_dir, run_id


def test_status_rebuilds_the_plan_of_a_run_on_a_removed_profile(tmp_path: Path) -> None:
    _process_path, runs_dir, run_id = _launch_on_former_profile(tmp_path, "gemini-flash-36")

    assert _step_states(runs_dir / run_id) == {"first": "current", "second": "current"}


def test_a_run_on_a_removed_profile_resumes_with_a_warning_and_reuses_its_steps(
    tmp_path: Path,
) -> None:
    process_path, runs_dir, run_id = _launch_on_former_profile(tmp_path, "gemini-flash-36")
    run_dir = runs_dir / run_id

    resumed = _run(process_path, runs_dir, run_id, "--variant", "gemini-flash-36")

    assert resumed.exit_code == 0, _message(resumed)
    assert "unknown adapter override" not in resumed.output
    assert "execution profile 'gemini-flash-36' was removed" in resumed.output
    assert "its successor is 'gemini-hydrogen'" in resumed.output
    assert sorted(_invocations(run_dir)) == ["first", "second"]
    assert _config(run_dir)["artifact_namespace"] == "gemini-flash-36"


@pytest.mark.parametrize(
    ("name", "model", "successor", "successor_model"),
    [
        ("gemini-flash", "gemini-3.5-flash", "gemini-hydrogen", "gemini-4-hydrogen"),
        ("pi-gemini-flash", "gemini-3.5-flash", "pi-gemini-hydrogen", "gemini-4-hydrogen"),
        ("pi-glm5", "glm-5-maas", "pi-glm52", "glm-5.2-maas"),
    ],
)
def test_a_removed_profile_whose_model_is_retired_names_the_override_that_continues(
    tmp_path: Path, name: str, model: str, successor: str, successor_model: str
) -> None:
    process_path, runs_dir, run_id = _launch_on_former_profile(tmp_path, name)
    run_dir = runs_dir / run_id
    # The plan still reads for status, as the run recorded it.
    assert set(_step_states(run_dir)) == {"first", "second"}

    refused = _run(process_path, runs_dir, run_id, "--variant", name)

    assert refused.exit_code != 0
    message = _message(refused)
    assert "unknown adapter override" not in message
    assert f"this run records execution profile {name!r}" in message
    assert f"no longer accepts its model {model!r}" in message
    assert f"Its successor is {successor!r} (model {successor_model})" in message
    assert f"--variant {successor}" in message
    assert f"--variant {name} --adapter-config model={successor_model}" in message

    kept = _run(
        process_path,
        runs_dir,
        run_id,
        "--variant",
        name,
        "--adapter-config",
        f"model={successor_model}",
    )
    assert kept.exit_code == 0, _message(kept)
    assert _config(run_dir)["artifact_namespace"] == name


def test_a_fresh_launch_on_a_removed_profile_names_its_successor(tmp_path: Path) -> None:
    process_path = _write_process(tmp_path / "proc")

    refused = _run(process_path, tmp_path / "runs", "fresh", "--variant", "gemini-flash")

    assert refused.exit_code != 0
    message = _message(refused)
    assert "unknown adapter override: 'gemini-flash'" in message
    assert "was removed from Metaproc's built-in profiles on 2026-11-18" in message
    assert "its successor is 'gemini-hydrogen'" in message


def test_the_registry_names_a_removed_profiles_successor() -> None:
    registry = ExecutionProfileRegistry.load()
    with pytest.raises(ValueError, match="unknown execution profile: 'pi-glm5'") as excinfo:
        registry.resolve("pi-glm5")
    assert "its successor is 'pi-glm52'" in str(excinfo.value)
    assert removed_profile_note("claude-opus") is None


def test_removed_profiles_are_gone_and_their_successors_ship() -> None:
    builtins = ExecutionProfileRegistry.load()
    for name, removed in REMOVED_EXECUTION_PROFILES.items():
        assert name not in builtins.entries
        assert removed.successor in builtins.entries
        definition = removed.definition
        assert definition.adapter in ADAPTER_REGISTRY
        model = str(definition.config["model"])
        # Either still served, or removed with a recorded reason.
        assert model in known_model_ids(definition.adapter) or removed_model_note(
            definition.adapter, model
        )


def test_only_recorded_removed_profiles_are_rebuilt() -> None:
    config: dict[object, object] = {
        "execution_profile": "gemini-flash-36",
        "step_variants": {"judge": "pi-glm5"},
        "resolved_profiles": [{"name": "claude-opus", "adapter": "claude-code-cli"}],
    }
    recorded = recorded_execution_profiles(config)
    assert sorted(recorded) == ["gemini-flash-36", "pi-glm5"]
    registry = ExecutionProfileRegistry.load(recorded_profiles=recorded)
    assert registry.entries["gemini-flash-36"].source.kind == "recorded"
    # A name a source still defines resolves from that source.
    shadowed = ExecutionProfileRegistry.load(recorded_profiles={"claude-opus": recorded["pi-glm5"]})
    assert shadowed.entries["claude-opus"].source.kind == "built-in"


# ── M4: a removed model ID says why ───────────────────────────────


@pytest.mark.parametrize(
    ("adapter", "model", "replacement"),
    [
        ("gemini-cli", "gemini-3.5-flash", "gemini-4-hydrogen"),
        ("gemini-cli", "gemini-3.7-flash", "gemini-3.8-flash or gemini-4-hydrogen"),
        ("gemini-cli", "auto-gemini-3", "auto"),
        ("codex-cli", "gpt-5.4", "gpt-6.1-sol"),
        ("codex-cli", "gpt-5.4-mini", "gpt-6-luna"),
        ("pi-cli", "deepseek-v4-flash", "deepseek-flash"),
        ("pi-cli", "zai-org/glm-5-maas", "zai-org/glm-5.2-maas"),
        ("pi-cli", "glm-5-maas", "glm-5.2-maas"),
        ("pi-cli", "deepseek-v3.2-maas", "deepseek-v4.1-flash-maas"),
    ],
)
def test_a_removed_model_is_refused_with_its_retirement_and_replacement(
    adapter: str, model: str, replacement: str
) -> None:
    with pytest.raises(ValueError, match=f"^unknown {adapter} model '{model}'; ") as excinfo:
        resolve_model(adapter, model)
    message = str(excinfo.value)
    assert "removed from Metaproc's model catalog on 2026-11-18" in message
    assert f"use {replacement}." in message
    assert "An explicit model is never replaced by the default." in message


def test_an_unknown_model_keeps_the_generic_refusal() -> None:
    with pytest.raises(ValueError, match="^unknown codex-cli model 'gpt-bogus'; choose a known"):
        resolve_model("codex-cli", "gpt-bogus")


def test_a_model_still_served_by_an_adapter_is_not_called_removed() -> None:
    assert resolve_model("gemini-cli", "gemini-3.6-flash") == "gemini-3.6-flash"
    assert removed_model_note("gemini-cli", "gemini-3.6-flash") is None
    assert removed_model_note("pi-cli", "gemini-3.6-flash") is not None


def test_every_removed_model_is_unknown_and_its_replacement_known() -> None:
    for model, removed in REMOVED_MODELS.items():
        for adapter in removed.adapters:
            assert model not in known_model_ids(adapter), (adapter, model)
            for replacement in removed.replacement.split(" or "):
                assert replacement in known_model_ids(adapter), (adapter, model, replacement)
