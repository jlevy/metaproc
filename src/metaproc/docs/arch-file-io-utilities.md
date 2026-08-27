---
title: "Architecture: File I/O Utilities"
description: The curated metaproc.io public surface for atomic writes, gzip-transparent reads, frontmatter helpers, templates, and artifact paths.
author: metaproc team
status: Approved
---
# Architecture: File I/O Utilities (`metaproc.io`)

**Date:** 2026-07-26 (last updated 2026-08-09) **Status:** Approved

`metaproc.io` is the curated public import surface for downstream callers and shared
internal use. Every helper documented here is importable directly from `metaproc.io`:

```python
from metaproc.io import (
    iter_jsonl_objects, read_yaml_file, to_yaml_string, atomic_output_file,
)
```

The surface re-exports helpers from `frontmatter_format` and `strif` without changing
their behavior. Metaproc’s gzip-aware artifact readers and strict template renderer are
local implementations.

## Public Surface

| Helper | Source | Purpose |
| --- | --- | --- |
| `iter_jsonl_objects` | `metaproc.io.gz_io` | Stream a JSONL file (gz-aware) as dicts |
| `iter_jsonl_records` | `metaproc.io.gz_io` | Stream a JSONL file as `(line_no, dict)` |
| `iter_text_lines` | `metaproc.io.gz_io` | Stream a text file (gz-aware) line by line |
| `iter_artifact_paths` | `metaproc.io.gz_io` | Iterate matching artifact paths (gz-aware) |
| `ArtifactPath` | `metaproc.io.gz_io` | Path with logical/physical/gz awareness |
| `resolve_existing_artifact` | `metaproc.io.gz_io` | Find the on-disk path for a logical artifact |
| `artifact_exists` | `metaproc.io.gz_io` | Check whether a logical artifact has any backing file |
| `logical_path` | `metaproc.io.gz_io` | Strip the `.gz` suffix from a path |
| `read_yaml_file` | `frontmatter_format` | Read a plain YAML file |
| `write_yaml_file` | `frontmatter_format` | Write a plain YAML file (atomic) |
| `from_yaml_string` | `frontmatter_format` | Parse a YAML string |
| `to_yaml_string` | `frontmatter_format` | Serialize to a YAML string |
| `new_yaml` | `frontmatter_format` | Custom ruamel YAML handle (advanced) |
| `fmf_read` | `frontmatter_format` | Read frontmatter MD (body + metadata) |
| `fmf_read_frontmatter` | `frontmatter_format` | Read frontmatter MD (metadata only) |
| `fmf_write` | `frontmatter_format` | Write frontmatter MD (atomic) |
| `FmFormatError` | `frontmatter_format` | Exception raised by frontmatter parse failures |
| `YamlSerializationError` | `frontmatter_format` | Exception raised for cyclic mapping-based writes |
| `fmf_read_artifact` | `metaproc.io.frontmatter` | gz-aware `fmf_read` |
| `fmf_read_frontmatter_artifact` | `metaproc.io.frontmatter` | gz-aware `fmf_read_frontmatter` |
| `read_yaml_artifact` | `metaproc.io.frontmatter` | gz-aware `read_yaml_file` |
| `atomic_output_file` | `strif` | Atomic write for arbitrary content |
| `render_template` | `metaproc.io.templating` | Render strict `{{name}}` placeholders from an explicit mapping |
| `strip_template_frontmatter` | `metaproc.io.templating` | Remove the template contract from a rendered document |
| `TemplateRenderError` | `metaproc.io.templating` | Error raised for missing or unused strict template values |

Typed envelope helpers (`load_frontmatter_typed`, `load_yaml_typed`) and state-file
helpers in `metaproc.io.state_io`, `metaproc.io.dispatch_manifest`,
`metaproc.io.claimed_items`, `metaproc.io.orchestrator_lease`, `metaproc.io.overrides`
stay on their submodules.
Callers that need those specialized contracts import them directly from their owning
module.

## Use This, Not That

