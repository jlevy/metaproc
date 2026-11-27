"""A `collect:` input without a manifest path is refused when the spec is parsed.

Before, it passed `metaproc plan` and failed only at dispatch of the consumer, after
the whole upstream fan-out had run, with "input 'outcomes' has no resolved path".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaproc.models.authored import IOSpec


def test_collect_without_path_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"collect: verify.*needs a 'path:'"):
        IOSpec(collect="verify", require="finished")


def test_collect_with_path_is_accepted() -> None:
    spec = IOSpec(path="{{run.dir}}/outcomes.yaml", collect="verify", require="finished")
    assert spec.collect == "verify"


def test_require_without_collect_is_rejected() -> None:
    with pytest.raises(ValidationError, match="only applies to a 'collect:' input"):
        IOSpec(path="x.yaml", require="finished")
