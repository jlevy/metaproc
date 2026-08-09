---
title: Browser Realtime-Streaming Smoke Check
description: Manual end-to-end sanity check after Phase 1 + Phase 3 + Phase 5 land. Catches regressions the automated suite can't see (actual cold-start wall-clock, browser DOM behaviour, watcher latency on real disk).
---
# Browser Realtime-Streaming Smoke Check

Run this from a Metabrowser source checkout after changing its inventory, event,
watcher, recent-file, or browser asset code.

This runbook covers what the automated test suite can’t — actual cold-start latency on a
35k-file repo, browser-side DOM behaviour, watcher latency on the real filesystem.

## 1. Cold-start budgets (Phase 1)

Set a workspace path and start the browser against it:

```bash
WORKSPACE_ROOT=/path/to/a/representative/workspace
uv run metabrowser "$WORKSPACE_ROOT" --no-open 2>&1 \
  | tee "$WORKSPACE_ROOT/.logs/metaproc-browser.log"
```

Open the browser to the printed URL. Watch the log for:

* `inventory walker complete: status=done files=N entries=M elapsed=Xms` — the walker
  should finish in **<30 s** on a 35k-file repo.
* `api_tree (inventory) ... took Xms` — first GET should be **<500 ms**; warm GETs
  should be **<10 ms**.
* `api_recent ... took Xms` — first GET should be **<100 ms**; warm GETs should be **<10
  ms**.

If a budget fails, use the benchmark tooling in the Metabrowser repository against
`$WORKSPACE_ROOT` to capture a structured report.

```bash
make bench
# …writes scratch/browser-bench/<timestamp>.json
```

The new `index_meta`, `capabilities`, `recent_24h`, `recent_all` cases are added to the
bench surface.

## 2. Skeleton → live patch flow (Phase 1.13)

In the running browser, hard-reload (Cmd-R / Ctrl-R) **while the walker is mid-pass** if
you can catch the timing.
Look for:

* Tree rows for not-yet-finalized dirs paint with a gray pulsing skeleton in the size +
  age cells.
* Within a few seconds, the skeletons swap in place to real values without the row
  re-rendering.
* No DOM flash, no scroll position lost.

If the walker has already finished by the time you reload, restart the server and reload
faster.
Or temporarily bump the fixture to a larger directory so the walker takes longer.

## 3. Recent tab live updates (Phase 3 + 5)

In a separate terminal, write a fresh file into the watched tree:

```bash
mkdir -p "$WORKSPACE_ROOT/scratch"
echo "smoke" > "$WORKSPACE_ROOT/scratch/smoke-$(date +%s).md"
```

In the browser:

* Click the **Recent** tab if not already there.
* The `scratch/smoke-...md` row should appear within ~2-3 s on a native (ext4 / apfs)
  mount, ~5 s on polling fallback.
* No manual reload needed.
* Repeat with a `.jsonl` write under `runs/.../.logs/` to exercise the active-file
  detection pipeline.

## 4. Cluster collapse (Phase 3)

Trigger a dispatch that writes 100+ files into one run dir within seconds (e.g. start a
smoke roster). In the Recent tab:

* The run dir paints as a **single collapsed row** with
  `runs/<date>/<roster>/<task> [N files]` style label.
* Clicking the row expands to show the files.

If every file shows as its own row (no collapse), the cluster threshold isn’t matching —
the spread is wider than 5 %, or the single-dir compaction didn’t fold the chain.
Check `docs/arch/arch-metaproc-core.md` for the spec; constants live in metabrowser’s
external `settings.py` (`RECENT_CLUSTER_PCT`).

## 5. Active-file badges (active_tracker)

Start a long-running dispatch.
In the browser:

* Files actively being written to under `.logs/` or `.state/` get a **green dot** badge
  in the row.
* When a file’s writer dies (PID gone), the dot turns to a **dimmer / red** indicator
  (depending on the active CSS rules for `.pid-dead`).
* Files quiet for ~30 s drop the badge entirely.

## 6. Watcher graceful fallback

Run `metabrowser` against a directory on an NFS / FUSE mount (if available).
Verify:

* Server log shows `watcher starting at <path> mode=polling reason=fs=nfs` (or similar).
* `/api/capabilities` reports
  `{ "backends": [{ "mode": "polling", "reason": "fs=nfs" }] }`.
* Recent tab still updates, just slower (~5 s instead of <2 s).

## 7. Cross-panel selection (P3.9)

* Click a file in **Files** tab — it highlights.
* Switch to **Recent** tab — same file is highlighted (if it’s in the window).
* Click another file in Recent — switch back to Files — that file is now highlighted.
* No selection ever survives in two places at once.

## 8. Window chips (P3.8)

* Default window is `24h`. Click `1h`, `7d`, `30d`, `all` in succession.
* The chip you clicked highlights as active.
* Results refilter immediately (no fetch latency now that the filter is
  FileStore-derived).
* Click the same chip twice — no extra work happens (dedup).

## 9. Server restart while browser open

Stop and restart `metabrowser`. In the still-open browser:

* The EventSource auto-reconnects.
* On reconnect, an `fs.snapshot` re-populates FileStore.
* No stale state survives the restart (verified by an `fs.resync_required` if the server
  detected a root swap).

## 10. Walker truncation banner

Serve a workspace large enough to hit `INVENTORY_MAX_FILES` (200 000 by default; bump it
down in metabrowser’s external `settings.py` to force the case):

```bash
.venv/bin/metabrowser "$WORKSPACE_ROOT" --no-open 2>&1 | tee "$RUNS_DIR_ROOT/.logs/metaproc-browser.log"  # never /tmp — see metaproc/docs/conventions.md § Logging Rules
```

* Server log shows `inventory walker complete: status=truncated …`.
* Browser shows a banner above the tree summary: “Tree partial: walker hit the file
  cap…”.
* Banner is absent when the walker reached `done`.

## 11. Slow-request log on SSE

Open the browser; leave it idle for ~30 s. The SSE heartbeat is 15 s.

* Server log must NOT contain `metabrowser slow server request … path=/api/events`
  warnings (the long-poll connection is intentionally held open).
  The skip list lives on `_SlowRequestLogMiddleware._LONG_LIVED_PATHS`.

## 12. Recent panel deep-window coverage

Pick a 24h / 7d / 30d window in the Recent tab on a workspace with files at depth 3+
(e.g. inside `runs/<date>/<roster>/<task>/...`). The panel must show all of them, not
just files at depths 0–2 (which is what FileStore alone covers — SSE scope is
`root-depth-2`).

* On chip change, the Network panel should show a `/api/recent?window=...` GET.
* If you write a fresh file deep under `runs/...` after the chip fetch, it should appear
  within ~2 s — the FileStore overlay merges in-window upserts from `fs.change` ops
  without a refetch.

## What to file if you see a regression

Open an issue / bead with:

1. Which budget or behaviour broke.
2. Screenshot or perf-log excerpt.
3. Whether `make bench` reproduces.
4. Whether the unit suite (`uv run pytest -k browser`) is green.

Beads under `--label browser-streaming-2026-04` track every deliverable; reference them
by id.

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
