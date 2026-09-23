---
type: is
id: is-01m23c4n1c7amwayndq70dc1tm
title: Disclose the Gemini respectGitIgnore posture change to consumers
kind: bug
status: open
priority: 1
version: 1
labels:
  - security
  - gemini
  - docs
dependencies: []
created_at: 2026-09-09T15:21:17.612Z
updated_at: 2026-09-09T15:21:17.612Z
---
v0.4.0 shipped context.fileFiltering.respectGitIgnore: False in GEMINI_DEFAULT_NATIVE_SETTINGS (src/metaproc/settings.py:320-324), new since v0.3.0 (git log -S respectGitIgnore v0.3.0..v0.4.0 shows only c3aa5ac, PR #49). The effect is that a Gemini step's own file tools can read ignored files anywhere in the workspace, not only declared runtime inputs.

The shipped design doc already states this plainly at src/metaproc/docs/metaproc-design.md:1752-1756, including the explicit warning that operators should treat the workspace, including files such as .env, as readable by a Gemini step. So the behavior is documented inside the wheel but was never surfaced where an upgrading operator would look.

The published v0.4.0 release notes carry two substantial Gemini sections (lines 101-134 and 302-318) covering session retention, dynamic model resolution, native_settings merge semantics and stdin prompt streaming, and mention none of this. Line 133-134 ('a workspace ignore rule can no longer block or distort prompt delivery') describes the separate @path-to-stdin change and is easy to misread as covering it.

Anyone who upgraded to v0.4.0 and runs Gemini steps in a workspace containing secrets has a wider blast radius than the release told them. Decide and record: whether disclosure in the amended notes is sufficient, or whether the default itself should be revisited so that reading ignored files is opt-in per step or per profile. Tracked separately from mp-lto0 because that bead is the notes sweep and this one may warrant a behavior change.
