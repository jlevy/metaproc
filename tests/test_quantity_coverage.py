"""What `Quantity` must refuse, so a summary cannot publish a gap as a number."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaproc.models.resources import CoverageState, Quantity


def test_a_gap_cannot_be_published_as_a_number() -> None:
    """The failure this type exists to prevent.

    A zero read as *instantaneous* or *free* is a measurement nobody made, and once
    it reaches a table or a chart it is indistinguishable from a real one.
    """
    with pytest.raises(ValidationError):
        Quantity(value=0.0, coverage=CoverageState.UNMEASURED, reason="no sampler")

    with pytest.raises(ValidationError):
        Quantity(value=0.0, coverage=CoverageState.NOT_APPLICABLE, reason="no agent here")


def test_a_gap_must_say_why() -> None:
    """An unexplained gap sends the reader back to the logs, which is what this replaces."""
    with pytest.raises(ValidationError):
        Quantity(coverage=CoverageState.UNMEASURED)

    with pytest.raises(ValidationError):
        Quantity(coverage=CoverageState.NOT_APPLICABLE)

    with pytest.raises(ValidationError):
        Quantity(value=12.0, coverage=CoverageState.ESTIMATED)


def test_a_measured_value_is_required_to_exist() -> None:
    with pytest.raises(ValidationError):
        Quantity(coverage=CoverageState.MEASURED)


def test_measured_zero_survives() -> None:
    """Measured zero and no measurement are different findings and stay different."""
    zero = Quantity.measured(0.0, unit="s")

    assert zero.value == 0.0
    assert zero.coverage is CoverageState.MEASURED
    assert zero.reason is None


def test_not_applicable_is_distinguishable_from_unmeasured() -> None:
    """Collapsing these loses the only one a reader can act on.

    `unmeasured` is a gap somebody could close by instrumenting something.
    `not_applicable` is a question that does not arise here, and chasing it is waste.
    """
    absent = Quantity.unmeasured(reason="provider meter reported no value", unit="count")
    inapplicable = Quantity.not_applicable(reason="step runs no agent", unit="count")

    assert absent.coverage is not inapplicable.coverage
    assert absent.value is inapplicable.value is None


def test_the_gap_carries_its_size() -> None:
    """How many observations a gap covers is the difference between a blip and a hole."""
    partial = Quantity.unmeasured(reason="sampler started late", samples=417)

    assert partial.sample_count == 417
