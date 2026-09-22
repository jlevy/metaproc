---
softschema:
  contract: metaproc.operations:AgentOperationsSummary/v1
  schema: .state/schemas/agent-operations-summary.v1.schema.yaml
  envelope: agent_operations
  status: enforced
agent_operations:
  run_id:
    example-roster-fit/run-20260914T153048Z.0000000000.example001
  generated_at: '2026-09-14T16:44:00.314874Z'
  trigger: finalization
  extractor_version: 1
  extraction_s: 0.822
  run:
    unavailable:
      variant: run config records no variant
      execution_profile: run config records no execution profile
    process: example-roster-fit
    state: failed
    state_source: finalization
    started_at: '2026-09-14T16:03:10Z'
    ended_at: '2026-09-14T16:43:51Z'
    elapsed_s: 2441.0
    item_count: 16
    backend: local
    revisions:
      git_sha: abcdef012
  setup:
    unavailable:
      elapsed_s: a setup step has no elapsed time
      share_of_elapsed: a setup step has no elapsed time
    step_ids:
    - stage-roster
    - summarize-fit
  stages:
  - unavailable:
      started_at: step has not started
      completed_at: step is completed
      elapsed_s: step is completed
      share_of_elapsed: step is completed
    step_id: stage-roster
    mode: code
    task_shape: scalar
    state: completed
    item_count: 0
  - step_id: assess-item
    mode: composite
    task_shape: mapped
    state: failed
    item_count: 16
    started_at: '2026-09-14T16:03:10Z'
    completed_at: '2026-09-14T16:43:51Z'
    elapsed_s: 2441.0
    share_of_elapsed: 1.0
  - unavailable:
      started_at: step has not started
      completed_at: step is completed
      elapsed_s: step is completed
      share_of_elapsed: step is completed
    step_id: summarize-fit
    mode: code
    task_shape: scalar
    state: completed
    item_count: 0
  items:
    unavailable:
      barrier_wait_s: no item passed between two mapped stages
    stages:
    - assess-item
    item_count: 16
    chain_running_s:
      count: 16
      p50: 1901.0
      p90: 2373.5
      max: 2440.0
      mean: 1921.938
      total: 30751.0
    chain_span_s:
      count: 16
      p50: 1901.0
      p90: 2373.5
      max: 2440.0
      mean: 1921.938
      total: 30751.0
    per_stage:
    - stage: assess-item
      item_count: 16
      running_s:
        count: 16
        p50: 1901.0
        p90: 2373.5
        max: 2440.0
        mean: 1921.938
        total: 30751.0
      steps:
      - step_id: judge-fit-gemini-flash-36
        mode: agent
        elapsed_s:
          count: 16
          p50: 759.1
          p90: 1766.2
          max: 2385.8
          mean: 923.831
          total: 14781.3
      - step_id: judge-fit-gemini-flash-38
        mode: agent
        elapsed_s:
          count: 16
          p50: 1821.55
          p90: 2371.1
          max: 2437.8
          mean: 1565.356
          total: 25045.7
    slowest:
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-11
      stages:
      - unavailable:
          wait_before_s: first mapped stage for this item
        stage: assess-item
        state: failed
        started_at: '2026-09-14T16:03:11Z'
        completed_at: '2026-09-14T16:43:51Z'
        running_s: 2440.0
        slowest_step:
          stage: assess-item
          step_id: judge-fit-gemini-flash-38
          elapsed_s: 2437.8
      chain_running_s: 2440.0
      chain_span_s: 2440.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 2437.8
    items:
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-07
      stages: []
      chain_running_s: 1579.0
      chain_span_s: 1579.0
      slowest_step:
        stage: assess-item
        step_id: reconcile-fit-decisions
        elapsed_s: 1153.0
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-15
      stages: []
      chain_running_s: 1579.0
      chain_span_s: 1579.0
      slowest_step:
        stage: assess-item
        step_id: reconcile-fit-decisions
        elapsed_s: 1092.8
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-01
      stages: []
      chain_running_s: 1577.0
      chain_span_s: 1577.0
      slowest_step:
        stage: assess-item
        step_id: reconcile-fit-decisions
        elapsed_s: 1182.9
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-06
      stages: []
      chain_running_s: 1901.0
      chain_span_s: 1901.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 1897.2
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-10
      stages: []
      chain_running_s: 1648.0
      chain_span_s: 1648.0
      slowest_step:
        stage: assess-item
        step_id: reconcile-fit-decisions
        elapsed_s: 971.8
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-14
      stages: []
      chain_running_s: 2317.0
      chain_span_s: 2317.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 2314.7
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-16
      stages: []
      chain_running_s: 1648.0
      chain_span_s: 1648.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 914.9
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-02
      stages: []
      chain_running_s: 1971.0
      chain_span_s: 1971.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 1968.2
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-05
      stages: []
      chain_running_s: 1902.0
      chain_span_s: 1902.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 1897.4
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-09
      stages: []
      chain_running_s: 2430.0
      chain_span_s: 2430.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 2427.5
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-11
      stages: []
      chain_running_s: 2440.0
      chain_span_s: 2440.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 2437.8
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-13
      stages: []
      chain_running_s: 2155.0
      chain_span_s: 2155.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 2152.2
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-03
      stages: []
      chain_running_s: 1901.0
      chain_span_s: 1901.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 1645.3
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-04
      stages: []
      chain_running_s: 1901.0
      chain_span_s: 1901.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 1816.3
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-08
      stages: []
      chain_running_s: 1901.0
      chain_span_s: 1901.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-38
        elapsed_s: 1776.1
    - unavailable:
        barrier_wait_s: item entered one mapped stage
      item_key: ITEM-12
      stages: []
      chain_running_s: 1901.0
      chain_span_s: 1901.0
      slowest_step:
        stage: assess-item
        step_id: judge-fit-gemini-flash-36
        elapsed_s: 1836.8
  steps:
    scope_count: 17
    ignored_state_dirs: 0
    rows:
    - process: example-roster-fit
      step_id: assess-item
      mode: composite
      elapsed_s:
        count: 16
        p50: 1901.0
        p90: 2373.5
        max: 2440.0
        mean: 1921.938
        total: 30751.0
    - process: example-roster-fit-item
      step_id: judge-fit-gemini-flash-38
      mode: agent
      elapsed_s:
        count: 16
        p50: 1821.55
        p90: 2371.1
        max: 2437.8
        mean: 1565.356
        total: 25045.7
    - process: example-roster-fit-item
      step_id: judge-fit-gemini-flash-36
      mode: agent
      elapsed_s:
        count: 16
        p50: 759.1
        p90: 1766.2
        max: 2385.8
        mean: 923.831
        total: 14781.3
    agent_total_s: 39827.0
    code_total_s: 5651.6
  parallelism:
    ceiling: 40
    peak_running: 16
    mean_running: 4.027
    health_samples: 326
    share_samples_at_cap: 0.5399
    share_samples_at_ceiling: 0.0
    pools:
    - source: .logs/runpool/events.jsonl
      ceiling: 40
      process_starts: 57
      peak_running: 16
      mean_running: 4.027
      health_samples: 326
      share_samples_at_cap: 0.5399
      share_samples_at_ceiling: 0.0
      cap_min: 1
      cap_max: 40
  retries:
    unavailable:
      wrote_nothing: no failed attempt recorded output failures
    attempt_records: 120
    by_disposition:
      permanent: 19
      retryable: 13
      succeeded: 88
    by_failure_class:
      timeout: 16
      unknown: 16
    live_attempts: 0
    by_step:
    - step_id: assess-item
      attempts: 32
      not_succeeded: 19
      by_failure_class:
        timeout: 3
        unknown: 16
    - step_id: judge-fit-gemini-flash-38
      attempts: 23
      not_succeeded: 10
      by_failure_class:
        timeout: 10
    - step_id: judge-fit-gemini-flash-36
      attempts: 34
      not_succeeded: 3
      by_failure_class:
        timeout: 3
    path_shapes:
      child_step: 86
      root_item: 32
      root_step: 2
    unreadable_records: 0
    non_record_attempt_files: 32
  agents:
    transcripts: 57
    transcripts_without_result: 13
    provider_s: 9277.011
    requested_models:
      gemini-3.6-flash: 34
      gemini-3.8-flash: 23
    served_models:
      gemini-3-flash-preview: 44
      gemini-3.6-flash: 31
      gemini-3.8-flash: 13
    model_mismatches: 0
    tokens:
      input_tokens: 3125972
      output_tokens: 381368
      cache_read_tokens: 4749357
      cache_write_tokens: 0
    meters:
    - key:
        provider: google
        product: gemini-cli
        meter: agent_invocations
        unit: count
      coverage: measured
      actual_quantity: 44.0
      unmeasured_event_count: 0
    - key:
        provider: google
        product: gemini-cli
        meter: api_requests
        unit: count
      coverage: unmeasured
      unmeasured_event_count: 44
    - key:
        provider: google
        product: gemini-cli
        meter: model_turns
        unit: count
      coverage: unmeasured
      unmeasured_event_count: 44
    tool_calls: 301
    tool_results_at_cap: 0
    oversized_transcripts: []
  resources:
    unavailable:
      actual_cost_usd: resource summary reports it unmeasured
    resource_finalization_state: failed
    list_cost_usd: 4.922148
    cpu_pct_avg: 82.708
    cpu_pct_max: 107.1
    rss_bytes_max: 893796352
    health_samples: 326
    swap_used_peak_gb: 4.61
    swap_growth_gb: 2.17
    swap_delta_max_gb_per_min: 5.26
    disk_free_min_gb: 291.22
    run_size_bytes: 10031104
    run_file_count: 679
  artifacts:
    summary: operations-summary.md
    resource_summary: resource-usage-summary.md
    process_status: .state/process-status.yaml
