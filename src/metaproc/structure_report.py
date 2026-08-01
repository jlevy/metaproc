"""Metaproc process structure-report helpers and value models.

The `StructureReport*` Pydantic models are owned by metaproc — they describe a
metaproc process graph (producer_step, consumers, carrier) and default to the
`metaproc:StructureReport/v1` contract id. They used to live in
`softschema.reports` while the in-repo workspace package owned softschema, but
the standalone `softschema` package correctly does not ship them; they moved
here as part of the consumer workflow cutover.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from softschema import (
    Contract,
    Contracts,
    SchemaProfile,
    SchemaStatus,
    SchemaWarning,
    WarningCode,
    parse_schema_metadata,
)

from metaproc.io import fmf_read
from metaproc.models.authored import IOSpec, ProcessSpec

STRUCTURE_REPORT_CONTRACT_ID = "metaproc:StructureReport/v1"


class SchemaStage(StrEnum):
    """Coarse storage and validation stage used by Metaproc reports."""

    prose = "prose"
    frontmatter = "frontmatter"
    validated_frontmatter = "validated_frontmatter"
    pure_data = "pure_data"


class StructureReportSummary(BaseModel):
    artifacts: int = 0
    warnings: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_stage: dict[str, int] = Field(default_factory=dict)
    by_profile: dict[str, int] = Field(default_factory=dict)
    by_warning_code: dict[str, int] = Field(default_factory=dict)


class StructureReportArtifact(BaseModel):
    id: str
    producer_step: str | None = None
    path: str
    format: str | None = None
    contract_id: str | None = Field(default=None, alias="schema")
    envelope_key: str | None = None
    schema_path: str | None = None
    profile: SchemaProfile
    status: SchemaStatus
    stage: SchemaStage
    consumers: list[str] = Field(default_factory=list)
    warnings: list[SchemaWarning] = Field(default_factory=list)


class StructureReportEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    carrier: str
    status: SchemaStatus


class StructureReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    contract_id: str = Field(default=STRUCTURE_REPORT_CONTRACT_ID, alias="schema", frozen=True)
    generated_at: str
    process_path: str
    run_dir: str | None = None
    summary: StructureReportSummary
    artifacts: list[StructureReportArtifact] = Field(default_factory=list)
    edges: list[StructureReportEdge] = Field(default_factory=list)
    warnings: list[SchemaWarning] = Field(default_factory=list)

    @field_validator("contract_id")
    @classmethod
    def _require_structure_report_contract(cls, value: str) -> str:
        if value != STRUCTURE_REPORT_CONTRACT_ID:
            raise ValueError(f"schema must be {STRUCTURE_REPORT_CONTRACT_ID!r}")
        return value


class StructureReportEnvelope(BaseModel):
    structure_report: StructureReport


def inspect_frontmatter_path(path: Path, registry: Contracts) -> dict[str, object]:
    """Return a small structured description of one Markdown/frontmatter artifact."""
    _content, frontmatter = fmf_read(path)
    if not isinstance(frontmatter, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        return {
            "path": str(path),
            "profile": SchemaProfile.frontmatter_md.value,
            "stage": SchemaStage.prose.value,
            "warnings": [{"code": "frontmatter-missing", "message": "no frontmatter mapping"}],
        }
    warnings: list[dict[str, str]] = []
    try:
        metadata = parse_schema_metadata(frontmatter.get("softschema"))
    except (TypeError, ValidationError) as exc:
        metadata = None
        warnings.append({"code": "document-invalid", "message": str(exc)})
    binding = registry.resolve(metadata.contract_id) if metadata is not None else None
    return {
        "path": str(path),
        "profile": SchemaProfile.frontmatter_md.value,
        "schema": metadata.contract_id if metadata is not None else None,
        "status": (binding.status if binding else SchemaStatus.soft).value,
        "stage": (_binding_stage(binding) if binding else SchemaStage.frontmatter).value,
        "envelope_key": binding.envelope_key if binding else None,
        "frontmatter_keys": sorted(str(key) for key in frontmatter),
        "warnings": warnings,
    }


def build_structure_report(
    *,
    process_path: Path,
    spec: ProcessSpec,
    registry: Contracts,
    run_dir: Path | None = None,
) -> StructureReport:
    artifacts = _collect_artifacts(spec, registry)
    edges = _collect_edges(spec, artifacts)
    summary = _summarize_artifacts(artifacts)
    warnings = [warning for artifact in artifacts for warning in artifact.warnings]
    return StructureReport(
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        process_path=str(process_path),
        run_dir=str(run_dir) if run_dir is not None else None,
        summary=summary,
        artifacts=artifacts,
        edges=edges,
        warnings=warnings,
    )


def _collect_artifacts(
    spec: ProcessSpec,
    registry: Contracts,
) -> list[StructureReportArtifact]:
    consumers = _consumer_map(spec)
    artifacts: list[StructureReportArtifact] = []
    for step in spec.steps:
        for name, output in step.outputs.items():
            artifacts.append(
                _artifact_from_io(
                    artifact_id=f"{step.id}.{name}",
                    io_spec=output,
                    registry=registry,
                    producer_step=step.id,
                    consumers=sorted(consumers.get(f"{step.id}.{name}", [])),
                )
            )
    for name, output in spec.outputs.items():
        artifacts.append(
            _artifact_from_io(
                artifact_id=f"process.{name}",
                io_spec=IOSpec(
                    path=output.path,
                    ref=output.ref,
                    kind="file",
                    format=output.format,
                    schema=getattr(output, "schema_", None),
                ),
                registry=registry,
                producer_step=None,
                consumers=[],
            )
        )
    return artifacts


def _artifact_from_io(
    *,
    artifact_id: str,
    io_spec: IOSpec,
    registry: Contracts,
    producer_step: str | None,
    consumers: list[str],
) -> StructureReportArtifact:
    fmt = io_spec.format
    contract_id = io_spec.schema_
    binding = registry.resolve(contract_id) if contract_id else None
    warnings: list[SchemaWarning] = []
    # When a contract_id is declared but no binding is registered, fall back
    # to `soft` status and surface the mismatch as a warning. Previously this
    # branch returned `SchemaStatus.legacy`; the standalone dropped that
    # sentinel.
    if contract_id and binding is None:
        warnings.append(
            SchemaWarning(
                code=WarningCode.DOCUMENT_CONTRACT_MISMATCH,
                message=(
                    f"{artifact_id} declares {contract_id!r} but no binding is registered; "
                    "treating artifact as soft"
                ),
            )
        )

    profile = binding.profile if binding else _profile_from_format(fmt)
    status = binding.status if binding else SchemaStatus.soft
    stage = _binding_stage(binding) if binding else _stage_from_format(fmt, contract_id)
    path = io_spec.path or (f"ref:{io_spec.ref}" if io_spec.ref else "")
    return StructureReportArtifact(
        id=artifact_id,
        producer_step=producer_step,
        path=path,
        format=fmt,
        schema=contract_id,
        envelope_key=binding.envelope_key if binding else None,
        schema_path=str(binding.schema_path) if binding and binding.schema_path else None,
        profile=profile,
        status=status,
        stage=stage,
        consumers=consumers,
        warnings=warnings,
    )


def _profile_from_format(fmt: str | None) -> SchemaProfile:
    # The standalone keeps only the frontmatter-md and pure-yaml profiles. JSON
    # and JSONL artifacts fall through to frontmatter-md here for reporting
    # purposes; their declared format is preserved on StructureReportArtifact.
    if fmt == "frontmatter-md":
        return SchemaProfile.frontmatter_md
    if fmt == "yaml":
        return SchemaProfile.pure_yaml
    return SchemaProfile.frontmatter_md


def _binding_stage(binding: Contract) -> SchemaStage:
    """Derive report stage from the stable public contract fields."""
    if binding.profile == SchemaProfile.pure_yaml:
        return SchemaStage.pure_data
    if binding.model is not None or binding.schema_path is not None:
        return SchemaStage.validated_frontmatter
    if binding.envelope_key is not None:
        return SchemaStage.frontmatter
    return SchemaStage.prose


def _stage_from_format(fmt: str | None, contract_id: str | None) -> SchemaStage:
    if fmt == "frontmatter-md" and contract_id:
        return SchemaStage.validated_frontmatter
    if fmt == "frontmatter-md":
        return SchemaStage.frontmatter
    if fmt in {"yaml", "json", "jsonl"}:
        return SchemaStage.pure_data
    return SchemaStage.prose


def _consumer_map(spec: ProcessSpec) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = defaultdict(set)
    for step in spec.steps:
        for io_spec in step.inputs.values():
            if io_spec.ref:
                consumers[io_spec.ref].add(step.id)
    return dict(consumers)


def _collect_edges(
    spec: ProcessSpec,
    artifacts: list[StructureReportArtifact],
) -> list[StructureReportEdge]:
    by_id = {artifact.id: artifact for artifact in artifacts}
    edges: list[StructureReportEdge] = []
    for step in spec.steps:
        for output in step.outputs:
            source_id = f"{step.id}.{output}"
            source = by_id.get(source_id)
            if source is None:
                continue
            for consumer in source.consumers:
                edges.append(
                    StructureReportEdge.model_validate(
                        {
                            "from": source_id,
                            "to": consumer,
                            "carrier": _carrier_for_artifact(source),
                            "status": source.status,
                        }
                    )
                )
    return edges


def _carrier_for_artifact(artifact: StructureReportArtifact) -> str:
    if artifact.format == "frontmatter-md":
        return "frontmatter_and_prose"
    if artifact.format in {"yaml", "json", "jsonl"}:
        return "pure_data"
    return "file"


def _summarize_artifacts(artifacts: list[StructureReportArtifact]) -> StructureReportSummary:
    by_status = Counter(artifact.status.value for artifact in artifacts)
    by_stage = Counter(artifact.stage.value for artifact in artifacts)
    by_profile = Counter(artifact.profile.value for artifact in artifacts)
    by_warning_code = Counter(
        warning.code for artifact in artifacts for warning in artifact.warnings
    )
    return StructureReportSummary(
        artifacts=len(artifacts),
        warnings=sum(by_warning_code.values()),
        by_status=dict(sorted(by_status.items())),
        by_stage=dict(sorted(by_stage.items())),
        by_profile=dict(sorted(by_profile.items())),
        by_warning_code=dict(sorted(by_warning_code.items())),
    )
