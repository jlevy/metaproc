---
type: is
id: is-01m0qry2rxx43yd8xsmej9vzy6
title: "Scale ratio test flakes on CI: min taken per side, not per ratio"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
created_at: 2026-08-23T16:58:18.781Z
updated_at: 2026-08-24T22:18:31.033Z
closed_at: 2026-08-24T22:18:31.033Z
close_reason: "Duplicate of mp-npza, which is closed and fixed by PR #39 (deterministic aligned-membership work guard, head 8d37591). Same test, same wall-clock ratio assertion in tests/execution_model/test_scale.py. Keeping mp-npza as the record since it carries the fix verification."
resolution: null
duplicate_of: null
---
`tests/execution_model/test_scale.py::TestEnvelope::test_readiness_does_not_degrade_quadratically_with_width`
fails intermittently on CI. Twice in one PR's history, on commits that cannot affect it
(one docs-only), on two different Python versions:

  version  small(s)  large(s)  ratio   threshold
  3.14     0.0388    0.2400    6.19    < 6.0  FAIL
  3.12     0.0225    0.1479    6.57    < 6.0  FAIL

It passes locally, repeatedly, and passed on the other two Python versions in the same
CI runs.

## Diagnosis

`_best_tick` already takes `min` over 3 rounds, with the comment "so one scheduling
hiccup cannot fail the build". That protects each measurement individually but not the
quantity actually asserted, because the test calls it twice:

    small = _best_tick(800)
    large = _best_tick(3200)
    ratio = large / small

The two calls run at different moments and so sample different CPU contention -- and the
suite runs under xdist (`-n logical`), so contention is set by whatever other tests share
the box. The absolute numbers move about 1.7x between runs on BOTH sides, which is larger
than the headroom between the 6.0 threshold and the 4x it is trying to prove.

The ratio is therefore an artifact of which moment each side was sampled in. Cross-pairing
the four observed halves makes it plain: small(3.14)/large(3.12) gives 3.81 (comfortable
pass) while small(3.12)/large(3.14) gives 10.67. Same code, same machine class.

## Proposed fix

Interleave the two measurements and take the min of the per-round RATIOS rather than the
ratio of two independent mins, so both sides of each ratio see the same contention:

    ratios = []
    for _ in range(rounds):
        s = _time_tick(small_state)
        l = _time_tick(large_state)
        ratios.append(l / s)
    ratio = min(ratios)

That keeps the test's discriminating power (indexed ~4x vs scan ~8x) instead of widening
the threshold, which would erode the very gap it exists to detect.

Alternatives, both worse: raising the threshold trades away discrimination in a test whose
whole purpose is to separate 4x from 8x; pinning the test to serial execution removes the
contention but slows the suite and hides the ordinary case.

## Scope note

Found while running PR #19 (toolchain bootstrap), which touches nothing in
`src/metaproc/execution_model/`. Deliberately NOT fixed there: it is an unrelated test
and changing a calibrated performance assertion is its owner's call, not a drive-by in a
devtools PR.
