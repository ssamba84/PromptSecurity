import httpx
import respx

import app.cache as cache_mod
from app.config import settings
from app.inspectors.prompt_security import PromptSecurityInspector
from app.models import Message
from app.pipeline import inspect_document

RAW_SECRET = "AKIAIOSFODNN7EXAMPLE"

_PS_SECRET_RESPONSE = {
    "status": "success",
    "result": {
        "action": "modify",
        "prompt": {
            "passed": False,
            "violations": ["Secrets"],
            "findings": {
                "Secrets": [
                    {
                        "category": "Access Tokens",
                        "entity": "AKIAIOSFODNN7EXAMPLE",
                        "entity_type": "AWS credentials",
                        "sanitized_entity": "[REDACTED_AWS_CREDENTIALS_1]",
                    }
                ]
            },
        },
    },
}

_PS_CLEAN_RESPONSE = {
    "status": "success",
    "result": {"action": "log", "prompt": {"passed": True, "violations": [], "findings": {}}},
}


@respx.mock
async def test_prompt_security_detects_secret():
    respx.post(settings.prompt_security_url).mock(
        return_value=httpx.Response(200, json=_PS_SECRET_RESPONSE)
    )
    result = await PromptSecurityInspector().inspect(
        [Message(role="user", content="key AKIAIOSFODNN7EXAMPLE")], {}
    )
    assert result.has_secrets is True
    assert result.provider == "prompt-security"
    assert result.findings[0].type == "AWS credentials"
    # The finding carries the provider's REDACTED value, never the raw secret.
    assert result.findings[0].snippet == "[REDACTED_AWS_CREDENTIALS_1]"
    assert RAW_SECRET not in (result.findings[0].snippet or "")


@respx.mock
async def test_prompt_security_clean():
    respx.post(settings.prompt_security_url).mock(
        return_value=httpx.Response(200, json=_PS_CLEAN_RESPONSE)
    )
    result = await PromptSecurityInspector().inspect(
        [Message(role="user", content="hello there")], {}
    )
    assert result.has_secrets is False
    assert result.findings == []


async def test_prompt_security_empty_text_skips_call():
    # No HTTP mock registered — if it tried to call out, httpx would error.
    result = await PromptSecurityInspector().inspect([Message(role="user", content="   ")], {})
    assert result.has_secrets is False


@respx.mock
async def test_no_raw_secret_persisted_to_durable_db(tmp_path, monkeypatch):
    """End-to-end: a real detection must not write the raw secret to the sqlite cache."""
    monkeypatch.setattr(settings, "cache_backend", "sqlite")
    monkeypatch.setattr(settings, "cache_db_path", str(tmp_path / "cache.db"))
    cache_mod.reset_cache()
    try:
        respx.post(settings.prompt_security_url).mock(
            return_value=httpx.Response(200, json=_PS_SECRET_RESPONSE)
        )
        result = await inspect_document(f"please rotate {RAW_SECRET} now", PromptSecurityInspector())
        assert result.has_secrets is True
        cache_mod.get_cache().close()  # flush + close so the file is fully written

        db_bytes = (tmp_path / "cache.db").read_bytes()
        assert RAW_SECRET.encode() not in db_bytes  # raw secret must never hit disk
        assert b"[REDACTED_AWS_CREDENTIALS_1]" in db_bytes  # redacted form is what's cached
    finally:
        cache_mod.reset_cache()
