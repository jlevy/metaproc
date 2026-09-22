---
process:
  name: nested-parent
  description: |
    Fixture for a mapped composite step whose child process runs an agent fan-out.
    Reproduces a common production shape: one child process per item, and inside it a
    per-item agent step, so the attempt and the result for that leaf are written from
    different scopes.

  inputs:
    units: { param: UNITS, as: string, required: false, default: "AAA" }

  deps:
    child_process: { path: ./child.process.md, as: path }
    roster:
      path: "{{run.dir}}/roster.md"
      as: "list<map<string, string>>"
      parse: { format: frontmatter-md, extract: progress }
      produced_by: scaffold-roster

  steps:
    - id: scaffold-roster
      mode: code
      handler: "nested_handlers.py:scaffold_roster"
      description: Materialize the roster the composite step maps over.
      outputs:
        roster:
          path: "{{run.dir}}/roster.md"
          kind: file
          format: frontmatter-md

    - id: authoring
      mode: composite
      uses: deps.child_process
      needs: [scaffold-roster]
      description: Run the child process once per rostered item.
      inputs:
        roster: deps.roster
      for_each:
        over: deps.roster
        bind: unit
        key: "{{unit}}"
        bind_fields: [unit]
---
# Nested Agent Fan-Out Parent

Runs `child.process.md` as a mapped composite step.
