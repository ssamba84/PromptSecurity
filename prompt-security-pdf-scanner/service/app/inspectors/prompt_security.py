"""Prompt Security adapter (project 1).

Maps the normalized conversation to Prompt Security's text-inspection API
(`POST /api/protect`, body `{"prompt": "<text>"}`) and maps the response back
to a normalized InspectResult.

Uses a single pooled AsyncClient (keep-alive) shared across concurrent chunk
inspections, and retries transient errors (429/5xx/network) with exponential
backoff.

Response shape (confirmed against the live API):
    result.prompt.findings.Secrets = [
        {category, entity, entity_type, sanitized_entity}, ...
    ]
    result.prompt.violations = ["Secrets", ...]
The same structure appears under result.response when a model response is sent.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import settings
from app.inspectors.base import Inspector
from app.models import Finding, InspectResult, Message

logger = logging.getLogger("inspection.prompt_security")

_TIMEOUT = httpx.Timeout(20.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PromptSecurityInspector(Inspector):
    name = "prompt-security"

    def __init__(self) -> None:
        self.url = settings.prompt_security_url
        self.app_id = settings.prompt_security_app_id
        # One pooled client, reused across all (including concurrent) calls.
        self._client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )

    async def inspect(self, messages: list[Message], metadata: dict) -> InspectResult:
        # Prompt Security's protect API inspects a single text blob.
        text = "\n\n".join(m.content for m in messages if m.content).strip()
        if not text:
            return InspectResult(has_secrets=False, provider=self.name, severity=None)

        data = await self._post_with_retry(text)
        findings = self._extract_secret_findings(data)
        return InspectResult(
            has_secrets=bool(findings),
            provider=self.name,
            findings=findings,
            severity="HIGH" if findings else None,
            raw=data,
        )

    async def _post_with_retry(self, text: str) -> dict:
        if not self.app_id:
            raise RuntimeError(
                "PROMPT_SECURITY_APP_ID is not set. Add it to service/.env "
                "(copy .env.example) — the APP-ID is a credential and is not committed."
            )
        headers = {"APP-ID": self.app_id, "Content-Type": "application/json"}
        last_exc: Exception | None = None
        for attempt in range(settings.max_retries + 1):
            try:
                resp = await self._client.post(self.url, headers=headers, json={"prompt": text})
                if resp.status_code in _RETRYABLE_STATUS and attempt < settings.max_retries:
                    await self._backoff(attempt, f"status {resp.status_code}")
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < settings.max_retries:
                    await self._backoff(attempt, repr(exc))
                    continue
                raise
        # Only reached if the loop exhausted retries on retryable status codes.
        if last_exc:
            raise last_exc
        raise RuntimeError("inspection failed after retries")

    @staticmethod
    async def _backoff(attempt: int, reason: str) -> None:
        delay = settings.retry_base_seconds * (2 ** attempt)
        logger.warning("retrying after %.2fs (attempt %d): %s", delay, attempt + 1, reason)
        await asyncio.sleep(delay)

    @staticmethod
    def _extract_secret_findings(data: dict) -> list[Finding]:
        """Pull the `Secrets` findings out of both the prompt and response sides."""
        result = data.get("result") or {}
        findings: list[Finding] = []
        for side in ("prompt", "response"):
            side_obj = result.get(side) or {}
            secrets = (side_obj.get("findings") or {}).get("Secrets") or []
            for s in secrets:
                findings.append(
                    Finding(
                        type=s.get("entity_type") or s.get("category") or "Secret",
                        # Never carry the RAW secret. Use the provider's redacted
                        # form (e.g. "[REDACTED_AWS_CREDENTIALS_1]") so the raw
                        # value is never returned to the client or written to the
                        # durable cache.
                        snippet=s.get("sanitized_entity") or "[redacted]",
                        severity="HIGH",
                    )
                )
        return findings
