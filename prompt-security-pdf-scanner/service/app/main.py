"""PDF secret inspection service (Prompt Security).

Two entry points, one inspection path:
  - POST /inspect       JSON {messages, metadata}     (generic text inspection)
  - POST /inspect/file  multipart file upload         (extracts PDF text)
Both funnel text through the smart inspection pipeline (normalize -> chunk +
overlap -> dedup/cache -> bounded-parallel dispatch -> merge) and log the result.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.inspectors.prompt_security import PromptSecurityInspector
from app.models import InspectRequest, InspectResult
from app.pdf import PdfExtractionError, extract_text
from app.pipeline import inspect_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("inspection")

app = FastAPI(title="PDF Secret Inspection Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single inspection backend for this project.
inspector = PromptSecurityInspector()

if not settings.prompt_security_app_id:
    logger.warning(
        "PROMPT_SECURITY_APP_ID is not set — inspections will fail until you add it "
        "to service/.env (copy .env.example)."
    )


def _log_result(source: str, result: InspectResult) -> None:
    level = logging.WARNING if result.has_secrets else logging.INFO
    s = result.stats
    logger.log(
        level,
        "inspected source=%s has_secrets=%s findings=%d types=%s | "
        "chars %s->%s (-%.0f%%) chunks=%s unique=%s cache_hits=%s api_calls=%s(naive=%s) %sms",
        source,
        result.has_secrets,
        len(result.findings),
        [f.type for f in result.findings],
        s.chars_in if s else "?",
        s.chars_inspected if s else "?",
        s.reduction_pct if s else 0,
        s.chunks if s else "?",
        s.unique_chunks if s else "?",
        s.cache_hits if s else "?",
        s.api_calls if s else "?",
        s.naive_api_calls if s else "?",
        s.duration_ms if s else "?",
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "inspector": inspector.name}


@app.post("/inspect", response_model=InspectResult)
async def inspect(req: InspectRequest) -> InspectResult:
    """Inspect already-normalized conversation / text."""
    text = "\n\n".join(m.content for m in req.messages if m.content)
    try:
        result = await inspect_document(text, inspector, source="chat")
    except Exception as exc:  # noqa: BLE001 - surface provider errors as 502
        logger.exception("inspection failed")
        raise HTTPException(status_code=502, detail=f"Inspection backend error: {exc}") from exc
    _log_result("chat", result)
    return result


@app.post("/inspect/file", response_model=InspectResult)
async def inspect_file(file: UploadFile = File(...)) -> InspectResult:
    """Inspect an uploaded PDF. Extracts text, then runs the pipeline."""
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit")

    filename = file.filename or "upload"
    content_type = (file.content_type or "").lower()
    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(status_code=415, detail="Only PDF files are supported")

    try:
        text = extract_text(data)
    except PdfExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not text:
        # No text layer (e.g. scanned PDF) — nothing to inspect, report clean.
        result = InspectResult(has_secrets=False, provider=inspector.name)
        logger.info("no extractable text in %s — skipping inspection", filename)
        return result

    try:
        result = await inspect_document(text, inspector, source=f"file:{filename}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("inspection failed for %s", filename)
        raise HTTPException(status_code=502, detail=f"Inspection backend error: {exc}") from exc

    _log_result(f"file:{filename}", result)
    return result
