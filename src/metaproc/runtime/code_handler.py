"""Public re-export of code-mode handler runtime helpers.

Domain handlers should import from here, not from ``metaproc.engine.*``,
which is private implementation detail.

Re-exported:

- ``CodeHandler`` — the ``(context, step) -> None`` callable protocol that
  every code-mode handler must satisfy.
- ``resolve_code_handler(handler_ref, process_dir)`` — load a handler from
  a file path or installed module path, validating signature.
- ``resolve_templates(text, vars_or_context)`` — expand ``{{...}}``
  placeholders (including ``run.*`` / ``step.*`` framework aliases and
  environment-variable fallback) in any string field.
- ``resolve_output_paths(outputs, variables)`` — expand templated paths in
  a step's named outputs and return a ``{name: Path}`` mapping.
"""

from __future__ import annotations

from metaproc.engine.code_handler import (
    CodeHandler,
    resolve_code_handler,
    resolve_templates,
)
from metaproc.engine.placeholders import resolve_output_paths

__all__ = [
    "CodeHandler",
    "resolve_code_handler",
    "resolve_output_paths",
    "resolve_templates",
]