| Operation | Use |
| --- | --- |
| Read YAML file from path | `metaproc.io.read_yaml_file(path)` |
| Read YAML from string | `metaproc.io.from_yaml_string(s)` |
| Serialize to string | `metaproc.io.to_yaml_string(value)` |
| Write YAML file (atomic) | `metaproc.io.write_yaml_file(value, path)` |
| Read state-artifact YAML (gz-aware) | `metaproc.io.read_yaml_artifact(path)` |
| Read frontmatter MD (gz-aware) | `metaproc.io.fmf_read_artifact(path)` |
| Read frontmatter MD metadata only (gz-aware) | `metaproc.io.fmf_read_frontmatter_artifact(path)` |
| Write frontmatter MD (atomic) | `metaproc.io.fmf_write(path, body, metadata)` |
| Write JSON or text file (atomic) | `metaproc.io.atomic_output_file(path)` |
| Iterate JSONL records as dicts (gz-aware) | `metaproc.io.iter_jsonl_objects(path)` |
| Iterate JSONL with line numbers (gz-aware) | `metaproc.io.iter_jsonl_records(path)` |
| Render a strict document template | `metaproc.io.render_template(text, values)` |
| Append-only JSONL event log | `path.open("a")` + `json.dumps + "\n"` |

Downstream packages should use `metaproc.io` for the helpers in this table.
These re-exports form the documented compatibility surface.
Metaproc implementation modules may import `frontmatter_format`, `strif`, or a
specialized `metaproc.io` submodule directly when they need an internal symbol, avoid a
circular import, or keep an implementation dependency explicit.
Those internal import paths are not downstream compatibility guarantees.

## YAML Parser Boundaries

Most modules parse YAML and frontmatter through `metaproc.io`. Modules may import
`ruamel.yaml.YAMLError` directly to catch the dependency’s specific parse failures.
Two production paths hold a parser handle of their own:

- **`metaproc/engine/yaml_repair.py`** uses `ruamel.yaml.YAML(typ='safe').load(...)`
  directly because the LLM-output repair path needs the parser handle in strict mode,
  and the module does a manual `---\n` split before any parser touches the text.
- **`metaproc/engine/schema_conform.py`** builds its serializer through the curated
  `new_yaml`, but in **round-trip** mode (`typ="rt"`) rather than the default `safe`,
  and reconfigures it: `allow_aliases=True` so an author’s anchor is not expanded into a
  duplicated mapping, `suppress_vals` disabled so an explicit null is never dropped, an
  explicit `null` representer so a null’s spelling does not depend on emit order, and
  `preserve_quotes` plus the artifact templates’ indentation so a one-scalar fix is a
  one-line diff. It imports `ruamel.yaml.YAML` for the return annotation only.

Tests may use `yaml.safe_dump` to construct synthetic fixtures.
That use does not define the production serialization contract.

## `frontmatter_format` Gotchas

The wrapper module `metaproc/io/frontmatter.py` carries one-line `# NOTE:` markers for
these. The longer version:

1. **Reads use `typ="safe"`.** YAML comments are dropped on parse, so a read-then-write
   round trip silently strips comments.
   This matches stdlib `yaml.safe_load` behavior.
2. **`to_yaml_string` suppresses `None` and empty-dict values by default.** PyYAML’s
   `safe_dump` does not.
   If you need a `None` value to round-trip (e.g., as an explicit `key: null` in the
   output), use `new_yaml(suppress_vals=())` to get a parser handle that does not
   suppress, then call its `.dump` method.
3. **Only `---`, `----`, `<!---`, `#---`, `//---`, `/*---` are valid frontmatter
   delimiters.** Five-dash openers (`-----`) are rejected by the parser.
4. **Hash-style frontmatter (`#---`) allows `#`-prefixed lines (shebang, PEP 723
   metadata) before the delimiter.** Other styles do not; any non-delimiter content
   before the opener is an error.
5. **Mapping-based writes are alias-free by default.** Repeated acyclic lists and
   mappings are expanded, so equal values serialize identically regardless of Python
   object identity. Cycles raise `YamlSerializationError` before an atomic target is
   replaced. Low-level YAML helpers expose `allow_aliases=True` when aliases are
   deliberate; `fmf_write` callers can supply raw YAML frontmatter for that advanced
   case.

If a specific call site relies on any of these behaviors in a non-obvious way, add a
concise `# NOTE:` that explains why the behavior matters there.

## References

- `src/metaproc/io/__init__.py`
- `src/metaproc/io/frontmatter.py`
- `src/metaproc/io/gz_io.py`
- `src/metaproc/io/templating.py`
- [`frontmatter-format`](https://github.com/jlevy/frontmatter-format)
- [`strif`](https://github.com/jlevy/strif)

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
