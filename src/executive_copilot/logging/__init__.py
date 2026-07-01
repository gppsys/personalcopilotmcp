"""Structured logging for Executive Copilot.

Provides a unified logging interface using structlog for
structured, context-rich logging.
"""

from executive_copilot.logging.config import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
