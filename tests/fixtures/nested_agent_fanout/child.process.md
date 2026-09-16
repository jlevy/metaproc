---
process:
  name: nested-child
  description: |
    Child process with an agent fan-out step, dispatched against the mock adapter the
    test registers.

  defaults:
    default_adapter: nested-fanout-mock
    adapters:
      nested-fanout-mock:
        type: nested-fanout-mock

  inputs:
    unit: { param: UNIT, as: string, required: false, default: "AAA" }

  deps:
    work:
      path: "{{run.dir}}/work.md"
      as: "list<map<string, string>>"
      parse: { format: frontmatter-md, extract: progress }
      produced_by: scaffold-work

  steps:
    - id: scaffold-work
      mode: code
      handler: "nested_handlers.py:scaffold_work"
      description: Materialize the per-item work list this child fans out over.
      outputs:
        work:
          path: "{{run.dir}}/work.md"
          kind: file
          format: frontmatter-md

    - id: write-profile
      mode: agent
      description: Write one profile per work item through the mock adapter.
      needs: [scaffold-work]
      inputs:
        work: deps.work
      for_each:
        over: deps.work
        bind: profile_task
        key: "{{profile_task}}"
        bind_fields: [profile_task]
      prompt_prefix: "task={{profile_task}}"
      outputs:
        profile:
          path: "{{run.dir}}/profiles/{{profile_task}}/profile.md"
          kind: file
          format: frontmatter-md
---
# Nested Agent Fan-Out Child

The `write-profile` step is the shape that fails: an agent fan-out inside a child
process of a mapped composite step.
