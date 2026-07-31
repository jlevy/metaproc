"""In-memory plugin registry implementation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel
from softschema import Contract, Contracts, SchemaStatus

from metaproc.io.frontmatter import ProgressSpec
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import Plan
from metaproc.models.qa import QaReport, QaSummary
from metaproc.models.usage import UsageReport
from metaproc.plugins.protocol import (
    AdapterConfigTransform,
    ExecutionEnvTransform,
    ProcessRule,
    ResourceEventSource,
    ShellCommandClassifier,
    ToolProfileSource,
    VizDecorator,
    VizDecoratorCallback,
    VizDecoratorPredicate,
)
from metaproc.structure_report import STRUCTURE_REPORT_CONTRACT_ID, StructureReport


class PluginRegistryImpl:
    """Concrete registry that plugins register extensions into."""

    def __init__(self) -> None:
        self.softschemas = Contracts()
        self.envelopes: dict[str, type[BaseModel]] = {}
        self.terminal_statuses: set[str] = set()
        self.process_rules: list[ProcessRule] = []
        self.compare_defaults: dict[str, list[str]] = {}  # envelope_key -> fields
        self.viz_decorators: list[VizDecorator] = []
        self.adapter_config_transforms: list[AdapterConfigTransform] = []
        self.execution_env_transforms: list[ExecutionEnvTransform] = []
        self.resource_event_sources: list[ResourceEventSource] = []
        self.tool_profile_sources: list[ToolProfileSource] = []
        self.bootstrap_env_defaults: dict[str, str] = {}
        self.shell_command_classifiers: list[ShellCommandClassifier] = []
        self.quality_directives: dict[str, tuple[str, ...]] = {}
        self._register_builtin_softschemas()

    def register_softschema(self, binding: Contract) -> None:
        self.softschemas.register(binding)

    def register_envelope(self, doc_type: str, model: type[BaseModel]) -> None:
        self.envelopes[doc_type] = model

    def register_terminal_statuses(self, statuses: frozenset[str]) -> None:
        self.terminal_statuses.update(statuses)

    def register_process_rule(self, rule: ProcessRule) -> None:
        self.process_rules.append(rule)

    def register_compare_defaults(self, fields: list[str], envelope_key: str) -> None:
        self.compare_defaults[envelope_key] = fields

    def register_viz_decorator(
        self,
        predicate: VizDecoratorPredicate,
        decorate: VizDecoratorCallback,
    ) -> None:
        self.viz_decorators.append(VizDecorator(predicate=predicate, decorate=decorate))

    def register_adapter_config_transform(self, transform: AdapterConfigTransform) -> None:
        self.adapter_config_transforms.append(transform)

    def register_execution_env_transform(self, transform: ExecutionEnvTransform) -> None:
        self.execution_env_transforms.append(transform)

    def register_resource_event_source(self, source: ResourceEventSource) -> None:
        self.resource_event_sources = [
            existing for existing in self.resource_event_sources if existing.name != source.name
        ]
        self.resource_event_sources.append(source)

    def register_tool_profile_source(self, source: ToolProfileSource) -> None:
        self.tool_profile_sources = [
            existing for existing in self.tool_profile_sources if existing.name != source.name
        ]
        self.tool_profile_sources.append(source)

    def register_bootstrap_env(self, defaults: Mapping[str, str]) -> None:
        self.bootstrap_env_defaults.update(defaults)

    def register_shell_command_classifier(self, classifier: ShellCommandClassifier) -> None:
        self.shell_command_classifiers.append(classifier)

    def register_quality_directives(self, directives: Mapping[str, Sequence[str]]) -> None:
        for step_id, inputs in directives.items():
            self.quality_directives[step_id] = tuple(inputs)

    def _register_builtin_softschemas(self) -> None:

        for contract_id, model, envelope_key in [
            ("metaproc:ProcessSpec/0.1", ProcessSpec, "process"),
            ("metaproc:ProgressSpec/0.1", ProgressSpec, "progress"),
            ("metaproc:Plan/0.4", Plan, "plan"),
            ("metaproc:Plan/0.5", Plan, "plan"),
            ("metaproc:QaReport/0.1", QaReport, "qa"),
            ("metaproc:QaReport/0.2", QaReport, "qa"),
            ("metaproc:QaSummary/0.1", QaSummary, "qa_summary"),
            ("metaproc:UsageReport/0.2", UsageReport, "usage"),
            (STRUCTURE_REPORT_CONTRACT_ID, StructureReport, "structure_report"),
        ]:
            self.register_softschema(
                Contract(
                    id=contract_id,
                    model=model,
                    envelope_key=envelope_key,
                    status=SchemaStatus.enforced,
                )
            )
