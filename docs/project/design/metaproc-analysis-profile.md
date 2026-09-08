# Metaproc Analysis Reference Profile

A worked profile from one downstream analysis domain, kept as a project record.
It was §7 of [metaproc-design.md](../../../src/metaproc/docs/metaproc-design.md) until
that document began shipping in the wheel: Metaproc core is consumer-agnostic, and a
domain profile inside the framework’s own documentation reaches every downstream
package.

It remains a useful illustration of how the authored process model is used in practice.
It is an example, not a contract.

## Analysis Reference Profile

The analysis workflow is the proving ground for the framework and serves as the primary
application profile.

## 7.1 Predict Process

```yaml
---
process:
  name: predict
  description: Pre-analysis packet generation and per-item prediction

  defaults:
    default_adapter: claude-code-cli
    adapters:
      claude-code-cli:
        type: claude-code-cli
        config:
          model: sonnet
          tools: [Read, Write, Edit, Bash, Grep, Glob, WebSearch, WebFetch]
          timeout_s: 900
          output_format: stream-json
          permission_mode: bypassPermissions

  deps:
    packet_manifest:
      path: "process/predict/{{form_version}}/packet.yaml"
      as: path
    predict_runbook:
      path: "process/predict/predict-item.runbook.md"
      as: path
    items:
      path: "{{run.dir}}/predict/items.md"
      as: list<map<string, string>>
      parse: {format: frontmatter-md, extract: items}
      produced_by: scaffold-day
    research_packets:
      path: "{{run.dir}}/predict/research-packets/"
      as: path
      produced_by: generate-research-packet
    precedent:
      path: "{{run.dir}}/predict/precedent/"
      as: path
      produced_by: retrieve-precedent

  steps:
    - id: scaffold-day
      mode: code
      handler: "scaffold_day.py:scaffold_day"
      description: Materialize the shared item roster

    - id: generate-research-packet
      mode: code
      needs: [scaffold-day]
      inputs:
        items: deps.items

    - id: predict-item
      mode: agent
      needs: [scaffold-day, generate-research-packet, retrieve-precedent]
      inputs:
        items: deps.items
        packet_manifest: deps.packet_manifest
        research_packets: deps.research_packets
        precedent: deps.precedent
      for_each:
        over: items
        bind: item
        bind_fields: [item, category, event_date, report_session, cutoff_date]
        batch_size: 5
      prompt_paths:
        - deps.predict_runbook
      description: Run the full prediction packet for one item
      prompt_prefix: |
        Follow the runbook at {{step.prompt_path}}.
        item={{item}}
        event_date={{event_date}}
        cutoff_date={{cutoff_date}}
        packet={{packet_manifest}}
      outputs:
        prediction:
          path: "{{run.dir}}/predict/{{run.variant}}/{{item}}/prediction.md"

    - id: qa-check
      mode: code
      needs: [predict-item]
      handler: "example_plugin.qa.handler:check"
---
```

Notes:

- `FORM_VERSION` selects a packet manifest on disk; the process spec no longer embeds
  packet-selection metadata in frontmatter
- packet ordering and required forms are read from `packet.yaml`, not duplicated in
  `predict-item.runbook.md`
- the roster is a shared process-level dep; per-item outputs stay variant-scoped

## 7.2 Retro Process

Retro uses the same run-id and variant layout as predict, but its static templates are
declared as named deps instead of living in a prose convention table.

```yaml
deps:
  retro_template:
    path: "process/retro/{{form_version}}/retro.template.md"
    as: path
  integrity_template:
    path: "process/retro/{{form_version}}/integrity.template.md"
    as: path
  items:
    path: "{{run.dir}}/retro/items.md"
    as: list<map<string, string>>
    parse: {format: frontmatter-md, extract: items}
    produced_by: scaffold-retro
  prediction:
    path: "{{run.dir}}/predict/{{run.variant}}/{{item}}/prediction.md"
    as: path
    produced_by: predict.predict-item

steps:
  - id: predict-retro
    mode: agent
    inputs:
      items: deps.items
      retro_template: deps.retro_template
      integrity_template: deps.integrity_template
      prediction: deps.prediction
    for_each:
      over: deps.items
      bind: item
      bind_fields: [item, category, event_date]
```

Version bumps become packet/template changes on disk, not edits to process frontmatter.

## 7.3 Mine Process

Mine is the reference workload for fan-out, validation, publication, and cloud/local
topology parity. The important target-state rule is that agent steps do not write
directly into shared mutable KB state.

```yaml
---
process:
  name: mine
  description: Historic precedent research with stage / validate / publish

  deps:
    roster:
      path: "{{run.dir}}/mine/events.md"
      as: list<map<string, string>>
      parse: {format: frontmatter-md, extract: items}
      produced_by: setup-roster
    kb_index:
      path: "knowledge-base/kb-index.yaml"
      as: path
    generate_record_runbook:
      path: "process/mine/generate-record.runbook.md"
      as: path

  steps:
    - id: setup-roster
      mode: code

    - id: extract-items
      mode: agent
      inputs:
        roster: deps.roster
      for_each:
        over: roster
        bind: event_id
        bind_fields: [event_id, item, period, event_date, category]
        batch_size: 50
      prompt_paths:
        - "{{deps.generate_record_runbook.path}}"
      outputs:
        candidate_record:
          path: "{{run.dir}}/mine/staged/{{event_id}}/"

    - id: validate-records
      mode: code
      needs: [extract-items]

    - id: publish-kb
      mode: code
      needs: [validate-records]
---
```

The per-item agent stage writes only into its own declared run output.
Validation and publication are separate harness-owned steps.
That keeps shared KB state deterministic and makes cloud/local execution equivalent.

## 7.4 Learn Process

Learn consumes run outputs and packet manifests, then emits a candidate next packet
version. Its approval gate is explicit in the DAG rather than hidden in prose.

Representative shape:

1. aggregate retros into learn
2. sample deep retros for mechanism review
3. update the current packet performance checkpoint
4. propose form improvements
5. `manual` approval gate
6. materialize a new `packet.yaml` plus templates for the candidate version
7. compare baseline versus candidate packet

This keeps “change the form” as an authored file change on disk, not a mutation of
process frontmatter.
