"""Pydantic models for QA report and QA summary frontmatter envelopes.

These are metaproc-level envelope types for the ``qa:`` and ``qa_summary:``
frontmatter blocks. The domain-specific ``QAReport`` model lives in the
workflow_package plugin; these envelopes wrap the frontmatter shape
that metaproc reads and validates.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class QaCheckResult(BaseModel):
    """Single check result within a QA report."""

    id: str
    status: str  # pass, fail, warn, skip
    message: str


class QaCheckCategory(BaseModel):
    """Aggregated check category (operational, omission, compliance, reasoning)."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0
    checks: list[QaCheckResult] = Field(default_factory=list)


class QaSummaryBlock(BaseModel):
    """Summary counters within a QA report envelope."""

    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0
    verdict: str = ""


class QaReport(BaseModel):
    """Inner model for the ``qa:`` frontmatter block on per-item QA reports.

    Shape matches what ``render_report()`` emits in the QA reporting module.
    Wrapped by ``QaReportEnvelope`` for frontmatter dispatch.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:QaReport/0.1", alias="schema")
    process: str
    item_id: str
    run_id: str
    variant: str
    timestamp: str
    summary: QaSummaryBlock = Field(default_factory=QaSummaryBlock)
    operational: QaCheckCategory = Field(default_factory=QaCheckCategory)
    omission: QaCheckCategory = Field(default_factory=QaCheckCategory)
    compliance: QaCheckCategory = Field(default_factory=QaCheckCategory)
    reasoning: QaCheckCategory = Field(default_factory=QaCheckCategory)


# ── QA Summary ─────────────────────────────────────────────────


class QaSummaryVariantTotal(BaseModel):
    """Per-variant total row in the qa_summary matrix."""

    variant: str
    verdict: str
    items: int = 0
    items_pass: int = 0
    items_warn: int = 0
    items_fail: int = 0


class QaSummaryMatrixEntry(BaseModel):
    """Per-item entry in the qa_summary matrix."""

    item_id: str
    variant: str
    verdict: str


class QaSummary(BaseModel):
    """Inner model for the ``qa_summary:`` frontmatter block.

    Shape matches what ``render_summary()`` emits in the QA reporting module.
    Wrapped by ``QaSummaryEnvelope`` for frontmatter dispatch.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="metaproc:QaSummary/0.1", alias="schema")
    process: str
    run_id: str
    timestamp: str
    variants: list[str] = Field(default_factory=list)
    matrix: list[QaSummaryMatrixEntry] = Field(default_factory=list)
    totals: list[QaSummaryVariantTotal] = Field(default_factory=list)


# ── Envelope wrappers for ENVELOPE_MAP dispatch ─────────────────


class QaReportEnvelope(BaseModel):
    """Envelope for ``qa:`` key dispatch in ``load_frontmatter_typed``."""

    qa: QaReport


class QaSummaryEnvelope(BaseModel):
    """Envelope for ``qa_summary:`` key dispatch in ``load_frontmatter_typed``."""

    qa_summary: QaSummary
