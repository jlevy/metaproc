"""Frontmatter loading and envelope lookup.

Handles YAML frontmatter parsing, document type detection, and
typed loading for process specs and fan-out source files.
"""

from __future__ import annotations

import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from frontmatter_format import fmf_read, fmf_read_frontmatter, from_yaml_string, read_yaml_file
from pydantic import BaseModel, ConfigDict, Field

from metaproc.io.gz_io import ArtifactPath, resolve_existing_artifact
from metaproc.models.authored import ProcessSpec
from metaproc.models.plan import PlanEnvelope
from metaproc.models.qa import QaReportEnvelope, QaSummaryEnvelope
from metaproc.models.runtime import MapItem
from metaproc.models.usage import UsageEnvelope

# NOTE: read uses typ="safe" — YAML comments are NOT preserved across read-then-write.
# NOTE: to_yaml_string suppresses None and empty-dict values by default; pass new_yaml(suppress_vals=...) to override.
# NOTE: only ---, ----, <!---, #---, //---, /*--- are valid frontmatter delimiters; ----- (five dashes) is rejected.
# NOTE: hash-style (#---) files allow #-prefixed lines (shebang, PEP 723) before the delimiter; other styles do not.


class ProcessEnvelope(BaseModel):
    """Envelope for process specification documents."""

    process: ProcessSpec


class ProgressSpec(BaseModel):
    """Inner model for progress frontmatter (fan-out source files)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    schema_: str = Field(default="metaproc:ProgressSpec/0.1", alias="schema")
    process: str = ""
    run_id: str = ""
    items: list[dict[str, Any]] = []


class ProgressEnvelope(BaseModel):
    """Envelope for progress tracking documents."""

    progress: ProgressSpec


# ── Envelope registry ────────────────────────────────────────────

ENVELOPE_MAP: dict[str, type[BaseModel]] = {
    "plan": PlanEnvelope,
    "process": ProcessEnvelope,
    "progress": ProgressEnvelope,
    "qa": QaReportEnvelope,
    "qa_summary": QaSummaryEnvelope,
    "usage": UsageEnvelope,
}


def register_envelopes(envelopes: dict[str, type[BaseModel]]) -> None:
    """Register additional envelope types for local/manual frontmatter loading."""
    ENVELOPE_MAP.update(envelopes)


def get_registered_envelopes() -> dict[str, type[BaseModel]]:
    """Return the merged envelope map from local registrations and plugins."""
    envelopes: dict[str, type[BaseModel]] = {}
    try:
        from metaproc.plugins.discovery import (  # noqa: PLC0415 -- guarded import (optional dep / circular)
            get_plugin_registry,
        )
    except ImportError:
        return dict(ENVELOPE_MAP)
    envelopes.update(get_plugin_registry().envelopes)
    # Explicit local/manual registrations should win over plugin defaults.
    envelopes.update(ENVELOPE_MAP)
    return envelopes


def load_frontmatter_typed(path: Path) -> BaseModel:
    """Parse YAML frontmatter, detect document type, return a validated model."""
    raw = fmf_read_frontmatter_artifact(path)
    if raw is None:
        msg = f"{path}: no YAML frontmatter found"
        raise ValueError(msg)
    if not isinstance(raw, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = f"{path}: frontmatter is not a mapping"
        raise TypeError(msg)
    envelope_map = get_registered_envelopes()
    for key, envelope_cls in envelope_map.items():
        if key in raw:
            return envelope_cls.model_validate(raw)
    msg = f"{path}: no recognized document type key; expected one of {list(envelope_map)}"
    raise ValueError(msg)


def load_yaml_typed[T: BaseModel](path: Path, model: type[T]) -> T:
    """Load a bare YAML file and validate against the given model."""
    raw = read_yaml_artifact(path)
    return model.model_validate(raw)


def fmf_read_artifact(path: Path | str) -> tuple[str, dict[str, Any] | None]:
    """Read frontmatter Markdown from a plain or gzipped artifact."""
    resolved = resolve_existing_artifact(Path(path))
    if not ArtifactPath(resolved).is_gzip:
        return fmf_read(resolved)
    with _temporary_text_artifact(resolved) as temp_path:
        return fmf_read(temp_path)


def fmf_read_frontmatter_artifact(path: Path | str) -> dict[str, Any] | None:
    """Read only frontmatter from a plain or gzipped artifact."""
    resolved = resolve_existing_artifact(Path(path))
    if not ArtifactPath(resolved).is_gzip:
        return fmf_read_frontmatter(resolved)
    with _temporary_text_artifact(resolved) as temp_path:
        return fmf_read_frontmatter(temp_path)


def read_yaml_artifact(path: Path | str) -> Any:
    """Read a bare YAML file from a plain or gzipped artifact."""
    resolved = resolve_existing_artifact(Path(path))
    if not ArtifactPath(resolved).is_gzip:
        return read_yaml_file(resolved)
    with ArtifactPath(resolved).open_text() as fh:
        return from_yaml_string(fh.read())


class _temporary_text_artifact:
    """Materialize compressed text for frontmatter_format's path-only reader."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._path: Path | None = None

    def __enter__(self) -> Path:
        suffix = self.path.with_suffix("").suffix
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            suffix=suffix,
        ) as tmp:
            with ArtifactPath(self.path).open_text() as src:
                tmp.write(src.read())
            self._path = Path(tmp.name)
        return self._path

    def __exit__(self, *_exc: object) -> None:
        if self._path is not None:
            with suppress(OSError):
                self._path.unlink()


# ── Generic item extraction ──────────────────────────────────────


def extract_items_from_envelope(envelope: BaseModel) -> list[dict[str, object]]:
    """Extract the items list from any envelope type."""
    for field_name in type(envelope).model_fields:
        inner = getattr(envelope, field_name)
        if isinstance(inner, BaseModel):
            items = getattr(inner, "items", None)
            if isinstance(items, list):
                return [
                    (
                        item.model_dump(exclude_none=True)
                        if isinstance(item, (BaseModel, MapItem))
                        else dict(cast("dict[str, Any]", item))
                    )
                    for item in cast("list[Any]", items)
                ]
    msg = f"envelope {type(envelope).__name__} has no inner model with an 'items' list"
    raise TypeError(msg)
