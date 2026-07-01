# Development Guide

## Prerequisites

- Python 3.12+
- uv (Python package manager)
- Git

## Getting Started

### 1. Clone and Setup

```bash
# Clone the repository
git clone <repository-url>
cd executive-copilot

# Install dependencies with uv
uv sync

# Install development dependencies
uv sync --dev
```

### 2. Configuration

```bash
# Copy example configuration
cp config/config.yaml config/config.local.yaml

# Edit local configuration
# Add your OAuth credentials, organization details, etc.
```

### 3. Initialize Database

```bash
# Create database tables
uv run copilot db init
```

### 4. Run the MCP Server

```bash
# Run with stdio transport (for Claude Desktop, etc.)
uv run copilot mcp
```

## Project Structure

```
executive-copilot/
├── src/executive_copilot/
│   ├── automation/       # Scheduled jobs
│   ├── config/           # Configuration loading
│   ├── context/          # Context engine
│   ├── database/         # Database and repositories
│   ├── exceptions/       # Exception hierarchy
│   ├── logging/          # Structured logging
│   ├── mcp/              # MCP server
│   ├── memory/           # Memory layer
│   ├── models/           # Domain models
│   ├── providers/        # Provider interfaces
│   │   ├── auth/         # Authentication providers
│   │   ├── base/         # Base classes
│   │   ├── calendar/     # Calendar providers
│   │   ├── document/     # Document providers
│   │   ├── email/        # Email providers
│   │   ├── news/         # News providers
│   │   ├── search/       # Search providers
│   │   └── task/         # Task providers
│   └── services/         # Service layer
├── tests/
│   ├── unit/
│   └── integration/
├── config/               # Configuration files
├── docs/                 # Documentation
└── scripts/              # Utility scripts
```

## Development Workflow

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov

# Run specific test file
uv run pytest tests/unit/test_models.py

# Run tests matching pattern
uv run pytest -k "test_email"
```

### Code Quality

```bash
# Run linter
uv run ruff check .

# Fix linting issues
uv run ruff check --fix .

# Type checking
uv run mypy src
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
uv run pre-commit install

# Run hooks manually
uv run pre-commit run --all-files
```

## Adding a New Provider

### 1. Create Provider Interface (if new type)

```python
# src/executive_copilot/providers/newtype/interface.py
from abc import abstractmethod
from executive_copilot.providers.base import Provider

class NewTypeProvider(Provider[DomainModel]):
    @abstractmethod
    async def some_operation(self) -> Something:
        ...
```

### 2. Implement Provider

```python
# src/executive_copilot/providers/newtype/implementation.py
from executive_copilot.providers.newtype.interface import NewTypeProvider

class ConcreteProvider(NewTypeProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="concrete",
            display_name="Concrete Provider",
            provider_type="newtype",
            capabilities=[...],
        )

    async def connect(self) -> None:
        # Initialize connection
        pass

    async def disconnect(self) -> None:
        # Cleanup
        pass

    async def health_check(self) -> bool:
        return True

    async def some_operation(self) -> Something:
        # Implementation
        pass
```

### 3. Register Provider

```python
# In application startup
from executive_copilot.providers.base import get_provider_registry

registry = get_provider_registry()
registry.register("newtype", "concrete", ConcreteProvider)
```

### 4. Add Configuration

```yaml
# config/providers.yaml
providers:
  - provider_type: "newtype"
    provider_name: "concrete"
    enabled: true
    settings:
      api_key: "${CONCRETE_API_KEY}"
```

## Adding a New MCP Tool

```python
# In src/executive_copilot/mcp/server.py

@mcp.tool()
async def new_tool(
    param1: str,
    param2: int = 10,
) -> dict[str, Any]:
    """Tool description for AI assistants.

    Args:
        param1: Description of param1
        param2: Description of param2

    Returns:
        Description of return value
    """
    result = await container.some_service.do_something(param1, param2)
    return {"data": result}
```

## Adding a Context Rule

```yaml
# config/context_rules.yaml
context_rules:
  - name: "new_rule"
    description: "Description of when this rule applies"
    priority: 75
    conditions:
      domain: "example.com"
      content_contains: "keyword"
    sources:
      - "memory"
      - "documentation"
```

## Adding an Automation Job

### 1. Create Job Class

```python
# src/executive_copilot/automation/jobs.py

class NewJob(Job):
    async def execute(self) -> JobResult:
        started_at = datetime.utcnow()

        # Do work
        items_processed = await self._do_work()

        return JobResult(
            job_name=self.name,
            status=JobStatus.COMPLETED,
            started_at=started_at,
            items_processed=items_processed,
        )
```

### 2. Register Job Type

```python
# In JOB_TYPES dict
JOB_TYPES = {
    ...
    "new_job": NewJob,
}
```

### 3. Configure Schedule

```yaml
# config/automation.yaml
automation_jobs:
  - job_name: "new_job"
    job_type: "new_job"
    enabled: true
    schedule: "0 * * * *"  # Every hour
    settings:
      some_setting: "value"
```

## Testing Guidelines

### Unit Tests

- Test domain models in isolation
- Mock external dependencies
- Focus on business logic

```python
def test_email_all_recipients():
    email = Email(...)
    assert len(email.all_recipients) == 3
```

### Integration Tests

- Test with real database (in-memory SQLite)
- Test service interactions
- Use fixtures for common setups

```python
@pytest.mark.asyncio
async def test_email_service_search(db_session, email_service):
    # Setup
    await email_service.save_email(sample_email)

    # Test
    results = await email_service.search_emails("query")

    # Assert
    assert len(results) > 0
```

## Debugging

### Enable Debug Logging

```bash
uv run copilot --log-level DEBUG mcp
```

### Database Inspection

```bash
# SQLite CLI
sqlite3 executive_copilot.db

# Show tables
.tables

# Describe table
.schema emails
```

### MCP Testing

Use the MCP Inspector or Claude Desktop for interactive testing.

## Common Patterns

### Async Context Manager for Resources

```python
async with provider as p:
    data = await p.fetch_data()
```

### Unit of Work Pattern

```python
async with unit_of_work(db_manager) as uow:
    entity = await uow.repository.get_by_id(id)
    # Modify entity
    await uow.repository.update(entity)
    await uow.commit()
```

### Immutable Domain Models

```python
# Models are frozen, use with_update for modifications
updated = entity.with_update(field="new_value")
```
