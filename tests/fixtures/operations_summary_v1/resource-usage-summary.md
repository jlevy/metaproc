---
softschema:
  contract: metaproc.resources:ResourceUsageSummary/v1
  schema: .state/schemas/resource-usage-summary.v1.schema.yaml
  envelope: resource_usage
  status: enforced
resource_usage:
  run_id:
    example-roster-fit/run-20260914T153048Z.5501910000.vpjugu14bq-week39-fitreview
  totals:
    wall_time_s: 104743.91100000002
    cpu_pct_avg: 82.70806451612904
    cpu_pct_max: 107.1
    rss_bytes_avg: 167752604.9032258
    rss_bytes_max: 893796352
    input_tokens: 3125972
    output_tokens: 381368
    cache_read_tokens: 4749357
    cache_write_tokens: 0
    list_cost_usd: 4.92214845
    tool_calls: 301
    tool_failures: 17
    tool_exec_s: 11083.537
    local_compute_s: 9649.599999999999
  provider_meters:
  - key:
      provider: google
      product: gemini-cli
      meter: agent_invocations
      unit: count
    coverage: measured
    actual_quantity: 44.0
    unmeasured_event_count: 0
    source_event_ids:
    - evt-01kglprtsw2bfp
    lineage:
    - terminal boundary in one agent log
  - key:
      provider: google
      product: gemini-cli
      meter: api_requests
      unit: count
    coverage: unmeasured
    unmeasured_event_count: 44
    source_event_ids:
    - evt-01kglprtsw2bfp
    lineage:
    - provider request boundary absent from agent log
  - key:
      provider: google
      product: gemini-cli
      meter: model_turns
      unit: count
    coverage: unmeasured
    unmeasured_event_count: 44
    source_event_ids:
    - evt-01kglprtsw2bfp
    lineage:
    - gemini terminal aggregate has no exact turn boundary
  coverage_gaps:
  - provider: google
    product: gemini-cli
    meter: api_requests
    unit: count
  - provider: google
    product: gemini-cli
    meter: model_turns
    unit: count
  budgets: []
  finalization:
    state: failed
    trigger: terminal
    finalized_at: '2026-09-14T16:43:59.108634Z'
    recovered: false
    source_event_count: 753
    terminal_error_type: CLIError
  artifacts:
    resources: resources.json
    events: .logs/resource-events.jsonl
    summary: resource-usage-summary.md
---
# Resource usage summary
