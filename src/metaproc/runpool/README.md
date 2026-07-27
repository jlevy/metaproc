# RunPool Module

RunPool design is documented in
[`metaproc/docs/arch/arch-runpool.md`](../../../docs/arch/arch-runpool.md).
Keep that file as the single design contract for subsystem boundary, memory and swap
telemetry, adaptive concurrency, host admission, visibility, and testing rules.

Key constraints:

- RunPool locking uses mkdir-based leases.
  Do not replace it with `flock(2)`, `fcntl.flock`, `fcntl.lockf`, `filelock`, or
  `portalocker`; see the design doc’s Locking Primitive section.
- Profile resource hints are ceilings.
  The adaptive controller owns live concurrency on a healthy host.

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

<!-- This document follows std-doc-guidelines.md.
Review guidelines before editing.
-->
