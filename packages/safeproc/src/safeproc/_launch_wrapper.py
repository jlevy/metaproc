"""The minimal launch wrapper: register, then become the target.

Invoked as ``python -m safeproc._launch_wrapper <cwd-or-empty> -- <argv...>`` with the
handshake pipe's write end on descriptor 3. It changes directory when asked, writes the
ready byte, marks the pipe close-on-exec so it closes the instant the target starts, and
``exec``s the target. Any failure before ``exec`` exits nonzero with the pipe still
silent, which the supervisor reads as ``WRAPPER_FAILED``.

Later phases add the broker registration between the directory change and the ready
byte; nothing about the handshake changes.
"""

from __future__ import annotations

import os
import sys

HANDSHAKE_FD = 3
HANDSHAKE_READY = b"R"
EXIT_USAGE = 125
EXIT_CANNOT_EXECUTE = 126
EXIT_NOT_FOUND = 127


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "--":
        return EXIT_USAGE
    cwd, target = argv[0], argv[2:]
    if not target:
        return EXIT_USAGE
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            return EXIT_CANNOT_EXECUTE
    try:
        os.write(HANDSHAKE_FD, HANDSHAKE_READY)
        os.set_inheritable(HANDSHAKE_FD, False)
    except OSError:
        return EXIT_CANNOT_EXECUTE
    try:
        os.execvp(target[0], target)
    except FileNotFoundError:
        return EXIT_NOT_FOUND
    except OSError:
        return EXIT_CANNOT_EXECUTE
    return EXIT_CANNOT_EXECUTE  # pragma: no cover - exec does not return


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
