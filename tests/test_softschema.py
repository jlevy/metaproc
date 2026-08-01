"""Phase 1 tests for ``softschema``.

Covers:
- compile: round-trip Pydantic → YAML → re-load; check_only mode detects drift.
- validate_structural: passes happy-path; catches enum / type / pattern errors.
- validate_semantic: runs cross-field invariants; surfaces ValidationError as list.
- validate_values: combined structural + semantic validation for extracted values.
- validate_artifact: frontmatter envelope and contract validation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, model_validator
from softschema import (
    Contract,
    Contracts,
    SchemaStatus,
    compile_model,
    validate_artifact,
    validate_semantic,
    validate_structural,
    validate_values,
)

from metaproc.structure_report import inspect_frontmatter_path

SAMPLE_CONTRACT = "example:Sample/v1"

# Fixture model: small, self-contained, exercises the full surface.


class _Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    score: int


class SampleModel(BaseModel):
    """Synthetic model with one cross-field invariant."""

    model_config = ConfigDict(extra="forbid")

    name: str
    direction: str  # "up" | "down"
    delta: float
    inner: _Inner | None = None
    when: date | None = None

    @model_validator(mode="after")
    def _direction_matches_delta(self) -> SampleModel:
        if self.direction == "up" and self.delta < 0:
            raise ValueError(f"direction=up but delta={self.delta}")
        if self.direction == "down" and self.delta > 0:
            raise ValueError(f"direction=down but delta={self.delta}")
        return self


# ── compile ──


def test_compile_writes_yaml(tmp_path: Path) -> None:
    out = tmp_path / "sample.schema.yaml"
    result = compile_model(SampleModel, out, contract_id=SAMPLE_CONTRACT)
    assert out.is_file()
    assert "x-softschema" in result.schema_yaml
    assert "schema_sha256" in result.schema_yaml
    assert SAMPLE_CONTRACT in result.schema_yaml
    assert "direction:" in result.schema_yaml


def test_compile_check_only_passes_when_in_sync(tmp_path: Path) -> None:
    out = tmp_path / "sample.schema.yaml"
    compile_model(SampleModel, out, contract_id=SAMPLE_CONTRACT)
    # Re-running in check_only mode should report no drift.
    check = compile_model(SampleModel, out, contract_id=SAMPLE_CONTRACT, check_only=True)
    assert check.drift is False


def test_compile_check_only_detects_drift(tmp_path: Path) -> None:
    out = tmp_path / "sample.schema.yaml"
    out.write_text("# stale committed schema\nproperties: {}\n")
    check = compile_model(SampleModel, out, contract_id=SAMPLE_CONTRACT, check_only=True)
    assert check.drift is True
    assert check.drift_diff is not None


def test_compile_check_only_missing_file(tmp_path: Path) -> None:
    out = tmp_path / "nonexistent.schema.yaml"
    check = compile_model(SampleModel, out, contract_id=SAMPLE_CONTRACT, check_only=True)
    assert check.drift is True


# ── validate_structural ──


def test_validate_structural_happy_path(tmp_path: Path) -> None:
    schema_path = tmp_path / "sample.schema.yaml"
    compile_model(SampleModel, schema_path, contract_id=SAMPLE_CONTRACT)
    values = {"name": "hello", "direction": "up", "delta": 1.5}
    result = validate_structural(values, schema_path)
    assert result.ok, f"unexpected errors: {result.errors}"


def test_validate_structural_catches_missing_required(tmp_path: Path) -> None:
    schema_path = tmp_path / "sample.schema.yaml"
    compile_model(SampleModel, schema_path, contract_id=SAMPLE_CONTRACT)
    # Missing required `name`.
    values = {"direction": "up", "delta": 1.5}
    result = validate_structural(values, schema_path)
    assert not result.ok
    assert any(
        "name" in str(e.get("path") or "") + " " + e.get("message", "") for e in result.errors
    )


def test_validate_structural_catches_type_mismatch(tmp_path: Path) -> None:
    schema_path = tmp_path / "sample.schema.yaml"
    compile_model(SampleModel, schema_path, contract_id=SAMPLE_CONTRACT)
    values = {"name": "hello", "direction": "up", "delta": "not a number"}
    result = validate_structural(values, schema_path)
    assert not result.ok


# ── validate_semantic ──


def test_validate_semantic_happy_path() -> None:
    values = {"name": "hello", "direction": "up", "delta": 1.5}
    result = validate_semantic(values, SampleModel)
    assert result.ok


def test_validate_semantic_runs_cross_field_invariant() -> None:
    values = {"name": "hello", "direction": "up", "delta": -1.0}
    result = validate_semantic(values, SampleModel)
    assert not result.ok
    assert any("direction=up" in str(e.get("msg", "")) for e in result.errors)


def test_validate_semantic_catches_missing_required() -> None:
    values = {"direction": "up", "delta": 1.5}
    result = validate_semantic(values, SampleModel)
    assert not result.ok
    assert any(e.get("type") == "missing" for e in result.errors)


def test_validate_values_alias() -> None:
    """`validate_values` is the public alias for `validate_semantic`."""
    values = {"name": "hello", "direction": "up", "delta": 1.5}
    assert validate_values(values, model=SampleModel).ok


# ── combined validate_values ──


def _write_doc(path: Path, frontmatter_yaml: str, body: str = "# title\n\nbody.\n") -> None:
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}")


def test_validate_values_combines_model_and_schema(tmp_path: Path) -> None:
    schema_path = tmp_path / "sample.schema.yaml"
    compile_model(SampleModel, schema_path, contract_id=SAMPLE_CONTRACT)
    result = validate_values(
        {"name": "hello", "direction": "up", "delta": 1.5},
        model=SampleModel,
        schema=schema_path,
    )
    assert result.ok


def test_validate_values_skips_semantic_when_no_model(tmp_path: Path) -> None:
    schema_path = tmp_path / "sample.schema.yaml"
    compile_model(SampleModel, schema_path, contract_id=SAMPLE_CONTRACT)
    result = validate_values(
        {"name": "hello", "direction": "up", "delta": 1.5},
        schema=schema_path,
    )
    assert result.structural.ok
    assert result.semantic.ok
    assert result.semantic.errors == []


def test_validate_values_requires_at_least_one_target() -> None:
    with pytest.raises(ValueError, match="model="):
        validate_values({"name": "hello"})


# ── Contract / registry ──


def test_validate_artifact_uses_envelope_binding(tmp_path: Path) -> None:
    doc = tmp_path / "sample.md"
    _write_doc(
        doc,
        """
        softschema: example:Sample/v1
        sample:
          name: hello
          direction: up
          delta: 1.5
        """,
    )
    binding = Contract(
        id="example:Sample/v1",
        model=SampleModel,
        envelope_key="sample",
        status=SchemaStatus.enforced,
    )

    result = validate_artifact(doc, contract=binding)

    assert result.ok
    assert result.contract_id == "example:Sample/v1"
    assert result.status == SchemaStatus.enforced
    assert result.values == {"name": "hello", "direction": "up", "delta": 1.5}


def test_validate_artifact_fails_when_document_softschema_contract_disagrees(
    tmp_path: Path,
) -> None:
    """Standalone treats document/binding contract mismatch as a hard structural error.

    The older consumer workspace emitted warning codes
    (document-softschema-disagrees-with-process etc.) and let validation succeed;
    the standalone fails closed instead.
    """
    doc = tmp_path / "sample.md"
    _write_doc(
        doc,
        """
        softschema:
          contract: other:Sample/v1
          status: soft
        sample:
          name: hello
          direction: up
          delta: 1.5
        """,
    )
    binding = Contract(
        id="example:Sample/v1",
        model=SampleModel,
        envelope_key="sample",
        status=SchemaStatus.enforced,
    )

    result = validate_artifact(doc, contract=binding)

    assert not result.ok
    assert result.structural.errors[0]["kind"] == "document_contract_mismatch"


def test_validate_artifact_reports_envelope_mismatch(tmp_path: Path) -> None:
    doc = tmp_path / "sample.md"
    _write_doc(
        doc,
        """
        wrong:
          name: hello
          direction: up
          delta: 1.5
        """,
    )
    binding = Contract(
        id="example:Sample/v1",
        model=SampleModel,
        envelope_key="sample",
    )

    result = validate_artifact(doc, contract=binding)

    assert not result.ok
    assert result.structural.errors[0]["kind"] == "envelope_mismatch"


def test_validate_artifact_reports_missing_compiled_schema_sidecar(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "sample.md"
    _write_doc(
        doc,
        """
        sample:
          name: hello
          direction: up
          delta: 1.5
        """,
    )
    binding = Contract(
        id="example:Sample/v1",
        model=SampleModel,
        envelope_key="sample",
        schema_path=tmp_path / "missing.schema.yaml",
    )

    result = validate_artifact(doc, contract=binding)

    assert not result.ok
    assert result.structural.errors[0]["kind"] == "schema_missing"


def test_inspect_frontmatter_reports_invalid_document_softschema(
    tmp_path: Path,
) -> None:
    doc = tmp_path / "sample.md"
    _write_doc(
        doc,
        """
        softschema:
          status: permissive
        sample:
          name: hello
        """,
    )

    result = inspect_frontmatter_path(doc, Contracts())

    warnings = result["warnings"]
    assert isinstance(warnings, list)
    assert warnings[0]["code"] == "document-invalid"
    assert "Field required" in warnings[0]["message"]
