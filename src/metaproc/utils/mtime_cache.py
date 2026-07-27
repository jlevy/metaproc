"""Mtime-keyed in-memory LRU cache for derived file values.

Adapted from kash's ``mtime_cache.py`` (https://github.com/jlevy/kash). Wraps
``cachetools.LRUCache`` keyed by absolute path; each entry stores a
``strif.file_mtime_hash`` fingerprint of ``(name, size, st_mtime_ns)``.

``read()`` returns a :class:`CacheRead` discriminating three outcomes so
callers can distinguish "cache miss, please re-derive" from "file vanished,
do not retry":

- hit:    ``value`` is the cached payload, ``present`` is True
- miss:   ``value`` is None, ``present`` is True (caller re-derives + updates)
- absent: ``value`` is None, ``present`` is False (file gone, caller skips)
"""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cachetools import LRUCache
from strif import file_mtime_hash

log = logging.getLogger(__name__)


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    updates: int = 0
    deletes: int = 0
    absents: int = 0


@dataclass(frozen=True, slots=True)
class CacheRead[T]:
    """Result of :meth:`MtimeCache.read`. See module docstring."""

    value: T | None = None
    present: bool = True

    @property
    def hit(self) -> bool:
        return self.value is not None and self.present

    @property
    def absent(self) -> bool:
        return not self.present


_ABSENT: CacheRead[Any] = CacheRead(value=None, present=False)
_MISS: CacheRead[Any] = CacheRead(value=None, present=True)


class MtimeCache[T]:
    """In-memory LRU cache with mtime-based invalidation."""

    def __init__(self, max_size: int, name: str, log_freq: int = 500):
        self.cache: LRUCache[str, tuple[str, T]] = LRUCache(maxsize=max_size)
        self.lock = threading.RLock()
        self.stats = CacheStats()
        self.prev_stats = CacheStats()
        self.name = name
        self.log_freq = log_freq

    def _cache_key(self, path: Path) -> str:
        return str(path.resolve())

    def read(self, path: Path) -> CacheRead[T]:
        """Look up ``path``. Returns a hit, miss, or absent CacheRead."""
        try:
            mtime_hash = file_mtime_hash(path)
        except FileNotFoundError:
            key = self._cache_key(path)
            with self.lock:
                if key in self.cache:
                    del self.cache[key]
                self.stats.absents += 1
                self.log_stats()
            return _ABSENT
        key = self._cache_key(path)
        with self.lock:
            self.log_stats()
            cache_entry = self.cache.get(key)
            if cache_entry:
                cached_mtime_hash, cached_value = cache_entry
                if cached_mtime_hash == mtime_hash:
                    self.stats.hits += 1
                    return CacheRead(value=copy.deepcopy(cached_value), present=True)
                del self.cache[key]
            self.stats.misses += 1
        return _MISS

    def update(self, path: Path, value: T) -> None:
        key = self._cache_key(path)
        value = copy.deepcopy(value)
        try:
            mtime_hash = file_mtime_hash(path)
        except FileNotFoundError:
            return
        with self.lock:
            self.log_stats()
            self.cache[key] = (mtime_hash, value)
            self.stats.updates += 1

    def delete(self, path: Path) -> None:
        key = self._cache_key(path)
        with self.lock:
            self.log_stats()
            if key in self.cache:
                del self.cache[key]
                self.stats.deletes += 1

    def log_stats(self) -> None:
        if self._stats_changed(self.log_freq):
            log.info(
                "%s file cache stats: hits: %s, misses: %s, updates: %s, deletes: %s, absents: %s",
                self.name,
                self.stats.hits,
                self.stats.misses,
                self.stats.updates,
                self.stats.deletes,
                self.stats.absents,
            )
            self.prev_stats = copy.deepcopy(self.stats)

    def _stats_changed(self, threshold: int) -> bool:
        return (
            max(
                self.stats.hits - self.prev_stats.hits,
                self.stats.misses - self.prev_stats.misses,
                self.stats.updates - self.prev_stats.updates,
                self.stats.deletes - self.prev_stats.deletes,
                self.stats.absents - self.prev_stats.absents,
            )
            > threshold
        )
