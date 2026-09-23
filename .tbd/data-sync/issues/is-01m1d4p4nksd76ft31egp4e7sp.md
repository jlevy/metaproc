---
type: is
id: is-01m1d4p4nksd76ft31egp4e7sp
title: Restrict raw-path produced refs to upstream dataflow
kind: bug
status: closed
priority: 1
version: 4
labels:
  - planning
  - release-blocker
dependencies: []
parent_id: is-01m1dbcer80nak10tnbg1jyq52
created_at: 2026-09-01T00:07:44.562Z
updated_at: 2026-09-01T05:26:11.020Z
closed_at: 2026-09-01T05:26:11.019Z
close_reason: "produced_refs now derives raw-path matches only from the consumer's transitive dependency ancestors, computed after the full needs graph (authored plus ref-derived edges) is resolved, and refuses a path two upstream steps both declare. Negative coverage added for unrelated, downstream, and duplicate producers; both new tests fail against the previous all-steps scan. make verify green: 4,569 passed, 8 skipped."
resolution: null
duplicate_of: null
---
The planner classifies a raw prompt path as run-produced when any other step declares the same output path, including an independent or downstream step. That can exempt a real authored file from existence and content fingerprinting even though no upstream producer supplies it, allowing stale reuse or a late runtime failure. Derive raw-path produced_refs only from the consumer's transitive dependency ancestors, reject ambiguous producer matches, and add negative coverage for unrelated, downstream, and duplicate producers.

## Notes

## Re-verified still open at main 72ae119, 2026-08-31

Confirmed in `_resolve_produced_refs` in `src/metaproc/engine/build_plan.py`. PR 55
keyed the comparison through `normalize_path_key` and excluded the step's own outputs,
but the producer set is still built by iterating every other step in the plan:

    for producer_id, outputs in step_outputs.items():
        if producer_id == step.id:
            continue
        ...
        produced_keys.add(normalize_path_key(output_spec.path))

There is no restriction to the consumer's transitive dependency ancestors and no
rejection of ambiguous matches when several steps declare the same output path. An
unrelated or downstream producer therefore still exempts an authored file from
existence and content fingerprinting. The sibling `_validate_raw_path_dataflow` builds
a comparable index and does surface multiple matches, so the strict shape already
exists in the same module for `inputs`.
