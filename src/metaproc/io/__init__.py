"""Curated public surface for metaproc file utilities.

See ``docs/arch/arch-file-io-utilities.md`` in the source repository for the public
surface and non-obvious ``frontmatter_format`` behavior.
"""

from __future__ import annotations

from frontmatter_format import (
    FmFormatError,
    YamlSerializationError,
    fmf_read,
    fmf_read_frontmatter,
    fmf_write,
    from_yaml_string,
    new_yaml,
    read_yaml_file,
    to_yaml_string,
    write_yaml_file,
)
from strif import atomic_output_file

from metaproc.io.frontmatter import (
    fmf_read_artifact,
    fmf_read_frontmatter_artifact,
    read_yaml_artifact,
)
from metaproc.io.gz_io import (
    ArtifactPath,
    artifact_exists,
    iter_artifact_paths,
    iter_jsonl_objects,
    iter_jsonl_records,
    iter_text_lines,
    logical_path,
    resolve_existing_artifact,
)
from metaproc.io.templating import (
    TemplateRenderError,
    render_template,
    strip_template_frontmatter,
)

__all__ = [
    "ArtifactPath",
    "FmFormatError",
    "TemplateRenderError",
    "YamlSerializationError",
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
]
