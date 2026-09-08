# Performance Notes

Operational performance discipline for metaproc.
Captures the patterns, tooling, and anti-patterns surfaced by the browser refactor and
the runpool/dispatch work upstream of it.
The browser itself now lives in the external
[metabrowser](https://github.com/jlevy/metabrowser) package; browser file references
below are historical examples from that refactor, cited as inline code rather than
links.

## Why This Doc Exists

metaproc is a thin framework around expensive things — agent invocations, file I/O over
big repos, JSONL log streams that can hit gigabytes.
A performance bug in the framework usually shows up as “the UI feels sluggish”, “the
orchestrator hangs”, or “the preflight took 15 seconds when it should have been
instant”. The
[“Worked example: the browser refactor”](#worked-example-the-browser-refactor) section
below is the receipts — measured numbers and the changes that produced them.

## Operating Principles

### Measure before you optimize, and *cold ≠ warm*

Every endpoint has two latencies that matter for different reasons:

- **Cold** — caches cleared, first request after a restart.
  Captures worst case + identifies what still depends on disk + cache build cost.
- **Warm** — steady state, what a user feels clicking around.

Optimizing the wrong one is a waste of time.
The external Metabrowser package owns its benchmark harness.
It reports cold and warm latencies with the full distribution, not just the median.

If you don’t have a harness for the area you’re touching, **build one before you
optimize**. Build it as in-process as possible — see “Tooling” below for the no-httpx
ASGI driver — so iterating on the code under test takes seconds, not a CI loop.

### Measure workflow timings from event logs

Human-readable run summaries are useful for orientation, but event logs are the timing
source of truth. Use `process-events.jsonl` and `runpool-events.jsonl` when comparing
step duration, retry behavior, and pool pressure across workflow changes.

- Break timings down by step and task before drawing conclusions from total wall time.
- If a generated summary disagrees with event logs, trust the event logs and fix the
  summary. In one workflow review, verification was summarized as 13 minutes but the
  event log showed 42 minutes.
- For variable-latency tools, per-call latency can dominate call count.
  Reducing query count does not help if the remaining calls are slow; parallelism or
  source substitution may matter more.

### Scope of work beats micro-optimization

The biggest browser win was reducing **what** we walk, not making the walk faster.
The activity poll went from 976 ms → 5 ms (191×) by scoping discovery to `.logs/` and
`.state/` instead of the entire repo, then using `scandir`. The `scandir` change alone
would have been a 2× win.
The scope change was the order-of-magnitude win.

Before reaching for a faster algorithm, ask:

- Are we doing this work for entries that don’t need it?
- Could we early-exit on the first match instead of fully aggregating?
- Is the cache scope right — per-request, per-root, per-process?

### Make the lazy thing actually lazy

Several of the browser’s “performance bugs” were eager work disguised as laziness:

- `highlight.js` ran on every `<pre><code>` in the page including collapsed log-event
  raw blocks, even though the user couldn’t see them.
- The JSONL parser pre-formatted `raw_json` per event, even though the client only
  renders an event’s raw block when expanded.
- `_dir_tree` walked every file under the lazy-load sentinel to compute a “summary”
  that’s already correct from a shallow walk.

Look for work that’s done at render-time but only consumed at
expand/click/scroll-into-view time.
Defer it until first use.

### Don’t pre-format what the consumer can compute on demand

If the client already has `evt.raw` and the fallback render path is
`evt.raw_json || JSON.stringify(evt.raw, null, 2)`, the server doesn’t need to ship
`raw_json` at all. We saved ~50% of the response size on big logs by removing one
`json.dumps(..., indent=2)` from a hot loop.

Rule: server-side serialization should produce the *minimum* shape that contains the
information; presentation work belongs to the consumer.

### Pay for I/O exactly once

The original JSONL parser opened the file twice — once to sniff the adapter from the
first 20 lines, once to parse the rest.
The original charts handler opened it again on top of that for its own sniff.
With a 38 MB log and a single user click that’s 114 MB of disk reads for one view.

Single-pass version buffers the first 20 valid lines, fires the detector, replays the
buffer through the parser, and continues.
Same detector, same parser, one read.

### Cap unbounded payloads at the boundary

Pi `message_update` events embed entire source files.
The original behaviour was “trust the data, ship it all” — which on one log meant
shipping 41 MB of JSON across a localhost socket and asking the browser to parse it.

Two caps now apply at the JSONL view boundary:

- 256 KiB per *line* (already existed) — drops oversized lines entirely rather than OOM
  the parser.
- 8 KiB per *event raw payload* once the file is over 2 MiB — sufficient for inspection
  in the UI, full bytes still available via `/raw`.

When a payload size is unbounded by an upstream system you don’t control, cap it at the
egress point and provide an escape hatch.

### Move blocking work off the event loop

asyncio handlers that do disk I/O, big JSON parses, or `subprocess` calls block every
other request. `asyncio.to_thread(...)` is a one-line fix.
The browser uses it for `_parse_jsonl_file`, `_dir_tree`, and the activity walk.
Activity polls and viz fetches stay snappy even mid-parse of a big log.

### Cache the immutable, TTL the slow-but-stale-tolerable

Three cache flavours, each with a clear policy:

- **Module-load-time** — static assets, bundled data files.
  The browser reads `styles.css` / `app.js` / `icons.js` once at module import and hands
  the same string to every request.
  No invalidation needed.
- **Per-resolved-root** — anything keyed on the served `ROOT_DIR`. Invalidated by the
  registered callback when `_set_root_dir` fires.
  Used for the gitignore checker, root-prefix string, etc.
- **TTL’d** — derived data that becomes stale on its own (filesystem mtimes, .gitignore
  edits). 5 s for sentinel summaries, 30 s for the activity discovery walk, 30 s for the
  gitignore checker. Short enough to be invisible, long enough to absorb burst traffic.

Avoid request-scoped caches that rebuild on every page load.

### `os.scandir` over `Path.iterdir() + .is_dir() + .stat()`

`Path.iterdir()` returns `Path` objects whose `is_dir()` and `stat()` each cost a
syscall. `os.scandir()` returns `DirEntry` objects whose `is_dir(follow_symlinks=False)`
is **free** (cached from the dirent’s d_type) and whose `stat(follow_symlinks=False)`
reuses the dirent stat where the kernel allows.
On a 35 k-file walk this is the difference between three syscalls per entry and one.

Always pass `follow_symlinks=False` unless you specifically need to follow symlinks —
it’s both faster *and* avoids walk loops on self-referential symlinks.

### Frontend: prefer `Array.join` over `string += part`

Repeated `+=` against a long accumulator is quadratic in some engines because each
concat copies the whole buffer.
Build into an array, join at the end.
Hot-path renderers (`renderTreeNodes`, `renderLogTab`) follow this convention; if you
add a new big renderer, do too.

### Frontend: diff against the previous state, don’t re-walk the world

The activity poll fires every 5 seconds.
The original handler did `document.querySelectorAll(".tree-item.tree-file")` and toggled
classes on every row — 35 k DOM reads + writes per poll on a big repo, all to update ~10
rows. The new handler keeps the previous active set, diffs against the new set, and
touches only the rows whose state changed.

Anywhere a frontend timer fires repeatedly, this is the shape to default to.

## Tooling

### Backend benchmark (offline, in-process)

The Metabrowser repository owns the in-process ASGI benchmark and its command-line entry
points. Run that harness from a Metabrowser checkout.
Two JSON reports diff cleanly with `jq`:

```bash
jq -s '.[0].results as $a | .[1].results as $b
       | [$a, $b] | transpose
       | map({name: .[0].name,
              before_med: .[0].warm.median_ms,
              after_med:  .[1].warm.median_ms})' \
   before.json after.json
```

### Frontend instrumentation

The Metabrowser package also owns browser-side fetch and render instrumentation.

To capture a snapshot:

```js
metaprocPerf.reset();          // clear ring buffer
// ...interact with the app...
metaprocPerf.report();         // console.table summary
metaprocPerf.copy();           // JSON to clipboard
metaprocPerf.download();       // JSON to file
```

The snapshot has the schema `metaproc-browser-perf/v1`. Same shape on every machine —
diff-able across runs.

### When the bench harness is the wrong tool

The in-process ASGI driver does not exercise:

- Real network framing / serialization overhead — both negligible on localhost but real
  over SSH tunnels (`metabrowser remote`).
- Real browser parse + paint cost — for that you need `metaprocPerf` in a real browser,
  or a headless-browser harness.
- Concurrent request behaviour — the bench is sequential.

If you’re chasing one of those, build the right harness for the question and document it
next to this one. Don’t try to extend `browser_bench.py` past its scope.

## Patterns From the Field (Browser Refactor)

| Pattern | Where | Win |
| --- | --- | ---: |
| Activity scoped to `.logs/`/`.state/` | `activity.py` | 191× |
| Static asset bundle cached at module load | `proc_browser.py` (`_static_assets`) | drops 1 ms + 5 syscalls per index hit |
| `os.scandir` everywhere a tree walk happens | `tree.py` | 2.5× tree fetch |
| Single-pass JSONL parser | `jsonl_view.py` | halves disk I/O on big logs |
| Per-event `raw_json` dropped server-side | `jsonl_view.py` (`_serialize_events`) | -50% response size |
| 8 KiB per-event raw cap above 2 MiB | `jsonl_view.py` | 41 MB → 5.7 MB |
| `asyncio.to_thread` for disk-bound work | `proc_browser.py` | event loop responsive mid-parse |
| Lazy `highlight.js` on log expand | `app.js` (`toggleEvent`, `highlightCode`) | first paint scales independently of event count |
| Array-join in hot renderers | `app.js` (`renderTreeNodes`, `renderLogTab`) | linear instead of quadratic |
| Activity-poll DOM diff | `app.js` (`pollActivity`) | per-poll DOM work proportional to changes, not tree size |

## Anti-Patterns to Avoid

- **`Path.iterdir()` + per-entry `.stat()`** in any walk that touches more than a few
  hundred entries. Use `os.scandir`.
- **Re-reading a file you already read** for a second pass — buffer the first read or
  restructure the parser.
- **Eagerly serializing data the consumer treats as lazy** — typical shape: render-time
  `JSON.stringify` on N items where the user only expands a handful.
- **Polled handlers that re-walk the full DOM/filesystem** when a diff against the
  previous state would suffice.
- **Caches with no invalidation story** when the data behind them changes (e.g. caching
  `gitignore` rules forever — `.gitignore` edits silently stop working).
- **Caches with no scope discipline** that grow unbounded — `cachetools.TTLCache` with
  `maxsize` and `ttl` is the right shape for most “cheap input → expensive output”
  lookups.
- **Synchronous disk I/O in an asyncio handler** that other handlers depend on for
  snappiness. `asyncio.to_thread`.
- **Reporting “X is faster” without a measurement.** When you can’t measure (e.g.
  browser-side without a headless harness), say so explicitly in the PR — use the
  “What’s measured vs what’s a code change” framing in the
  [browser refactor PR](#browser-refactor-pr-context) below.

## Worked Example: The Browser Refactor

The principles above came out of refactoring `metabrowser`. This section is the receipts
— measured numbers, what changed, and the follow-ups intentionally deferred — kept here
so the reasoning travels with the rules.

### Measured speedups

| Endpoint | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| `/api/activity` (poll every 5 s) | **976 ms** | **5 ms** | **191×** |
| `/api/file` for 38 MB JSONL | 1282 ms / 41 MB | **393 ms / 5.7 MB** | **3.3×** |
| `/api/tree?depth=2` (35 k-file repo) | 600 ms | **239 ms** | **2.5×** |
| `/api/tree?depth=4` (35 k-file repo) | 1216 ms | **768 ms** | 1.6× |
| `/` (index page) | 1.0 ms | **0.6 ms** | 1.7× |
| `/api/file` markdown / text / small JSONL | < 2 ms | < 2 ms | — |
| `/api/charts` (38 MB JSONL) | 222 ms | 222 ms | — |
| `/api/viz` (process spec) | 7 ms | 7 ms | — |

Numbers are warm-cache median over 8 iterations against the in-repo working tree (35 k
files, ~40 MB largest log).
Capture with `make bench` (see “Tooling” above).

### Where time goes (post-fix)

Hot paths in steady state, sorted by impact on user-felt latency:

1. **Tree fetch — depth=2 root: ~240 ms warm.** Walk every directory under the served
   root that the user can see.
   Bottleneck is `scandir`
   + `entry.stat()` per file (~10 µs each on Linux ext4) + the `.gitignore` checker on
     every directory. Cost scales linearly with `(directories visited × depth)`.
2. **Charts extraction — ~220 ms for a 38 MB JSONL.** Re-reads the whole file and
   re-parses every event to build the time-series and tally tree.
   Cost scales with file size, not event count.
3. **Activity poll — ~5 ms steady state, 230 ms cold.** Walks `.logs/` and `.state/`
   once per 30-second TTL window, then re-stats every discovered file each 5-second
   poll.
4. **Visual tab — ~7 ms.** Loads + projects a process spec.
5. **Index page — ~0.6 ms.** Serves the cached static bundle.

### Module map

The refactor split the 1740-line `proc_browser.py` monolith into six focused modules.
All public names re-exported from `proc_browser` so external callers (tests, `serve.py`)
didn’t break.

| Module | Lines | Responsibility |
| --- | ---: | --- |
| `proc_browser.py` | ~600 | Starlette routes, app wiring, `main()`, ROOT_DIR proxy. |
| `tree.py` | ~330 | Directory walk + caches, gitignore checker, sentinel summaries. |
| `charts.py` | ~320 | Chart-data extractors with `(path, mtime_ns)` memo. |
| `activity.py` | ~190 | File activity tracking, scoped discovery. |
| `jsonl_view.py` | ~165 | Single-pass JSONL parser + payload caps. |
| `file_kinds.py` | ~140 | Kind detector chain + view registry. |
| `paths_safe.py` | ~120 | ROOT_DIR + path safety + relativization. |
| `sse.py` | ~170 | Live JSONL tail (Server-Sent Events). |

Markdown → HTML rendering moved to the client (`marked` from CDN). Net: −230 lines of
regex Python, smaller JSON payload, GFM-spec compliance from a maintained library.

### What was *not* changed (and why)

- **Charts time-series extraction** still re-parses the whole JSONL. A run-level cache
  keyed on `(path, mtime_hash)` would drop this to memo-lookup but adds invalidation
  complexity. Open question for a future round.
- **No virtual-list for the log view.** A 1800-event log creates 1800 DOM rows; with the
  lazy hljs change this is fine in practice (~30 ms paint).
  If we ever care about 50 k-event logs, virtualize the list.
- **Tree depth=4 is still ~770 ms.** Walking ~35 k files end-to-end has a floor set by
  syscall count. Depth=2 + lazy expansion is the intended user-facing path; depth=4 is
  mostly used by tests + the bench harness.
- **`/api/viz`** is already fast enough to feel instant.
  Beyond parser-level changes there’s no obvious win.

### Browser refactor PR context

review used a “what’s measured vs what’s a code change” framing in the test plan because
the bench harness only covers the backend.
The frontend optimizations (lazy hljs, array-join, activity-poll DOM diff) were
informed-but-unmeasured changes; the PR body separated those out explicitly so reviewers
knew which claims had numbers behind them and which they had to verify in a real browser
via `metaprocPerf.report()`. Adopt the same framing whenever the available harness can’t
see the whole picture.

## When to Revisit

Re-run `make bench` and check that the **Measured speedups** table above still holds
after any change to:

- `src/metaproc/logutil/parsing.py` (the JSONL parsers feed every JSONL view + chart)
- `src/metaproc/osutils/ignore_filter.py` (gitignore checker)
- browser tree, activity, and JSONL-view modules, which now live in the external
  [metabrowser](https://github.com/jlevy/metabrowser) package

If a change moves a number by more than ~20% in either direction, update the table in
the same commit. Stale perf docs are worse than no perf docs.

## Cross-References

- [development.md](development.md) — concise framework dev guide.
- [MetaBrowser architecture](https://github.com/jlevy/metabrowser/blob/main/docs/architecture.md)
  — the standalone browser design.
- [Metaproc MetaBrowser plugin](../src/metaproc/metabrowser_plugin/README.md) — the
  Metaproc-owned browser integration.
- [conventions.md](../src/metaproc/docs/conventions.md) — naming and structure rules;
  some apply to perf-relevant code (e.g. caching key naming).
- Metabrowser’s benchmark and frontend instrumentation sources.

## Phase 1 Cold-Start Baseline (2026-05-05)

Measured against a representative workspace (~35 k files) before the streaming-spec
Phase 1 work landed:

| Endpoint | Cold (after restart) | Warm |
| --- | --- | --- |
| `/api/tree` | 19.9 s | 4.0 s |
| `/api/activity` | 14.1 s | sub-ms |
| `/api/activity` (worst observed under heavy dispatch I/O) | 132 s | — |
| `loadTree` total (fetch + render) | 23.0 s | 6.7 s |

## Phase 1 Budgets (Target Post-Streaming Phase 1)

| Endpoint | Cold target | Source |
| --- | --- | --- |
| `/api/tree` first-byte | **<500 ms** | InventoryIndex eager pre-warm |
| `/api/activity` | **<50 ms** | reads `.logs/.state` from InventoryIndex |
| Walker reaches `done` status | **<30 s** on idle disk | walker BFS + post-order |
| `loadTree` total (fetch + render) | **<4 s** | both above + skeleton render |
| Live decoration update from local mtime change to DOM | **<1 s** | fs.change ops on /api/events |

Run Metabrowser’s external benchmark harness against a representative large workspace;
record a new measurement here when the implementation or watcher cascade changes.

## Phase 1 Cold-Start Results (2026-05-06, Post-review)

Measured with the new `--skip-prewarm`-toggleable inventory pre-warm in the external
benchmark harness. The synthetic workspace had about 70,000 files, with 56,353 indexed
after visibility filtering.
Idle disk, no concurrent dispatch.

| Endpoint | Pre-review baseline (filesystem walk) | Post-review (inventory-backed) | Target |
| --- | --- | --- | --- |
| `/api/tree?depth=2` cold | 1044 ms | **372 ms** | <500 ms ✓ |
| `/api/tree?depth=4` cold | 1239 ms | 669 ms | (no target) |
| `/api/activity` cold | 350 ms | **119 ms** | <50 ms (close) |
| `/api/activity` warm (median) | 1.5 ms | 1.8 ms | (warm) |
| Walker → `done` | (not pre-warmed in baseline) | **6.8 s** (56 353 files) | <30 s ✓ |
| `/api/recent?window=24h` cold | 3.9 ms | 19.8 ms | (no target) |
| `/api/recent?window=all` cold | 0.5 ms | 24.6 ms | (no target) |

Tree budget met (372 ms cold vs 500 ms target).
Walker comfortably under (6.8 s vs 30 s). Activity cold is 119 ms vs 50 ms target — over
budget but ~100× better than the 14.1 s baseline; the warm path (1.8 ms) is what users
feel after first paint.
The first-call overhead is dominated by the cached-discovery rebuild after a hard cache
clear; a real browser session pays it once, then sees the warm number on every
subsequent poll. Filing as “follow-up” rather than “regression” — the post-Phase-5
watcher cascade may close the gap.

Recent jumped from sub-ms to ~20 ms cold because the live overlay adds an `entries_flat`
payload (the SPA’s live-overlay base) on top of the clustered tree.
Still well under any user-perceptible threshold.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
