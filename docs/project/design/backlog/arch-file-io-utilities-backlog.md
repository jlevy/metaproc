# Architecture: File IO Utilities: Future Work

Backlog extracted from
[arch-file-io-utilities.md](../../../../src/metaproc/docs/arch-file-io-utilities.md),
which ships in the wheel and describes the system as it is.
Where it might go is a project record and lives here.

## Future Considerations

### Open Questions

- Should the typed envelope loaders join the top-level public surface, or should their
  model and plugin dependencies remain explicit through `metaproc.io.frontmatter`?
- Does the append-only JSONL contract need a dedicated single-writer helper, or are
  direct append operations clearer at the event-log boundaries that own them?

### Potential Improvements

- Add a documentation contract check that compares the Public Surface table with
  `metaproc.io.__all__` so new exports cannot land without a deliberate documentation
  decision.
- Add focused examples for template rendering if downstream adoption shows that
  signatures alone are insufficient.
  Compression-transparent artifact lookup is described in the shipped document’s
  Compressed Artifacts section and in the
  [artifact compression design record](../artifact-compression.md).
- Switch `DEFAULT_WRITE_CODEC` to Zstandard once Metabrowser reads `.zst` and that
  release is adopted here; the gates are listed in the design record.
