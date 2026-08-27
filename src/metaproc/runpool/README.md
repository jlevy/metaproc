# RunPool Module

RunPool design is documented in
[`metaproc/docs/arch-runpool.md`](../docs/arch-runpool.md), also readable as
`metaproc help arch-runpool`. Keep that file as the single design contract for subsystem
boundary, memory and swap telemetry, adaptive concurrency, host admission, visibility,
and testing rules.

Key constraints:

- RunPool locking uses mkdir-based leases.
  Do not replace it with `flock(2)`, `fcntl.flock`, `fcntl.lockf`, `filelock`, or
  `portalocker`; see the design doc’s Locking Primitive section.
- Profile resource hints are ceilings.
  The adaptive controller owns live concurrency on a healthy host.

## Using RunPool as a library

RunPool is supported for direct use, not only through `run-parallel`. Any tool with a
launch loop — a batch driver, a transfer pool, a one-off script — can get adaptive
concurrency, health tracking, and host admission without adopting process specs.

```python
import asyncio
from metaproc.runpool import ProcessConfig, RunPool, RunPoolConfig
from metaproc.runpool.backend import LocalBackend, PreparedLaunch

async def main() -> None:
    config = RunPoolConfig(
        max_concurrency=8,
        initial_concurrency=2,   # the controller ramps toward the ceiling
        min_concurrency=1,
        state_dir=Path("./.state/pool"),
        logs_dir=Path("./.logs/pool"),
        host_admission_enabled=True,
    )
    async with RunPool(config, backend=LocalBackend()) as pool:
        results = await asyncio.gather(*[
            pool.submit(ProcessConfig(
                launch=PreparedLaunch(command=("my-tool", item), log_path=...),
                label=item,
            ))
            for item in items
        ])
```

Use the context manager.
Leaving a pool unshut leaks its monitor task and its event log handle, and strands any
host admission slots its processes hold — and a stranded slot is invisible capacity loss
for every other run on the machine.

Set `initial_concurrency` low and `max_concurrency` high: the controller sizes actual
concurrency from live memory pressure, so a hand-set low ceiling removes the safety
mechanism rather than providing one.

**What RunPool is not.** It admits and supervises processes; it does not own durable
dependencies, retries across restarts, roster expansion, downstream invalidation, or run
completion. Those belong to the orchestrator above it.
A recurring launch shape that starts growing its own queue, lease, or resume protocol is
a signal to graduate it into the declarative scheduler rather than to extend this API.

## Implementation

This module contains the implementation:

- `pool.py`: pool manager, adaptive controller, health sampling, status writes
- `backend.py`: launch backend protocol and local subprocess backend
- `host_admission.py`: disk-backed aggregate host admission across local RunPools
- `events.py`: append-only JSONL event writer
- `event_models.py`: typed pool event schemas
- `status.py`: status models and atomic status I/O
- `kill.py`: external kill/drain protocol
- `monitor.py`: process-health helpers
- `semaphore.py`: adaptive concurrency primitive
- `mock_backend.py`: deterministic backend for tests
- `registry.py`: backend registration and resolution

<!-- This document follows common-doc-guidelines.md.
See github.com/jlevy/practical-prose and review guidelines before editing.
-->
