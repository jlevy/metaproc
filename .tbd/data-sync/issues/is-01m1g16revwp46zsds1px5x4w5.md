---
type: is
id: is-01m1g16revwp46zsds1px5x4w5
title: Windows provider capability record for Safeproc
kind: task
status: open
priority: 3
version: 1
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies: []
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-09-02T03:04:38.106Z
updated_at: 2026-09-02T03:04:38.106Z
---
Write the Windows capability record: Job Objects (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_JOB_MEMORY, QueryInformationJobObject), atomic placement via CreateProcess CREATE_SUSPENDED + AssignProcessToJobObject + ResumeThread or PROC_THREAD_ATTRIBUTE_JOB_LIST, identity via PID + GetProcessTimes, host budget via GlobalMemoryStatusEx, process cost via GetProcessMemoryInfo PrivateUsage, degradation via commit charge, no safe pause primitive, exit via process-handle wait. Deliverable is a capability record and a provider design, not an implementation. Origin: review F5 of pull request 62. After mp-3i22.
