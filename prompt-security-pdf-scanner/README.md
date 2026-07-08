# PDF Secret Scanner — Chrome extension + inspection service (Prompt Security)

Captures **PDF uploads to ChatGPT**, sends them to a local **file inspection
service**, and if the PDF contains a secret (e.g. an AWS key like
`AKIAIOSFODNN7EXAMPLE`) shows a **non-blocking pop-up** and logs the result.
Secret detection uses the **Prompt Security** text inspection API.

![Non-blocking alert shown on ChatGPT when a secret is detected](docs/alert-mockup.svg)

> *UI rendering of the in-page alert (`extension/src/alert.js`). Replace with a real
> screenshot from your browser if you prefer.*

📐 See **[DESIGN.md](./DESIGN.md)** for design decisions & trade-offs, and
**[docs/SURVEY.md](./docs/SURVEY.md)** for a side survey of local detection (regex /
entropy / combined) vs. the API — latency, accuracy, and memory.

---

## How it works

```
Chrome extension (MV3)                         Inspection service (FastAPI)
──────────────────────                         ────────────────────────────
content.js captures a PDF upload
  (file picker / drag-drop / paste)
        │  base64 via runtime message
        ▼
background.js ── multipart POST ─────────────▶ POST /inspect/file
                                                 │  pdf.py  → extract text
   ◀── { has_secrets, findings, ... } ───────── │  → [{role:"user", content}]
        │                                        │  inspector.inspect(messages)
        ▼                                        │  → Prompt Security /api/protect
alert.js shows a non-blocking modal             │  log result (stdout)
  if has_secrets
```

- **Capture** is non-blocking: the extension only *observes* the upload (it never
  calls `preventDefault`), so ChatGPT still receives the file.
- **Extraction** happens in the service (`pypdf`), keeping the extension thin.
- **Inspection** goes through one `Inspector` interface, so the backend is swappable.

## Repo layout

```
extension/                  Chrome MV3 extension (unpacked)
  manifest.json
  src/
    content.js              entry: pick site profile, wire capturer, show alerts
    site_profiles.js        hostname → capture config
    capturers/file_upload.js  capture PDF uploads (picker / drag-drop / paste)
    background.js           service worker: POST to backend, store results
    alert.js / alert.css    injected non-blocking modal
    popup/                  toolbar popup: service URL, on/off, recent results
  icons/
service/                    Python FastAPI inspection service
  app/
    main.py                 POST /inspect, POST /inspect/file, GET /health
    config.py               env / .env settings (incl. pipeline knobs)
    models.py               Message, Finding, InspectResult, InspectStats
    pdf.py                  PDF → text (pypdf)
    pipeline.py             smart pipeline: normalize → chunk+overlap → dedup/cache → parallel → merge
    cache.py                pluggable chunk-result cache (memory | durable sqlite)
    inspectors/
      base.py               Inspector interface (the seam)
      prompt_security.py    Prompt Security adapter (pooled client + retry/backoff)
  tests/                    pytest suite + PDF fixtures + benchmark.py
  requirements.txt
  .env.example
tools/
  secret_detection_survey.py  local regex/entropy/combined vs API survey (see docs/SURVEY.md)
docs/
  SURVEY.md                 local-vs-API detection survey (latency/accuracy/memory)
  alert-mockup.svg
```

---

## Prerequisites

- **Python 3.10+** built against a modern TLS stack (OpenSSL 1.1.1+/3.x).
  > ⚠️ macOS's *system* `python3` (3.9, LibreSSL 2.8.3) fails the HTTPS call to
  > Prompt Security with `TLSV1_ALERT_PROTOCOL_VERSION`. Use Homebrew /
  > python.org Python (e.g. `python3.13`). Check with:
  > `python3 -c "import ssl; print(ssl.OPENSSL_VERSION)"`
- **Google Chrome** (or any Chromium browser with MV3 support).

## 1) Run the inspection service

```bash
cd service
python3.13 -m venv .venv            # any Python 3.10+ with modern OpenSSL
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                # then set PROMPT_SECURITY_APP_ID in .env
uvicorn app.main:app --port 8000
```

