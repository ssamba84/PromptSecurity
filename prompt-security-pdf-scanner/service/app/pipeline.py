"""Smart inspection pipeline.

Turns one document's text into a normalized InspectResult while minimizing what
we send to the inspection API and keeping detection correct:

  1. normalize  — fold unicode, strip control chars, collapse whitespace, drop
                  empty + duplicate lines (cuts tokens/noise, so fewer chunks).
  2. chunk      — small docs stay a single chunk; large docs are split to a char
                  budget with OVERLAP so a secret straddling a boundary is still
                  wholly present in one window.
  3. dedup+cache— identical chunks are inspected once; a content-hash TTL cache
                  means repeated content / re-uploads cost 0 API calls.
  4. dispatch   — surviving chunks are inspected concurrently (bounded), reusing
                  the inspector's pooled client with retry/backoff.
  5. merge      — findings are aggregated and de-duplicated (overlap regions can
                  surface the same secret twice), and stats are recorded.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata

from app.cache import get_cache
from app.config import settings
from app.inspectors.base import Inspector
from app.models import Finding, InspectResult, InspectStats, Message

# Control chars to strip, keeping \t (09) and \n (0a).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS_RE = re.compile(r"[ \t ]+")

def clear_cache() -> None:
    get_cache().clear()


def normalize_text(text: str) -> str:
    """Denoise extracted PDF text without breaking secrets.

    Conservative: collapses whitespace runs and drops empty/duplicate lines
    (repeated page headers/footers), which cuts tokens meaningfully. It does not
    reflow words, so contiguous tokens (e.g. an AWS key) are preserved intact.
    """
    t = unicodedata.normalize("NFKC", text)
    t = _CONTROL_RE.sub("", t)
    seen: set[str] = set()
    lines: list[str] = []
    for raw_line in t.split("\n"):
        line = _WS_RE.sub(" ", raw_line).strip()
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)


def make_chunks(text: str, budget: int, overlap: int) -> list[str]:
    """Split text into overlapping windows. Small docs stay a single chunk."""
    if len(text) <= budget:
        return [text]
    if overlap >= budget:  # guard against non-progressing windows
        overlap = budget // 4
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + budget, n)
        chunks.append(text[start:end])
        if end == n:
            break
        start = end - overlap
    return chunks


async def inspect_document(text: str, inspector: Inspector, *, source: str = "document") -> InspectResult:
    t0 = time.monotonic()
    chars_in = len(text)
    normalized = normalize_text(text) if settings.enable_normalization else text
    chars_inspected = len(normalized)

    def _stats(**kw) -> InspectStats:
        return InspectStats(
            chars_in=chars_in,
            chars_inspected=chars_inspected,
            reduction_pct=round(100 * (chars_in - chars_inspected) / chars_in, 1) if chars_in else 0.0,
            duration_ms=int((time.monotonic() - t0) * 1000),
            **kw,
        )

    if not normalized.strip():
        return InspectResult(has_secrets=False, provider=inspector.name, stats=_stats())

    chunks = make_chunks(normalized, settings.chunk_char_budget, settings.chunk_overlap_chars)

    # Deduplicate identical chunks by content hash.
    by_hash: dict[str, str] = {}
    for c in chunks:
        by_hash.setdefault(hashlib.sha256(c.encode("utf-8")).hexdigest(), c)

    cache = get_cache()
    findings_by_hash: dict[str, list[Finding]] = {}
    to_call: dict[str, str] = {}
    cache_hits = 0
    for h, c in by_hash.items():
        cached = await cache.get(h)  # durable (sqlite) or in-memory
        if cached is not None:
            findings_by_hash[h] = cached
            cache_hits += 1
        else:
            to_call[h] = c

    # Inspect the surviving unique, uncached chunks with bounded concurrency.
    sem = asyncio.Semaphore(max(1, settings.max_concurrency))

    async def _run(h: str, chunk: str) -> tuple[str, list[Finding]]:
        async with sem:
            res = await inspector.inspect([Message(role="user", content=chunk)], {"source": source})
        await cache.put(h, res.findings)
        return h, res.findings

    if settings.early_exit_on_first_hit:
        # Stop as soon as any chunk reports a finding; cancel the rest.
        tasks = [asyncio.create_task(_run(h, c)) for h, c in to_call.items()]
        api_calls = 0
        try:
            for fut in asyncio.as_completed(tasks):
                h, findings = await fut
                api_calls += 1
                findings_by_hash[h] = findings
                if findings:
                    break
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)  # drain cancellations
    else:
        for h, findings in await asyncio.gather(*(_run(h, c) for h, c in to_call.items())):
            findings_by_hash[h] = findings
        api_calls = len(to_call)

    # Merge findings across chunks, de-duplicating overlap-region repeats.
    merged: list[Finding] = []
    seen: set[tuple] = set()
    for h in by_hash:
        for f in findings_by_hash.get(h, []):
            key = (f.type, f.snippet)
            if key in seen:
                continue
            seen.add(key)
            merged.append(f)

    severity = next((f.severity for f in merged if f.severity), None)
    return InspectResult(
        has_secrets=bool(merged),
        provider=inspector.name,
        findings=merged,
        severity=severity,
        stats=_stats(
            chunks=len(chunks),
            unique_chunks=len(by_hash),
            cache_hits=cache_hits,
            api_calls=api_calls,
            naive_api_calls=len(chunks),
        ),
    )
