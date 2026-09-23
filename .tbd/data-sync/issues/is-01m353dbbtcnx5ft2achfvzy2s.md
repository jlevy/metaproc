---
type: is
id: is-01m353dbbtcnx5ft2achfvzy2s
title: Give metaproc.io a named helper per write contract and collapse the boilerplate
kind: task
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:42:56.122Z
updated_at: 2026-09-22T18:03:00.848Z
closed_at: 2026-09-22T18:03:00.848Z
close_reason: null
resolution: null
duplicate_of: null
---
Roughly twenty-five sites across the package write

```python
state_dir.mkdir(parents=True, exist_ok=True)
...
with atomic_output_file(target) as tmp_path:
    Path(tmp_path).write_text(to_yaml_string(data))
```

when `atomic_write_text(target, to_yaml_string(data), make_parents=True)` says the same
thing. The collapse is behaviour-preserving: strif's `make_parents` calls
`mkdir(mode=0o777, parents=True, exist_ok=True)` on the staged file's parent, which is
the destination's own directory.

It is not purely cosmetic. `Path.write_text(s)` with no `encoding=` uses
`locale.getpreferredencoding(False)`; `atomic_write_text` pins `encoding="utf-8"`. Every
collapsed site stops depending on the platform locale — see mp-9lm5.

The cause is in the curated surface: `metaproc.io` re-exports only `atomic_output_file`
from strif (`io/__init__.py:22`), and `src/metaproc/docs/arch-file-io-utilities.md`
answers "Write JSON or text file (atomic)" with only `atomic_output_file(path)`. There is
no whole-string helper and no `make_parents` idiom on offer, so every caller reinvents
the three-line form.

Work:

- Re-export `atomic_write_text`, `atomic_write_bytes`, `temp_output_file`,
  `temp_output_dir`, `copyfile_atomic`, and `copytree_atomic` from `metaproc.io`, and add
  them to the doc's "Public Surface" and "Use This, Not That" tables. All are in strif
  3.1.0's `__all__`.
- Give all five `filesystem-rules` contracts a named row in that table, including
  create-only (`io/mkdir_lock`) and private staging (`temp_output_file`), so choosing a
  non-atomic contract means picking a name rather than reaching for a raw call.
- Collapse the call sites. `io/state_io.py:60-63` is the highest-leverage one: it backs
  `write_status_at`, `write_attempt_at`, `write_result_at`, `write_manual_ack_at`,
  `write_collected_inputs_at`, `write_run_plan`, `_write_task_attempt_at`, and
  `_write_task_attempt_anomalies_at`.

Four sites must **not** be collapsed, and each needs a comment saying why:

- `dispatch/retry_later.py:123` and `dispatch/credential_pool.py:905` create the parent
  at `0o700`. `strif.make_parent_dirs` defaults to `0o777`, so `make_parents=True` would
  widen a credential-adjacent directory.
- `runpool/status.py:205-214` chmods the *staged* file to 0644 before commit; the `mkdir`
  collapses but the body cannot.
- `engine/schema_conform.py:312` passes `newline=""` to preserve a CRLF document's line
  endings. `atomic_write_text` has no `newline` parameter, and dropping it would reflow
  the file and defeat the round-trip preservation the surrounding code goes to some
  length for.

Do not add `backup_suffix` anywhere readers poll the destination — strif moves the old
file to the backup before installing the new one, so the destination is briefly absent.
`status.yaml`, `process-status.yaml`, `runpool-status.yaml`, the orchestrator lease, and
the kill sentinel are all polled.
