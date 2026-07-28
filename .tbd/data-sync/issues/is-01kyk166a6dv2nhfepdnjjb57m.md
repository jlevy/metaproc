---
type: is
id: is-01kyk166a6dv2nhfepdnjjb57m
title: CI lint gate fails on agent Co-Authored-By trailers in git history
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01kyk15xd6m1m2vyzexds7xswy
created_at: 2026-07-28T00:14:57.349Z
updated_at: 2026-07-28T00:32:25.846Z
closed_at: 2026-07-28T00:32:25.846Z
close_reason: "Fixed and verified on claude/docs-review-ci-fixes-7re7um: hygiene allowlist for agent no-reply emails (CI lint unblocked); README/docs reorganized into an audience-based map with extraction-residue repairs across 25 files; skill allowed-tools conformance + dogfooded install with drift test. Full make verify green: 3786 passed, 8 skipped."
---
public_hygiene.py flags noreply@anthropic.com (Claude Co-Authored-By trailers on deb8452 and f54d594) as a potential personal email, so the lint job fails on main and on every future Claude-co-authored commit. Fix: allowlist agent/bot no-reply attribution addresses (noreply@anthropic.com, noreply@github.com) with a test contract.
