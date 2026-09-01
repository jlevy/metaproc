---
type: is
id: is-01m0r92q2y1pe7dmhrcj6nst7q
title: Native mapped composite scopes and shared leaf admission
kind: epic
status: closed
priority: 1
version: 27
spec_path: docs/project/specs/active/plan-2026-08-25-consolidated-mapped-scope-runtime.md
labels:
  - execution-model
dependencies: []
child_order_hints:
  - is-01m0r93gwcj17mn4dmw1ts7fqa
  - is-01m0r93hdc3x84yqjwf2a3xn03
  - is-01m0r93hy045zzjtyw4brakhaw
  - is-01m0r93je6fk789d26aef6wx11
  - is-01m0r93k0sfy1ye28jj2f7db1z
  - is-01m0r93kk96jbzs27d9fmx762k
  - is-01m0r93m6cz6dytw4c1m2bbyaj
  - is-01m0r93mr72xw9k0p8tn94a07d
  - is-01m0rm18400gvqf9d61s4138mg
  - is-01m0rm18kbm24khxjemevb1ybv
  - is-01m0rs74ra22b0tz8n4v86kd8g
  - is-01m0rs7df0g28zgnsykar366kb
  - is-01m0s228q78197md50endt0eky
  - is-01m0vhr5rv34k6cbvr6wqx24sw
  - is-01m0vhs620ptcvxv074ccx88z4
  - is-01m0vjtjdxm7g8gkrxznmp5qd2
  - is-01m0vk0mxg72tsmdgcxsm2hz8n
  - is-01m0wpb4m8fr758mmbnkd7x6vr
  - is-01m0xrg4vr6n4znzxz0kkxxxt7
  - is-01m0yzwdz90x505k59tq1w73xa
created_at: 2026-08-23T21:40:27.869Z
updated_at: 2026-09-01T05:22:40.140Z
closed_at: 2026-09-01T05:22:40.139Z
close_reason: "The mapped composite scope epic shipped: for_each on composite steps, one recursive execution context, one run-owned RunPool for local mapped leaves, and mapped scopes projected through the existing views. Plan moved to docs/project/specs/done/. Open children mp-82ls, mp-rrfn, mp-f77b, mp-9bx5, mp-0paw, and mp-7t7p are post-release extensions, not remaining epic scope."
resolution: null
duplicate_of: null
---
Deliver the smallest safe native mapped-composite primitive: reuse one recursive RunExecutionContext, invoke child scopes in-process through the neutral fan-out runner, retain existing child and parent output declarations, write durable per-item state, and route local scalar agent leaves through one run-owned RunPool plus existing host admission. Exact-head verification and the successive smoke ladder gate expansion; schedulers, weighted claims, and new lineage stores remain evidence-triggered.
