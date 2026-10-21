---
type: is
id: is-01m5fzzfvnph8pndp186jv3fzw
title: Let one run-owned RunPool serve mapped agent steps on profiles with different pool resources
kind: feature
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-10-21T19:45:58.645Z
updated_at: 2026-10-21T19:45:58.645Z
---
Regression against v0.4.1, accepted for the v0.5.0 release and recorded in its Compatibility notes.

Since mapped agent leaves joined the run-owned RunPool (94efd8e), `RunPoolOwner.get_pool` in `src/metaproc/commands/run_process.py` applies the profile-equality rule to them: a later profile whose resolved pool ceiling, `host_max_concurrency`, `estimated_process_rss_bytes`, or `initial_memory_budget_fraction` differs from the sizing profile's is refused with "cannot share the run-owned RunPool". v0.4.1 gave each mapped step its own pool, so a local run mapping one step over a Claude profile and another over a Gemini or Codex profile worked; it now fails the later step before launch. The bundled profiles fall into three non-matching groups: Claude and pi-* (500 MB, 0.25), gemini-cli (250 MB, 0.5), codex-gpt55 (1024 MB, 0.25, hint 20, host 20).

Workarounds today: align those resources in the consumer's own execution profiles, or run the steps as separate runs.

Fix direction: per-lane resource accounting in RunPool, so each lane admits against its own process estimate and host-slot limit while sharing the one memory, provider, and operator ceilings. Done when a mixed Claude + Gemini mapped run succeeds locally without aligned resources, and arch-runpool.md § Several Execution Profiles in One Run and the operator reference describe the new rule.
