"""Sanity test for the curated ``metaproc.io`` public surface.

Confirms that every name declared in ``metaproc.io.__all__`` is importable
and resolves to the expected helper from its source module.
"""

from __future__ import annotations

import frontmatter_format
import strif

import metaproc.io as io_mod
from metaproc.io import frontmatter as _io_frontmatter
from metaproc.io import gz_io as _io_gz_io
from metaproc.io import templating as _io_templating

EXPECTED_PUBLIC = {
    "ArtifactPath",
    "FmFormatError",
    "TemplateRenderError",
    "artifact_exists",
    "atomic_output_file",
    "fmf_read",
    "fmf_read_artifact",
    "fmf_read_frontmatter",
    "fmf_read_frontmatter_artifact",
    "fmf_write",
    "from_yaml_string",
    "iter_artifact_paths",
    "iter_jsonl_objects",
    "iter_jsonl_records",
    "iter_text_lines",
    "logical_path",
    "new_yaml",
    "read_yaml_artifact",
    "read_yaml_file",
    "render_template",
    "resolve_existing_artifact",
    "strip_template_frontmatter",
    "to_yaml_string",
    "write_yaml_file",
}


def test_all_matches_expected() -> None:
    assert set(io_mod.__all__) == EXPECTED_PUBLIC


def test_every_public_name_is_importable() -> None:
    for name in io_mod.__all__:
        assert hasattr(io_mod, name), name


def test_gz_io_helpers_resolve_to_source_module() -> None:
    for name in (
        "ArtifactPath",
        "artifact_exists",
        "iter_artifact_paths",
        "iter_jsonl_objects",
        "iter_jsonl_records",
        "iter_text_lines",
        "logical_path",
        "resolve_existing_artifact",
    ):
        assert getattr(io_mod, name) is getattr(_io_gz_io, name)


def test_frontmatter_artifact_helpers_resolve_to_source_module() -> None:
    for name in (
        "fmf_read_artifact",
        "fmf_read_frontmatter_artifact",
        "read_yaml_artifact",
    ):
        assert getattr(io_mod, name) is getattr(_io_frontmatter, name)


def test_frontmatter_format_reexports_resolve_to_source() -> None:

    for name in (
        "FmFormatError",
        "fmf_read",
        "fmf_read_frontmatter",
        "fmf_write",
        "from_yaml_string",
        "new_yaml",
        "read_yaml_file",
        "to_yaml_string",
        "write_yaml_file",
    ):
        assert getattr(io_mod, name) is getattr(frontmatter_format, name)


def test_templating_helpers_resolve_to_source_module() -> None:

    for name in ("TemplateRenderError", "render_template", "strip_template_frontmatter"):
        assert getattr(io_mod, name) is getattr(_io_templating, name)


def test_atomic_output_file_resolves_to_strif() -> None:

    assert io_mod.atomic_output_file is strif.atomic_output_file
