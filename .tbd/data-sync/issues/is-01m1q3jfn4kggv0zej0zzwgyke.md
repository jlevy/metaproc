---
type: is
id: is-01m1q3jfn4kggv0zej0zzwgyke
title: Pi adapter accepts no_session_persistence as a silent no-op
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
created_at: 2026-09-04T21:00:40.483Z
updated_at: 2026-09-09T06:19:42.221Z
closed_at: 2026-09-09T06:19:42.215Z
close_reason: "Fixed by honoring the key, and the report's premise was backwards. pi_cli.py passed --no-session unconditionally, so a profile setting no_session_persistence: true was getting exactly the isolation it asked for; the four default profiles (pi-glm5, pi-gemini-flash, pi-gpt55, pi-gemini-pro) were never mis-served. The real defect was the inverse: an explicit false was silently overridden, because the allow-list accepted the key and build_command never read it. build_command now drops --no-session only on an explicit false, matching claude_cli.py:560. The default stays stateless and no built-in profile sets false, so no shipped profile changes behavior. This reinstates the change reverted at bd34a67, which PR #68 held back to keep its breaking surface narrow rather than on any correctness objection. Three tests in tests/test_adapters_ported.py::TestPiCliAdapter cover explicit false, explicit true, and the key absent; mutation-checked against the unconditional flag, where the false case fails. 17 Pi adapter tests pass."
resolution: null
duplicate_of: null
---
src/metaproc/adapters/pi_cli.py lists 'no_session_persistence' in its allowed-key set but never reads it, and four default execution profiles (pi-glm5, pi-gemini-flash, pi-gpt55, pi-gemini-pro) set it to true. The key therefore promises session isolation that the adapter does not deliver.

Same class of bug as the Gemini case fixed in mp-858m, but unrelated to the Gemini startup memory spike, so it was kept out of that PR to avoid widening the breaking surface on a change that needs downstream workflow testing.

Fix: either wire the key to a real pi-cli capability or reject it, and drop it from the default profiles.
