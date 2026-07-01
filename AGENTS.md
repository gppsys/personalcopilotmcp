# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Executive Copilot is an MCP (Model Context Protocol) server that provides AI assistants with access to an executive's digital workspace. It aggregates data from multiple providers (email, calendar, tasks, documents) and presents a unified interface.

**This is not a chat application.** The UI is any MCP-compatible client (Codex Desktop, ChatGPT, Cursor, VS Code).

## Tech Stack

- **Python 3.12+**
- **FastMCP** - MCP server framework
- **SQLModel** - Database ORM (SQLite dev, PostgreSQL ready)
- **Pydantic** - Data validation and settings
- **APScheduler** - Job scheduling
- **httpx** - Async HTTP client
- **structlog** - Structured logging
- **pytest** - Testing
- **Ruff** - Linting
- **mypy** - Type checking
- **uv** - Package management

## Common Commands

```bash
# Install dependencies
uv sync
uv sync --dev  # Include dev dependencies

# Run MCP server
uv run copilot mcp

# Database operations
uv run copilot db init   # Create tables
uv run copilot db drop   # Drop tables

# Testing
uv run pytest                        # Run all tests
uv run pytest tests/unit/            # Unit tests only
uv run pytest -k "test_email"        # Pattern match
uv run pytest --cov                  # With coverage

# Code quality
uv run ruff check .                  # Lint
uv run ruff check --fix .            # Lint and fix
uv run mypy src                      # Type check

# View config
uv run copilot config
```

## Architecture Principles

1. **Clean Architecture**: Layers are domain → application → infrastructure
2. **Providers contain no business logic**: Only data fetching and mapping
3. **Services orchestrate**: Business logic lives in services
4. **Domain models are immutable**: Use `model.with_update()` for changes
5. **Repository pattern**: No direct database access from services
6. **Configuration over code**: Rules in YAML, not hardcoded

## Key Directories

```
src/executive_copilot/
├── models/      # Domain models (Email, Task, Person, etc.)
├── providers/   # External system interfaces (email/, calendar/, task/)
├── services/    # Business logic (EmailService, CalendarService)
├── context/     # Context Engine (determines info sources)
├── memory/      # Persistent knowledge store
├── database/    # SQLModel tables and repositories
├── automation/  # APScheduler jobs
├── mcp/         # MCP server and tool definitions
└── config/      # Configuration loading
```

## Adding Features

### New MCP Tool
```python
# In src/executive_copilot/mcp/server.py
@mcp.tool()
async def new_tool(param: str) -> dict[str, Any]:
    """Description for AI."""
    result = await container.service.operation(param)
    return {"data": result}
```

### New Provider
1. Create interface in `providers/{type}/interface.py`
2. Implement provider class
3. Register in `providers/base/registry.py`
4. Add config in `config/providers.yaml`

### New Context Rule
```yaml
# In config/context_rules.yaml
- name: "rule_name"
  conditions:
    domain: "example.com"
  sources: ["memory", "documentation"]
```

### New Automation Job
1. Create job class in `automation/jobs.py`
2. Register in `JOB_TYPES` dict
3. Configure in `config/automation.yaml`

## Testing Patterns

```python
# Unit test
def test_email_model():
    email = Email(...)
    assert email.sender_domain == "example.com"

# Async test with fixtures
@pytest.mark.asyncio
async def test_repository(db_session):
    repo = PersonRepository(db_session)
    result = await repo.get_by_id(id)
    assert result is not None
```

## Configuration

- Base: `config/config.yaml`
- Organizations: `config/organizations.yaml`
- Context rules: `config/context_rules.yaml`
- Automation: `config/automation.yaml`
- Local overrides: `config/config.local.yaml` (gitignored)
- Environment: `COPILOT_` prefix (e.g., `COPILOT_DATABASE__HOST`)

## Current Sprint Status

**Sprint 1 Complete**: Architecture foundation built. All interfaces defined, no provider implementations yet.

**Next**: Sprint 2 - Outlook Email Provider implementation

## Code Style

- Use async/await for all I/O
- Type hints on all functions
- Domain models are frozen (immutable)
- Providers map to domain models, never expose external types
- Services use dependency injection via ServiceContainer
- Configuration in YAML, secrets in environment variables
