---
type: is
id: is-01m76jt5bwfqa7zkze6vva62tj
title: Design exact_inputs reuse that binds the bytes that run
kind: feature
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m76a30djgz1eds7sfd07wq3b
created_at: 2026-11-12T00:34:06.330Z
updated_at: 2026-11-12T00:34:06.330Z
---
`reuse_policy: exact_inputs` is listed in `metaproc help design` §10.2 for deterministic
code steps, but no release has implemented it. PR #121 first added it as content digests
of a step's handler source file and authored input files inside the step fingerprint.
Reviews A and B showed that this does not make reuse exact, so the PR removed the
implementation and now refuses the value at planning, with an error that names this
bead. This bead owns the design, and lifting that refusal is part of it.

Reviews (PR #121, head dee3914e5e001aace3abc728d81b0c6f859845e1):

- A: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
- B: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5867092636

## Findings Against the Removed Implementation

- A1 (High): a launch fingerprints each task when the task starts, so a covered file
  edited mid-launch leaves item records under different hashes. Which one the resume
  reads depends on directory order, and stale outputs can be reused. A module already
  imported in the launching process runs the cached code, but the record names the
  edited file.
- A3 (Medium): the roster is excluded by input name, so the documented
  `over: deps.<name>` form still hashes it, and adding a roster row re-runs every item.
- A4 (Medium): a file the run writes is hashed unless its path equals a declared output
  exactly. A file inside an upstream directory output gives the plan and the result
  different fingerprints, and the runtime projection rejects the output
  (`step-mismatch`).
- A5 (Medium): locating a module-path handler imports its parent packages during
  `plan`, `deps`, `status` and viz, and fails planning on a host where the module is not
  importable, even with `validate_spec=False`.
- A9 (Medium): every fingerprint call re-reads every covered file (items times file
  size, again for each mapped-composite item scope).
- A10 (Low): a path that is neither a file, a directory nor absent raises a raw
  `OSError` out of `fingerprint_step`.
- A14 (Low, suggestion): an `exact_inputs` consumer of a `never` step's output is reused
  over new bytes. Consider cascading from a `never` step when its declared outputs
  changed across the re-run.
- B2 (Medium): an opted-in step in the child of a mapped composite never re-runs for a
  completed item scope. The completed-scope loop republishes the child plan and checks
  outputs but never compares child `step_hash`, and the projection then rejects the
  output.
- B3 (High): the handler digest is of the file on disk, not of the code that ran: a
  module imported before a child scope plans, and a timestamp `.pyc` that matches a
  same-size edit in the same second.
- B5 (Low, by reading): a gcp worker fingerprints its own workspace copy of the covered
  files, so a workspace that differs from the orchestrator's re-dispatches the step, and
  spends its budget, on every resume. Runbook bytes already behave this way.

## Requirements for a Sound Design (Review B, A1 Re-Check)

1. Compare per task. Mixed records from `run-step`, `run-parallel` and earlier launches
   must not be judged by one arbitrary item record (B1, tracked as `mp-jft4`).
2. Take the digests once, at launch start, for every scope the launch can enter, rather
   than at each child scope's plan publication, and run the bytes that were hashed (B3).
3. Verify on `run-step`, `run-parallel` and gcp workers instead of adopting the plan's
   value: compute locally, compare with the planned value, and refuse the task naming
   the role that differs (B5).
4. Compare completed mapped-composite item scopes against the value (B2).
5. Write the same value to the completion mirror (`run_process.py`, the completion-time
   `process-status.yaml` write), which `recorded_step_hash` and `status --steps` read
   first.

Also state the residual: an edit reverted before the next resume still reuses work done
on the edited bytes.

## Design Choice

Both reviews name an alternative to putting content in the fingerprint: a per-task record
of the input and handler digests the task actually used, written where the handler is
loaded and the inputs are opened, and compared per item where `_item_task_is_done`
decides reuse. It needs no exclusion list (A3, A4), covers run-written and per-item
inputs, and is the pattern `CollectedInputsRecord` already follows for `collect:` inputs.
It costs a new record, a comparison in the walk and in item reuse, and a status column.
Choose before a release, because either changes what an opted-in step records.

Depends on the B1 fix (`mp-jft4`) and the A2 fix (`mp-xy7c`), which are bugs on `main` independent of `exact_inputs`.
