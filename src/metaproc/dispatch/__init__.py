"""Dispatch-side helpers for per-item credential pool orchestration.

Introduced by plan-2026-04-21-auth-credential-pool.md. Houses the
credential pool state machine, slot coordinator, and preflight quota
gate that sit between :mod:`metaproc.commands.run_parallel` /
:mod:`metaproc.commands.run_process` and the
:class:`metaproc.adapters.base.AuthCapableCliAdapter` subprotocol.
"""
