import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _dummy_app_id(monkeypatch):
    """Tests mock the HTTP layer, so they need a non-empty APP-ID (any value) to
    pass the presence check — never a real credential."""
    monkeypatch.setattr(settings, "prompt_security_app_id", "test-app-id")
    yield
