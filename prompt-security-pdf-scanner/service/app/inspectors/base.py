"""The inspection seam: every backend implements this one interface.

Keeping inspection behind a single interface makes the backend swappable and
keeps the FastAPI routes and the extension independent of any specific provider.
"""
from __future__ import annotations

import abc

from app.models import InspectResult, Message


class Inspector(abc.ABC):
    #: stable identifier surfaced in InspectResult.provider and logs
    name: str = "base"

    @abc.abstractmethod
    async def inspect(self, messages: list[Message], metadata: dict) -> InspectResult:
        """Inspect a normalized conversation and return a normalized verdict."""
        raise NotImplementedError
