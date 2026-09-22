"""Tests for model separation — ported from example_plugin.

Validates that framework models are cleanly structured with proper exports.
Domain-specific tests (a consumer's own models, schema registry, envelopes,
validate CLI, and domain constants) are skipped.
"""

from __future__ import annotations

import inspect
import re
import sys

import metaproc.io.frontmatter as fm
import metaproc.models.authored as authored
import metaproc.models.plan as plan_mod
import metaproc.models.runtime as runtime
from metaproc.models.runtime import get_terminal_statuses

# ── Module separation ──────────────────────────────────────────────


def test_authored_module_exports_core_types():
    """metaproc.models.authored exports ProcessSpec, ProcessStep, IOSpec, etc."""

    for name in (
        "AdapterConfig",
        "ForEach",
        "IOSpec",
        "ParamDef",
        "ProcessDefaults",
        "ProcessSpec",
        "ProcessStep",
    ):
        assert hasattr(authored, name), f"missing export: {name}"


def test_plan_module_exports_plan_types():
    """metaproc.models.plan exports Plan, ResolvedStep, FanOut."""

    for name in ("Plan", "ResolvedStep", "FanOut"):
        assert hasattr(plan_mod, name), f"missing export: {name}"


def test_runtime_module_exports_runtime_types():
    """metaproc.models.runtime exports StatusRecord, AttemptRecord, etc."""

    for name in (
        "StatusRecord",
        "AttemptRecord",
        "ResultRecord",
        "MapItem",
        "MapItemsFrontmatter",
        "register_terminal_statuses",
        "get_terminal_statuses",
    ):
        assert hasattr(runtime, name), f"missing export: {name}"


def test_frontmatter_module_exports():
    """metaproc.io.frontmatter exports loading and envelope utilities."""

    for name in (
        "ProcessEnvelope",
        "ENVELOPE_MAP",
        "register_envelopes",
        "load_frontmatter_typed",
        "load_yaml_typed",
        "extract_items_from_envelope",
    ):
        assert hasattr(fm, name), f"missing export: {name}"


def test_authored_has_zero_domain_imports():
    """metaproc.models.authored must not import from domain-specific modules.

    Stated as an allow-list. A deny-list only catches the domain modules someone
    thought to name, and naming them puts a consumer's module names in this
    repository; the framework's own dependency set is short and knowable.
    """

    allowed_third_party = {"pydantic"}
    source = inspect.getsource(authored)
    offenders: list[str] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("from ", "import ")) or stripped.startswith("#"):
            continue
        match = re.match(r"(?:from|import)\s+([A-Za-z_][\w.]*)", stripped)
        if match is None:
            continue
        root = match.group(1).split(".")[0]
        if root == "metaproc" or root in sys.stdlib_module_names or root in allowed_third_party:
            continue
        offenders.append(stripped)

    assert not offenders, f"non-framework import found: {offenders}"


def test_terminal_statuses_registered_in_framework():
    """Framework terminal statuses include completed and cached."""

    merged = get_terminal_statuses()
    # Framework defaults
    assert "completed" in merged
    assert "cached" in merged


def test_old_plan_models_not_present():
    """PlanFrontmatter, PlanFanOutPreview should not exist in plan module.

    PlanEnvelope was re-added as the proper envelope type for the plan: key.
    """

    assert not hasattr(plan_mod, "PlanFrontmatter")
    assert not hasattr(plan_mod, "PlanFanOutPreview")
    assert hasattr(plan_mod, "PlanEnvelope")
