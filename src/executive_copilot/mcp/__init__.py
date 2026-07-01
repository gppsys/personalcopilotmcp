"""MCP (Model Context Protocol) server for Executive Copilot.

This module provides the MCP server implementation that allows
AI assistants to interact with the Executive Copilot platform.
"""

from executive_copilot.mcp.server import create_mcp_server

__all__ = ["create_mcp_server"]
