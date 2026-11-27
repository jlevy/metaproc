---
type: is
id: is-01m366ze2fvh6any9chsx5shrw
title: "on_change: new_run compares bound path values as plain strings across mount aliases"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-23T04:04:28.878Z
updated_at: 2026-09-23T04:04:28.878Z
---
A new_run input whose value is a filesystem path is bound and compared as the plain string it resolved to. Only an input whose param is RUNS_DIR is normalised across the shared-filesystem mount spellings a run may be entered through. A resume of the same run through the other spelling therefore refuses on every bound path input, although it is the same file. Either normalise bound path values the way RUNS_DIR is normalised (mount-alias aware) or bind an identity for path inputs that does not depend on the spelling (for example the path relative to the run root or to RUNS_DIR when it is under one). Observed while reading the input-bindings comparison at 57f61dc; not yet reproduced on a shared filesystem.