Health check: `curl http://127.0.0.1:8000/health` → `{"status":"ok","inspector":"prompt-security"}`

> If port 8000 is busy, run on another port (`--port 8010`) and set the same URL
> in the extension popup.

## 2) Load the extension

1. Open `chrome://extensions` → enable **Developer mode** (top-right).
2. **Load unpacked** → select the `extension/` folder.
3. Click the extension icon → confirm **Inspection service URL** = `http://127.0.0.1:8000`
   (matches the port above). The default is `127.0.0.1`, not `localhost`, to avoid
   an IPv6 (`::1`) resolution mismatch with uvicorn.

---

## Testing

### A. Service only (no browser)

```bash
python -m tests.make_fixtures                                   # generate fixtures
curl -F file=@tests/fixtures/secret.pdf http://localhost:8000/inspect/file   # has_secrets: true
curl -F file=@tests/fixtures/clean.pdf  http://localhost:8000/inspect/file   # has_secrets: false
```

Expected for `secret.pdf`:
```json
{"has_secrets": true, "provider": "prompt-security", "severity": "HIGH",
 "findings": [{"type": "AWS credentials", "snippet": "AKIAIOSFODNN7EXAMPLE", "severity": "HIGH"}]}
```
The service logs each inspection (WARNING when a secret is found, INFO when clean).

### B. Full browser flow

1. With the service running and the extension loaded, open <https://chatgpt.com>.
2. Upload `service/tests/fixtures/secret.pdf` → a red **“Potential secret detected”**
   modal appears (top-right, dismissable); the upload is **not** blocked.
3. Upload `clean.pdf` → no alert.
4. Open the extension popup → **Recent inspections** lists both.

### C. Unit tests

```bash
cd service && source .venv/bin/activate
pytest -q          # PDF extraction, Prompt Security adapter, and pipeline (HTTP mocked)
```

### D. Pipeline benchmark (live)

With the service running, generate a large, noisy multi-page PDF (repeated
headers + filler + one planted AWS key) and print the pipeline stats twice:

```bash
python -m tests.benchmark http://127.0.0.1:8000
```

Representative output:
```
=== First upload (cold cache) ===
  has_secrets : True
  findings    : [('AWS credentials', 'AKIAIOSFODNN7EXAMPLE')]
  chars       : 148598 -> 67582  (-54.5% noise)
  chunks      : 2 (unique 2)
  API calls   : 2   vs naive per-chunk: 2   cache hits: 0
  latency     : 1340 ms
=== Second upload (warm cache — re-upload) ===
  API calls   : 0   vs naive per-chunk: 2   cache hits: 2
  latency     : 3 ms
```

---

## Smart inspection pipeline

For large / repetitive documents, the service doesn't just forward the whole
text in one call. `pipeline.py` runs five steps, each returning measurable
`stats` on the response:

1. **Normalize (fewer tokens sent).** Unicode NFKC fold, strip control chars,
   collapse whitespace runs, drop empty + duplicate lines (repeated page
   headers/footers). In the benchmark this removes **~55%** of characters. It's
   deliberately conservative — it never reflows words, so a contiguous secret
   token stays intact.
2. **Chunk with overlap (correctness).** Small docs stay a single call. Large
   docs are split to `chunk_char_budget` with `chunk_overlap_chars` of overlap,
   so a secret straddling a chunk boundary is still wholly present in one window
   and can't slip through the cracks.
