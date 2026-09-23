---
type: is
id: is-01kzkzaqdvdzbq3294v12q4zq0
title: "Review R1: Cover unavailable distribution metadata for --version"
kind: task
status: closed
priority: 3
version: 3
spec_path: docs/releases/v0.2.1.md
labels: []
dependencies: []
parent_id: is-01kzkxx9rxha6mkaswemn192sb
created_at: 2026-08-09T19:17:25.050Z
updated_at: 2026-08-09T19:19:19.379Z
closed_at: 2026-08-09T19:19:19.378Z
close_reason: Added and verified the PackageNotFoundError fallback test; final make verify passed with 3,946 tests and 8 expected skips.
---
Senior release review found that the intentional PackageNotFoundError fallback in the new CLI version callback lacks a failure-path test. Add a focused test proving it exits successfully and reports unknown without running command bootstrap.
