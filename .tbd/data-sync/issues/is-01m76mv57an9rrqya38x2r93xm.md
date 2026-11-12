---
type: is
id: is-01m76mv57an9rrqya38x2r93xm
title: "PR #121 A7: changelog omits what an upgrade does to a resumable run"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10.local
labels: []
dependencies: []
parent_id: is-01m76msm7xe6a3n7dcegpafnrx
hold: null
hold_until: null
created_at: 2026-11-12T01:09:36.105Z
updated_at: 2026-11-12T01:09:37.163Z
started_at: 2026-11-12T01:09:36.749Z
closed_at: 2026-11-12T01:09:37.162Z
close_reason: "Fixed in 6d0ae82: the Unreleased entry covers the narrowed scope and says what upgrading does to a resumable run whose spec declares a value this release refuses (never where unsupported, exact_inputs, an undocumented value): it does not resume until edited, the edit changes the fingerprint of the steps it touches, and resuming at the launch release avoids that. Confirmed by reading against the 0.5.0 precedent; check_doc_dates, check_links and Flowmark pass."
resolution: null
duplicate_of: null
---
Medium. CHANGELOG.md:12-19, :23-27; precedent :494-497.

Review: https://github.com/jlevy/metaproc/pull/121#pullrequestreview-5866221123
PR: #121, reviewed head dee3914e5e001aace3abc728d81b0c6f859845e1
