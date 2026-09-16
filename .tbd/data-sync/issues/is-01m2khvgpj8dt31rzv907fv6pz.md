---
type: is
id: is-01m2khvgpj8dt31rzv907fv6pz
title: "PR #79 review R7: mapped failure summaries must normalize per-item evidence paths and bound their size"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:00.626Z
updated_at: 2026-09-15T22:56:27.195Z
closed_at: 2026-09-15T22:56:27.190Z
close_reason: "Fixed in e29ceb0: failure_cause strips evidence suffix; summarize_failure_causes caps 5 causes / 4,000 chars. Tests: test_identical_mapped_failures_count_as_one_cause_despite_per_item_evidence, test_many_distinct_causes_render_the_most_frequent_within_a_bounded_summary."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Medium. src/metaproc/commands/run_process.py:1226-1230 counts distinct status.error strings, but command/handler messages end in a per-item log or traceback path, so identical failures render as 1 x ... each; a 200-item failure yields ~280 KB copied into process-status.yaml, step_fail and process_complete events, the fail-fast CLIError and metaproc status. Fix: strip the evidence suffix before counting (helper beside command_failure_message), render top-N causes plus 'and K more', bound total length, state the normalization in metaproc-design.md (~1955-1960).
