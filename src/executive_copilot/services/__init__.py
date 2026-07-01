"""Service layer for Executive Copilot.

Services orchestrate providers and contain business logic.
They should never directly access databases or external systems.
"""

from executive_copilot.services.ai import AIMessage, AIService, AITextRequest, AITextResult
from executive_copilot.services.base import Service
from executive_copilot.services.container import ServiceContainer

__all__ = [
    "AIMessage",
    "AIService",
    "AITextRequest",
    "AITextResult",
    "Service",
    "ServiceContainer",
]
