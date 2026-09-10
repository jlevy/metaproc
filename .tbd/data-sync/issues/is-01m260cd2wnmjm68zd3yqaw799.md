---
type: is
id: is-01m260cd2wnmjm68zd3yqaw799
title: RunPool stability, operator diagnostics, and execution follow-ups
kind: epic
status: in_progress
priority: 1
version: 40
spec_path: docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md
docs:
  - path: docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md
  - path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
  - path: docs/project/design/contract-failure-primitives.md
labels: []
dependencies: []
child_order_hints:
  - is-01m260d25xyfwy5az5h5x0m35e
  - is-01m260d2ny6f4835930cy7wy54
  - is-01m260d36webbne6z3h2wqqc67
  - is-01m260d3nayrk57pafw5vsrg89
  - is-01m260d45fc3cf869rkndpayy9
  - is-01m260jstxtek1v68pmkdd521m
  - is-01m260jt4etysk1jzb3sgef0r0
  - is-01m260jte48rwwmrfvddj9cyyz
  - is-01m260z0663rrdsqt0drpvdq5f
  - is-01m260z0fgtg9ypvj5tk7bv1j5
  - is-01m260z0rb417gzrk85v4gtcx4
  - is-01m260z10t7p5h7maqpws2x4rc
  - is-01m260z1a9y9q4t6504vta2syt
  - is-01m260z1jxnav5y684hgm6xsz4
  - is-01m260z1v7cj4wtcghqr3gvn0k
  - is-01m269e4bfj2frt09fr0pdavcw
  - is-01m269mehrtqg1chz8dqsfzhwr
  - is-01m269w80vt7jb8gjanfp3nfj5
  - is-01m0r93gwcj17mn4dmw1ts7fqa
  - is-01m0r93mr72xw9k0p8tn94a07d
  - is-01m0r93hdc3x84yqjwf2a3xn03
  - is-01m0r93hy045zzjtyw4brakhaw
  - is-01m0r93kk96jbzs27d9fmx762k
  - is-01m0r93m6cz6dytw4c1m2bbyaj
  - is-01m0rbq50a74x57jtyp35zrkwh
  - is-01m0s0r624c0eszrgnq4qgjjbe
  - is-01m0t7zqm3kx2kkj4m1hpnfvk4
  - is-01m0t7zs3etjttp22nytn7abcn
  - is-01m0v08xkcyw3vsqp02kyhpn5h
  - is-01m0v08xy21dx5v6c0mp6sjz9w
  - is-01m0vr1qhtvv7y9r7k5qs5tprg
  - is-01m0zswfrt6dcn0qm5t0yftfjd
  - is-01m0vhr8b8gkgykcywwcq4z55c
created_at: 2026-09-10T15:53:31.981Z
updated_at: 2026-09-10T19:11:02.472Z
---
Own PR75 findings F4-F10, prominent operator diagnostics and direct per-agent raw-log debugging, and the related mapped-execution follow-ups. Preserve completed review and F1-F3 fix evidence, keep unresolved findings open through acceptance, and retain existing implementation owners, paused holds, and evidence-triggered flexibility. The active execution plan is the governing backlog; host/Safeproc plans supply subsystem implementation detail.

## Notes

The full RunPool and execution design review is published at https://github.com/jlevy/metaproc/pull/75#issuecomment-5621778931 . F1-F3 remain implemented and closed; F4-F10 remain open with implementation owners and acceptance criteria.

PR 75 commit 5546a02665bc0d43f4dc809b9b4d87f85e7f13b6 consolidates execution stability, operator diagnostics, documentation, and evidence-gated flexibility under docs/project/specs/active/plan-2026-09-10-runpool-execution-followups.md. Existing host-safety/Safeproc plans remain detailed implementation references. Contract extensions now belong to child epic mp-d019; the old contract plan is retained as a design record.

The operator's requirement for prominent causes is owned by mp-5les; direct per-attempt original log access throughout execution is owned by child mp-83g2. Those runtime features remain open. The immediate manual and generated-skill correction, mp-4wq7, encourages direct source-log inspection and discloses current combined stdout/stderr and filtered Pi capture limitations.

The graph audit moved 15 open roots from closed parents, updated 28 execution follow-up plan links, repaired 97 historical closed-bead plan paths, preserved paused work and evidence triggers, and resolved mp-f5m5 as a duplicate of still-open mp-ux0f with its unique requirements retained. All 47 open nodes in the two selected epic trees had active parents and existing governing plans before closing the two completed audit/guidance tasks.

Full make verify and the pre-push verification hook passed on the published tree: 4684 tests passed, 8 skipped; formatting, lint, types, browser checks, public hygiene, locked Python/npm audits, distribution inspection, and installed-wheel smoke checks passed. CI https://github.com/jlevy/metaproc/actions/runs/34518494536 passed all five checks on 5546a02. PR 75 remains open for review. The epic remains open for its runtime findings and execution follow-ups.
