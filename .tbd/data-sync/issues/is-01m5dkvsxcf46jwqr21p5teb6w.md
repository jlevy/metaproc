---
type: is
id: is-01m5dkvsxcf46jwqr21p5teb6w
title: "PR #108 F2: sh-as-leader changes the signalled exit-status form and can put a bash job line in the transcript"
kind: bug
status: open
priority: 3
version: 1
labels: []
dependencies: []
parent_id: is-01m5dkv38535m7wq6pae32v5kc
created_at: 2026-10-20T21:35:46.091Z
updated_at: 2026-10-20T21:35:46.091Z
---
Since the Gemini wrapper keeps \`sh\` alive over a \`cat | gemini\` pipeline, \`sh\` is the leader and waits for the pipeline, so a Gemini killed by signal N yields returncode 128+N instead of -N. Nothing in \`src/\` reads negative return codes (verified by grep during review), so this is cosmetic in the error text.

Separately, on macOS \`/bin/sh\` is bash 3.2.57 and the plain-pipeline form prints a job line to stderr when the last pipeline element dies by signal, e.g. \`/bin/sh: line 1: 82615 Done ... 82616 Killed: 9 | ...\`. \`run-step --wait\` and \`LocalBackend\` route stderr into the JSONL transcript, so a non-JSON line lands there in that failure case. dash (Linux) prints nothing. The command-substitution form drafted in F1 prints nothing on bash either.
