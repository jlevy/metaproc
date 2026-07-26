"""Structure-report compatibility with the published softschema contract."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from softschema import Contract, SchemaProfile

from metaproc.structure_report import SchemaStage, _binding_stage


class _Payload(BaseModel):
    value: str


def test_binding_stage_uses_stable_contract_fields() -> None:
    assert (
        _binding_stage(Contract(id="yaml", profile=SchemaProfile.pure_yaml))
        == SchemaStage.pure_data
    )
    assert _binding_stage(Contract(id="model", model=_Payload)) == SchemaStage.validated_frontmatter
    assert (
        _binding_stage(Contract(id="schema", schema_path=Path("schema.json")))
        == SchemaStage.validated_frontmatter
    )
    assert _binding_stage(Contract(id="envelope", envelope_key="report")) == SchemaStage.frontmatter
    assert _binding_stage(Contract(id="prose")) == SchemaStage.prose
