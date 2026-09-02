# safeproc

Process-tree monitoring, owned launch, and host-safety coordination for macOS and Linux.

Safeproc watches a process tree and, when a host is measurably about to fail, removes
the least it can to save it.
It is the successor to a standalone macOS memory guard that watched several hosts die
and was rebuilt after each one; the mechanisms here exist because of specific failures,
which its journal corpus records.

This package is incubating inside the Metaproc repository as an independently buildable
uv workspace member.
It has no runtime dependencies, imports nothing from Metaproc, and is not published from
this repository.

## What exists today

- `safeproc watch --pid PID`: observe an existing process tree and write a journal.
  Observation is the default and sends no signals.
  `--policy guard` enables the intervention policy: pause the producer, shed the largest
  workers proportionally, and abort only when the host is measurably failing and
  shedding is exhausted.
- `safeproc replay JOURNAL`: run a recorded journal back through the same policy and
  report the decisions it produces.
- A Linux provider built on procfs: `MemAvailable`, the caller’s own cgroup headroom,
  Pressure Stall Information as a three-state capability, swap-in rate, and proportional
  set size behind an accuracy gate.
- The owned-launch primitive (`safeproc.launch`): `posix_spawn` into a new session
  without forking the supervisor, a wrapper handshake, and exit observed through a
  `pidfd` or `kqueue` reader on the event loop.
  This is the proven seam the later `SafeProcess` builds on.
- A macOS provider ported from the memory guard: `host_statistics64`, `proc_pid_rusage`,
  `vm.swapusage`, the swap-volume suspension distance, and the kernel pressure alarm,
  read through `ctypes` without forking.
  It has not yet been validated natively; see the handoff note in
  `docs/architecture.md`.

The owned launch path, the per-user broker, and the sentinel are later phases of the
[incubation plan](../../docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md).

## Usage

```sh
safeproc watch --pid 4242                       # observe and journal
safeproc watch --pid 4242 --policy guard        # observe and intervene
safeproc watch --pid 4242 --policy guard --dry-run
safeproc watch --pattern my-orchestrator --once
safeproc replay safeproc-4242.jsonl
```

Exit codes: `0` the tree finished, `1` nothing matched or the platform is unsupported,
`2` the tree was aborted, `3` `--once` found danger.

## Development

From the repository root:

```sh
make safeproc-format
make safeproc-lint-check
make safeproc-test
make safeproc-build
```

`make verify` runs all of them.
The package is strict-typed, has an enforced import boundary, and keeps a
standard-library-only hot path.

## Documentation

- [Architecture](docs/architecture.md): layers, platform capabilities, provenance, and
  the macOS handoff.
- [RunPool Host Safety Envelope](../../docs/project/specs/active/plan-2026-09-01-runpool-host-safety.md):
  the policy this package implements.
- [Safeproc Local Incubation](../../docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md):
  the package plan and phase list.
