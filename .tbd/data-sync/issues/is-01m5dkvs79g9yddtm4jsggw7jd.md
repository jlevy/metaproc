---
type: is
id: is-01m5dkvs79g9yddtm4jsggw7jd
title: "PR #108 F1: Gemini prompt wrapper has no pipefail, so a truncated prompt can pass as success"
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
parent_id: is-01m5dkv38535m7wq6pae32v5kc
created_at: 2026-10-20T21:35:45.383Z
updated_at: 2026-10-20T21:35:45.383Z
---
In \`src/metaproc/adapters/gemini_cli.py\` (~line 612) the wrapper is \`/bin/sh -c 'prompt_file=\$1; shift; exec 3< "\$prompt_file" || exit; /bin/cat <&3 3<&- | "\$@" 3<&-'\`. The step status is the pipeline last element (gemini); POSIX sh has no portable \`pipefail\` (dash lacks it). If \`/bin/cat\` dies mid-stream, Gemini receives a truncated prompt, and if Gemini then exits 0 the step is marked complete on a partial prompt. \`/bin/cat\` is also an absolute path that does not exist on NixOS, while \`gemini\` is already PATH-resolved.

The \`exec 3< "\$prompt_file" || exit\` guard already covers the unreadable-prompt case, so the remaining window is a mid-stream death of the producer.

A portable structural form was drafted and checked against bash-as-sh during review (captures the producer status through a side descriptor inside a command substitution, resolves cat via PATH, and as a side effect suppresses the bash job line in F2):

    exec 3< "$prompt_file" || exit; exec 5>&1; producer=$( { { cat <&3 3<&- 5>&-; printf %s "$?" >&4; } | "$@" 3<&- 4>&- >&5 5>&-; } 4>&1 ); consumer=$?; exec 5>&-; [ "$consumer" -eq 0 ] || exit "$consumer"; case $producer in 0) exit 0;; "") producer=1;; esac; echo "metaproc-gemini: prompt stream ended early (cat exited $producer)" >&2; exit "$producer"

Not committed: it is hard to review and sits on the critical prompt-delivery path, so it deserves its own PR. Needs the command pin in \`tests/test_adapters_ported.py:352-363\` updated and a test with a consumer that reads 10 bytes of a 1 MiB prompt and exits 0.
