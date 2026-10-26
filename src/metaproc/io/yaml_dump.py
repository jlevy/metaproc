"""YAML handles for dumping that keep ruamel's pure-Python emitter.

Raw ``ruamel.yaml.YAML(typ="safe")`` handles can select libyaml's ``CEmitter`` when
the optional C extension is installed. Its wrapping and flushing differ from the pure
emitter. Metaproc's writers use :func:`new_dump_yaml` to name the deterministic dump
contract. The supported frontmatter-format factory already uses pure emitters; this
factory preserves its options and keeps the pure emitter if a customizer changes it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import ruamel.yaml.emitter
from frontmatter_format import KeySort, YamlTyp, new_yaml
from ruamel.yaml import YAML

__all__ = ["new_dump_yaml"]


class _Omitted:
    """Leave an argument at the public factory's default."""


_OMITTED = _Omitted()


def new_dump_yaml(
    *,
    key_sort: KeySort[str] | None = None,
    suppress_vals: Callable[[Any], bool] | None | _Omitted = _OMITTED,
    stringify_unknown: bool = False,
    typ: YamlTyp = "safe",
    allow_aliases: bool = False,
) -> YAML:
    """Return ``new_yaml(...)`` configured identically, emitting with the pure emitter.

    Only the emitter changes. Output is byte-identical to what ``new_yaml`` writes in a
    process without the C extension, and the stream is flushed at the end of each dump.
    Round-trip handles already use the pure ``RoundTripEmitter`` and are returned as
    built. Use the handle for dumping; parsing goes through ``read_yaml_file`` and
    ``from_yaml_string``. This does not change their parser or resolve compatibility
    differences when the C extension is installed.
    """
    suppression_options: dict[str, Any] = {}
    if suppress_vals is not _OMITTED:
        suppression_options["suppress_vals"] = suppress_vals
    yaml = new_yaml(
        key_sort=key_sort,
        stringify_unknown=stringify_unknown,
        typ=typ,
        allow_aliases=allow_aliases,
        **suppression_options,
    )
    # ruamel resolves the emitter class from this attribute at each dump, so replacing
    # it here is equivalent to constructing the handle with ``pure=True``.
    emitter_cls: object = yaml.Emitter
    if not (isinstance(emitter_cls, type) and issubclass(emitter_cls, ruamel.yaml.emitter.Emitter)):
        yaml.Emitter = ruamel.yaml.emitter.Emitter
    return yaml
