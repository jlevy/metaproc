---
type: is
id: is-01m0w1hgtakbg9hstyqksmq0j0
title: Resolve GCP secrets inside containers so Batch agent logs never hold plaintext
kind: bug
status: in_progress
priority: 0
version: 3
labels:
  - gcp
  - security
dependencies: []
created_at: 2026-08-25T08:45:42.089Z
updated_at: 2026-09-01T05:22:39.831Z
---
Live GCP validation showed Batch agent logs expanding Environment.secret_variables into plaintext values in the generated docker command line. Stop sending secret values through Batch secret_variables. Serialize only Secret Manager resource references in ordinary job env, hydrate them with the attached service account inside each Metaproc container before adapter/bootstrap use, redact all diagnostics, cover gcp-run/orchestrator/worker assembly and resolution, and require provider credential rotation outside this code change.

## Notes

The code half of this is done and merged. PR #44 landed at 72c77f7: Batch specs carry
only validated Secret Manager version references, all three entrypoints hydrate them with
the attached service account inside the container before adapter or bootstrap use,
dispatch rejects ambient target plaintext, resource diagnostics redact every dispatched
target independent of name, and the obsolete Batch `secret_variables` compatibility
surfaces are removed. Architecture docs and runbooks were updated with it.

What this bead still tracks is the part no repository check can answer, and it is the
reason the bead stays open rather than closing with the pull request:

- the candidate-image probe and a successful live canary on a rebuilt image. The
  pre-merge canary proved the old image does not expose the marker in agent logs, and
  also proved that entrypoint changes ahead of the wheel require an immutable image
  rebuild to take effect. Until a canary runs against the rebuilt image, no evidence
  says the shipped fix is live.
- provider credential rotation. Any secret that reached a Batch agent log in plaintext
  before this fix is still exposed and is outside what a code change can remediate.

Both are live-environment actions. Confirm them and close, or split whichever is already
done. Scoped down on 2026-08-31 during the v0.4.0 release audit; it does not block the
tag, because the fix itself is on main.
