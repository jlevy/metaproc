# Architecture: RunPool: Future Work

Backlog extracted from [arch-runpool.md](../../../../src/metaproc/docs/arch-runpool.md),
which ships in the wheel and describes the system as it is.
Where it might go is a project record and lives here.

## Future Considerations

### Open Questions

- Should disk level continue influencing the aggregate capacity level, or become purely
  diagnostic except at near-full disk?
- How should the auth-pool-aware classifier interaction be resolved?
  When N parallel orchestrators share a 2-label OAuth pool, the resulting 429 failures
  get misclassified by the `claude-startup-exit-1-silent` known-bug regex due to
  debug-log prepend ordering in
  `metaproc.dispatch.pool_dispatch.classify_failure_for_slot`. See
  [arch-claude-code-harness.md § False-positive classifier pitfall](../../../../src/metaproc/docs/arch-claude-code-harness.md)
  for the diagnostic checklist and fix candidates.
  Cost: 16 items permanent-failed on 2026-05-23 batch; ABORT severity prevented retry.

### Potential Improvements

- Add cgroup-aware Linux telemetry for containerized workers.
- Add a pressure-shedding policy for sustained critical memory pressure.
- Build a stable RSS benchmark for `codex-gpt55` and for Linux hosts (Claude and pi-cli
  on macOS are now sampled; see § Per-adapter RSS benchmarks).
- Revisit `codex-gpt55` host cap using observed RSS and swap-growth data from clean
  runs.
- Add Windows support only after a clear telemetry and process-tree design exists.
