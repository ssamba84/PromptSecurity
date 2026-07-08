"""Chunk-result cache with a pluggable, optionally durable backend.

The cache is content-addressed: key = sha256(normalized chunk), value = the
findings for that chunk. Two consequences:

  * Re-uploads and shared boilerplate cost zero API calls.
  * With a DURABLE backend (sqlite), this doubles as "resume where we left off":
    if the service is interrupted mid-inspection, on retry every already-done
    chunk is a cache hit read from disk, so only the unfinished chunks are sent
    to the API again.

Backend is chosen by settings.cache_backend ("memory" | "sqlite").
"""
from __future__ import annotations

import abc
import asyncio
import json
import sqlite3
import threading
import time

from app.config import settings
from app.models import Finding


class Cache(abc.ABC):
    @abc.abstractmethod
    async def get(self, key: str) -> list[Finding] | None: ...

    @abc.abstractmethod
    async def put(self, key: str, findings: list[Finding]) -> None: ...

    @abc.abstractmethod
    def clear(self) -> None: ...

    def close(self) -> None:  # optional
        pass

    @staticmethod
    def _expired(stored_at: float) -> bool:
        ttl = settings.cache_ttl_seconds
        return ttl > 0 and (time.time() - stored_at) > ttl

    @staticmethod
    def _encode(findings: list[Finding]) -> str:
        return json.dumps([f.model_dump() for f in findings])

    @staticmethod
    def _decode(payload: str) -> list[Finding]:
        return [Finding(**d) for d in json.loads(payload)]


class MemoryCache(Cache):
    """In-process cache. Fast, but lost on restart."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, list[Finding]]] = {}

    async def get(self, key: str) -> list[Finding] | None:
        if settings.cache_ttl_seconds <= 0:
            return None
        item = self._d.get(key)
        if not item:
            return None
        stored_at, findings = item
        if self._expired(stored_at):
            self._d.pop(key, None)
            return None
        return findings

    async def put(self, key: str, findings: list[Finding]) -> None:
        if settings.cache_ttl_seconds > 0:
            self._d[key] = (time.time(), findings)

    def clear(self) -> None:
        self._d.clear()


class SqliteCache(Cache):
    """Durable cache backed by a local SQLite file — survives restarts.

    SQLite calls are synchronous; they're tiny, but we run them via
    asyncio.to_thread so the event loop is never blocked, and guard the single
    shared connection with a lock (chunks are inspected concurrently).
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS chunk_cache ("
            "  hash TEXT PRIMARY KEY,"
            "  findings TEXT NOT NULL,"
            "  stored_at REAL NOT NULL"
            ")"
        )
        self._conn.commit()

    async def get(self, key: str) -> list[Finding] | None:
        if settings.cache_ttl_seconds <= 0:
            return None
        return await asyncio.to_thread(self._get, key)

    def _get(self, key: str) -> list[Finding] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT findings, stored_at FROM chunk_cache WHERE hash = ?", (key,)
            ).fetchone()
        if not row:
            return None
        payload, stored_at = row
        if self._expired(stored_at):
            with self._lock:
                self._conn.execute("DELETE FROM chunk_cache WHERE hash = ?", (key,))
                self._conn.commit()
            return None
        return self._decode(payload)

    async def put(self, key: str, findings: list[Finding]) -> None:
        if settings.cache_ttl_seconds <= 0:
            return
        await asyncio.to_thread(self._put, key, findings)

    def _put(self, key: str, findings: list[Finding]) -> None:
        payload = self._encode(findings)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO chunk_cache (hash, findings, stored_at) VALUES (?, ?, ?)",
                (key, payload, time.time()),
            )
            self._conn.commit()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunk_cache")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# --- singleton, chosen by config -------------------------------------------

_instance: Cache | None = None


def get_cache() -> Cache:
    global _instance
    if _instance is None:
        if settings.cache_backend == "sqlite":
            _instance = SqliteCache(settings.cache_db_path)
        else:
            _instance = MemoryCache()
    return _instance


def reset_cache() -> None:
    """Rebuild the cache from current settings (used by tests / reconfig)."""
    global _instance
    if _instance is not None:
        _instance.close()
    _instance = None
