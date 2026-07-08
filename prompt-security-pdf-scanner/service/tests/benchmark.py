"""Benchmark / demo for the smart inspection pipeline.

Builds a large, noisy multi-page PDF (repeated headers/footers + filler + one
AWS key), sends it to a running service, and prints the pipeline stats twice to
show:
  - noise reduction from normalization,
  - chunking with overlap,
  - API calls made vs. a naive per-chunk baseline,
  - the content-hash cache making a re-upload cost zero API calls.

Usage (service must be running):
    python -m tests.benchmark [service_url]
    python -m tests.benchmark http://127.0.0.1:8000
"""
from __future__ import annotations

import io
import sys

import httpx
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

SECRET_KEY = "AKIAIOSFODNN7EXAMPLE"


def build_noisy_pdf(num_lines: int = 1500) -> bytes:
    """A large PDF: repeated boilerplate (dedup-able) + unique filler + one key."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    y = height - 72
    text = c.beginText(72, y)

    def newline(s: str):
        nonlocal text, y
        text.textLine(s)
        y -= 14
        if y < 72:
            c.drawText(text)
            c.showPage()
            reset()

    def reset():
        nonlocal text, y
        y = height - 72
        text = c.beginText(72, y)

    for i in range(num_lines):
        # Repeated boilerplate header/footer (normalization dedups these).
        newline("CONFIDENTIAL - INTERNAL DISTRIBUTION ONLY")
        # Unique content with extra whitespace noise.
        newline(f"Record {i:05d}:   status = ok      region = us-east-1     ")
        if i == num_lines // 2:
            newline(f"AWS_ACCESS_KEY_ID = {SECRET_KEY}")
    c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()


def show(label: str, r: dict) -> None:
    s = r.get("stats") or {}
    print(f"\n=== {label} ===")
    print(f"  has_secrets : {r.get('has_secrets')}")
    print(f"  findings    : {[(f['type'], f['snippet']) for f in r.get('findings', [])]}")
    print(f"  chars       : {s.get('chars_in')} -> {s.get('chars_inspected')}  (-{s.get('reduction_pct')}% noise)")
    print(f"  chunks      : {s.get('chunks')} (unique {s.get('unique_chunks')})")
    print(f"  API calls   : {s.get('api_calls')}   vs naive per-chunk: {s.get('naive_api_calls')}   cache hits: {s.get('cache_hits')}")
    print(f"  latency     : {s.get('duration_ms')} ms")


def main() -> None:
    url = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    pdf = build_noisy_pdf()
    print(f"Generated PDF: {len(pdf):,} bytes; posting to {url}/inspect/file")

    with httpx.Client(timeout=60) as client:
        r1 = client.post(f"{url}/inspect/file", files={"file": ("big.pdf", pdf, "application/pdf")})
        r1.raise_for_status()
        show("First upload (cold cache)", r1.json())

        r2 = client.post(f"{url}/inspect/file", files={"file": ("big.pdf", pdf, "application/pdf")})
        r2.raise_for_status()
        show("Second upload (warm cache — re-upload)", r2.json())


if __name__ == "__main__":
    main()
