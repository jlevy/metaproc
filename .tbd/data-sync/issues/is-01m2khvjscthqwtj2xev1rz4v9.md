---
type: is
id: is-01m2khvjscthqwtj2xev1rz4v9
title: "PR #79 review S5: run-process code logs are named by UTC second instead of attempt id"
kind: task
status: closed
priority: 3
version: 2
labels: []
dependencies: []
parent_id: is-01m2khtp39hxxwknrtk7gzqpfs
created_at: 2026-09-15T22:09:02.762Z
updated_at: 2026-09-15T22:56:38.802Z
closed_at: 2026-09-15T22:56:38.799Z
close_reason: "Done in d5af5ee: run-process code logs named process_<attempt_id>.log. Test: test_run_process_code_attempts_in_the_same_second_keep_their_own_logs."
resolution: null
duplicate_of: null
---
PR #79 (https://github.com/jlevy/metaproc/pull/79#issuecomment-5688716379), Suggestion. _execute_code_step names its log process_<UTC seconds>.log while run-step and run-parallel use process_<attempt_id>.log; two run-process attempts inside one second overwrite the file the durable error points at. Use the attempt id in all three.
