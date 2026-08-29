"""Tests for metaproc.plugins — registry and discovery."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel
from softschema import Contract, SchemaProfile, SchemaStatus, validate_artifact

from metaproc.io import to_yaml_string
from metaproc.io.frontmatter import ENVELOPE_MAP
from metaproc.models.authored import ProcessStep
from metaproc.models.plan import RUN_PLAN_SNAPSHOT_CONTRACT, RunPlanSnapshot
from metaproc.models.resources import HierarchyRef, ResourceEvent
from metaproc.models.runtime import get_terminal_statuses
from metaproc.models.usage import ToolRunProfile
from metaproc.models.viz import NodeDecoration, StepDetails, VizNode
from metaproc.plugins.discovery import (
    discover_and_load_plugins,
    ensure_plugins_loaded,
    get_plugin_registry,
    reset_plugin_discovery,
)
from metaproc.plugins.registry import PluginRegistryImpl


class TestPluginRegistry:
    def test_builtin_run_plan_snapshot_contract(self):
        binding = PluginRegistryImpl().softschemas.resolve(RUN_PLAN_SNAPSHOT_CONTRACT)

        assert binding is not None
        assert binding.model is RunPlanSnapshot
        assert binding.envelope_key == "run_plan"
        assert binding.profile is SchemaProfile.pure_yaml
        assert binding.status == SchemaStatus.enforced

    def test_builtin_run_plan_snapshot_validates_real_yaml_artifact(self, tmp_path: Path):
        path = tmp_path / "run-plan.yaml"
        path.write_text(
            to_yaml_string(
                {
                    "run_plan": RunPlanSnapshot(
                        run_id="example/run-1",
                    ).model_dump(mode="json", by_alias=True)
                }
            ),
            encoding="utf-8",
        )
        registry = PluginRegistryImpl().softschemas

        validation = validate_artifact(
            path,
            contract_id=RUN_PLAN_SNAPSHOT_CONTRACT,
            registry=registry,
        )

        assert validation.ok

    def test_register_envelope(self):
        reg = PluginRegistryImpl()

        class Inner(BaseModel):
            val: str = "x"

        class MyEnvelope(BaseModel):
            test_doc: Inner

        reg.register_envelope("test_doc", MyEnvelope)
        assert "test_doc" in reg.envelopes
        assert "test_doc" not in ENVELOPE_MAP

    def test_register_softschema_registers_binding(self):
        reg = PluginRegistryImpl()

        class MyModel(BaseModel):
            x: int = 1

        reg.register_softschema(
            Contract(
                id="test:Test/v1",
                model=MyModel,
                envelope_key="test",
                status=SchemaStatus.permissive,
            )
        )

        binding = reg.softschemas.resolve("test:Test/v1")
        assert binding is not None
        assert binding.model is MyModel
        assert binding.envelope_key == "test"
        assert binding.status == SchemaStatus.permissive

    def test_register_terminal_statuses(self):
        reg = PluginRegistryImpl()
        reg.register_terminal_statuses(frozenset({"reviewed", "published"}))
        assert "reviewed" in reg.terminal_statuses
        assert "published" in reg.terminal_statuses
        # Local registration should not mutate global status lookup
        all_statuses = get_terminal_statuses()
        assert "reviewed" not in all_statuses

    def test_register_compare_defaults(self):
        reg = PluginRegistryImpl()
        reg.register_compare_defaults(["direction", "move_pct"], "prediction")
        assert reg.compare_defaults["prediction"] == ["direction", "move_pct"]

    def test_register_process_rule(self):
        reg = PluginRegistryImpl()

        class FakeRule:
            rule_id = "test-rule"

            def check(self, process_path, raw_data):
                return []

        reg.register_process_rule(FakeRule())
        assert len(reg.process_rules) == 1
        assert reg.process_rules[0].rule_id == "test-rule"

    def test_register_viz_decorator(self):

        reg = PluginRegistryImpl()

        def _is_predict_step(node: VizNode) -> bool:
            return node.kind == "step" and node.id.startswith("predict-")

        def _decorate(node: VizNode) -> NodeDecoration:
            return NodeDecoration(badge="form=v3", accent_token="--accent-earnings")

        reg.register_viz_decorator(_is_predict_step, _decorate)
        assert len(reg.viz_decorators) == 1
        target = VizNode(
            id="predict-ticker",
            kind="step",
            label="predict",
            step=StepDetails(
                step_id="predict-ticker",
                mode="agent",
                process_schema_token="metaproc:ProcessSpec/0.1",
                source_path="x/process.md",
            ),
        )
        dec = reg.viz_decorators[0]
        assert dec.predicate(target)
        assert dec.decorate(target).badge == "form=v3"

    def test_register_runtime_extension_hooks(self):
        reg = PluginRegistryImpl()

        def adapter_transform(
            _step: ProcessStep,
            config: dict[str, object],
            _params: Mapping[str, str],
        ) -> dict[str, object]:
            return {**config, "consumer": True}

        def env_transform(env: dict[str, str], _item_vars: Mapping[str, str]) -> dict[str, str]:
            return {**env, "CONSUMER": "1"}

        class FakeResourceSource:
            name = "fake"
            source_kind = "fake_events"
            adapter = "fake"

            def discover(self, run_dir: Path) -> Sequence[Path]:
                _ = run_dir
                return []

            def extract(
                self,
                *,
                log_path: Path,
                hierarchy: HierarchyRef,
                source_path: str,
                source_size_bytes: int | None = None,
                source_mtime_ns: int | None = None,
            ) -> Sequence[ResourceEvent]:
                _ = (
                    log_path,
                    hierarchy,
                    source_path,
                    source_size_bytes,
                    source_mtime_ns,
                )
                return []

        class FakeToolProfileSource:
            name = "fake"

            def matches(self, path: Path) -> bool:
                _ = path
                return False

            def aggregate(
                self,
                paths: Sequence[Path],
                *,
                variant_fn: Callable[[Path], str],
            ) -> dict[str, ToolRunProfile]:
                _ = (paths, variant_fn)
                return {}

        reg.register_adapter_config_transform(adapter_transform)
        reg.register_execution_env_transform(env_transform)
        reg.register_resource_event_source(FakeResourceSource())
        reg.register_tool_profile_source(FakeToolProfileSource())
        reg.register_resource_event_source(FakeResourceSource())
        reg.register_tool_profile_source(FakeToolProfileSource())
        reg.register_quality_directives({"research": ("setup.md", "sources.json")})

        assert reg.adapter_config_transforms == [adapter_transform]
        assert reg.execution_env_transforms == [env_transform]
        assert reg.resource_event_sources[0].name == "fake"
        assert reg.tool_profile_sources[0].name == "fake"
        assert reg.quality_directives["research"] == ("setup.md", "sources.json")


class TestPluginDiscovery:
    def test_discover_returns_registry(self):
        reg = discover_and_load_plugins()
        assert isinstance(reg, PluginRegistryImpl)

    def test_discover_with_existing_registry(self):
        reg = PluginRegistryImpl()
        result = discover_and_load_plugins(reg)
        assert result is reg

    def test_ensure_scans_once_then_reuses_the_registry(self, monkeypatch):
        """Repeat calls must not rescan: the viz-model route is polled per render."""
        scans = 0
        real_entry_points = importlib.metadata.entry_points

        def _counting_entry_points(**kwargs):
            nonlocal scans
            scans += 1
            return real_entry_points(**kwargs)

        monkeypatch.setattr(importlib.metadata, "entry_points", _counting_entry_points)
        reset_plugin_discovery()

        first = ensure_plugins_loaded()
        second = ensure_plugins_loaded()

        assert scans == 1
        assert second is first
        assert first is get_plugin_registry()

    def test_ensure_rescans_after_reset(self, monkeypatch):
        """`reset_plugin_discovery` is the seam tests use to install entry points."""
        reset_plugin_discovery()
        ensure_plugins_loaded()
        reset_plugin_discovery()

        scans = 0
        real_entry_points = importlib.metadata.entry_points

        def _counting_entry_points(**kwargs):
            nonlocal scans
            scans += 1
            return real_entry_points(**kwargs)

        monkeypatch.setattr(importlib.metadata, "entry_points", _counting_entry_points)
        ensure_plugins_loaded()

        assert scans == 1


class TestFakePlugin:
    def test_plugin_registration_flow(self):
        """End-to-end: a fake plugin registers schemas and conventions."""
        reg = PluginRegistryImpl()

        class FakePlugin:
            name = "test-plugin"

            def register(self, registry):
                class MyModel(BaseModel):
                    ticker: str

                registry.register_softschema(
                    Contract(
                        id="test:Ticker/v1",
                        model=MyModel,
                        envelope_key="ticker",
                        status=SchemaStatus.permissive,
                    )
                )
                registry.register_terminal_statuses(frozenset({"test_done"}))
                registry.register_compare_defaults(["ticker"], "tickers")

        plugin = FakePlugin()
        plugin.register(reg)

        assert reg.softschemas.resolve("test:Ticker/v1") is not None
        assert "test_done" in reg.terminal_statuses
        assert reg.compare_defaults["tickers"] == ["ticker"]
