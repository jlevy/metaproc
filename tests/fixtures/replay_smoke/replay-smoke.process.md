---
process:
  name: replay-smoke
  description: |
    A three-stage item-aligned chain plus a fan-in, sized for the replay-equivalence
    test. Every semantics the trace adapter must translate appears once: alignment,
    declared retry with on_invalid, a deliberate operational failure, a deliberate
    contract failure, and a collector that requires finished rather than succeeded.

  deps:
    roster:
      path: ./roster.md
      as: path

  steps:
    - id: stage-a
      mode: code
      handler: "replay_handlers.py:stage_a"
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item, fail_in]
        key: "{{item}}"
      outputs:
        out:
          path: "{{run.dir}}/items/{{item}}/stage-a.json"
          kind: file

    - id: stage-b
      mode: code
      handler: "replay_handlers.py:stage_b"
      needs: [stage-a]
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item, fail_in]
        key: "{{item}}"
        align: same_key
        retry:
          max_retries: 2
          initial_backoff_s: 0.05
          backoff_multiplier: 1.0
      outputs:
        out:
          path: "{{run.dir}}/items/{{item}}/stage-b.json"
          kind: file
          on_invalid:
            missing: retry

    - id: stage-c
      mode: code
      handler: "replay_handlers.py:stage_c"
      needs: [stage-b]
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item, fail_in]
        key: "{{item}}"
        align: same_key
      outputs:
        out:
          path: "{{run.dir}}/items/{{item}}/stage-c.json"
          kind: file

    - id: review
      mode: code
      handler: "replay_handlers.py:review"
      needs: [stage-c]
      inputs:
        outcomes:
          path: "{{run.dir}}/review/outcomes.yaml"
          collect: stage-c
          require: finished
      outputs:
        out:
          path: "{{run.dir}}/review/verdict.json"
          kind: file
---
# Replay Smoke Process

The smallest spec that exercises every translation the replay harness performs. The
chain runs per item; `dlta` and `echo` fail at stage-b by different mechanisms and
therefore never reach stage-c; the review still runs because its collected input
requires terminal outcomes rather than successful ones.
