"""Tests for the smart inspection pipeline (no network — uses a fake inspector)."""
import pytest

import app.cache as cache_mod
from app.config import settings
from app.inspectors.base import Inspector
from app.models import Finding, InspectResult, Message
from app.pipeline import inspect_document, make_chunks, normalize_text

MARKER = "AKIAXXAWSKEYEXAMPLE"  # 19 chars


class FakeInspector(Inspector):
    """Detects MARKER locally and counts how many API-equivalent calls happen."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    async def inspect(self, messages, metadata):
        self.calls += 1
        text = messages[0].content
        findings = [Finding(type="AWS credentials", snippet=MARKER, severity="HIGH")] if MARKER in text else []
        return InspectResult(has_secrets=bool(findings), provider=self.name, findings=findings)


# --- normalization ---------------------------------------------------------

def test_normalize_collapses_whitespace_and_dedups_lines():
    raw = "Header\nHeader\n  foo   bar \n\n\x00baz\n"
    assert normalize_text(raw) == "Header\nfoo bar\nbaz"


def test_normalize_preserves_a_secret_token():
    key = "AKIAIOSFODNN7EXAMPLE"
    assert key in normalize_text(f"my key is    {key}   \n")


def test_normalize_reduces_length():
    raw = ("Confidential Header\n" * 50) + ("word    " * 100)
    assert len(normalize_text(raw)) < len(raw)


# --- chunking + overlap ----------------------------------------------------

def test_small_doc_is_single_chunk():
    assert make_chunks("hello world", 100, 10) == ["hello world"]


def test_large_doc_is_chunked_with_overlap():
    text = "".join(str(i % 10) for i in range(300))
    chunks = make_chunks(text, 100, 20)
    assert len(chunks) > 1
    # The tail of one chunk equals the head of the next (the overlap window).
    assert chunks[0][-20:] == chunks[1][:20]
    # Full coverage: concatenating without overlap reproduces the text.
    assert chunks[0] + "".join(c[20:] for c in chunks[1:]) == text


# --- end-to-end pipeline ---------------------------------------------------

@pytest.fixture(autouse=True)
def _mem_cache(monkeypatch):
    # Hermetic tests: force the in-memory backend and a fresh cache each test.
    monkeypatch.setattr(settings, "cache_backend", "memory")
    cache_mod.reset_cache()
    yield
    cache_mod.reset_cache()


async def test_secret_on_chunk_boundary_is_caught(monkeypatch):
    monkeypatch.setattr(settings, "enable_normalization", False)
    monkeypatch.setattr(settings, "chunk_char_budget", 50)
    monkeypatch.setattr(settings, "chunk_overlap_chars", 25)
    # Place the marker starting at index 40 so it straddles the 50-char boundary.
    text = ("." * 40) + MARKER + ("." * 40)
    result = await inspect_document(text, FakeInspector())
    assert result.has_secrets is True
    assert result.stats.chunks > 1  # it really was chunked


async def test_overlap_duplicate_finding_is_deduped(monkeypatch):
    monkeypatch.setattr(settings, "enable_normalization", False)
    monkeypatch.setattr(settings, "chunk_char_budget", 50)
    monkeypatch.setattr(settings, "chunk_overlap_chars", 25)
    # Marker sits inside the overlap region -> present whole in two chunks.
    text = ("." * 30) + MARKER + ("." * 60)
    result = await inspect_document(text, FakeInspector())
    assert result.has_secrets is True
    assert len(result.findings) == 1  # merged, not double-counted


async def test_cache_makes_reupload_cost_zero_calls():
    text = f"line one\nplease rotate {MARKER}\nline three"
    fake = FakeInspector()
    r1 = await inspect_document(text, fake)
    assert r1.has_secrets and fake.calls == 1
    assert r1.stats.api_calls == 1 and r1.stats.cache_hits == 0

    r2 = await inspect_document(text, fake)  # identical content
    assert r2.has_secrets and fake.calls == 1  # no new API call
    assert r2.stats.api_calls == 0 and r2.stats.cache_hits == 1


async def test_clean_text_reports_no_secret():
    result = await inspect_document("just a normal sentence", FakeInspector())
    assert result.has_secrets is False
    assert result.stats.api_calls == 1


async def test_early_exit_stops_after_first_hit(monkeypatch):
    monkeypatch.setattr(settings, "enable_normalization", False)
    monkeypatch.setattr(settings, "chunk_char_budget", 30)
    monkeypatch.setattr(settings, "chunk_overlap_chars", 0)
    monkeypatch.setattr(settings, "max_concurrency", 1)  # deterministic ordering
    monkeypatch.setattr(settings, "early_exit_on_first_hit", True)
    # Marker is in the first 30-char chunk; several chunks follow.
    text = MARKER + ("." * 120)  # ~5 chunks of 30 chars
    result = await inspect_document(text, FakeInspector())
    assert result.has_secrets is True
    assert result.stats.chunks > 1
    assert result.stats.api_calls == 1  # exited after the first chunk; rest skipped


async def test_early_exit_off_inspects_all_chunks(monkeypatch):
    monkeypatch.setattr(settings, "enable_normalization", False)
    monkeypatch.setattr(settings, "chunk_char_budget", 30)
    monkeypatch.setattr(settings, "chunk_overlap_chars", 0)
    monkeypatch.setattr(settings, "early_exit_on_first_hit", False)
    text = MARKER + ("." * 120)
    result = await inspect_document(text, FakeInspector())
    assert result.has_secrets is True
    # All unique chunks inspected (identical dot-chunks are de-duped, so compare
    # against unique_chunks, not raw chunks).
    assert result.stats.api_calls == result.stats.unique_chunks
