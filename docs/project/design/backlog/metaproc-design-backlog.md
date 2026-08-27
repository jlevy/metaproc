# Metaproc Design: Future Work

Backlog extracted from
[metaproc-design.md](../../../../src/metaproc/docs/metaproc-design.md), which ships in
the wheel and describes the system as it is.
Where it might go is a project record and lives here.

## Future Considerations

### Open Questions

- The Plan schema is now at `metaproc:Plan/0.6` (adds reporting-only `resource_budgets`;
  0.5 added `lane_matrix` and `ExecutionLane`). The lane execution model is not yet
  documented in this arch doc.
  [unverified] whether lane-based dispatch is fully integrated into `run-process` or
  still under development.
- `overrides.yaml` (operator escape hatches via `metaproc override`) is referenced in
  the runtime state inventory (section 5.1) but not covered in its own subsection.
  The interaction between overrides and the resume/fingerprint system (section 10) is
  undocumented.
- Several newer CLI commands (`liveness-watch`, `resume-daemon`, `run-manifest`,
  `softschema`, `trace`) lack design-level documentation in this doc.
  Their operational semantics are only in code docstrings.
- The `codex-cli` adapter section (§12.2) is thorough but the Codex adapter is
  relatively new. [unverified] whether all described auth modes have been validated
  end-to-end in production cloud runs.

### Potential Improvements

- Extract the per-adapter reference (§12.2) into a separate adapter-catalog doc as the
  adapter count grows, keeping this doc focused on the contract and wire format.
- The illustrative downstream profile (§7) could move to an application-profile doc,
  leaving this doc strictly framework-scoped.
- Add a “Reading Guide” section at the top to help readers navigate the more than 21
  sections by use case (operator, process author, adapter implementer, framework
  contributor).
- Consolidate the cloud execution summary (§21) further: much of its content is now
  covered in
  [arch-cloud-execution.md](../../../../src/metaproc/docs/arch-cloud-execution.md), and
  the duplication creates maintenance burden.
- Document the `dispatch` subsystem (slot coordinator, credential pool) which is
  referenced by the adapter registry but not covered in this doc.
  See `src/metaproc/dispatch/` for the implementation.

See also [metaproc-design-rev3-proposals.md](../metaproc-design-proposals.md) for the
original future-work backlog.

## 16. Optional Workspace/State Surface (Future)

An advanced execution-profile feature, not yet implemented.

```yaml
workspace:
  root: .
  isolation: worktree
  writable:
    - train.py
    - experiments/
  commit_policy: explicit
```

Needed for:

- mutation/evaluation loops
- candidate/incumbent comparisons
- autoresearch-style workflows
