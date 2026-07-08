"""Runtime configuration, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Prompt Security text inspection API.
    # The APP-ID is a credential — it is NOT committed. Put it in service/.env
    # (copy .env.example). Empty by default so a missing key fails loudly.
    prompt_security_url: str = "https://eu.prompt.security/api/protect"
    prompt_security_app_id: str = ""

    # Service
    allowed_origins: str = "*"
    max_upload_mb: int = 25

    # --- Smart inspection pipeline ---
    # Normalize/denoise extracted text before inspection (cuts tokens sent).
    enable_normalization: bool = True
    # Only chunk documents whose (normalized) text exceeds this many characters.
    # MEASURED: /api/protect silently truncates at ~48,500 tokens and returns 200
    # with no error — a secret past the cutoff is silently missed. Detection was
    # reliable up to ~18.5k tokens (~100 KB) in probing. 40,000 chars ≈ 8-10k
    # tokens keeps every chunk with a comfortable (~5x) margin under the cap.
    chunk_char_budget: int = 40000
    # Overlap between adjacent chunks. Must be >= the longest secret you expect,
    # so a secret straddling a boundary is still wholly present in one window.
    chunk_overlap_chars: int = 200
    # Max concurrent chunk inspections (bounded parallelism).
    max_concurrency: int = 5
    # Stop as soon as any chunk reports a finding (cheaper, but reports only the
    # first hit and leaves later chunks uninspected). Off = inspect every chunk
    # and report all findings.
    early_exit_on_first_hit: bool = False
    # Content-hash result cache. Backend: "sqlite" (durable, survives restarts,
    # enables resume-after-interruption) or "memory" (in-process only).
    cache_backend: str = "sqlite"
    cache_db_path: str = "inspection_cache.db"
    # Cache entry TTL (seconds). 0 disables the cache.
    cache_ttl_seconds: int = 3600
    # Retry/backoff for transient API errors (429/5xx/network).
    max_retries: int = 3
    retry_base_seconds: float = 0.5

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
