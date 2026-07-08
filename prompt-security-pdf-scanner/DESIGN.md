# Design decisions & trade-offs

This documents *why* the solution is shaped the way it is. For setup/usage see the
[README](./README.md).

## Architecture in one line

A thin **Chrome extension** (sensor) captures PDF uploads and shows alerts; a
**FastAPI service** does the heavy lifting (extraction, inspection, caching) and
holds the API key. They talk over a small HTTP contract.

## Key decisions

1. **Thin sensor, backend brains.** The extension only captures and alerts; PDF
   parsing, the third-party API call, the API key, caching, and logging live in
   the service. Rationale: never ship a secret-bearing API key to client JS, keep
   heavy/regressable logic server-side, and centralize policy/logging.

2. **Provider behind one interface (`Inspector`).** `inspect(messages, metadata)
   -> InspectResult`. Swapping Prompt Security for another backend is one class +
   one env var; the routes, pipeline, and extension never change — so the whole
   pipeline is reusable across inspection providers.

3. **Capture via DOM events, not the network layer.** We hook file-picker
   `change` / drag-drop / paste. It's simple and reliable and satisfies the
   assignment. The production-grade upgrade is a **network-level hook** (wrap
   `fetch`/XHR to catch the actual upload regardless of UI) — described, not built,
   because it's browser-heavy and more coupled to ChatGPT internals.

4. **The pipeline treats chunking as correctness, not just speed.** I probed the
   undocumented API: it **silently truncates at ~48,500 tokens** and returns `200`
   with no error — a secret past the cutoff is silently missed. So a whole-document
   call can report a large PDF "clean" while it hides a secret. The pipeline
   therefore: **normalizes** (cuts ~55% of characters as noise), **chunks under the
   measured cap with overlap** (boundary secrets can't split), inspects chunks with
   **bounded parallelism + retry/backoff**, and **de-dupes findings** from overlap
   regions.

5. **Durable, content-addressed cache ⇒ resume for free.** The cache key is
   `sha256(chunk)`. Making it durable (SQLite) means an interrupted run resumes on
   retry: already-inspected chunks are read from disk, only the rest hit the API.
   No separate job-state machine needed for the common case.

6. **Secrets are never written at rest.** Findings carry the provider's
   `sanitized_entity` (`[REDACTED_...]`), never the raw value; the cache stores only
   `hash → {type, severity, redacted}`; logs record finding *types* only. Enforced
   by a test that asserts the raw key never appears in the DB file.

7. **Results are logged.** Each inspection logs a structured line (verdict + the
   pipeline stats: chars in→out, chunks, cache hits, api calls, latency) — WARNING
   when a secret is found, INFO when clean, and never the raw secret.

## Trade-offs & things deliberately NOT built

Scoping to a few-hour assignment, these are described rather than built (over-
building reads as poor prioritization):

- **Network-level capture** — most robust sensor, but browser-heavy.
- **Egress minimization** (local candidate pass → send only suspicious spans) —
  the sharpest idea, but it trades a little recall for privacy; a tunable knob,
  off by default.
- **Multi-tenancy / auth** — the service is single-tenant, localhost, no auth.
- **OCR for scanned PDFs, blocking mode, more file types / AI sites, Redis-shared
  cache, explicit job table.** All listed as next steps in the README.

## Deployment

Productized, the backend most likely runs **hosted, HTTPS, multi-tenant**, with a
centrally-managed extension pointing at it and Prompt Security called server-side.
Because that routes all content through your servers, the security-conscious
variants run inspection in the customer's **VPC/tenant** or push
**egress-minimization** to the endpoint. See the README's *Deployment models*.

## Testing strategy

- **Unit**: PDF extraction, the Prompt Security adapter (HTTP mocked with respx),
  the pipeline (normalization, overlap, boundary-secret, overlap-dedup, cache),
  and the durable cache (persists across instances; TTL; no-raw-secret-at-rest).
- **Live**: `tests/benchmark.py` drives the real API with a large noisy PDF and
  prints the pipeline stats; a probe script measured the token-truncation limit.
- **CI**: GitHub Actions runs the unit suite on every push.
