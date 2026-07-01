"""Command-line interface for Executive Copilot."""

import asyncio
import sys
from pathlib import Path

from executive_copilot.config import load_config
from executive_copilot.logging import configure_logging, get_logger
from executive_copilot.providers.base import get_provider_registry


def main() -> int:
    """Main entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Executive Copilot - MCP-based intelligent assistant",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config"),
        help="Path to configuration directory",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override log level",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Output logs in JSON format",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # MCP server command
    mcp_parser = subparsers.add_parser("mcp", help="Run MCP server")
    mcp_parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport type",
    )

    # Database commands
    db_parser = subparsers.add_parser("db", help="Database operations")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_subparsers.add_parser("init", help="Initialize database tables")
    db_subparsers.add_parser("drop", help="Drop all tables")

    # Automation commands
    auto_parser = subparsers.add_parser("automation", help="Automation operations")
    auto_subparsers = auto_parser.add_subparsers(dest="auto_command")
    auto_subparsers.add_parser("list", help="List scheduled jobs")
    run_parser = auto_subparsers.add_parser("run", help="Run a job now")
    run_parser.add_argument("job_name", help="Name of job to run")

    # Config command
    subparsers.add_parser("config", help="Show configuration")

    # Admin panel command
    admin_parser = subparsers.add_parser("admin", help="Run admin web panel")
    admin_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to",
    )
    admin_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind to",
    )
    admin_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload",
    )

    # Stop command
    stop_parser = subparsers.add_parser("stop", help="Stop running admin server")
    stop_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port the admin server is running on",
    )

    # TUI command
    subparsers.add_parser("tui", help="Run terminal configuration UI")

    # Sync pending replies command
    sync_parser = subparsers.add_parser(
        "sync-replies",
        help="Scan sent emails and create follow-ups for unanswered ones",
    )
    sync_parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="How many days of sent emails to scan (default: 7)",
    )
    sync_parser.add_argument(
        "--min-wait",
        type=int,
        default=2,
        help="Minimum days without reply before flagging (default: 2)",
    )

    # Scan commitments command
    commit_parser = subparsers.add_parser(
        "scan-commitments",
        help="Scan emails for commitments made by/to you",
    )
    commit_parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="How many days of emails to scan (default: 7)",
    )
    commit_parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Use keyword matching instead of AI",
    )

    # Serve command (admin + automation)
    serve_parser = subparsers.add_parser(
        "serve",
        help="Run admin panel with background automation jobs",
    )
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind to",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Configure logging
    log_level = args.log_level or config.logging.level
    configure_logging(
        level=log_level,
        json_format=args.json_logs or config.logging.json_format,
    )

    logger = get_logger(__name__)

    if args.command is None:
        parser.print_help()
        return 0

    # Handle commands
    if args.command == "mcp":
        return run_mcp_server(config)
    elif args.command == "db":
        return asyncio.run(run_db_command(config, args.db_command))
    elif args.command == "automation":
        return asyncio.run(run_automation_command(config, args))
    elif args.command == "config":
        return show_config(config)
    elif args.command == "admin":
        return run_admin_panel(args)
    elif args.command == "stop":
        return stop_admin_server(args)
    elif args.command == "tui":
        return run_tui_config(args)
    elif args.command == "sync-replies":
        return asyncio.run(run_sync_replies(config, args))
    elif args.command == "scan-commitments":
        return asyncio.run(run_scan_commitments(config, args))
    elif args.command == "serve":
        return asyncio.run(run_serve(config, args))

    return 0


def run_mcp_server(config: "AppConfig") -> int:  # type: ignore[name-defined]
    """Run the MCP server."""
    from executive_copilot.mcp.server import main as mcp_main

    mcp_main()
    return 0


async def run_db_command(config: "AppConfig", command: str | None) -> int:  # type: ignore[name-defined]
    """Run database commands."""
    from executive_copilot.database.connection import init_database

    logger = get_logger(__name__)

    db_manager = init_database(config.database)

    if command == "init":
        logger.info("Initializing database tables")
        await db_manager.create_tables()
        logger.info("Database initialized successfully")
    elif command == "drop":
        logger.warning("Dropping all database tables")
        await db_manager.drop_tables()
        logger.info("Database tables dropped")
    else:
        logger.error("Unknown database command")
        return 1

    await db_manager.close()
    return 0


async def run_automation_command(config: "AppConfig", args: object) -> int:  # type: ignore[name-defined]
    """Run automation commands."""
    from executive_copilot.automation import AutomationEngine
    from executive_copilot.database.connection import init_database
    from executive_copilot.services.container import ServiceContainer

    logger = get_logger(__name__)

    db_manager = init_database(config.database)
    container = ServiceContainer(
        config=config,
        db_manager=db_manager,
        provider_registry=get_provider_registry(),
    )

    engine = AutomationEngine(config, container)

    command = getattr(args, "auto_command", None)

    if command == "list":
        await engine.start()
        jobs = engine.list_jobs()
        for job in jobs:
            print(f"- {job['job_name']}: next run at {job.get('next_run', 'not scheduled')}")
        await engine.stop()
    elif command == "run":
        job_name = getattr(args, "job_name", None)
        if not job_name:
            logger.error("Job name required")
            return 1
        await engine.start()
        result = await engine.run_job_now(job_name)
        if result:
            print(f"Job {result.job_name}: {result.status.value}")
            if result.error_message:
                print(f"Error: {result.error_message}")
        await engine.stop()
    else:
        logger.error("Unknown automation command")
        return 1

    await container.shutdown()
    return 0


def show_config(config: "AppConfig") -> int:  # type: ignore[name-defined]
    """Show current configuration."""
    import json

    # Convert to dict, excluding secrets
    config_dict = config.model_dump(exclude={"oauth"})
    print(json.dumps(config_dict, indent=2, default=str))
    return 0


def run_admin_panel(args: object) -> int:
    """Run the admin web panel with graceful shutdown support."""
    import asyncio
    import signal

    import uvicorn

    from executive_copilot.admin.app import create_admin_app

    config_dir = getattr(args, "config", Path("config"))
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8080)
    reload = getattr(args, "reload", False)

    print("\n  Executive Copilot Admin Panel")
    print(f"  Running at http://{host}:{port}")
    print(f"  Config directory: {Path(config_dir).absolute()}")
    print(f"\n  To stop: POST http://{host}:{port}/api/shutdown")
    print("  Or press Ctrl+C\n")

    shutdown_event = asyncio.Event()
    app = create_admin_app(config_dir, shutdown_event=shutdown_event)

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    def handle_shutdown(signum: int, frame: object) -> None:
        print("\n  Shutting down gracefully...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    async def run_server() -> None:
        await server.serve()

    asyncio.run(run_server())
    print("  Admin panel stopped.\n")
    return 0


def stop_admin_server(args: object) -> int:
    """Stop a running admin server."""
    import httpx

    port = getattr(args, "port", 8080)
    url = f"http://127.0.0.1:{port}/api/shutdown"

    print(f"\n  Stopping admin server on port {port}...")

    try:
        response = httpx.post(url, timeout=5.0)
        if response.status_code == 200:
            print("  Server shutdown initiated successfully.\n")
            return 0
        else:
            print(f"  Unexpected response: {response.status_code}\n")
            return 1
    except httpx.ConnectError:
        print(f"  No server running on port {port}.\n")
        return 1
    except Exception as e:
        print(f"  Error: {e}\n")
        return 1


def run_tui_config(args: object) -> int:
    """Run the terminal configuration UI."""
    from executive_copilot.tui.app import run_tui

    config_dir = getattr(args, "config", Path("config"))
    run_tui(config_dir)
    return 0


async def run_serve(config: "AppConfig", args: object) -> int:  # type: ignore[name-defined]
    """Run admin panel with background automation."""
    import signal

    import uvicorn

    from executive_copilot.admin.app import create_admin_app
    from executive_copilot.automation import AutomationEngine
    from executive_copilot.database.connection import init_database
    from executive_copilot.providers.base import get_provider_registry
    from executive_copilot.services.container import ServiceContainer

    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8080)

    print("\n  Executive Copilot Server")
    print(f"  Admin panel: http://{host}:{port}")
    print("  Automation:  enabled")
    print("\n  Press Ctrl+C to stop\n")

    db_manager = init_database(config.database)
    await db_manager.create_tables()

    container = ServiceContainer(
        config=config,
        db_manager=db_manager,
        provider_registry=get_provider_registry(),
    )

    engine = AutomationEngine(config, container)
    await engine.start()

    shutdown_event = asyncio.Event()
    app = create_admin_app(config.config_dir, shutdown_event=shutdown_event)

    uvicorn_config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(uvicorn_config)

    def handle_shutdown(signum: int, frame: object) -> None:
        print("\n  Shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        await server.serve()
    finally:
        await engine.stop()
        await container.shutdown()
        print("  Server stopped.\n")

    return 0


async def run_sync_replies(config: "AppConfig", args: object) -> int:  # type: ignore[name-defined]
    """Sync pending replies from sent emails."""
    from executive_copilot.admin.credentials import CredentialManager
    from executive_copilot.database.connection import init_database
    from executive_copilot.services.pending_replies import PendingRepliesService

    logger = get_logger(__name__)

    days_back = getattr(args, "days_back", 7)
    min_wait = getattr(args, "min_wait", 2)

    print("\n  Syncing pending replies...")
    print(f"  Scanning last {days_back} days, flagging after {min_wait} days without reply\n")

    credential_manager = CredentialManager(config.config_dir / ".credentials")
    db_manager = init_database(config.database)

    # Ensure tables exist
    await db_manager.create_tables()

    service = PendingRepliesService(
        config=config,
        credential_manager=credential_manager,
        db_manager=db_manager,
    )

    try:
        stats = await service.sync(days_back=days_back, min_wait_days=min_wait)

        print("  Results:")
        print(f"    Processed: {stats['processed']} sent emails")
        print(f"    Created:   {stats['created']} new follow-ups")
        print(f"    Updated:   {stats['updated']} existing follow-ups")
        print(f"    Skipped:   {stats['skipped']} (replied or too recent)")
        if stats["errors"]:
            print(f"    Errors:    {stats['errors']}")
        print()

    except Exception as e:
        logger.error("Sync failed", error=str(e))
        print(f"  Error: {e}\n")
        return 1

    finally:
        await db_manager.close()

    return 0


async def run_scan_commitments(config: "AppConfig", args: object) -> int:  # type: ignore[name-defined]
    """Scan emails for commitments."""
    from executive_copilot.admin.credentials import CredentialManager
    from executive_copilot.database.connection import init_database
    from executive_copilot.services.commitment_detection import CommitmentDetectionService

    logger = get_logger(__name__)

    days_back = getattr(args, "days_back", 7)
    use_ai = not getattr(args, "no_ai", False)

    print("\n  Scanning for commitments...")
    print(f"  Looking back {days_back} days")
    print(f"  Using {'AI extraction' if use_ai else 'keyword matching'}\n")

    credential_manager = CredentialManager(config.config_dir / ".credentials")
    db_manager = init_database(config.database)

    await db_manager.create_tables()

    ai_service = None
    if use_ai:
        try:
            from executive_copilot.providers.base import get_provider_registry
            from executive_copilot.services.container import ServiceContainer

            container = ServiceContainer(
                config=config,
                db_manager=db_manager,
                provider_registry=get_provider_registry(),
            )
            ai_service = container.ai
        except Exception as e:
            logger.warning("AI unavailable, using keyword matching", error=str(e))
            print(f"  Note: AI unavailable ({e}), using keyword matching\n")

    service = CommitmentDetectionService(
        config=config,
        credential_manager=credential_manager,
        db_manager=db_manager,
        ai_service=ai_service,
    )

    try:
        stats = await service.scan_emails(days_back=days_back)

        print("  Results:")
        print(f"    Emails scanned:     {stats['emails_scanned']}")
        print(f"    Commitments found:  {stats['commitments_found']}")
        print(f"      I promised:       {stats['i_promised']}")
        print(f"      They promised:    {stats['they_promised']}")
        print(f"    Skipped:            {stats['skipped']} (already processed)")
        if stats["errors"]:
            print(f"    Errors:             {stats['errors']}")
        print()

    except Exception as e:
        logger.error("Scan failed", error=str(e))
        print(f"  Error: {e}\n")
        return 1

    finally:
        await db_manager.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