---
# Operations Summary

Run `example-roster-fit/run-20260914T153048Z.0000000000.example001`
ended `failed`. Machine-consumed values are in the validated YAML frontmatter; this body
is explanatory, and `n/a` marks a figure listed under Unavailable Figures.

## Run

| Figure | Value |
| --- | --- |
| Process | example-roster-fit |
| State | failed (from finalization) |
| Started | 2026-09-14T16:03:10Z |
| Ended | 2026-09-14T16:43:51Z |
| Elapsed | 40.7 min (2,441.0 s) |
| Items | 16 |
| Variant | n/a |
| Execution profile | n/a |
| Backend | local |
| Revisions | git_sha `abcdef012` |
| Setup | n/a |
| Extraction | 0.82 s |

Setup steps: `stage-roster`, `summarize-fit`.

## Stages

| Step | Shape | Items | State | Elapsed | Share |
| --- | --- | ---: | --- | ---: | ---: |
| `stage-roster` | scalar | 0 | completed | n/a | n/a |
| `assess-item` | mapped | 16 | failed | 2,441.0 s | 100.0% |
| `summarize-fit` | scalar | 0 | completed | n/a | n/a |

## Per Item

16 items across mapped stages `assess-item`. Chain running time sums each item’s
running time per stage; barrier wait is the time between an item’s completion in one
stage and its start in the next.

