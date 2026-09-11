"""What the coverage vocabulary must refuse, so a gap is never published as a number.

`Quantity` owns the four-state vocabulary; the meter models own three of it. Both
halves are pinned here, because the boundary between them is the part a reader has
to get right.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaproc.models.resources import (
    CoverageState,
    MeteredQuantity,
    MeterKey,
    MeterRollup,
    Quantity,
)

_METER = MeterKey(provider="anthropic", product="claude-code", meter="api_requests", unit="count")


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


def test_a_provider_meter_cannot_claim_not_applicable() -> None:
    """The meter models take the narrower vocabulary, and refuse the fourth state.

    A meter is identified by its key, so a meter that does not apply is one nobody
    emits. Admitting the state here would be worse than useless: `MeterRollup`
    derives coverage from the evidence it reconciled and can never return it, and
    `aggregate_meter_rollups` counts every non-measured, non-estimated quantity as
    an unmeasured event. An inapplicable meter would arrive at a reader as a gap
    worth chasing, which is the confusion `Quantity` exists to prevent.
    """
    with pytest.raises(ValidationError):
        MeteredQuantity.model_validate(
            {
                "key": _METER.model_dump(),
                "coverage": "not_applicable",
                "lineage": ["step runs no agent"],
            }
        )

    with pytest.raises(ValidationError):
        MeterRollup.model_validate(
            {
                "key": _METER.model_dump(),
                "coverage": "not_applicable",
                "source_event_ids": ["evt-no-agent-1"],
                "lineage": ["step runs no agent"],
            }
        )


def test_the_meter_vocabulary_is_the_rest_of_the_coverage_vocabulary() -> None:
    """One vocabulary, narrowed at the meter models rather than forked into a second."""
    meter = MeteredQuantity(
        key=_METER,
        coverage=CoverageState.UNMEASURED,
        lineage=["provider request boundary absent from agent log"],
    )

    assert meter.coverage is CoverageState.UNMEASURED
