---
type: is
id: is-01m0zfkvtf12ag7e6y0rbdg7mw
title: "Documentation organization: project docs, design doc, concepts reconciliation"
kind: epic
status: open
priority: 1
version: 43
spec_path: docs/project/specs/active/plan-2026-08-26-documentation-organization.md
labels:
  - docs
dependencies: []
child_order_hints:
  - is-01m0zfmahvt4gkgb6458mbwctd
  - is-01m0zfmayr72s63rwxm2724mt1
  - is-01m0zfmbchshq1m9evnysb8ctx
  - is-01m0zfmbsnfzgsk3esdrrqgrf7
  - is-01m0zfmnmjz12f0evrmddyh8az
  - is-01m0zfmp3h1fefdr5bc88zp9c8
  - is-01m0zfmpjey3fp75kgm27agrbz
  - is-01m0zfmy8kjkexwgbmk5d6dctk
  - is-01m0zfmyqp1z1qzmvw1rjv5x95
  - is-01m0zfmz65hdv50jy1nynv12mn
  - is-01m0zfnc283zfm99g119a9zjxx
  - is-01m0zfnceeb2dymj96gs0v5kz4
  - is-01m0zfncvgne8mxt2q5ewdr9dk
  - is-01m0zfnd8tfw2a906p8p7j5ytr
  - is-01m0zfnq4rkb9hwkbjkerm6f5f
  - is-01m0zfnqhywnf6yf83gks58e1p
  - is-01m0zfnqytks2xjry3qd2zac9x
  - is-01m0zfnrc1pv65hd87f5j82j6z
  - is-01m0zfp2wxy4t1c76mez33z2v7
  - is-01m0zfp3a1snmnqjt7pgp791sc
  - is-01m0zg02jxkanjfngqpypfb6jn
  - is-01m10z8xv335zd4zcknjmnbct8
  - is-01m10z8y4mxd1hc0hk68h0pmxh
  - is-01m10z8ydpwg284vkt3p18d4kk
  - is-01m10z8ypvc86kvrgc42sd8xpy
  - is-01m10z8yzxdnmg714n8extpy92
  - is-01m10z8z9aynagg69rfanemcnf
  - is-01m10za6g75kbhq91bb8pmfk22
  - is-01m10za6rzvktagv7yxmpa8yr9
  - is-01m10za71e18j8h4e9205ztgw7
  - is-01m10za79z71tv7we12nc2f0z2
  - is-01m10za7jwtfxc4kan59ca5q1a
  - is-01m10za7vryv4mrqvcq98jqr5c
  - is-01m10za84cxdzqqen0qa9w4x6d
  - is-01m10za8d67et41efm9qre9mkm
  - is-01m10za8p2y72rq1zbs27xhfxv
  - is-01m10za924d7rd3snc610kyhxt
  - is-01m10zatjx1pt57vatcdfgzgdn
  - is-01m10zatw6s0pmrs75p57waq26
  - is-01m10zav5m1qxns81taqs7m9d8
  - is-01m10zavekbtk3qf7944msf7xr
created_at: 2026-08-26T16:49:22.254Z
updated_at: 2026-08-27T06:43:59.719Z
---
Umbrella for the 2026-08-26 documentation audit, revised 2026-08-27 after review.

The plan changed direction: instead of filing the core documents under docs/project/, they move INTO the wheel. Fifteen 'metaproc help' topics, 75,844 words, so an agent using Metaproc as a dependency has the complete picture without the repository. The README lists and links every first-party document and names its CLI equivalent. Project-internal material - backlog, authoring revision history, repository-maintenance scaffolding - comes out of the shipped docs and moves under docs/project/.

Six phases, deliberately separated so a large rename stays reviewable:
1. Move and wire - git mv, topic registry, distribution and shipped-link gates. No prose edits.
2. README and link direction - every doc listed with its topic.
3. Cohesion review - read the fifteen as a set; produces beads, not edits.
4. Externalize internal material - revision history, Future Considerations, maintenance blockquotes out; release versions in.
5. Tighten - act on the Phase 3 findings; 75,844 words is a large payload to hand an agent.
6. Reconcile the concepts docs - now higher stakes, because both ship in the same wheel.

Rationale, figures, and the topic table are in the linked spec.
