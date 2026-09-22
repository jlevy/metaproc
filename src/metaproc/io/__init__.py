"""Curated public surface for metaproc file utilities.

See ``src/metaproc/docs/arch-file-io-utilities.md`` in the source repository for the public
surface and non-obvious ``frontmatter_format`` behavior.
"""

from __future__ import annotations

from frontmatter_format import (
    FmFormatError,
    YamlSerializationError,
    fmf_read,
    fmf_read_frontmatter,
    fmf_split_frontmatter,
    fmf_write,
    from_yaml_string,
    new_yaml,
    read_yaml_file,
    to_yaml_string,
    write_yaml_file,
)
from strif import (
    atomic_output_file,
    atomic_write_bytes,
    atomic_write_text,
    copyfile_atomic,
    copytree_atomic,
    temp_output_dir,
    temp_output_file,
)

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
from metaproc.io.secret_io import SECRET_FILE_MODE, write_secret_text
from metaproc.io.templating import (
    TemplateRenderError,
    render_template,
    strip_template_frontmatter,
)

__all__ = [
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
    "fmf_read_frontmatter_artifact",
    "fmf_split_frontmatter",
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
]
