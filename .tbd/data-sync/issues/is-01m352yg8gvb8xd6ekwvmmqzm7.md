---
type: is
id: is-01m352yg8gvb8xd6ekwvmmqzm7
title: Make text encoding explicit at every file I/O site (PLW1514)
kind: task
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:34:49.616Z
updated_at: 2026-09-22T18:03:00.852Z
closed_at: 2026-09-22T18:03:00.852Z
close_reason: null
resolution: null
duplicate_of: null
---
`Path.write_text(s)` and `Path.read_text()` without an `encoding=` argument use
`locale.getpreferredencoding(False)`, not UTF-8. Metaproc writes YAML, Markdown, and
prompt text that routinely carries non-ASCII, and reads agent output it does not
control, so the encoding is part of every one of those contracts and is currently
left to the environment. Under a non-UTF-8 locale the same run directory is written
one way and read another.

`ruff check --preview --select PLW1514 --statistics src tests devtools` reports 143
sites.

The rule is a Ruff preview rule, so enabling it naively would turn on every other
preview rule in the selected sets. Enable it bounded instead — verified to activate
PLW1514 and nothing else:

```toml
[tool.ruff.lint]
preview = true
explicit-preview-rules = true
select = [..., "PLW1514", ...]
```

Note the interaction with the atomic-write work: `strif.atomic_write_text` already
defaults to `encoding="utf-8"`, so every write site that collapses onto it is fixed by
that change and needs no separate annotation. Do that work first and this bead shrinks
to the read sites plus the writes that stay on a raw call for a named reason.

Ruff's autofix for this rule is classified unsafe because it forces UTF-8 regardless of
platform. That is the intended semantics here, but apply it as a reviewed change rather
than a blind `--unsafe-fixes` run: a site that genuinely wants the locale encoding
should say `encoding="locale"` instead, and only a human reading the site can tell.
