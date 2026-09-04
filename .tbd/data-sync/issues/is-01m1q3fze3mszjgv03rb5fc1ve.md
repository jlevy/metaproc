---
type: is
id: is-01m1q3fze3mszjgv03rb5fc1ve
title: Prevent Gemini CLI startup session-scan memory spike
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
created_at: 2026-09-04T20:59:18.338Z
updated_at: 2026-09-04T21:14:28.306Z
closed_at: 2026-09-04T21:14:28.306Z
close_reason: "Shipped in PR #68: adapter ships general.sessionRetention.enabled=false, merges native settings, and rejects the no-op no_session_persistence key. Mechanism reproduced (4,078 MB heap / exit 134 over a real 12,313-session bucket) and early-exit verified in pinned 0.55.1 and current nightly. End-to-end CLI before/after left to downstream real-workflow validation, listed in the PR."
resolution: null
duplicate_of: null
---
Gemini CLI runs session-retention cleanup at startup as an un-awaited background task. Cleanup enumerates the current project's chats/ directory and concurrently parses every saved session JSONL via an unbounded Promise.all before it knows which sessions are expired. With an accumulated project bucket this turns a 0.4 GB process tree into 5+ GB.

Controlled research (docs/project/research/research-2026-09-01-gemini-cli-project-state-memory.md) isolated the cause: same prompt/model/repo/host, only the project-state regime varied. 0.39 GB clean, 5.07 GB with a copied 3.4 GiB bucket, 0.40 GB with the same bucket and retention disabled (gemini-cli 0.55.1).

Source validated at v0.55.1 (41327e407) and v0.60.0-nightly.20260904: cleanupExpiredSessions() early-exits on !settings.general?.sessionRetention?.enabled BEFORE getAllSessionFiles(), so the native setting prevents the scan. The unbounded Promise.all is still present in the newest nightly, so upgrading does not fix it.

Metaproc ships no mitigation today: sessionRetention appears nowhere in src/, and no_session_persistence is accepted by the Gemini adapter allow-list but never read.

Fix:
- add general.sessionRetention.enabled=false to GEMINI_DEFAULT_NATIVE_SETTINGS
- deep-merge that key so a profile-supplied native_settings cannot silently drop it
- reject the no-op no_session_persistence key for Gemini and Pi rather than accepting it
- note the disk tradeoff: the same switch also gates cleanupToolOutputFiles
