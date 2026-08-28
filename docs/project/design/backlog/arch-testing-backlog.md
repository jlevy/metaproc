# Architecture: Testing: Future Work

Backlog extracted from [arch-testing.md](../../../../src/metaproc/docs/arch-testing.md),
which ships in the wheel and describes the system as it is.
Where it might go is a project record and lives here.

## Future Considerations

### Open Questions

- `--max-concurrency` is not yet honored for sibling code-mode steps in the engine.
  Does this affect `smoke-core` wall clock when run with `--max-concurrency 4`?
  (Composite children in `smoke-adapters-all` already parallelize, so only code-mode
  aggregators are affected.)
- The codex-cli JSONL stream still does not carry a model ID. If a future codex release
  adds one, `--assert-model` for codex should move from informational to hard assertion.
- A live, standalone cloud execution smoke still needs a published image and an
  operator-provided GCP project.
  The committed cloud tier intentionally stops at job rendering so repository
  verification never creates infrastructure or spend.

### Potential Improvements

- Promote the negative-control smoke
  ([`smoke-adapters-negative-control.process.md``process/self-test/smoke-adapters-negative-control.process.md`)
  into the tier table as its own row, now that it has landed and covers all four
  adapters.
- Add a `smoke-softschema` tier that runs the no-token softschema-validation runbook as
  a process spec, slotting between `smoke-core` and the per-adapter smokes.
- Track wall-clock actuals in CI (once CI exists) to keep the tier-table estimates
  honest; current numbers are single-laptop observations.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
