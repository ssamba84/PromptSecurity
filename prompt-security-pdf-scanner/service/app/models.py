"""Normalized data models shared across the service.

Every capture source (PDF upload, ...) is normalized into a list[Message], and
the inspection backend returns an InspectResult. Keeping these normalized keeps
the routes and the extension independent of the backend's wire format.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    """Canonical inspection input — source-agnostic."""

    role: str = "user"  # "user" | "assistant" | "system"
    content: str


class Finding(BaseModel):
    """A single normalized detection (e.g. a leaked secret)."""

    type: str  # e.g. "AWS credentials" or a provider category
    snippet: Optional[str] = None  # short excerpt, if the provider returns one
    severity: Optional[str] = None


class InspectRequest(BaseModel):
    """Body for POST /inspect."""

    messages: list[Message]
    metadata: dict = Field(default_factory=dict)


class InspectStats(BaseModel):
    """Observability for the inspection pipeline — proves the optimizations work."""

    chars_in: int = 0  # extracted text length before normalization
    chars_inspected: int = 0  # length after normalization (what we actually chunk)
    reduction_pct: float = 0.0  # % of characters removed as noise
    chunks: int = 0  # chunks produced (1 for small docs)
    unique_chunks: int = 0  # after content-hash dedup
    cache_hits: int = 0  # chunks served from the result cache (0 API calls)
    api_calls: int = 0  # actual calls made to the inspection API
    naive_api_calls: int = 0  # calls a naive per-chunk approach would have made
    duration_ms: int = 0


class InspectResult(BaseModel):
    """Normalized inspection verdict returned to the extension."""

    has_secrets: bool
    provider: str  # provider id, e.g. "prompt-security"
    findings: list[Finding] = Field(default_factory=list)
    severity: Optional[str] = None
    stats: Optional[InspectStats] = None
    # Raw provider response, kept for debugging / logging. Not shown to users.
    raw: Optional[dict] = None
