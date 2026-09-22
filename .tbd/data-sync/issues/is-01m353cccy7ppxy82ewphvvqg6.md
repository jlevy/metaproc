---
type: is
id: is-01m353cccy7ppxy82ewphvvqg6
title: Fix every write that needs atomic publication and lacks it
kind: bug
status: closed
priority: 1
version: 2
labels: []
dependencies: []
parent_id: is-01m352sgcsjsxpwnprykp8rpsv
created_at: 2026-09-22T17:42:24.414Z
updated_at: 2026-09-22T18:03:00.845Z
closed_at: 2026-09-22T18:03:00.845Z
close_reason: null
resolution: null
duplicate_of: null
---
Every write that needs atomic publication and does not have it, as found by the audit and
by `devtools/check_atomic_writes.py`.

**Secrets — write-then-chmod leaves a window, and the write is not atomic.**
`adapters/claude_cli.py:746, 942, 954, 1000`, `adapters/codex_cli.py:588, 693, 696`,
`adapters/gemini_cli.py:146`. Each writes the file at the umask default and narrows it to
0600 afterwards. The enclosing directories are 0700, so the window is bounded, but the
shape is wrong and the atomicity gap is real: a crash mid-write leaves a truncated OAuth
blob that the refresh path later reads back. Note an atomic publish through a temp file
starts with the *temp* file's mode, so the mode must be set on the staged path — a plain
`atomic_write_text` would publish at the umask default and make this worse.
`codex_cli.py:696` has a security consequence beyond corruption: a truncated
`config.toml` silently lets codex fall back to the OS keychain or a stray
`OPENAI_API_KEY`, which is the leak the file exists to prevent.

**`cloud/gcp/container_bootstrap.py:170`** — `~/.pi/agent/models.json` is written with no
`chmod` at all, so it sits at the umask default permanently in a 0755 directory. A
literal provider key in the operator's local `models.json` flows through
`build_pi_models_json` → `METAPROC_PI_MODELS_JSON` → this file verbatim.

**`cloud/gcp/container_bootstrap.py:318`** — truncates and rewrites a real
`pyproject.toml` in the live `/workspace` tree. A crash between the read and the rewrite
leaves it empty, and the subsequent `uv pip install` fails with an unrelated parse error.

**`trace/store.py:27`** — `path.open("w")` then streams spans. `open("w")` truncates
first, so a crash leaves a short JSONL that `TraceEvent.model_validate` rejects, and the
previous good trace is already gone. Streaming into the staged temp file is explicitly
allowed and keeps the memory profile.

**`runpool/backend.py:103`** — the invocation sidecar, written immediately before
`create_subprocess_exec`. Large (it carries the env dump) and written exactly when a
spawn-time crash happens.

**`commands/skill.py:101`** — the committed `.agents/skills/` and `.claude/skills/`
`SKILL.md` copies that a drift test enforces. A truncated one shows up as a real diff and
gets loaded by agents.

**`commands/resume_daemon.py:75`** — the tombstone explaining why a checkpoint stopped
being re-dispatched. Truncated content reads as a wrong reason.

**`commands/run_parallel.py:1402, 1404`** and **`dispatch/slot_coordinator.py:568`** —
`shutil.copytree` / `copy2` straight into operator-visible trees.

**`devtools/synthesize_fixtures.py:203`** and **`tests/test_runpool_golden.py:226`** —
both rewrite repository-tracked files. An interrupted `make` leaves a committed fixture
truncated, and the next `--check` reports it as drift rather than corruption.

Everything else the checker flags is a deliberate non-atomic contract and gets a named
`# write-contract:` marker instead: the subprocess-attached live logs
(`run_step.py:591, 604`, `engine/runtime.py:270`, `runpool/backend.py:264, 302`), the
writability probes (`engine/preflight.py:137`, `commands/auth_check.py:694`), the probe
scratch files (`commands/probe_tool_use.py:143, 158`), and `gzip_text.py:252`.
