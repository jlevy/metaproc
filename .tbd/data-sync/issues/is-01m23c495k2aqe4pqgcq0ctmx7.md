---
type: is
id: is-01m23c495k2aqe4pqgcq0ctmx7
title: "Amend the v0.4.0 release notes: 23 undocumented changes, 7 misdescriptions"
kind: bug
status: closed
priority: 1
version: 2
labels:
  - docs
  - release
dependencies: []
created_at: 2026-09-09T15:21:05.459Z
updated_at: 2026-09-09T16:07:15.861Z
closed_at: 2026-09-09T16:07:15.860Z
close_reason: |-
  Corrected in both surfaces. docs/project/releases/v0.4.0.md amended at eee0f69, and the GitHub release body at https://github.com/jlevy/metaproc/releases/tag/v0.4.0 regenerated from it via gh release edit; the published body is byte-identical to the file apart from a trailing newline GitHub appends. The v0.4.0 tag still points at 2a0aade and no artifact was touched, since PyPI is immutable and nothing about the code changed.

  Added: the Gemini respectGitIgnore posture (also added to CHANGELOG), status --remote and bare run-id auto-detection removal (added to the Compatibility enumeration so the list is complete), status --check now failing runs whose items all completed, workstation Filestore alias resume removal, gcp run --job-name format validation, the uv baked-environment pinning for no-workspace runs, the Pi npm scope move to @earendil-works, the batch_backend secret API removal, a consolidated runtime-artifact entry covering run-plan.yaml, accepted-anomalies.yaml, TaskOutputProjection, result.yaml attempt_id and the four moved schema tokens, METAPROC_GCP_SECRET_REFS_JSON, and a smaller-surface entry for pool rollup, TraceExtractor scope_local, the /bin/sh wrapper, check-handlers/check-headers variable expansion, GCS upload policy, result_contract_failed, and the httpx2 dev dependency drop.

  Corrected: the '3.6 profile is unchanged' claim, which this document contradicted 100 lines later and which the CHANGELOG repeated; the overstated scope of the Gemini profile re-sizing, which left pi-gemini-flash and pi-gemini-pro untouched; the unconditional runtime service account requirement, which is conditional on a job binding a secret reference; the Ctrl-C versus SIGTERM split, since Ctrl-C keeps a hard reap as a backstop; the launch-refusal enumeration, which named two of four classes; the documentation paths bullet, which omitted two shipped-document renames including one that shipped in v0.3.0; and the 'without writing an additional runtime ledger' phrasing, which read as a promise that no new artifact appeared while run-plan.yaml is new.

  The public-hygiene gate rejected the first draft for a home-directory path in the Filestore example, now a placeholder. lint-check clean, 4,613 passed with 8 skipped.

  mp-x1qh stays open deliberately: it asks whether respectGitIgnore: false should remain the default, which disclosure does not settle.
resolution: null
duplicate_of: null
---
The v0.4.0 release notes were written from the CHANGELOG, PR bodies and bead descriptions rather than from the diff. A post-publication review of all 26 merged PRs (134 commits, 316 files) found the notes materially incomplete: roughly 23 undocumented user-visible changes and 7 misdescriptions across four review clusters, with a fifth surface audit outstanding.

PyPI 0.4.0 is immutable, so the remedy is amending the notes in both places they live: docs/project/releases/v0.4.0.md and the GitHub release body at https://github.com/jlevy/metaproc/releases/tag/v0.4.0.

MUST FIX — security posture, undisclosed:
- Gemini steps can read .gitignore'd files including .env. settings.py:320-324 ships context.fileFiltering.respectGitIgnore: False, new in v0.4.0. src/metaproc/docs/metaproc-design.md:1752-1756 states the consequence; the notes' two Gemini sections never mention it. Notes line 133-134 ("a workspace ignore rule can no longer block or distort prompt delivery") is about prompt delivery and is easy to misread as covering this.

