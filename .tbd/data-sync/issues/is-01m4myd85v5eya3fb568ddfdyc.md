---
type: is
id: is-01m4myd85v5eya3fb568ddfdyc
title: "PR #105 B1: working directory .gemini/settings.json outranks the private home and can widen tools.core"
kind: bug
status: closed
priority: 2
version: 3
delegate: claude-code@spud10
labels: []
dependencies: []
parent_id: is-01m4mybyqptgk7zm6660wmwtzb
hold: null
hold_until: null
created_at: 2026-10-11T07:39:02.696Z
updated_at: 2026-10-11T13:51:12.587Z
started_at: 2026-10-11T13:26:57.809Z
closed_at: 2026-10-11T13:51:12.586Z
close_reason: "Fixed in 535fff9 (prepare_env refuses a working directory overriding asserted settings). Disposition: https://github.com/jlevy/metaproc/pull/105#issuecomment-6109722673"
resolution: null
duplicate_of: null
---
Medium, introduced. Review B https://github.com/jlevy/metaproc/pull/105#pullrequestreview-5482362930. adapters/gemini_cli.py:559-566 writes the home; nothing inspects the cwd, while workspace scope refuses a conflicting file (:193-211). Gemini merges user < trusted workspace < system, and --skip-trust trusts every cwd. Fix: under user scope read <cwd>/.gemini/settings.json and, when it sets a Metaproc-asserted key to a different value, raise under METAPROC_ADAPTER_STRICT and otherwise warn naming file and keys; document in credential-setup.runbook.md:385-387 and metaproc-operator-reference.md:800-802.
