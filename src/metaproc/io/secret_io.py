"""Publish a file whose contents must never be readable by another user.

A credential file has two contracts at once, and the obvious code keeps only one of
them:

```python
creds_file.write_text(blob)     # visible, at the umask default
creds_file.chmod(0o600)         # ...narrowed a moment later
```

Between those lines the secret sits at whatever the umask allows, usually 0644. The
window is small and, where the parent directory is already 0700, not reachable by
another user — but the shape is wrong, and the ordering is the kind that stops being
safe when somebody later moves the file somewhere less protected.

Atomic publication alone does not fix it. `strif.atomic_output_file` stages a *new*
temp file, so the published file takes the temp file's mode, not the destination's:
routing a secret through `atomic_write_text` publishes it at the umask default and
makes the window permanent instead of brief.

The fix is to create the staged file with the mode it must end up with, before any
byte lands in it, and let the atomic rename carry both properties to the destination.
`tempfile.mkstemp` gets this right for a private temp file, and
`metaproc/cloud/gcp/gcp_credentials.py` uses it for exactly that reason; this module is
the same idea for a file that has a published destination.
"""

from __future__ import annotations

import os
from pathlib import Path

from strif import atomic_output_file

SECRET_FILE_MODE = 0o600
"""Owner read/write. Nothing else, ever, at any instant."""


def write_secret_text(
    dest_path: str | Path,
    text: str,
    *,
    make_parents: bool = False,
    mode: int = SECRET_FILE_MODE,
    encoding: str = "utf-8",
) -> None:
    """Atomically publish *text* at *dest_path*, never readable beyond *mode*.

    The staged file is created with `O_CREAT | O_EXCL` at *mode*, written, and
    committed by rename, so a reader sees no file, the previous file, or the complete
    new one — and never a file at a wider mode than *mode*.

    `make_parents` creates the destination's directory at the process umask, which is
    usually too wide for a directory holding secrets. Create such a directory yourself
    with the mode you want and leave this false; it is here for the case where the
    directory's own mode is already established.
    """
    data = text.encode(encoding)
    with atomic_output_file(dest_path, make_parents=make_parents) as staged:
        # `O_EXCL` matters even though the staged name is unique: it turns "something
        # is already here" into an error rather than a silent write into a file
        # somebody else made, which is the whole reason a fixed staging name is unsafe.
        fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:])
        finally:
            os.close(fd)
