"""MCP server entry point with clean stdio for Claude Desktop."""
import logging
import os
import sys
from pathlib import Path

# Redirect ALL stdout to stderr BEFORE any imports
# This ensures debug logs don't break MCP's JSON protocol
_original_stdout = sys.stdout
sys.stdout = sys.stderr

# Suppress all logging
logging.disable(logging.CRITICAL)
os.environ["FASTMCP_LOG_LEVEL"] = "ERROR"

# Set working directory to project root (where this package is installed)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
os.chdir(PROJECT_ROOT)

# structlog defaults to printing on stdout, which would corrupt the protocol
# as soon as a tool logs (e.g. an OAuth token refresh). Send it to stderr.
from executive_copilot.logging import configure_stdio_safe_logging

configure_stdio_safe_logging()

# Now import and run
from executive_copilot.config import load_config
from executive_copilot.mcp.server import create_mcp_server


def main() -> None:
    config = load_config(PROJECT_ROOT / "config")
    mcp = create_mcp_server(config)

    # Restore stdout just for MCP's JSON communication
    sys.stdout = _original_stdout
    mcp.run()

if __name__ == "__main__":
    main()
