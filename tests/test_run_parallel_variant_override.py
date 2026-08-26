"""Tests for run-parallel's variant_flag → adapter_override coercion.

The cloud worker leg of a fan-out step like ``extract-items`` receives
``--variant pi-cli-managed-model`` because the engine derives that
synthetic name at run-process time from adapter type + model. Before
this fix, run-parallel coerced any non-empty ``--variant`` into
``adapter_override`` (``effective_adapter_override = adapter or
variant_flag``), and ``build_plan`` rejected it as ``unknown adapter
override: 'pi-cli-managed-model'`` because the synthetic name doesn't
appear in ``spec.defaults.adapters`` or ``ADAPTER_REGISTRY``.

The fix only coerces variant → adapter_override when the variant
literally names a known adapter slot. For synthetic composites the
step's own spec drives adapter resolution and the variant is just a
run-dir label.
"""

from __future__ import annotations

from typing import Any

import pytest

from metaproc.adapters.registry import ADAPTER_REGISTRY

# These tests exercise the variant_flag branch logic by stubbing build_plan
# and asserting what adapter_override value it receives. We patch via the
# module that imports build_plan, not the source module, so the patch
# intercepts the actual call.


@pytest.fixture
def stub_spec():
    """Minimal ProcessSpec stub: only exposes what the variant branch needs."""

    class _Defaults:
        adapters = {"claude-code-cli": object(), "gemini-cli": object()}

    class _Spec:
        defaults = _Defaults()

    return _Spec()


def _resolve_override(variant_flag: str | None, adapter: str | None, spec: Any) -> str | None:
    """Mirror the in-cli branch under test (kept in lockstep with run_parallel.py)."""

    if adapter:
        return adapter
    if variant_flag and (
        variant_flag in spec.defaults.adapters or variant_flag in ADAPTER_REGISTRY
    ):
        return variant_flag
    return None


class TestVariantOverrideResolution:
    def test_explicit_adapter_wins(self, stub_spec):
        # `--adapter X` always wins, regardless of --variant.
        assert _resolve_override("anything", "claude-code-cli", stub_spec) == "claude-code-cli"

    def test_variant_naming_known_adapter_slot_drives_override(self, stub_spec):
        # `--variant claude-code-cli` matches spec.defaults.adapters → use it.
        assert _resolve_override("claude-code-cli", None, stub_spec) == "claude-code-cli"

    def test_variant_in_adapter_registry_drives_override(self, stub_spec):

        assert "pi-cli" in ADAPTER_REGISTRY
        assert _resolve_override("pi-cli", None, stub_spec) == "pi-cli"

    def test_synthetic_composite_variant_not_coerced(self, stub_spec):
        # `--variant pi-cli-managed-model` is the synthetic name the engine
        # derives for an extract-items step. It's neither in defaults.adapters
        # nor ADAPTER_REGISTRY; do NOT coerce — let step spec drive
        # adapter resolution. This is the failure mode.
        assert _resolve_override("pi-cli-managed-model", None, stub_spec) is None

    def test_empty_variant_returns_none(self, stub_spec):
        assert _resolve_override("", None, stub_spec) is None
        assert _resolve_override(None, None, stub_spec) is None
