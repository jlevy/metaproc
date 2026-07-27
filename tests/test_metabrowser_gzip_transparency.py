"""Round-trip equivalence tests: a ``foo.jsonl.gz`` produces the same
envelopes as its uncompressed twin (modulo additive ``.gz``-only fields).

These tests pin the byte-identical-envelope contract that the whole
ArtifactPath abstraction depends on. If a future change accidentally
forks behavior between gzipped and plain artifacts, one of these
assertions trips.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import Any

from metabrowser import server as proc_browser
from starlette.responses import FileResponse, StreamingResponse

from metaproc.metabrowser_plugin import sidekick

# Real Claude-format events so the parser branch produces meaningful
# output (adapter detection wants ~20 lines minimum).
_CLAUDE_EVENTS = [
    '{"type":"system","subtype":"init","cwd":"/tmp","tools":["Read"]}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Hello"}]}}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Read","input":{}}]}}',
    '{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}',
    '{"type":"result","subtype":"success","duration_ms":42,"total_cost_usd":0.001,"is_error":false}',
] * 5
_PAYLOAD_BYTES = ("\n".join(_CLAUDE_EVENTS) + "\n").encode("utf-8")


class _Params:
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)


class _Headers:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self._data = {k.lower(): v for k, v in (data or {}).items()}

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key.lower(), default)


class _FakeRequest:
    def __init__(self, path: str, accept_encoding: str = "") -> None:
        self.query_params = _Params({"path": path})
        self.headers = _Headers({"accept-encoding": accept_encoding})


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    plain = tmp_path / "events.jsonl"
    plain.write_bytes(_PAYLOAD_BYTES)
    gz = tmp_path / "events.jsonl.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(_PAYLOAD_BYTES)
    proc_browser._set_root_dir(tmp_path)
    return plain, gz


# Fields that are *expected* to differ between the .gz and plain
# responses. Stripping them yields the contract-shape that must match.
_VOLATILE_FIELDS = {
    "path",
    "size",
    "mtime_hash",
    "size_uncompressed",
    "logical_ext",
    "compressed",
    "compression",
}


def _strip_volatile(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k not in _VOLATILE_FIELDS}


def _api_file(path: str) -> dict[str, Any]:
    fake = _FakeRequest(path)
    response = asyncio.run(proc_browser.api_file(fake))  # pyright: ignore[reportArgumentType]
    return json.loads(bytes(response.body).decode())


def _api_charts(path: str) -> dict[str, Any]:
    fake = _FakeRequest(path)
    response = asyncio.run(sidekick.charts_handler(fake))  # pyright: ignore[reportArgumentType]
    return json.loads(bytes(response.body).decode())


# ── /api/file equivalence ─────────────────────────────────────────


def test_api_file_envelopes_match_modulo_volatile_fields(tmp_path: Path) -> None:
    _setup(tmp_path)
    plain_env = _api_file("events.jsonl")
    gz_env = _api_file("events.jsonl.gz")

    # Strip volatile fields and assert the contract-shape matches exactly.
    assert _strip_volatile(plain_env) == _strip_volatile(gz_env)

    # Volatile fields behave per spec:
    assert plain_env["size"] == len(_PAYLOAD_BYTES)
    assert gz_env["size"] < len(_PAYLOAD_BYTES)  # gz is smaller on disk
    assert gz_env["size_uncompressed"] == len(_PAYLOAD_BYTES)
    assert gz_env["logical_ext"] == ".jsonl"
    assert gz_env["compressed"] is True
    assert gz_env["compression"] == "gzip"
    assert "size_uncompressed" not in plain_env
    assert "compressed" not in plain_env


# ── /api/charts equivalence ───────────────────────────────────────


def test_api_charts_returns_same_chart_data_for_both_forms(tmp_path: Path) -> None:
    _setup(tmp_path)
    plain_charts = _api_charts("events.jsonl")
    gz_charts = _api_charts("events.jsonl.gz")
    # Chart payload depends only on parsed events, not on disk encoding.
    assert plain_charts == gz_charts


# ── /raw decompressed-body equivalence ─────────────────────────────


def test_raw_gz_decompressed_body_matches_plain_file(tmp_path: Path) -> None:
    """When the client doesn't accept gzip, /raw stream-decompresses on
    the fly. The decoded bytes must equal the plain file's bytes."""
    _setup(tmp_path)
    fake = _FakeRequest("events.jsonl.gz", accept_encoding="identity")
    response = asyncio.run(proc_browser.raw_file(fake))  # pyright: ignore[reportArgumentType]
    assert isinstance(response, StreamingResponse)

    chunks: list[bytes] = []

    async def _drain() -> None:
        async for chunk in response.body_iterator:
            assert isinstance(chunk, (bytes, bytearray, memoryview))
            chunks.append(bytes(chunk))

    asyncio.run(_drain())
    decompressed = b"".join(chunks)
    assert decompressed == _PAYLOAD_BYTES


def test_raw_gz_passthrough_body_is_gzip_bytes_decoding_to_plain(tmp_path: Path) -> None:
    """When the client accepts gzip, /raw ships the on-disk bytes
    verbatim (no decompression). Manually gunzip them and assert they
    decode to the plain payload."""
    _, gz = _setup(tmp_path)
    fake = _FakeRequest("events.jsonl.gz", accept_encoding="gzip")
    response = asyncio.run(proc_browser.raw_file(fake))  # pyright: ignore[reportArgumentType]
    assert isinstance(response, FileResponse)
    assert response.headers["content-encoding"] == "gzip"
    # FileResponse points at the on-disk file. Read it directly and
    # assert it gunzips to the original payload.
    on_disk = Path(response.path).read_bytes()
    assert on_disk == gz.read_bytes()
    assert gzip.decompress(on_disk) == _PAYLOAD_BYTES


# ── /api/stream rejects .gz ────────────────────────────────────────


def test_api_stream_returns_400_for_gz(tmp_path: Path) -> None:
    """Sealed-log invariant from the streaming side."""
    _setup(tmp_path)
    fake = _FakeRequest("events.jsonl.gz")
    response = asyncio.run(proc_browser.api_stream(fake))  # pyright: ignore[reportArgumentType]
    assert response.status_code == 400
