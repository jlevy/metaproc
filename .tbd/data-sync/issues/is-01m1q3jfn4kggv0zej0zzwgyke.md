---
type: is
id: is-01m1q3jfn4kggv0zej0zzwgyke
title: Pi adapter accepts no_session_persistence as a silent no-op
kind: bug
status: open
priority: 2
version: 1
labels: []
dependencies: []
created_at: 2026-09-04T21:00:40.483Z
updated_at: 2026-09-04T21:00:40.483Z
---
src/metaproc/adapters/pi_cli.py lists 'no_session_persistence' in its allowed-key set but never reads it, and four default execution profiles (pi-glm5, pi-gemini-flash, pi-gpt55, pi-gemini-pro) set it to true. The key therefore promises session isolation that the adapter does not deliver.

Same class of bug as the Gemini case fixed in mp-858m, but unrelated to the Gemini startup memory spike, so it was kept out of that PR to avoid widening the breaking surface on a change that needs downstream workflow testing.

Fix: either wire the key to a real pi-cli capability or reject it, and drop it from the default profiles.
