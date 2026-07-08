"""Tests for the durable cache backend."""
import pytest

from app.cache import MemoryCache, SqliteCache
from app.config import settings
from app.models import Finding

FINDINGS = [Finding(type="AWS credentials", snippet="AKIAIOSFODNN7EXAMPLE", severity="HIGH")]


async def test_memory_roundtrip():
    c = MemoryCache()
    assert await c.get("h1") is None
    await c.put("h1", FINDINGS)
    got = await c.get("h1")
    assert got[0].snippet == "AKIAIOSFODNN7EXAMPLE"


async def test_sqlite_persists_across_instances(tmp_path):
    db = str(tmp_path / "cache.db")
    c1 = SqliteCache(db)
    await c1.put("hABC", FINDINGS)
    c1.close()

    # A brand-new instance on the same file == "after a restart".
    c2 = SqliteCache(db)
    got = await c2.get("hABC")
    assert got is not None and got[0].type == "AWS credentials"
    c2.close()


async def test_sqlite_ttl_expiry(tmp_path, monkeypatch):
    db = str(tmp_path / "cache.db")
    c = SqliteCache(db)
    await c.put("h", FINDINGS)
    assert await c.get("h") is not None
    monkeypatch.setattr(settings, "cache_ttl_seconds", -1)  # everything expired / disabled
    assert await c.get("h") is None
    c.close()
