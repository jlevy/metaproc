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
- Add focused examples for template rendering and gzip-transparent artifact lookup if
  downstream adoption shows that signatures alone are insufficient.
