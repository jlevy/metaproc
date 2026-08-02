"""Plugin protocol and extension types.

Defines the contract that domain plugins must implement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel
from softschema import Contract

from metaproc.models.viz import NodeDecoration, VizNode

if TYPE_CHECKING:
    from metaproc.logutil.parsing import LogEvent, LogFile
    from metaproc.models.authored import ProcessStep
    from metaproc.models.resources import (
        HierarchyRef,
        ProviderMeterObservation,
        ResourceEvent,
    )
    from metaproc.models.usage import ToolRunProfile


AdapterConfigTransform = Callable[
    ["ProcessStep", dict[str, object], Mapping[str, str]], dict[str, object]
]
ExecutionEnvTransform = Callable[[dict[str, str], Mapping[str, str]], dict[str, str]]
ShellCommandClassifier = Callable[[str], Mapping[str, str] | None]


class ResourceEventSource(Protocol):
    """Consumer-owned source of typed resource events."""

    name: str
    source_kind: str
    adapter: str

    def discover(self, run_dir: Path) -> Sequence[Path]: ...

    def extract(
        self,
        *,
        log_path: Path,
        hierarchy: HierarchyRef,
        source_path: str,
        source_size_bytes: int | None = None,
        source_mtime_ns: int | None = None,
    ) -> Sequence[ResourceEvent]: ...


class ProviderMeterSource(Protocol):
    """Consumer-owned extractor for sanitized nested provider observations."""

    name: str

    def extract(
        self,
        *,
        log_path: Path,
        log_file: LogFile,
        log_events: Sequence[LogEvent],
        hierarchy: HierarchyRef,
        source_path: str,
    ) -> Sequence[ProviderMeterObservation]: ...


class ToolProfileSource(Protocol):
    """Consumer-owned parser for external tool-event profiles."""

    name: str

    def matches(self, path: Path) -> bool: ...

    def aggregate(
        self,
        paths: Sequence[Path],
        *,
        variant_fn: Callable[[Path], str],
    ) -> dict[str, ToolRunProfile]: ...


class RuleViolation(BaseModel):
    rule_id: str
    message: str
    path: str | None = None


class ProcessRule(Protocol):
    rule_id: str

    def check(self, process_path: Path, raw_data: dict[str, object]) -> Sequence[RuleViolation]: ...


VizDecoratorPredicate = Callable[[VizNode], bool]
VizDecoratorCallback = Callable[[VizNode], NodeDecoration]


class VizDecorator(BaseModel):
    """A plugin-contributed node decorator — predicate + callback pair."""

    model_config = {"arbitrary_types_allowed": True}

    predicate: VizDecoratorPredicate
    decorate: VizDecoratorCallback


class PluginRegistry(Protocol):
    """Registry interface exposed to plugins during registration."""

    def register_softschema(self, binding: Contract) -> None: ...
    def register_envelope(self, doc_type: str, model: type[BaseModel]) -> None: ...
    def register_terminal_statuses(self, statuses: frozenset[str]) -> None: ...
    def register_process_rule(self, rule: ProcessRule) -> None: ...
    def register_compare_defaults(self, fields: list[str], envelope_key: str) -> None: ...
    def register_viz_decorator(
        self,
        predicate: VizDecoratorPredicate,
        decorate: VizDecoratorCallback,
    ) -> None: ...
    def register_adapter_config_transform(self, transform: AdapterConfigTransform) -> None: ...
    def register_execution_env_transform(self, transform: ExecutionEnvTransform) -> None: ...
    def register_resource_event_source(self, source: ResourceEventSource) -> None: ...
    def register_provider_meter_source(self, source: ProviderMeterSource) -> None: ...
    def register_tool_profile_source(self, source: ToolProfileSource) -> None: ...
    def register_bootstrap_env(self, defaults: Mapping[str, str]) -> None: ...
    def register_shell_command_classifier(self, classifier: ShellCommandClassifier) -> None: ...
    def register_quality_directives(self, directives: Mapping[str, Sequence[str]]) -> None: ...


class MetaprocPlugin(Protocol):
    name: str

    def register(self, registry: PluginRegistry) -> None: ...
