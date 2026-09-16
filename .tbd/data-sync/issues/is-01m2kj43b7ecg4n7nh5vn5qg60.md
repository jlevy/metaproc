---
type: is
id: is-01m2kj43b7ecg4n7nh5vn5qg60
title: "PR #83 review P1-R4: parallelism section emits one PoolRow per agent step stream"
kind: bug
status: closed
priority: 2
version: 2
labels: []
dependencies: []
parent_id: is-01m2kj39h261sajhsakeg5hhh1
created_at: 2026-09-15T22:13:41.862Z
updated_at: 2026-09-16T00:17:02.675Z
closed_at: 2026-09-16T00:17:02.673Z
close_reason: "Fixed in 381f378: only streams with a pool_start or process_start get a PoolRow, the rest are counted in empty_streams, and streams come from the kept scopes. Tests: test_parallelism_reports_pools_and_counts_streams_that_started_nothing, test_a_wide_root_folds_to_one_pool_row_in_bounded_time_and_size."
resolution: null
duplicate_of: null
---
PR #83 part 1 R4 (Medium). _parallelism_figures emits a PoolRow for every RunPool event stream, including each scalar step's admission stream (45 of 46 rows empty on a 3-cohort root). Keep rows for streams with pool_start or process_start, count the rest, discover streams from evidence.scopes. engine/operations_summary.py:853-858, runpool/log_files.py:15-28.
