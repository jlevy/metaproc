---
type: is
id: is-01m353dbr89c9fm5bcfc1a5r66
title: Sort every traversal whose order is observable
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:42:56.520Z
updated_at: 2026-09-22T18:03:00.858Z
closed_at: 2026-09-22T18:03:00.858Z
close_reason: null
resolution: null
duplicate_of: null
---
`filesystem-rules`: "Sort by a documented key whenever output or mutation order is
observable. 'Whatever order the filesystem returned' is reproducible on one machine and
nowhere else."

Sites where the order is observable and the traversal is unsorted:

**Order decides which file wins — the worst kind.**

- `engine/pathing.py:177-179` — `glob_mod.glob(...)` then `return Path(matches[0])`.
  Which path a step resolves to depends on `os.scandir` order.
- `engine/pathing.py:190-192` — `find_item_dir`, same shape, same fix.
- `commands/auth_check.py:673-676` — `*run_dir.glob("*/progress.md")` then
  `next((p for p in ... if p.exists()), None)`. With more than one phase directory,
  which `progress.md` gets parsed and reported depends on enumeration order.

**Order is observable in output or in mutation sequence.**

- `commands/pool.py:1450-1451` — two unsorted `rglob` calls feed `override_paths`, which
  both the write loop (printing `wrote ... to <path>`) and the clear loop (printing
  `removed override <path>`) iterate.
- `commands/run_process.py:1740` — `for sub in step_state.iterdir():` builds the list the
  rename loop at 1749-1751 then mutates in that order; on partial failure the order is
  observable. The sibling helper `_child_scope_status_paths` at 1758-1779 already ends in
  `return sorted(matches)` — match it.
- `engine/input_validation.py:202` — unsorted glob feeds `_check_output_format`, so the
  order of `OutputFailure` entries persisted in `status.yaml` varies between runs.
- `dispatch/auth_usage.py:695-702` — `runs_dir.iterdir()` feeds a sort on `st_mtime`.
  Python's sort is stable, so runs sharing an mtime keep enumeration order and the
  "newest `max_runs`" window is nondeterministic. Add a documented tiebreak,
  `key=lambda p: (mtime, p.name)`. Two nits at the same site: the
  `try: candidates.append(child) / except OSError: continue` is dead (`list.append`
  cannot raise `OSError`; the `stat()` that can is in the sort key, outside the guard),
  and `p.stat() if p.exists()` is a TOCTOU that can still raise from inside `sort`.

**Gitignore semantics are order-dependent.**

- `osutils/ignore_filter.py:101` — `os.walk(root)` collects nested `.gitignore` patterns
  into one flat list. Last matching pattern wins, and `GitIgnoreSpec` honours that, so
  **filesystem enumeration order changes which files are ignored**. Sort the walk and
  `dirnames.sort()` after the `.git` prune at line 102.

**Cosmetic but operator-visible.**

- `logutil/parsing.py:1833-1840` — unsorted `os.scandir` assigns tail colours via
  `color_counter[0] % len(COLORS)`, so a file's colour differs between runs on the same
  directory.

Separately, two traversals filter after descending instead of pruning before, which
`filesystem-rules` asks for the other way round: `devtools/public_hygiene.py:291-296` and
`devtools/check_links.py:49-53` both `rglob("*")` over the whole repository and then drop
paths whose parts hit `SKIP_PARTS`. They walk the entire virtualenv and `node_modules` on
every `make verify`, and because `Path.rglob` swallows `OSError` during iteration, an
unreadable subtree silently shrinks the candidate set while the scan reports "passed".
