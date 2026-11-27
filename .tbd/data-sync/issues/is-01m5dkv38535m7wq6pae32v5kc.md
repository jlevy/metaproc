---
type: is
id: is-01m5dkv38535m7wq6pae32v5kc
title: "Address PR #108 review: launch contract follow-ups"
kind: task
status: open
priority: 2
version: 7
labels: []
dependencies: []
child_order_hints:
  - is-01m5dkvs79g9yddtm4jsggw7jd
  - is-01m5dkvsxcf46jwqr21p5teb6w
  - is-01m5dkvte38ytvp6yb5170755p
  - is-01m5dkwfrzevm5fvhkn36pk721
  - is-01m5dkwge5z0b0k3jgdhy6wa3a
  - is-01m5dkwh1assrdsnkr65hf89k4
created_at: 2026-10-20T21:35:22.883Z
updated_at: 2026-10-20T21:36:09.766Z
---
Follow-ups from the review of PR #108, https://github.com/jlevy/metaproc/pull/108.

PR #108 replaced the hard-coded `adapter_type == "gemini-cli"` launcher selection at three call sites with a single owned-process-group launcher for every adapter, and fixed the SIGTERM/SIGHUP orphan that the owned group introduced. The items below were found during that review and deliberately left out of the PR's scope.

## Notes

Review comments addressed in the PR itself: the hard-coded adapter-type branches (jlevy's comments on `auth_check.py` and `probe_tool_use.py`). Children here are the deferred findings.
