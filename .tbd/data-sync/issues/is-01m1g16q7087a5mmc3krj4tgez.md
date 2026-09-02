---
type: is
id: is-01m1g16q7087a5mmc3krj4tgez
title: "Spike: nonforking owned launch in Python (os.posix_spawn + stdlib exit waiter under asyncio)"
kind: task
status: open
priority: 1
version: 2
spec_path: docs/project/specs/active/plan-2026-09-01-safeproc-local-incubation.md
labels: []
dependencies:
  - type: blocks
    target: is-01m1fyjweng4ryydmr4vqsvpa1
parent_id: is-01m1fxnwnyqvq1gg8ak7317kyc
created_at: 2026-09-02T03:04:36.832Z
updated_at: 2026-09-02T03:04:38.669Z
---
Prove on macOS and Linux that an isolated session and process group can be created with os.posix_spawn(setsid=True, setpgroup=...) without forking the supervising parent, that exit can be observed without SIGCHLD or Popen (pidfd_open + loop reader on Linux 5.3+; select.kqueue EVFILT_PROC/NOTE_EXIT on macOS; waiter-thread fallback), that descriptor hygiene holds under PEP 446 plus explicit file_actions, and that the launch-wrapper registration handshake is observable by the supervisor (pipe or broker event) with a defined outcome when the wrapper dies between spawn and exec. Origin: review F2 of pull request 62. CPython 3.12 subprocess.py:1825-1839 uses posix_spawn only without start_new_session/close_fds; backend.py:270 uses start_new_session=True. Blocks mp-3c0g.
