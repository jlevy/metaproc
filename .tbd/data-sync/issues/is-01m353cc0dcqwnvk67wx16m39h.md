---
type: is
id: is-01m353cc0dcqwnvk67wx16m39h
title: Make the atomic-write rule enforceable with a project check
kind: task
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:42:24.013Z
updated_at: 2026-09-22T18:03:00.833Z
closed_at: 2026-09-22T18:03:00.833Z
close_reason: null
resolution: null
duplicate_of: null
---
`filesystem-rules` requires the atomic-publication rule to be executable at the boundary
where it applies, not advisory. Ruff cannot express it: `Path.write_text` is a method on
a value, not an import, so `flake8-tidy-imports` cannot reach it, and no bundled rule
knows which paths in this package are staged temp files and which are published
destinations.

`devtools/check_atomic_writes.py` is an AST check in the shape of the existing
`devtools/check_plc0415_justifications.py`. It flags a truncating write (`write_text`,
`write_bytes`, `open(..., "w")`, `Path.open("w")`, `os.fdopen(..., "w")`) whose
destination is not already a private staging path, tracking the staging taint through
`with atomic_output_file(...) as tmp`, through `tmp / "part.yaml"` and `Path(tmp)`, and
through a `TemporaryDirectory` held in a variable rather than a `with`.

It deliberately does **not** flag append mode or `open("x")`. Routing an append through
replace-the-whole-file loses the concurrency property that made append correct, so the
check must never nudge anyone toward that.

Escaping it is possible and visible:

    log_fh = log_path.open("w")  # write-contract: live-stream -- the operator tails this

The contract name must be one of `live-stream`, `private-staging`, `create-only`,
`external-tool`, and must carry a reason after ` -- `. A bare suppression is rejected:
naming a contract is a design decision recorded at the site; a bare suppression is not.

Wired into `devtools/lint.py`, so `make lint-check` and CI run it with no workflow change.

Two properties that keep it a gate rather than a document:

- `tests/test_check_atomic_writes.py` holds probe fixtures in both directions — snippets
  it must reject and snippets it must not — plus `test_the_package_has_no_unnamed_
  truncating_writes`, which runs the check against the real tree.
- The check returns exit 2 when it scanned zero files. A moved directory or a typo'd path
  is how a check starts passing vacuously, and that failure is silent unless the count is
  asserted.
