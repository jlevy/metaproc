"""Generic bundled-markdown loader.

Uses ``importlib.resources`` plus the project's standard ``frontmatter-format``
parser, with no metaproc-internal imports. Lets a package ship ``.md`` docs
inside its own tree and read their bodies at runtime — including under
zipped/wheel installs. Ported from kash's
``load_help_topics.py`` (the ``page_field`` lazy loader), adapted to
``importlib.resources`` for zip-safety.

Used by ``metaproc help <topic>`` to serve docs bundled into the wheel.
"""

from __future__ import annotations

from dataclasses import field
from importlib import resources
from typing import Any

from frontmatter_format import fmf_read


def load_resource_md(package: str, name: str) -> str:
    """Read ``<name>.md`` from ``package`` and return its Markdown body.

    The ``.md`` suffix is appended if absent. Raises ``ValueError`` when the
    package or doc cannot be read. Malformed frontmatter raises the parser's
    validation error instead of leaking metadata or silently returning invalid
    authoring content.
    """
    filename = name if name.endswith(".md") else f"{name}.md"
    try:
        resource = resources.files(package).joinpath(filename)
        with resources.as_file(resource) as path:
            body, _metadata = fmf_read(path)
        return body
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError, OSError) as exc:
        raise ValueError(f"Unknown doc: {package}:{name}") from exc


def resource_doc_field(package: str, name: str) -> Any:
    """Return a ``dataclasses.field`` whose default lazily loads ``<name>.md``.

    Use as a field default in a topic dataclass so the markdown is read when an
    instance is constructed (kash's ``page_field`` equivalent), not at import.
    """
    return field(default_factory=lambda: load_resource_md(package, name))
