---
progress:
  schema: metaproc:ProgressSpec/0.1
  process: replay-smoke
  items:
    - item: alfa
      fail_in: none
    - item: brvo
      fail_in: none
    - item: chrl
      fail_in: none
    - item: dlta
      fail_in: stage-b
    - item: echo
      fail_in: silent-in-stage-b
---
# Replay Smoke Roster

Five synthetic items, none standing for anything real. `fail_in` marks the two that fail on purpose: `dlta` raises in
stage-b, which is an operational failure with no structured record, and `echo` returns
successfully from stage-b without writing its declared output, which output validation
catches as a contract failure and retries until the declared budget is spent. The three
others succeed everywhere.
