# Changelog

All notable user-facing changes are recorded here.

This project uses [Semantic Versioning](https://semver.org/) while it is in the 0.x
development series.

## [Unreleased][unreleased]

### Added

- **`Quantity`, a reported value that carries how well it is known.** `Metrics` fields
  are nullable with the `None`-versus-`0` distinction carried only in a docstring, so a
  reader cannot tell “no data” from “not instrumented” from “this step runs no agent”.
  Those are three findings, one null, and only one of them is worth acting on.
  Worse, a gap that reaches a table or a chart as `0` is indistinguishable from a real
  measurement and reads as *instantaneous* or *free*.

  `Quantity` carries a value with its `coverage`, the `reason` it is not measured, and
  the `sample_count` the gap covers, and it refuses the combinations that would lose
  that: a gap carrying a value, a gap without a reason, a measured quantity without a
  value. `CoverageState` gains `not_applicable` to name the third case, which is a
  question that does not arise rather than an answer nobody collected.
  `MeteredQuantity` already had this discipline for provider usage meters, which are
  keyed by provider/product/meter/unit; `Quantity` covers everything else a summary
  reports, such as a wall time, a request count or a concurrency figure.

  This is groundwork, and it changes no artifact.
  Nothing in Metaproc emits a `Quantity` yet, `ResourceUsageSummary.totals` is still
  `Metrics`, and `resource-usage-summary.md` still renders an absent value as
  `unmeasured`. The type is public so a consumer can build on the vocabulary before the
  summary that will use it lands.

### Changed

- **Provider meters accept only the coverage states they can reconcile.**
  `MeteredQuantity.coverage` and `MeterRollup.coverage` are now `MeterCoverage`, which
  is `CoverageState` without `not_applicable`. Nothing could ever produce that state for
  a meter, since a meter is identified by its key and one that does not apply is one
  nobody emits, while `MeterRollup` derives coverage from the evidence it reconciled and
  meter aggregation counts anything neither measured nor estimated as an unmeasured
  event. Admitting it would have reported an inapplicable meter as a gap worth chasing,
  which is the confusion `Quantity` exists to prevent.

  The compiled `resource-usage-summary.v1` schema spells `provider_meters[].coverage` as
  an inline enum instead of referencing a shared `$defs` entry.
  The accepted values are unchanged.

## [0.4.1][] - 2026-09-10

### Fixed

- **An explicit model selection is preserved, or refused — never quietly replaced.**
  Every adapter previously validated the configured model against its own allowlist and,
  on a miss, logged a warning and passed its default instead, so a step pinned to one
  model ran on another and the run’s cost and quality were attributed to a name that
  never served it. `claude-code-cli`, `codex-cli`, `gemini-cli`, and `pi-cli` now route
  selection through one reviewed catalog: an omitted selection takes the adapter
  default, and an explicit one is either passed through exactly as written or rejected
  with an error naming the adapter and the identifier.
  `metaproc auth check` reports such a rejection as a failed check line rather than
  aborting the probe.

- **RunPool controller, health, and quota evidence survives the typed reader.** The pool
  event log already recorded the concurrency controller’s memory, provider, and operator
  ceilings, its effective target and bottleneck, active RSS and log-byte totals,
  periodic `health_sample` records, and the `quota_pause_started` / `quota_pause_tick` /
  `quota_pause_resumed` sequence, but the typed event models narrowed the extra fields
  away on read and had no model at all for the health and quota-pause records.
  A consumer reading the typed stream saw a thinner run than the one on disk and could
  not distinguish a pool waiting on a provider quota from one that had gone quiet.
  The models now retain every field the logger emits.

- **Cleanup and admission accounting survive a bookkeeping failure.** An exception while
  recording a started child no longer skips the cleanup obligations a poll failure
  already honored, so admission stays held until the child is reaped rather than being
  released under a running process.
  A failure writing `host_slot_acquired` releases the lease the caller never received
  instead of leaking a slot.
  A shutdown that fails while the pool is unwinding a caller’s exception now raises the
  original error with the shutdown failure attached as a note rather than replacing it.
  Quota-pause timers are cancelled and drained during shutdown, and the event and health
  loggers are closed even when the shutdown record fails to write.

### Added

- **A reviewed model catalog with dated evidence.** `metaproc.config.model_catalog`
  centralizes accepted identifiers, per-adapter defaults, the source URLs each entry was
  reviewed against, a review date and interval, and lifecycle notes recording retirement
  dates and account-dependent routes.
  No adapter default changed and nothing was removed from any accepted set.
  `claude-code-cli` gains `claude-fable-5-1`, `claude-fable-5`, `claude-opus-5`,
  `claude-sonnet-5`, `claude-opus-4-8`, and the `fable` alias; `codex-cli` gains
  `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.6`, and
  `gpt-5.3-codex-spark`; `gemini-cli` gains `gemini-3.5-flash-lite`; and `pi-cli` gains
  the new Anthropic and OpenAI identifiers plus Gemini 3.8, 3.7, 3.6 Flash and 3.5
  Flash-Lite. `claude-fable-5-1` is accepted by `claude-code-cli` only.
  Acceptance means Metaproc preserves an identifier, not that an account, client, or API
  can serve it.

- **Pi model acceptance derives from the packaged provider catalog.** Accepted `pi-cli`
  identifiers now come from `pi-models.default.json` plus a small retained set of native
  selections, so adding a provider entry no longer requires a second, separately
  maintained allowlist edit that could silently disagree with it.
  Vertex MaaS entries resolve under both publisher-qualified and short IDs.

- **Four more Codex reasoning-effort values.** `none`, `xhigh`, `max`, and `ultra` are
  the remaining named values in the pinned codex-cli 0.147.0 parser and join `minimal`,
  `low`, `medium`, and `high`. Accepting the syntax does not mean every model supports
  every effort.

- **Pressure checks carry their hold hysteresis.** Every `pressure_check` record now
  includes `consecutive_normal` and `consecutive_elevated`.

- **Refreshed provider data.** `pi-models.default.json` adds the Gemini Vertex 3.8, 3.7,
  3.6 Flash and 3.5 Flash-Lite entries and the GPT-6 Astra and GPT-5.6 Sol, Terra, and
  Luna entries, the latter using the per-model `openai-responses` override the pinned Pi
  client supports. Vertex Gemini entries accepting image input are marked as such, and
  the `gemini-3.1-pro-preview` context window is corrected to 1,048,576 tokens.
  Entries whose exact identity this review could not establish in primary documentation
  say so. `pricing.md` is refreshed against the same sources.

### Changed

- **An unknown model name now fails the step instead of running the default.** A process
  spec, execution profile, or adapter map naming an identifier Metaproc does not
  recognize raises `unknown <adapter> model <name>` at command construction.
  Such a run was already not doing what its spec said; the failure is the disclosure.
  Check pinned model names against the accepted sets before upgrading, and extend
  `metaproc.config.model_catalog` for an identifier it does not yet carry.
  An empty-string model is treated as an explicit selection and rejected on the same
  grounds; omit the field to take the default.

- **The Agent Skill’s debugging guidance is reversed where it was wrong.** The skill
  previously told an agent never to inspect a run with ad hoc shell commands over the
  run directory, `.state/`, or `.logs/`. That holds for run state and lifecycle and not
  for debugging, since some captures merge stdout and stderr or filter native events, so
  a rollup or extracted trace can omit evidence only the captured log holds.
  The skill keeps the strong rule for state and lifecycle while directing an agent to
  read the relevant attempt’s captured output and native transcript directly when
  diagnosing a failure, report the cause with its evidence location, and say what is
  missing when the logs do not contain it.
  The committed `.agents/` and `.claude/` copies are regenerated.

### Documentation

- `metaproc help operator` gains **Direct Agent Debugging**, **Two Concurrent Runs on
  One Host** (a shared disk-backed slot gate, each run bringing its own limit, admission
  failing open, and `METAPROC_HOST_MAX_LOCAL_AGENTS` composing by minimum so it can only
  lower a cap), **Reading Pool Health**, **The Subprocess Count Is a Display Estimate**,
  and **A Green Run Is Not a Reviewed Run**.
- `metaproc help design` gains §13 “Cause Preservation at Aggregation Boundaries”: no
  aggregation boundary may discard a cause it received.
  Three current gaps are named so they are not rediscovered as novel.
  §11 now documents the fan-in manifest’s actual emitted field set, which omits the
  artifact path and rendered message a consumer may have assumed were present.
- The runpool and execution documents are aligned with the admission, pressure, retry,
  artifact, and cause-preservation contracts as they behave, and the scalar-admission
  description now matches where that gate sits for mapped as well as scalar leaves.
- The shipped-document freshness gate compares Git author timestamps in UTC, so a commit
  authored west of UTC late in the day no longer fails a document whose header is
  correct. The UTC header convention is written down alongside the check.

## [0.4.0][] - 2026-09-09

### Documentation

- The core documentation set now ships inside the package.
  `metaproc help` serves 17 topics instead of 3, covering the design doc, the
  conventions, the artifact catalog, the general framework model, the credential and
  cloud-dispatch runbooks, and all seven architecture documents, so a consumer reading
  from an installed wheel has the same documentation as a reader of the repository.
  `metaproc help` with no topic lists them in reading order with approximate sizes.
- `metaproc help developer` and the Agent Skill catalog list the full topic set.
  Regenerate committed skill copies with `metaproc skill metaproc --install`.
- Documentation paths moved: `docs/arch/` is gone, its contents now in
  `src/metaproc/docs/`; `arch-metaproc-core.md` is `metaproc-design.md`;
  `docs/releases/` is `docs/project/releases/`. Project-internal material (revision
  histories, future-work backlogs) moved under `docs/project/design/`.
- New `devtools/check_shipped_links.py` gate: a relative link in a shipped document must
  resolve inside `src/metaproc/`, so links that are valid in a checkout but dead in the
  wheel fail CI.
- New `devtools/check_doc_dates.py` gate: a shipped document’s `last updated` date must
  not be older than its most recent substantive commit.
  Reflows are ignored, so `make format` does not invalidate every date at once.

These documentation changes altered no runtime behavior, artifact shape, or CLI flag.

### Added

- **Mapped composite scopes**: a `mode: composite` step may now declare `for_each` and
  run one child process scope per item in-process under `<run>/<step>/<item-key>/`. All
  scopes share the parent run execution context and executable-leaf ceiling; each parent
  item records durable status, attempt history, validated outputs, and a result.
  Each scope binds one canonical identity to its run directory, template variables, task
  state, and child event stream, so a fixed child output path remains isolated per item.
  Dot-only item keys are rejected before path construction.
  Scope evaluation defaults to a bounded concurrency of 32 and waits for siblings to
  finish after an ordinary item exception.
  Cancellation terminally records the affected parent attempt, and mapped items emit
  start, completion, and failure events.
  The first implementation is single-host: `gcp-worker` partitioning and whole-scope
  `for_each.retry` are rejected, while retries remain available on child leaves.
  Unsupported mapped-worker topology is rejected before any DAG step or cloud dispatch
  starts.

- **Gemini 3.7 and 3.8 Flash**: `gemini-3.7-flash` and `gemini-3.8-flash` join the
  validated model set, and a `gemini-flash-38` execution profile ships beside the
  existing 3.6 one. The 3.6 profile still pins `gemini-3.6-flash`, so a profile name that
  says 36 keeps meaning 3.6, though its lane sizing moved with the other gemini-cli
  profiles. All three models sit in Google’s short-term-availability class, which retires
  a model 45 days after its replacement ships; 3.8 is the newest and therefore the
  longest-lived of the three.

### Fixed

- **A bare resume no longer re-invokes completed code fan-out items**: a `mode: code`
  step with `for_each` that is not part of an item-aligned chain received every
  non-terminal item from discovery, completed ones included, and invoked all of them.
  Resuming such a step with the same `RUN_ID` therefore re-ran every handler that had
  already succeeded and recorded a fresh attempt for each, and an item already running
  under a live sibling could be invoked concurrently.
  The step now reuses an item whose task record is `completed` or `cached` and whose
  declared output still validates, which is the same guard the aligned-chain executor
  already applied per item; both paths now share one implementation.
  Reuse is earned by a valid artifact rather than by a status file alone, so an item
  whose output has since been removed still reruns, and `--force` is unaffected.
  The step’s progress line now reports reused items instead of counting them as
  actionable.

- **A Gemini step is answered by the model it asked for**: gemini-cli rewrites any model
  id ending in `flash` that it does not recognize to its own default before the request
  leaves the process, and under Vertex authentication that default is
  `gemini-3.5-flash`. A step pinned to `gemini-3.6-flash` was answered by 3.5 with no
  error and no warning, and the CLI’s own `stats.models` named the substitute.
  Two changes close it.
  The adapter now asserts gemini-cli’s dynamic model-resolution setting beneath its
  native-settings defaults, which passes an unrecognized id through untouched, so the
  requested model reaches the provider.
  And a successful agent result whose terminal `stats.models` omits the requested model
  is now rejected for both scalar and fan-out agent steps, before valid-output rescue,
  so a substitution that happens anyway fails loudly instead of being recorded as a
  success by the wrong model.
  This is upstream `google-gemini/gemini-cli#28859`, unfixed at 0.55.1 and later, so
  upgrading the CLI is not a way out.

- **`metaproc browse` renders Metaproc kinds under browser SDK 0.5**: the Markdown and
  agent-log Metabrowser kinds are now loaded explicitly before Metaproc registers views
  that embed their renderers.
  SDK 0.5 stopped eagerly loading renderer namespaces for unrelated kinds, so a view
  that borrows one had to ask for it.

- **A refused launch reports every class of problem at once**: `run-process`,
  `run-step`, `plan`, and `deps` now collect unresolved template placeholders, unset
  operator parameters, missing input files, and unusable execution profiles together and
  report them as one grouped message, instead of raising on the first non-empty class.
  Bringing up a cohort took four launches to learn about four problems, and `--dry-run`
  reported the same single class.
  Process-input errors are additionally tagged as a parameter or a file, because “pass a
  `--var`” and “produce a file” are different fixes.
  Per-item messages are unchanged and a launch with one class of problem reads exactly
  as it did.

- **A fail-fast stop no longer names a flag nobody passed**: a step failure reported
  `Step '<id>' failed (--no-continue-on-error set)` unconditionally, including when the
  policy came from a default.
  The message now states the policy in force rather than asserting how it was set, and
  names `continue-on-step-failure` as well when a scope is what stopped.

- **Gemini no longer scans its whole project history at startup**: Gemini CLI runs
  session-retention cleanup as an un-awaited background task during startup.
  Before it knows which sessions are expired, cleanup enumerates the project’s saved
  conversations and parses every one of them through a single unbounded `Promise.all`.
  On an accumulated project bucket that turns a roughly 0.4 GB process tree into 5 GB or
  more for the same short prompt, which is enough to destabilize a host running several
  agents at once. The adapter now ships `general.sessionRetention.enabled: false` in its
  native settings, which makes cleanup return before it enumerates anything.
  The guard is Gemini-specific: matched probes of Claude Code, Codex CLI, and Pi under
  the same conditions found no comparable startup cost, so no equivalent setting ships
  for those adapters. Gemini native settings are also merged rather than replaced, so a
  profile-supplied `native_settings` block cannot silently drop a host-safety default it
  never mentioned. Two consequences worth knowing: Gemini no longer prunes its own
  `chats/` and `tool-outputs/` directories, so bounded retention becomes an external
  operation; and `native_settings: null` no longer suppresses settings injection
  entirely, because the retention guard is re-asserted beneath the defaults.
  An operator who deliberately sets the key still wins.

- **Gemini steps can read files excluded by ignore rules**: the adapter now ships
  `context.fileFiltering.respectGitIgnore: false` in its Gemini native settings, so the
  agent’s own file tools read ignored files anywhere in the workspace rather than only
  declared runtime inputs.
  Treat the workspace, including files such as `.env`, as readable by a Gemini step, and
  keep material an agent should not read out of the process directory rather than
  relying on an ignore rule.
  gemini-cli steps only; an operator can restore the previous behavior through
  `native_settings`.

- **The Pi adapter honors `no_session_persistence` instead of ignoring it**: `pi-cli`
  received `--no-session` unconditionally, so the key was accepted by the allow-list and
  never consulted, and a spec asking for session persistence was silently overridden.
  The default is unchanged and still stateless; only an explicit `false` now keeps
  sessions, matching how the Claude adapter already treats the same key.
  No built-in Pi profile sets `false`, so no shipped profile changes behavior.

- **The Gemini adapter no longer accepts `no_session_persistence`**: the key was in the
  allow-list but was never read, so a process spec could ask for session isolation and
  silently not get it.
  Gemini CLI has no headless flag that disables session recording, so the key is now
  rejected with an explanation rather than accepted as a no-op.
  No built-in Gemini profile set it.
  The equivalent no-op in the Pi adapter is tracked separately and unchanged here.

- **An agent’s own verdict outranks its exit code**: some adapters write every declared
  output, emit a terminal success record, and then exit nonzero while shutting down.
  A scalar agent step whose agent reported success and whose declared outputs all
  validate now completes instead of failing, and the exit code is retained as an
  accepted anomaly as attempt-owned, versioned evidence rather than discarding it.
  The in-memory attempt projection exposes that evidence through `anomalies`, while the
  existing strict `metaproc:TaskAttemptRecord/0.1` payload stays readable by prior
  releases. Readers accept the short-lived pre-release inline form so those runs remain
  usable. The override is narrow by design: it never applies to a step with no declared
  outputs, to an agent that did not claim success, or to a process the supervising
  RunPool killed. Startup banners are also no longer mistaken for failure causes — a
  terminal `result` record is consulted before the last-non-JSON-line fallback, and that
  fallback now only considers lines written after the last structured record, so a
  terminal-capability notice printed at startup can no longer be reported as the reason
  a step failed.

- **Agent transcripts are free of terminal styling**: all four agent launch paths, and
  the probe paths that launch an adapter CLI and parse its output, now seed the child
  environment with `NO_COLOR`, `FORCE_COLOR=0`, `CLICOLOR`/`CLICOLOR_FORCE` off, and
  `TERM=dumb`. A step’s authored `env` block is still applied afterward, so an explicit
  override remains possible.
  ANSI sequences in a JSONL transcript have no reader and were corrupting failure
  classification and tool-use probes.

- **A raw path an upstream step writes is execution state, not authored input**:
  `produced_refs` now covers a raw `prompt_paths` or `uses` entry whose exact path a
  step this one depends on declares as an output, so its bytes leave the step
  fingerprint the way a dep-ref’s already did and the plan can be published before the
  run produces the file.
  The match is keyed through `normalize_path_key`, so a doubled slash or a `./` segment
  no longer decides the question.
  Only transitive dependency ancestors count: a step that declares the same output path
  while running independently or downstream supplies nothing to the reader, and a path
  two upstream steps both declare is refused at plan time rather than resolved
  arbitrarily.

- **Fan-out status totals come from the recorded plan**: `run-process` fan-outs have no
  `progress.md`, which belongs to the legacy `run-parallel` surface, so `status` treated
  the number of tasks observed so far as the total and reported `pending` as zero while
  dispatch was still in progress.
  It now reads the per-step item roster from the recorded run plan when no legacy items
  file exists.

- **The Gemini adapter honors a configured working directory**: `working_directory` is
  now an accepted config key and is returned to the launcher instead of `None`, so a
  Gemini step runs where its process configuration says it should.

- **GCP runs require explicit storage posture**: `metaproc gcp run` now rejects its
  default Filestore placement when `METAPROC_GCP_FILESTORE_SERVER` is unset, before
  uploading artifacts or dispatching a job.
  Callers that intentionally want ephemeral task storage must pass `--no-filestore`.

- **GCP dispatch artifacts are immutable**: `metaproc gcp run` validates its artifact
  identity before packaging and creates wheel and workspace objects only when their GCS
  names are unused, so a later dispatch cannot replace bytes referenced by an existing
  job. Re-running a dispatch that failed after uploading stays possible: an existing
  object with an identical digest is accepted as already uploaded, while one with
  different content fails with the object name and the instruction to pick a new
  `--job-name`.

- **Gemini prompt transport ignores workspace ignore rules**: the Gemini CLI adapter
  keeps its durable audit prompt while streaming those bytes through stdin, so an
  ignored prompt path cannot block or distort prompt delivery.

- **Static validation resolves defaulted process inputs**: handler and header checks now
  resolve declared process-input defaults instead of reporting false missing-input
  failures.

- **Visualization projections preserve authored and resolved fields**: `VizModel` now
  includes public process outputs, complete process-input and default declarations, and
  the resource, failure, execution-profile, namespace, and fan-out fields carried by a
  resolved plan. The MetaBrowser process and step panels render these fields, and
  projection tests enforce field parity with the authored and resolved source models.
  When a run directory is supplied, the same model includes a rebuildable view of
  scalar, mapped, and nested task records.
  A result is consumable only when it names the latest successful attempt, matches the
  current step fingerprint and declared output port, and resolves to an available
  artifact of the declared kind.
  Historical unbound, stale, missing, undeclared, and external outputs remain visible as
  diagnostics. Hydrated runs safely rebase portable paths to the local run root; no
  additional runtime ledger is written.

- **GCP credentials hydrate inside the container**: Batch job specifications now carry
  Secret Manager references rather than plaintext-expanded `secret_variables`, require
  an explicit runtime service account, reject registered credentials passed through
  `--env` or plugin bootstrap variables, and retry transient startup failures.
  Roll out the hydration-capable agent image before its dispatcher; a stale image cannot
  interpret the reference-only contract.

- **GCP artifact upload honors the selected project**: `gcp run` now passes its required
  `METAPROC_GCP_PROJECT` through wheel and workspace uploads, so service-account ADC
  without an embedded project ID can dispatch normally.

- **Fan-in failure propagation across dependency diamonds**: `require: finished` now
  tolerates failure only for affected direct dependencies collected with that policy.
  A separate success-requiring path from the same failure still blocks the consumer,
  while an unaffected required dependency does not erase the tolerant collection.

- **Resume rejects changed resolved inputs**: an existing run now compares the persisted
  resolved-variable mapping with the new launch before reusing task state.
  Only known equivalent Filestore aliases for `RUNS_DIR` normalize across local and
  cloud topology; mismatch errors list field names without exposing their values.

- **Duplicate fan-out keys fail before execution**: item discovery now rejects two
  source rows that resolve to the same `for_each.key` before either can write the shared
  task, log, output, or child-scope namespace.

- **Cloud authentication policy propagation**: `run-process --cloud` now carries the
  complete authentication-pool configuration as one typed value through orchestrator
  dispatch. Selection policy and future fields can no longer be silently dropped while
  neighboring authentication flags continue to reach the cloud job.

- **Filesystem status fails closed**: `status` and `pool status` reject a nonexistent
  local run directory instead of projecting an empty tree as complete or healthy.

- **Live ownership outranks carried terminal status**: a resumed run writes a fresh
  process projection when recursive evaluation begins, and `status`, `wait`, and
  completion checks no longer report a prior failure or cancellation as terminal while
  an orchestrator still owns the run.

- **Cloud identity and orchestrator admission remain explicit**: a mounted Filestore
  preserves attached-identity ADC precedence on persistent GCP hosts, and full-cloud
  dispatch now supplies its own `METAPROC_GCP_ORCHESTRATOR` admission marker instead of
  depending only on `BATCH_TASK_INDEX`.

- **Attempt finalization survives auth-pool teardown failure**: a credential teardown
  exception records the affected attempt as lost before propagating the operational
  error.

- **Terminal paths retain owned capacity until cleanup finishes**: local scalar agent
  launches now reuse the local launch backend and drain late launches before returning.
  On completion, timeout, or cancellation, agent and code-command process groups are
  terminated, stubborn descendants are killed, and log filters are flushed before run
  slots or host admission are released.
  Late credential leases are likewise torn down before credential capacity is released.
  The local backend now treats an explicit `PreparedLaunch.env` as the complete child
  environment, so credential variables scrubbed by an adapter cannot leak back in from
  the Metaproc process.
  Cleanup after an exited leader is fenced by process identity, cleanup failures are
  reported without replacing the command result or cancelling the remaining shutdown
  work. Ctrl-C follows cooperative asyncio cancellation; SIGTERM retains the hard
  descendant reaper for externally terminated orchestrators.
  Forced RunPool shutdown gives cancelled futures a terminal attempt and credential
  outcome, suppresses retry churn, and drains queued and late-launching submissions so
  work cannot start after the pool has closed.
  That drain is bounded; final status, events, health state, and log closure still run
  if backend cleanup wedges.
  Descendant tracking is pruned to live identities, late group members are discovered
  after leader exit, and sampled commands fence both leader and group identity before
  signalling. Active or failed work outranks carried cancellation when deriving process
  status, so a later partial run does not remain falsely cancelled.
  Long-running Python handlers can observe that request through
  `StepContext.cancel_requested()`.

### Changed

- **A scope answers to the same failure policy as the root**: `_orchestrate` walks
  topological levels, and on a step failure inside a scope it raised immediately instead
  of continuing the walk, so independent branches in that scope were never considered.
  In a mapped per-item scope, where each item is its own subgraph, one step failing
  discarded a sibling branch’s output for that item even though the sibling declared no
  dependency on it; the sibling was left `pending`, having never reached the blocked
  filter. A scope now continues past a failure exactly as the root does, blocking the
  failure’s true transitive dependents and leaving the rest to run, and still reports
  failed at the end. `--continue-on-step-failure` stays additive, so
  `--no-continue-on-error` still fails the root fast while scopes keep walking; only the
  default pair changes behavior.
  Two consequences: a failing scope now spends more, not less, because the independent
  branches it used to abandon will run; and steps that previously stayed `pending` after
  a sibling’s failure are now `completed` or `blocked`.

- **A refused launch and a failed run have different exit codes**: `run-process` and
  `run-step` now exit 2 when a launch is refused before any step runs: unresolved
  placeholders or failed process-input validation.
  This matches the documented validation exit code that `plan` and `deps` already used
  for the same two checks.
  A completed run still exits 0 and a step failure still exits 1. A retry script that
  treated any nonzero exit as a run failure will now see 2 for an invocation that never
  started.

- **Gemini lanes are sized from measured memory**: the shipped Gemini execution profiles
  estimate 250 MB per process with a 0.5 initial memory budget fraction, replacing the
  500 MB and 0.25 clean-state figures.
  With session retention off, a Gemini process measures 113 MB mean across a run tree
  and 187 MB on its own; the old estimate held cohorts at 7 of 20 lanes.
  Claude profiles are unchanged.

- **Agent CLI adapter module names**: the four source modules now follow one
  executable-based convention: `claude_cli.py`, `codex_cli.py`, `gemini_cli.py`, and
  `pi_cli.py`. Adapter type strings and adapter class names are unchanged.

- **Agent CLI runtime pins**: the exact adapter contracts now target Claude Code
  2.1.234, Codex 0.147.0, and Pi 0.84.2. Each was the newest stable release outside the
  14-day package cool-off at the time of review.
  Pi’s installation hint now uses the active `@earendil-works/pi-coding-agent` package
  scope.

- **Gemini CLI runtime pin**: the local adapter, agent image, and supply-chain policy
  now agree on Gemini CLI 0.55.1, the newest stable release outside the repository’s
  14-day package cool-off at the time of review.

- **Metabrowser 0.9 and browser plugin SDK 0.5**: the optional `browser` extra now
  requires `metabrowser==0.9.1`, and the bundled Metabrowser plugin targets browser SDK
  0.5 instead of 0.1. Plugin discovery refuses a manifest declaring the wrong SDK, so
  the two move together.
  Three contract changes were carried: navigation goes through
  `mb.navigation.open({ path })` rather than the removed `mb.openPath`; the Document
  view reuses the markdown built-in’s `mountRendered`, which is what `renderRendered`
  became; and plugin assets are no longer eager tags in the page — Metabrowser loads
  them per selected kind from a declared descriptor, preserving manifest script order.
  Metaproc’s views, kinds, data hooks, and sidekick routes are unchanged, and the
  `MetaprocDomainViews.openPath` re-export, which nothing consumed, is gone.

- **Composite output boundaries are enforced**: scalar and mapped composites now
  validate every declared child-process output before completing.
  Resume revalidates those child outputs even when the mapped parent publishes only a
  subset; a missing or invalid child output makes only that item actionable again.
  Existing composite specs with inaccurate output declarations must correct or remove
  those declarations.

- **SoftSchema 0.8 structural diagnostics**: require `softschema>=0.8.0,<0.9` and map
  structural failures to its stable error `code` instead of a JSON Schema engine
  keyword. Property-level failures now resolve to the affected field in Metaproc’s
  existing `location` value.
  SoftSchema 0.8 adds a `repair` subcommand and its supporting API upstream, and always
  reports a `repairs` list on a validate result; ordinary schema verdicts are unchanged.
  `metaproc softschema repair` is unaffected and keeps routing through Metaproc’s own
  YAML repair. Supported composed and conditional schemas can therefore use
  `status: enforced` without adding a Metaproc-specific schema layer.

- **Typed cloud authentication transport**: the internal `OrchestratorDispatchConfig`
  constructor now accepts one `AuthPoolFlags` value instead of separate
  authentication-policy fields.
  This keeps the operator-to-orchestrator and orchestrator-to-worker boundaries on the
  same transport shape.

- **One credential-pool lifecycle for scalar and fan-out agents**: non-fan-out agent
  steps now lease the configured pool label, apply the same credential scope and scrub
  rules, classify failures, walk fallback labels on retry, and emit the same
  `auth_lease_acquired` and `auth_outcome` evidence as RunPool items.
  Nested leaves bind slots and event join keys to their path-relative child scope, so
  credential material stays inside the logical run tree even when a run directory is
  symlinked to another volume.
  Composite fan-out slot paths and authentication-event `run_id` values now include that
  child scope; consumers should treat the field as a run-tree path scope rather than a
  root-only identifier.
  Blocking credential storage work runs through the run-owned executor.
  Scalar quota scans run only for the blocking `refuse` posture; admission failures
  before the first launch create no attempt, while exhaustion after a retry makes the
  existing task state terminal.
  Adapter mismatches emit an explicit warning, log record, and `auth_skipped` event
  before using ambient authentication, including on worker entrypoints.

- **One execution context across recursive scopes**: local `run-process` execution now
  shares one executable-leaf ceiling across fan-out pools, scalar steps, code work, and
  composite descendants.
  Synchronous handlers and code commands use the run-owned executor, while scalar agent
  processes reuse the local launch backend without blocking the event loop.
  `--force` reaches composite descendants while root step selectors remain root-scoped.
  Command-backed code steps at the same DAG level may now run concurrently and acquire
  the shared run ceiling; fan-out paths retain their step ceilings as well.
  The executor defaults to 32 workers and grows to an explicit higher run ceiling, so
  its implementation capacity never silently reduces that ceiling.
  In the initial single-profile topology, the context also lazily owns one adaptive
  RunPool. Scalar agent leaves in every mapped child submit prepared launches to that
  pool, which supplies shared pressure response, process-tree supervision, status, and
  events. The existing run leaf ceiling and host admission remain the hard run and
  cross-run boundaries; command-backed code work retains its existing supervised path.
  Commands share the process directory, so authored steps that mutate shared files,
  repositories, or lockfiles must declare per-item paths or provide their own
  synchronization.

- **Full-cloud GCP topology is now enforced**: launching `run-process` with
  `--backend gcp-worker` from an operator host now fails unless `--cloud` is also set,
  and direct non-dry `run-parallel --backend gcp-worker` execution requires the GCP
  Batch runtime marker.
  The bare backend form remains available to the inner GCP Batch orchestrator, and dry
  runs remain available for inspection.

### Removed

- **Persistent GCP gateway compatibility**: removed `gcp remote`, `gcp remote-run`,
  `gcp self-install`, remote status routing, workstation Filestore path aliases, and the
  `METAPROC_GATEWAY_HOST` and `METAPROC_GCP_FILESTORE_REMOTE_RUNS_DIR` environment
  variables. Batch-native status and logs remain available, and filesystem-oriented
  commands now require an explicit locally visible run directory.
- **Framework-owned run archiving**: removed `gcp archive`; consumers own durable run
  publication and retention.
- **Split-tree cloud compatibility**: removed `status --cloud-runs-dir`,
  `validate --cloud-runs-dir`, and `pool retry-missing`. Hydrated and full-cloud runs
  use one run tree for state, output validation, and recovery.

## [0.3.0][] - 2026-08-25

### Added

- **Durable per-attempt task history**: scalar and fan-out work managed by
  `run-process`, `run-parallel`, or waited `run-step` now writes a typed
  `metaproc:TaskAttemptRecord/0.1` before execution and finalizes it once with its
  disposition and failure class.
  Replay consumes the exact history when present and retains status-based compatibility
  for historical run trees.
  Attempt success waits for every attempt-owned validator, including the fan-out
  write-boundary check, and outputless tasks reach a durable terminal state.
  Process startup reconciles both attempt history and mutable task status: it closes
  attempts orphaned by a crash and rebuilds a missing terminal projection without
  disturbing work owned by a live step-scoped pool.
  Resume rejects status or attempt history addressed to another run, step, or item.

- **Item-aligned chains, fan-in collections, and declared retry**: process specs can now
  chain steps against the same fan-out item, collect fan-out results into a typed fan-in
  outcome, and declare retry policy in the spec.
  A resume enters a chain even when its head is already complete, rerunning the
  incomplete tasks and reusing the completed ones; `--force` remains the explicit
  operation for invalidating a step and its downstream work.

- **Actionable invalid-output retries**: agent steps append the latest structured
  validation failures to the next retry prompt, including output, failure kind, path,
  contract, invariant, location, and message.
  Fan-out and non-fan-out execution use the same feedback; transport failures never
  create or replace it.

- **Schema conform for agent-authored YAML**: a frontmatter scalar that YAML would
  resolve to the wrong type is requoted against the contract that is about to judge it,
  so a value genuinely named `1850` survives a `type: string` field.
  The contract’s own model decides what is wrong — only pydantic `string_type` errors
  are acted on — and the document’s own serializer decides how to rewrite it, so `1.10`,
  `007`, `1e3`, and `0x1F` keep their written form.
  A real type disagreement still fails.

- **Structured contract failure primitives**: validation failures now carry softschema’s
  `validator`, `path`, and `message` rather than a rendered sentence, plus a `kind` that
  subdivides `INVALID_OUTPUT` and adds `unreadable`. `validate_item_outputs_detailed`
  exposes them; `validate_item_outputs` keeps its existing string view.

- **Host admission for scalar launches, and RunPool as a library**: a `run-process`
  invocation with no `for_each` is now admitted through the same host gate as fan-out
  work, so several orchestrators on one machine account for each other instead of
  launching blind. Admission is deliberately best-effort: an unreachable gate or a wait
  timeout lets the launch proceed rather than failing a step that worked before
  admission existed. Enabled for the local backend.

- **GCP Batch dispatch hardening**: `metaproc gcp run` accepts repeatable
  `--workspace-package PATH` to install current-branch consumer packages, prints Batch
  state transitions while provisioning and executing, and emits an exact resource
  identity that `gcp status`, `gcp logs`, and `gcp cancel` can reattach to.
  Default workspace archives exclude top-level and vendored Metaproc source layouts, and
  safe in-repository symlinks are materialized as regular archive content while external
  links and directory-link cycles are rejected.

### Changed

- **Code-step outputs are no longer YAML-repaired**: `run-parallel`’s `mode: code`
  fan-out ran the frontmatter auto-repair pass over each item’s declared outputs before
  validating them, which `run-process`’s code path never did.
  Both code paths now leave the document alone, so a handler that emits unparsable
  frontmatter fails its item instead of being silently rewritten.
  Repair and conform stay scoped to agent-authored output.
  A process whose code handler was relying on the repair pass will start reporting
  `invalid_outputs`; fix the handler’s serializer rather than the artifact.

- **A Gemini CLI below the supported minimum is refused up front**: the adapter passes
  `--skip-trust`, which gemini-cli introduced in 0.40, so an older CLI failed every
  agent step partway through a run with an unexplained “Unknown arguments”.
  This was previously only a warning.
  The refusal reports the version found, the path it resolved, and the remedy; drift at
  or above the minimum stays a warning.
  A stale CLI shadowing the pinned binary on `PATH` now fails immediately instead of
  costing a whole run.

- **`cryptography` moves to 50.0.0**, retiring the audited advisory waiver for
  `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247`. No advisory waiver is active in this
  release.

- **Development toolchain**: this repository now pins uv 0.12.3 and Node 24.19.0, tracks
  the `simple-modern-uv` v0.5.0 template, and installs its pinned, checksum-verified
  toolchain at agent session start.
  This affects contributors, not consumers of the published package.

### Fixed

- **Retry classification no longer depends on an artifact’s filename**: the decision to
  retry a missing output or give up on a structural mismatch was recovered by
  substring-matching a rendered error sentence that contained the artifact’s name.
  Two declared outputs missing for the same transient reason could receive opposite
  verdicts because one was named for a schema manifest.
  The decision now reads the structured failure kind.

- **Representation-only validation failures**: `date`, `datetime`, `time`, `Decimal`,
  and `UUID` values are normalized to their serialized form before the structural pass,
  so a quoted and an unquoted YAML date stop disagreeing.
  Only types with an unambiguous serialized form convert.

- **Cloud log tailing**: Cloud Logging entry datetimes are normalized to RFC3339 before
  being used as tail watermarks, fixing a log tail that could fail mid-run.
  A blocking generic run now reuses and closes one Cloud Logging client instead of
  leaking a client per poll.

- **Wheel overrides preserve the image dependency closure**: a verified Metaproc wheel
  override installs with `--no-deps`, keeping the audited dependencies and per-package
  release-cutoff exceptions baked into the image, and nested `uv` commands stay on the
  baked environment after package installation.

## [0.2.1][] - 2026-08-09

### Added

- **Self-identifying typed IDs**: `metaproc.ids` now provides registered
  `prefix-payload` allocation, validation, deterministic derivation, timestamped child
  derivation, and read compatibility for published underscore-form identities.
- **Exact GCP run correlation**: orchestrator and worker jobs retain a readable run
  label and a collision-resistant exact-identity key.
  Cloud inventory recovers the exact run ID from structured job metadata and keeps
  colliding readable labels separate.
- **Ledger-backed resource observability**: normalized runtime and agent evidence now
  drives strict hierarchical reports, provider meters, coverage gaps, reporting-only
  budgets, terminal finalization, inactive-run recovery, and CLI and Metabrowser views.
- **Code-step telemetry**: handlers and child processes launched by `run-process`,
  `run-step`, and `run-parallel` contribute CPU, memory, and lifecycle evidence to the
  root run ledger.
- **Installed version option**: `metaproc --version` reports the distribution version.

### Changed

- **Default run IDs**: generated run IDs are compact, time-ordered `run-...` typed
  identities. Process and title remain metadata instead of identity components;
  `RUN_ID_TEMPLATE` remains available for explicitly configured legacy formats.
- **Cloud run lookup**: `gcp status`, `gcp logs`, and `gcp cancel` query the exact
  identity key and recover mixed-generation, unkeyed jobs only when their structured
  `RUN_ID` verifies as the same run.
  Fully legacy runs retain the readable-label fallback.
  Local-directory status reads the immutable identity from run config rather than a
  process-directory basename or sanitized job label.
  Exact typed run IDs are not constrained by the legacy 63-character label heuristic.
- **Resource document contract**: new `resources.json` files use the registered
  `metaproc:ResourcesDocument/0.1` token.
  Strict readers for historical `metaproc.resources/v1` and `metaproc.resources/v2`
  artifacts remain available.
- **SoftSchema dependency**: Metaproc now requires `softschema>=0.6.0,<0.7` and follows
  its document terminology.
  Consumer repositories control their own dependency-source resolution.
- **Portable Agent Skill and documentation map**: generated agent-specific skill copies
  are drift-checked against the packaged skill, and the public docs route users through
  audience-oriented manuals and maintained architecture references.

### Fixed

- **Resource finalization and attribution**: inactive successful runs no longer recover
  as failed, historical refreshes write the complete report set, code-mode sampling
  excludes unrelated processes, and nested or fan-out work retains its owning node and
  item identity.
- **Tool latency**: paired Claude and Gemini tool spans derive a non-negative duration
  from valid timestamps instead of rolling up as zero.
- **Cloud source preflight**: vendored and submodule Metaproc paths are detected before
  dispatch so current-branch source changes cannot silently use image-baked code.
- **Cloud artifact contracts**: environment templates and operator docs include the
  required SHA-256 value for each downloaded wheel and workspace URI.

## [0.2.0][] - 2026-07-31

### Added

- Dependency-aware execution of Markdown process specs.
- Local, agent-CLI, and optional GCP Batch execution backends.
- Resumable run state, validation, tracing, resource reports, and RunPool controls.
- Credential-pool operations and adapter integrations.
- A packaged Metabrowser plugin and portable Agent Skill.
- Reproducible uv-based development, verification, build, and publishing workflows.

### Changed

- Require `softschema>=0.4.0,<0.5` and `frontmatter-format>=0.4.0,<0.5` (previously
  `softschema>=0.1.4,<0.2` and `frontmatter-format>=0.3.0`). See the
  [softschema 0.2.0](https://github.com/jlevy/softschema/releases/tag/v0.2.0) and
  [softschema 0.3.0](https://github.com/jlevy/softschema/releases/tag/v0.3.0),
  [softschema 0.4.0](https://github.com/jlevy/softschema/releases/tag/v0.4.0), and
  [frontmatter-format 0.4.0](https://github.com/jlevy/frontmatter-format/releases/tag/v0.4.0)
  release notes for the complete upstream migration surface.
- `metaproc softschema validate` now includes softschema’s `outcome` discriminator
  (`valid`, `invalid`, or `input_error`) alongside the existing `ok` field.
- Mapping-based YAML and frontmatter writes are deterministic and alias-free: repeated
  lists and mappings are expanded instead of emitting anchors.
  Cyclic values raise `YamlSerializationError` without replacing an existing target.

### Breaking

- `metaproc softschema compile` now requires `--contract CONTRACT_ID`. softschema 0.3
  makes the contract id a required input to `compile_model`, so the sidecar always
  records the contract it was compiled for.
- Softschema 0.2 enforces the contract-id grammar `[namespace:]Name[/version]`. This
  applies to plugin `Contract` registrations, process-spec `schema` fields, artifact
  `softschema.contract` metadata, and the `--schema` and `--contract` CLI options.
  All externally authored IDs must use the new form.
  Metaproc’s structure-report ID is now `metaproc:StructureReport/v1`, renamed from
  `metaproc.structure_report.v1`; the other built-in IDs were already valid.
- Structure reports written by earlier versions no longer validate.
  Regenerate them with `metaproc structure-report`, or update both `softschema.contract`
  and `structure_report.schema` to `metaproc:StructureReport/v1`.
- Softschema 0.3 and 0.4 restrict YAML inputs to bounded, JSON-compatible values.
  Aliases and anchors, merge keys, explicit tags, duplicate or non-string keys, unsafe
  integers, negative zero, non-finite numbers, excessive depth, and oversized inputs are
  rejected. Bare and quoted date- or timestamp-shaped scalars are accepted as strings in
  0.4; callers that need temporal objects must construct them explicitly after
  validation.
- Compiled schemas are validated offline and remote `$ref` targets are never fetched
  implicitly. Schemas consumed through Metaproc must be self-contained: use local `$defs`
  references or a registered Pydantic model instead of network-resolved references.

[unreleased]: https://github.com/jlevy/metaproc/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/jlevy/metaproc/releases/tag/v0.4.1
[0.4.0]: https://github.com/jlevy/metaproc/releases/tag/v0.4.0
[0.3.0]: https://github.com/jlevy/metaproc/releases/tag/v0.3.0
[0.2.1]: https://github.com/jlevy/metaproc/releases/tag/v0.2.1
[0.2.0]: https://github.com/jlevy/metaproc/releases/tag/v0.2.0

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
