# Changelog

All notable changes to the safeproc package are recorded here.
The package is unreleased and unpublished; versions begin with the first `safeproc-v*`
tag after extraction.

## Unreleased

### Added

- Package scaffold as a uv workspace member with no runtime dependencies, strict typing,
  an enforced import boundary against Metaproc, and source-free wheel builds.
- Neutral models: scoped host and tree samples, the guard policy with the memory guard’s
  calibrated defaults, pressure states, danger reasons split into measured and
  predictive, and versioned journal records.
- The pure pressure engine: floor, alarm, swap-line, red-line ratio, time-to-impact, and
  compressor-slope triggers; the producer-pause duty cycle; proportional shedding sized
  by memory; fault attribution before any victim; and the two-condition abort rule.
- Deterministic replay of a journal through the engine.
- A Linux procfs provider and a macOS provider ported from the memory guard.
- The owned-launch primitive: `posix_spawn` with a new session, the wrapper handshake,
  and `pidfd` or `kqueue` exit observation under `asyncio`.
- `ProcessMonitor` with observation as the default and an explicit guard policy, plus
  the `safeproc watch` and `safeproc replay` commands.
