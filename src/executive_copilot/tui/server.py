"""TUI server runner."""

import argparse
from pathlib import Path

from executive_copilot.tui.app import run_tui


def main() -> None:
    """Run the terminal UI."""
    parser = argparse.ArgumentParser(description="Executive Copilot Terminal Configuration")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config"),
        help="Path to configuration directory",
    )

    args = parser.parse_args()
    run_tui(args.config)


if __name__ == "__main__":
    main()