| Measure | Count | p50 | p90 | Max | Mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| Chain running | 16 | 31.7 min | 39.6 min | 40.7 min | 32.0 min |
| Barrier wait | n/a | n/a | n/a | n/a | n/a |
| Chain span | 16 | 31.7 min | 39.6 min | 40.7 min | 32.0 min |

### Per Stage

| Measure | Count | p50 | p90 | Max | Mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| `assess-item` item | 16 | 31.7 min | 39.6 min | 40.7 min | 32.0 min |
| `assess-item` / `judge-fit-gemini-flash-36` | 16 | 12.7 min | 29.4 min | 39.8 min | 15.4 min |
| `assess-item` / `judge-fit-gemini-flash-38` | 16 | 30.4 min | 39.5 min | 40.6 min | 26.1 min |

### Slowest Items

| Item | Chain running | Barrier wait | Chain span | Slowest step |
| --- | ---: | ---: | ---: | --- |
| `ITEM-11` | 40.7 min | n/a | 40.7 min | `assess-item` / `judge-fit-gemini-flash-38` (40.6 min) |

## Steps

3 step types across 17 scopes.
Agent steps total 39,827.0 s; code steps total 5,651.6 s. A mapped step contributes one
sample per item.

| Step | Process | Mode | Count | p50 | p90 | Max | Total |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `assess-item` | `example-roster-fit` | composite | 16 | 1,901.0 s | 2,373.5 s | 2,440.0 s | 30,751.0 s |
| `judge-fit-gemini-flash-38` | `example-roster-fit-item` | agent | 16 | 1,821.5 s | 2,371.1 s | 2,437.8 s | 25,045.7 s |
| `judge-fit-gemini-flash-36` | `example-roster-fit-item` | agent | 16 | 759.1 s | 1,766.2 s | 2,385.8 s | 14,781.3 s |