3. **Dedup + durable content-hash cache (fewer calls, zero recall loss, resume).**
   Identical chunks are inspected once; a TTL cache keyed by `sha256(chunk)` means
   repeated content and re-uploads cost **0** API calls (benchmark: re-upload 2→0
   calls, 1340ms→3ms). The default backend is **SQLite**, so the cache *survives
   restarts* — see [Durability & resume](#durability--resume).
4. **Bounded-parallel dispatch (latency + resilience).** Surviving chunks are
   inspected concurrently (`max_concurrency`) over a pooled keep-alive client,
   with retry + exponential backoff on 429/5xx/network errors. Optionally,
   `early_exit_on_first_hit` cancels the remaining chunks as soon as one flags
   (cheaper, but reports only the first finding) — off by default so a scan
   reports everything.
5. **Merge + dedup findings.** Findings are aggregated and de-duplicated by
   `(type, entity)` so the same secret seen in an overlap region isn't reported
   twice.

Config knobs (env / `.env`): `enable_normalization`, `chunk_char_budget`,
`chunk_overlap_chars`, `max_concurrency`, `early_exit_on_first_hit`,
`cache_backend`, `cache_db_path`, `cache_ttl_seconds`, `max_retries`,
`retry_base_seconds`.

### Durability & resume

The cache backend is pluggable (`cache_backend = sqlite | memory`). The default,
**sqlite**, persists `chunk_hash → findings` to `cache_db_path` (a local file).

Because the cache is **content-addressed and durable**, an interrupted inspection
**resumes for free**: on retry, every already-inspected chunk is read from disk,
so only the unfinished chunks call the API — no separate job-state machine needed.

Demonstrated end-to-end:
```
run 1: upload large PDF        -> API calls: 2   (2 chunk results written to sqlite)
       << kill the service >>            (in-memory state gone; inspection_cache.db remains)
run 2: restart (fresh process) -> upload same PDF
       first upload            -> API calls: 0, cache hits: 2, 4 ms   (served from disk)
```

**Level 2 (design, not built):** an explicit **job table** (`job_id`, per-chunk
`pending`/`done` status + result) for progress reporting and mid-*request* resume;
and swapping SQLite for **Redis/Postgres** so the cache/jobs are shared across
horizontally-scaled service instances.

### Data handling

- **In transit:** the extension sends the PDF bytes to the backend; the backend
  sends *extracted text* to Prompt Security. That egress is inherent to inspecting
  content (see egress-minimization under [Performance/scaling → Next](#performance--scaling)).
- **At rest:** the durable cache stores only `chunk_hash → {finding type, severity,
  redacted value}`. It never stores raw file contents (bytes/chunk text are held in
  memory for the request only) and **never stores the raw secret** — findings carry
  Prompt Security's `sanitized_entity` (e.g. `[REDACTED_AWS_CREDENTIALS_1]`), not the
  real value. Logs record finding *types* only. (Enforced by a test that asserts the
  raw key never appears in the DB file.)
- **Tenancy:** single-tenant, no auth (dev posture). Production needs authn/z and a
  per-tenant cache namespace — see Production-readiness.

### Why chunking is a *correctness* requirement (measured)

Prompt Security doesn't publish request limits, so I probed `/api/protect` with
prompts from 1 KB to 2 MB (a trailing AWS key in each):

| prompt size | HTTP | tokens counted | secret detected? |
|---|---|---|---|
| ≤ 100 KB | 200 | up to ~18,500 | **yes** |
| 250 KB | 200 | 46,306 | **no** |
| ≥ 500 KB | 200 | plateaus at **48,547** | **no** |

Two things stand out: the API **never errors** (always `200`), and it **silently
truncates at ~48,500 tokens** — the trailing secret is dropped from ~250 KB up,
with no signal that anything was skipped. So a naive "send the whole document"
approach would **silently report a large PDF as clean even when it contains a
secret past the cutoff.** Chunking each window well under that cap
(`chunk_char_budget = 40,000` ≈ 8-10k tokens, ~5× margin) is what makes detection
correct on large files — not just faster.

---

## Limitations

- **Scanned / image-only PDFs** have no text layer → nothing extracted (needs OCR).
- **Encrypted PDFs** handled only if they open with an empty password.
- Capture happens at **file selection** (picker / drag-drop / paste). A future
  ChatGPT upload path that never surfaces a `File` through DOM events could be missed.
- **Very large PDFs** are chunked with overlap (see the pipeline). One residual
  edge case: if PDF extraction splits a *single* secret token across a line break,
  normalization keeps the break rather than reflowing, so the token stays broken —
  overlap can't help *within* a token. Rare in practice for key-like secrets.
- The alert is **advisory, not blocking** — by design per the assignment.
- **Trusted Types**: chatgpt.com enforces Trusted Types, so the alert is built with
  `createElement`/`textContent` (never `innerHTML`, which would throw).
- **Dev-only posture**: `http://127.0.0.1`, permissive CORS, no service auth. The
  Prompt Security `APP-ID` is read from `service/.env` (gitignored) and is never
  committed; the service refuses to inspect if it's missing.
- **ChatGPT only** (`chatgpt.com`, `chat.openai.com`) — other sites need a match
  pattern + a `site_profiles.js` entry.
- Requires **Python with modern OpenSSL** (see Prerequisites).

## What it needs to be production-ready

- **AuthN/Z + rate limiting** on the service; per-org policy config.
- **Secret management** for the APP-ID (vault / secret manager), not `.env`.
- **Hosted HTTPS endpoint** instead of localhost; CORS locked to the extension origin.
- **User consent & privacy notice** — file content leaves the device to a third-party API.
- **Resilience**: retries with backoff, timeouts, circuit breaker.
- **Observability**: structured logs, metrics, tracing, audit trail of detections.
- **Packaging**: signed extension via the Chrome Web Store + enterprise managed policy.
- **Broader coverage**: more file types (docx, txt, pptx, images+OCR), more AI sites,
  optional blocking mode.

## Deployment models (productization)

Today the service runs on `localhost` — a development stand-in. Productized, the
backend would run one of three ways:

| Model | Where it runs | Pros | Cons |
|---|---|---|---|
| **A. Hosted, multi-tenant** (typical for enterprise DLP) | Central cloud / on-prem server; centrally-managed extension calls it over HTTPS | Central policy/logging/updates; **API key stays server-side**; easy to scale | **All inspected content transits your servers** → requires multi-tenancy, authn/z, isolation, compliance |
| **B. Local agent per device** (Chrome native messaging) | A companion app/daemon on each endpoint; extension talks to it locally | Raw content **stays on the endpoint** until it hits the sanctioned API — best privacy | Ship + maintain a native app on every device; heavier ops |
| **C. No backend (WASM in the extension)** | Parsing + API calls run inside the extension | No server to operate | **API key exposed to the client**; limited heavy processing; loses central policy/logging — usually a non-starter for DLP |

**Most likely: Model A** — a hosted, HTTPS, multi-tenant service with the extension
deployed via enterprise policy and Prompt Security called server-side. Our
`localhost` + no-auth + single-tenant setup is exactly the dev shortcut; the gap to
production is the Production-readiness list above.

Because Model A means *all* content flows through your servers, the security-
conscious variants either **run inspection in the customer's own VPC/tenant** or do
**on-device pre-filtering / egress-minimization** so only suspicious spans leave the
endpoint (see Performance/scaling → Next). That's what makes the hosted model
acceptable to security-sensitive customers.

## Performance / scaling

**Implemented (see [Smart inspection pipeline](#smart-inspection-pipeline)):**
- Text **normalization** to cut tokens sent (~55% in the benchmark).
- **Token-budget chunking with overlap** — minimizes calls without dropping
  boundary secrets.
- **Durable content-hash dedup + cache** (SQLite) — re-uploads / shared
  boilerplate cost 0 calls, and inspection **resumes across restarts**.
- **Bounded-parallel dispatch** over a pooled keep-alive client, with retry/backoff.
- **Optional early-exit** (`early_exit_on_first_hit`) — stop at the first flagged chunk.
- **`stats`** on every response for measurability.

**Next (design, not built):**
- **Egress minimization (the sharpest one):** a security tool shouldn't ship the
  *entire* document to a third party to check it. A local candidate pass (regex for
  known key formats + Shannon entropy for random tokens) could send only suspicious
  spans + context, minimizing off-device data. Trade-off: skipping "boring" chunks
  risks a false negative on something only Prompt Security would catch — so it's a
  tunable privacy-vs-recall knob, off by default. Evidence for this direction — a
  regex+entropy pre-screen at ~microseconds/KB with strong recall on known formats —
  is in **[docs/SURVEY.md](./docs/SURVEY.md)**.
- **Redis-backed cache** shared across service instances (the in-process cache is
  per-instance).
- Offload heavy PDF extraction to a **worker pool / queue**; stream large uploads to disk.
- **Horizontal scale** the stateless service behind a load balancer.