MUST FIX — breaking or behavior-changing, undisclosed:
- status --remote removed (PR #38). The Compatibility removal list enumerates seven other flags, so omitting this reads as "it survived". Bare `metaproc status <run-id>` also stops resolving.
- status --check can now exit 1 for a run whose items all completed, when the process record is failed/cancelled (run_status.py:739-744). CI gates that passed will now fail.
- Workstation-mounted Filestore aliases no longer resume (run_process.py:1136-1149, prefix match replaced substring). Breaking; not in Compatibility.
- gcp run injects UV_PROJECT_ENVIRONMENT=/opt/venv and UV_NO_SYNC=1 into no-workspace runs (container_bootstrap.py:60-63, :484). Changes what user commands do in-container; "uv" appears nowhere in the notes.
- gcp run --job-name now rejects uppercase, underscores, leading digits, and >63 chars (gcp_run.py:155-159).
- Pi CLI npm package moved scope: @mariozechner/pi-coding-agent -> @earendil-works/pi-coding-agent (pi_cli.py:40-42). The notes give the version but not the rename, so a copied install line fails.
- batch_backend secret API removed: GCP_SECRET_REFS, resolve_gcp_secret_ref, GCPBatchConfig.secret_env_vars. Python import break; the notes document a smaller break of the same kind (adapter module renames).

SHOULD FIX — artifact and schema surface, undisclosed:
- VizModel 0.2 -> 0.4 (skipping 0.3), StepDetails 0.1 -> 0.2, ProcessHeader 0.1 -> 0.2 (models/viz.py:388, :136, :210). Consumers pinning on these tokens break.
- New per-run artifact <scope>/.state/run-plan.yaml (paths.py:152-153), schema-enforced. Notes line 66-67 say hydrated runs work "without writing an additional runtime ledger", which reads as reassurance none appeared.
- accepted-anomalies.yaml (paths.py:107) and metaproc:TaskAttemptAnomalies/0.1 described but never named.
- ResultRecord gains attempt_id (runtime.py:241).
- status --json gains process_execution_state and process_error; status text gains FAILED/CANCELLED labels and a Failure: line.
- pool rollup emits an extra run-owned-pool row labelled "." (pool.py:1190-1200).
- TraceExtractor plugin protocol gains scope_local; third-party extractors silently skip composite child scopes without it.
- Two shipped-doc renames omitted: metaproc-concepts-and-principles.md -> metaproc-concepts.md (already shipped at v0.3.0, so a packaged-path reference breaks) and docs/process-framework-concepts.md -> src/metaproc/docs/process-framework-theory.md.
- METAPROC_GCP_SECRET_REFS_JSON added as a reserved dispatcher env var.
- GCS upload policy: 16 MiB chunks, 120s per-request timeout, 10-minute retry deadline (dispatch_artifacts.py:53-55).
- Gemini launched under /bin/sh -c; /bin/sh is now a hard requirement for that adapter (adapters/gemini.py:218-228).
- check-handlers and check-headers now expand process vars in dep paths; specs that used to FAIL now pass.

MISDESCRIPTIONS to correct:
- "the 3.6 profile is unchanged" (line 95) is contradicted by the same document at line 320-323: gemini-flash-36 resources went 500MB/0.25 -> 250MB/0.5.
- "The shipped Gemini execution profiles" overstates: pi-gemini-flash and pi-gemini-pro still carry 500MB/0.25.
- The exit-2 Compatibility bullet names two refusal classes; there are at least four (execution profiles, unknown adapter override, multi-producer path conflict).
- "require an explicit runtime service account" is unconditional in the notes but conditional in code (secret_hydration.py:62-68, `if refs and not service_account_email`). A secret-free job still runs as the default compute SA.
- "Ctrl-C follows cooperative asyncio cancellation while SIGTERM retains the hard descendant reaper" — cli.py:160-163 hard-reaps on Ctrl-C too, as a backstop.

Full per-cluster findings with file:line are in the session transcript. Also worth deciding: whether the multi-producer plan-time rejection belongs in Compatibility rather than Fixes, since a spec that planned on v0.3.0 now exits 2.