## Parallelism

Peak 16 and time-weighted mean 4.0 running pooled tasks against a ceiling of 40; 54.0%
of 326 health samples were at the current cap and 0.0% at the ceiling.

| Pool stream | Ceiling | Starts | Peak | Mean | Cap range | Samples | At cap | At ceiling |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `.logs/runpool/events.jsonl` | 40 | 57 | 16 | 4.0 | 1 to 40 | 326 | 54.0% | 0.0% |

## Retries

120 attempt records (child_step 86, root_item 32, root_step 2); 0 without a disposition;
32 other `attempt.yaml` files; 0 unreadable.
Attempts that wrote nothing: n/a.

By disposition: permanent 19, retryable 13, succeeded 88.

By failure class: timeout 16, unknown 16.

| Step | Attempts | Not succeeded | Failure classes |
| --- | ---: | ---: | --- |
| `assess-item` | 32 | 19 | timeout 3, unknown 16 |
| `judge-fit-gemini-flash-38` | 23 | 10 | timeout 10 |
| `judge-fit-gemini-flash-36` | 34 | 3 | timeout 3 |

## Agents

| Figure | Value |
| --- | ---: |
| Transcripts | 57 |
| Transcripts without a terminal result | 13 |
| Provider time | 154.6 min (9,277.0 s) |
| Requested models | gemini-3.6-flash 34, gemini-3.8-flash 23 |
| Served models | gemini-3-flash-preview 44, gemini-3.6-flash 31, gemini-3.8-flash 13 |
| Invocations missing the requested model | 0 |
| Input tokens | 3,125,972 |
| Output tokens | 381,368 |
| Cache read tokens | 4,749,357 |
| Tool calls | 301 |
| Tool results at the 16 MiB cap | 0 |

| Meter | Coverage | Quantity |
| --- | --- | ---: |
| `google/gemini-cli/agent_invocations/count` | measured | 44 |
| `google/gemini-cli/api_requests/count` | unmeasured | n/a |
| `google/gemini-cli/model_turns/count` | unmeasured | n/a |

## Resources

| Figure | Value |
| --- | ---: |
| List cost | USD 4.92 |
| Actual cost | n/a |
| CPU average | 82.7% |
| CPU peak | 107.1% |
| Peak RSS | 0.83 GiB |
| Swap peak | 4.61 GB |
| Swap growth over the run | 2.17 GB |
| Peak swap growth rate | 5.26 GB/min |
| Minimum free disk | 291.22 GB |
| Run size on disk | 9.6 MiB |
| Run files | 679 |
| Health samples | 326 |
| Resource finalization | failed |

## Unavailable Figures

| Section | Figure | Reason |
| --- | --- | --- |
| run | `variant` | run config records no variant |
| run | `execution_profile` | run config records no execution profile |
| setup | `elapsed_s` | a setup step has no elapsed time |
| setup | `share_of_elapsed` | a setup step has no elapsed time |
| items | `barrier_wait_s` | no item passed between two mapped stages |
| retries | `wrote_nothing` | no failed attempt recorded output failures |
| resources | `actual_cost_usd` | resource summary reports it unmeasured |
