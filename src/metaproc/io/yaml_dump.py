"""YAML handles for dumping that keep ruamel's pure-Python emitter.

When ruamel's optional C extension (``_ruamel_yaml``) is importable, every
``YAML(typ="safe")`` handle, including the default ``new_yaml()``, emits through
libyaml's ``CEmitter``. That emitter breaks long plain scalars at different points than
the pure ``Emitter``, so the same values serialize to different bytes depending on what
is installed, and it does not flush the output stream when a dump ends, so a dump to a
handle that is never flushed or closed leaves an empty file. Metaproc's own safe writers
use :func:`new_dump_yaml`, which keeps the pure emitter in either environment.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import ruamel.yaml.emitter
from frontmatter_format import KeySort, YamlTyp, new_yaml
from frontmatter_format.yaml_util import none_or_empty_dict
from ruamel.yaml import YAML

__all__ = ["new_dump_yaml"]


def new_dump_yaml(
    *,
    key_sort: KeySort[str] | None = None,
    suppress_vals: Callable[[Any], bool] | None = none_or_empty_dict,
    stringify_unknown: bool = False,
    typ: YamlTyp = "safe",
    allow_aliases: bool = False,
) -> YAML:
    """Return ``new_yaml(...)`` configured identically, emitting with the pure emitter.

    Only the emitter changes. Output is byte-identical to what ``new_yaml`` writes in a
    process without the C extension, and the stream is flushed at the end of each dump.
    Round-trip handles already use the pure ``RoundTripEmitter`` and are returned as
    built. Use the handle for dumping; parsing goes through ``read_yaml_file`` and
    ``from_yaml_string``.
    """
    yaml = new_yaml(
        key_sort=key_sort,
        suppress_vals=suppress_vals,
        stringify_unknown=stringify_unknown,
        typ=typ,
        allow_aliases=allow_aliases,
    )
    # ruamel resolves the emitter class from this attribute at each dump, so replacing
    # it here is equivalent to constructing the handle with ``pure=True``.
    emitter_cls: object = yaml.Emitter
    if not (isinstance(emitter_cls, type) and issubclass(emitter_cls, ruamel.yaml.emitter.Emitter)):
        yaml.Emitter = ruamel.yaml.emitter.Emitter
    return yaml
