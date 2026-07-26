"""Shared SSH command construction utilities."""

from __future__ import annotations

import shlex


def wrap_with_stdin_watchdog(inner_cmd: str) -> str:
    """Wrap a remote command so it dies when the SSH channel closes.

    Problem: ``gcloud compute ssh`` (and non-TTY ssh generally) does not reliably
    deliver SIGHUP to remote processes when the local SSH channel closes. Result:
    every ``metabrowser remote`` that's Ctrl-C'd leaves an orphan ``metabrowser serve``
    on the remote host; subsequent sessions then tunnel to the *oldest* orphan
    because the default port is taken by it.

    Fix: on the remote, run ``inner_cmd`` in the background and block on ``cat``
    reading from stdin. The SSH channel *is* stdin; when the channel closes,
    ``cat`` gets EOF, the EXIT trap fires, and the child is killed. Works
    without PTY allocation.
    """
    # Detach inner_cmd's stdin to /dev/null so it can't compete with `cat`
    # for bytes from the SSH channel. Use a subshell so the `< /dev/null`
    # redirection applies to the compound command even if it contains `&&`.
    # After `cat` returns (channel closed or signal received), kill the child
    # explicitly — we cannot rely on the trap alone because bash would
    # otherwise block in `wait` and the EXIT trap would never fire.
    script = (
        f"( {inner_cmd} ) </dev/null & PID=$!; "
        "trap 'kill $PID 2>/dev/null' INT TERM HUP EXIT; "
        "cat >/dev/null; "
        "kill $PID 2>/dev/null; "
        "wait $PID 2>/dev/null"
    )
    return "bash -c " + shlex.quote(script)


def build_ssh_command(
    host: str,
    *,
    remote_cmd: str,
    ssh_options: str = "",
    gcp: bool = False,
    zone: str = "",
    project: str = "",
    iap: bool = False,
    interactive: bool = False,
) -> list[str]:
    """Build an SSH command for a one-off remote command (no tunnel).

    Parameters
    ----------
    iap:
        Use ``--tunnel-through-iap`` for GCP VMs without external IPs.
        Only valid when ``gcp=True``.
    interactive:
        Allocate a TTY (``-t``) for commands that need terminal interaction
        (e.g. tmux attach).  For GCP mode this is passed after ``--``.
    """
    if gcp:
        cmd = ["gcloud", "compute", "ssh", host, "--zone", zone]
        if project:
            cmd += ["--project", project]
        if iap:
            cmd += ["--tunnel-through-iap"]
        cmd += ["--"]
        if interactive:
            cmd += ["-t"]
        cmd += [remote_cmd]
    else:
        cmd = ["ssh"]
        if ssh_options:
            cmd += shlex.split(ssh_options)
        if interactive:
            cmd += ["-t"]
        cmd += [host, remote_cmd]
    return cmd


def build_ssh_tunnel_command(
    host: str,
    *,
    remote_cmd: str,
    local_port: int,
    remote_port: int,
    ssh_options: str = "",
    gcp: bool = False,
    zone: str = "",
    project: str = "",
    iap: bool = False,
) -> list[str]:
    """Build an SSH command with port forwarding + remote command.

    Parameters
    ----------
    iap:
        Use ``--tunnel-through-iap`` for GCP VMs without external IPs.
        Only valid when ``gcp=True``.
    """
    tunnel_spec = f"{local_port}:localhost:{remote_port}"

    if gcp:
        cmd = ["gcloud", "compute", "ssh", host, "--zone", zone]
        if project:
            cmd += ["--project", project]
        if iap:
            cmd += ["--tunnel-through-iap"]
        cmd += ["--", "-L", tunnel_spec, remote_cmd]
    else:
        cmd = ["ssh"]
        if ssh_options:
            cmd += shlex.split(ssh_options)
        cmd += ["-L", tunnel_spec, host, remote_cmd]

    return cmd
