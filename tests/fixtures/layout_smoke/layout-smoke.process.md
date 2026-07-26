---
process:
  name: layout-smoke
  description: |
    Tiny fixture used by the run-dir layout integration test
    (golden-layout test, bead internal-reference). Exercises:
    - a code-mode scaffold step (non-fan-out)
    - an agent-mode fan-out step writing one artifact per item, dispatched
      against a mock adapter registered by the test
    - a code-mode summarize step that reads the per-task artifacts

  defaults:
    default_adapter: layout-smoke-mock
    adapters:
      layout-smoke-mock:
        type: layout-smoke-mock

  inputs:
    items: { param: ITEMS, as: string, required: false, default: "AAA,BBB" }

  deps:
    items:
      path: "{{run.dir}}/items.md"
      as: "list<map<string, string>>"
      parse: { format: frontmatter-md, extract: items }
      produced_by: scaffold

  steps:
    - id: scaffold
      mode: code
      handler: "layout_smoke_handlers.py:scaffold_items"
      description: Materialize a tiny items.md at the run dir root.
      outputs:
        items:
          path: "{{run.dir}}/items.md"
          kind: file
          format: frontmatter-md

    - id: write-artifact
      mode: agent
      description: Write one artifact per item via the layout-smoke-mock adapter.
      needs: [scaffold]
      inputs:
        items: deps.items
      for_each:
        over: deps.items
        bind: item
        key: "{{item}}"
        bind_fields: [item]
        batch_size: 4
      prompt_prefix: "item={{item}}"
      outputs:
        artifact:
          path: "{{run.dir}}/artifacts/{{item}}/out.md"
          kind: file
          format: frontmatter-md

    - id: summarize
      mode: code
      handler: "layout_smoke_handlers.py:summarize"
      description: Roll the per-item artifacts up into a single summary.
      needs: [write-artifact]
      outputs:
        summary:
          path: "{{run.dir}}/summary.md"
          kind: file
          format: frontmatter-md
---
# Layout Smoke Fixture

Used by `test_layout_golden.py` to exercise the run-dir layout end-to-end.
The `write-artifact` step’s adapter is registered at test-time as a tiny subprocess that
writes a marker file at the declared output path.
