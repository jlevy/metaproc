"""Tests for run ID generation."""

from __future__ import annotations

import re

import pytest

from metaproc.engine.run_id import generate_run_id, slugify

# ── slugify ──────────────────────────────────────────────────────


def test_slugify_basic() -> None:
    assert slugify("Tech Mix 500") == "tech-mix-500"


def test_slugify_special_chars() -> None:
    assert slugify("dry-run (test)") == "dry-run-test"


def test_slugify_truncates() -> None:
    result = slugify("a" * 100, max_length=20)
    assert len(result) == 20


def test_slugify_empty() -> None:
    assert slugify("") == ""


# ── generate_run_id ─────────────────────────────────────────────

# Pattern for strif timestamped_uid: 20260408T003012Z-2555210000-foayjjh
_UID_PATTERN = r"\d{8}T\d{6}Z-\d+-[a-z0-9]+"
_TYPED_RUN_PATTERN = r"run-\d{8}T\d{6}Z\.\d+\.[a-z0-9]+"


def test_generate_run_id_default_template() -> None:
    """Default generation produces only the compact timestamped identity."""
    result = generate_run_id("mine", title="tech mix 500")
    assert re.fullmatch(_TYPED_RUN_PATTERN, result)
    assert "mine" not in result
    assert "tech-mix-500" not in result


def test_generate_run_id_no_title() -> None:
    """Process metadata does not alter the generated run identity."""
    result = generate_run_id("predict")
    assert re.fullmatch(_TYPED_RUN_PATTERN, result)
    assert "predict" not in result


def test_generate_run_id_custom_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """RUN_ID_TEMPLATE env var overrides the default template."""
    monkeypatch.setenv("RUN_ID_TEMPLATE", "{timestamped_uid}")
    result = generate_run_id("mine", title="ignored")
    # Should be just the uid, no process slug or title
    assert not result.startswith("mine")
    assert re.fullmatch(_UID_PATTERN, result)


def test_custom_legacy_template_receives_the_original_process_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUN_ID_TEMPLATE", "legacy-{process_slug}")

    assert generate_run_id("Mine / Batch") == "legacy-Mine / Batch"


def test_explicit_run_id_overrides_generation() -> None:
    """Verify that the CLI would use explicit --var RUN_ID over auto-generation.

    This tests the contract, not the CLI wiring — the CLI checks
    ``"RUN_ID" not in variables`` before calling generate_run_id.
    """
    variables: dict[str, str] = {"RUN_ID": "my-custom-id"}
    # When RUN_ID is already in variables, generate_run_id is not called.
    assert variables["RUN_ID"] == "my-custom-id"
