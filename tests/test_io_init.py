"""Sanity test for the curated ``metaproc.io`` public surface.

Confirms that every name declared in ``metaproc.io.__all__`` is importable
and resolves to the expected helper from its source module.
"""

from __future__ import annotations

import frontmatter_format
import pytest
import strif

import metaproc.io as io_mod
from metaproc.io import frontmatter as _io_frontmatter
from metaproc.io import gz_io as _io_gz_io
from metaproc.io import secret_io as _io_secret_io
from metaproc.io import templating as _io_templating

EXPECTED_PUBLIC = {
    "ArtifactPath",
    "FmFormatError",
    "SECRET_FILE_MODE",
    "TemplateRenderError",
    "YamlSerializationError",
    "artifact_exists",
    "atomic_output_file",
    "atomic_write_bytes",
    "atomic_write_text",
    "copyfile_atomic",
    "copytree_atomic",
    "fmf_read",
    "fmf_read_artifact",
    "fmf_read_frontmatter",
    "fmf_split_frontmatter",
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
    "temp_output_dir",
    "temp_output_file",
    "to_yaml_string",
    "write_secret_text",
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
        "YamlSerializationError",
        "fmf_read",
        "fmf_read_frontmatter",
        "fmf_split_frontmatter",
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


def test_strif_reexports_resolve_to_source() -> None:
    """One named helper per write contract, all re-exported without rewrapping.

    `filesystem-rules` asks that choosing something other than atomic publication be a
    visible decision with a name on it. That only works if each contract has a name on
    offer here: publish-replace (`atomic_write_text` / `atomic_write_bytes` /
    `atomic_output_file`), copy-and-publish (`copyfile_atomic` / `copytree_atomic`),
    and private staging (`temp_output_file` / `temp_output_dir`).
    """
    for name in (
        "atomic_output_file",
        "atomic_write_bytes",
        "atomic_write_text",
        "copyfile_atomic",
        "copytree_atomic",
        "temp_output_dir",
        "temp_output_file",
    ):
        assert getattr(io_mod, name) is getattr(strif, name), name


def test_secret_helpers_resolve_to_source_module() -> None:
    for name in ("SECRET_FILE_MODE", "write_secret_text"):
        assert getattr(io_mod, name) is getattr(_io_secret_io, name)


def test_yaml_mapping_serialization_is_alias_free() -> None:
    shared_value = {"items": ["one", "two"]}
    sharing = {"left": shared_value, "right": shared_value}
    unshared = {
        "left": {"items": ["one", "two"]},
        "right": {"items": ["one", "two"]},
    }

    serialized = io_mod.to_yaml_string(sharing)

    assert serialized == io_mod.to_yaml_string(unshared)
    assert "&id" not in serialized
    assert "*id" not in serialized


def test_cyclic_frontmatter_write_preserves_existing_target(tmp_path) -> None:
    target = tmp_path / "artifact.md"
    original = "existing content\n"
    target.write_text(original)
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(io_mod.YamlSerializationError, match="cyclic"):
        io_mod.fmf_write(target, "body\n", cyclic)

    assert target.read_text() == original
