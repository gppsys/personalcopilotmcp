"""Admin panel server runner."""

import argparse
from pathlib import Path

import uvicorn

from executive_copilot.admin.app import create_admin_app
from executive_copilot.logging import configure_logging


def main() -> None:
    """Run the admin panel server."""
    parser = argparse.ArgumentParser(description="Executive Copilot Admin Panel")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config"),
        help="Path to configuration directory",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind to",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )

    args = parser.parse_args()

    configure_logging(level="INFO")

    print("\n  Executive Copilot Admin Panel")
    print(f"  Running at http://{args.host}:{args.port}")
    print(f"  Config directory: {args.config.absolute()}\n")

    app = create_admin_app(args.config)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
