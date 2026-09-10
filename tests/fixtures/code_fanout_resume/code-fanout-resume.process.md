---
process:
  name: code-fanout-resume
  description: |
    One standalone mode:code fan-out step. It deliberately declares no `align`, and
    nothing depends on it, so the engine routes it through the plain fan-out executor
    rather than the item-aligned chain executor. That is the path this fixture exists to
    cover.

  deps:
    roster:
      path: ./roster.md
      as: path

  steps:
    - id: project
      mode: code
      handler: "fanout_handlers.py:project"
      for_each:
        over: deps.roster
        bind: item
        bind_fields: [item]
        key: "{{item}}"
      outputs:
        out:
          path: "{{run.dir}}/items/{{item}}/project.json"
          kind: file
---
# Code Fan-Out Resume Process

The smallest spec that reaches the standalone code fan-out executor. A second step, or
an `align: same_key` on this one, would route the work through the aligned-chain
executor instead and stop exercising this path.
