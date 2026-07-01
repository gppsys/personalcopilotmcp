# Executive Copilot Architecture

## Overview

Executive Copilot is an MCP (Model Context Protocol) server that provides AI assistants with access to an executive's digital workspace. It aggregates data from multiple providers (email, calendar, tasks, documents) and presents a unified interface for intelligent assistance.

## Design Philosophy

- **Clean Architecture**: Clear separation between layers (domain, application, infrastructure)
- **SOLID Principles**: Single responsibility, open for extension, dependency inversion
- **Provider Abstraction**: External systems accessed through well-defined interfaces
- **Domain-Driven Design**: Rich domain models that encapsulate business logic
- **Configuration Over Code**: Business rules defined in YAML, not hardcoded

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                         MCP Layer                                │
│              (FastMCP server with tool definitions)              │
├─────────────────────────────────────────────────────────────────┤
│                       Service Layer                              │
│    (EmailService, CalendarService, ContextService, etc.)         │
├─────────────────────────────────────────────────────────────────┤
│                       Domain Layer                               │
│         (Models, Business Rules, Value Objects)                  │
├─────────────────────────────────────────────────────────────────┤
│                    Infrastructure Layer                          │
│       (Providers, Repositories, Database, Config)                │
└─────────────────────────────────────────────────────────────────┘
```

### MCP Layer

The MCP layer exposes tools to AI assistants:
- `get_inbox()` - Retrieve inbox emails
- `search_emails()` - Search email content
- `today_schedule()` - Get calendar events
- `my_tasks()` - Get assigned tasks
- `daily_summary()` - Combined daily briefing
- `organization_context()` - Get organization information
- `search_everything()` - Unified search across all sources

### Service Layer

Services orchestrate providers and contain business logic:
- **EmailService**: Email operations, classification, organization matching
- **CalendarService**: Event management, scheduling, availability
- **TaskService**: Task management across providers
- **ContextService**: Determine information sources for queries
- **MemoryService**: Persistent knowledge management
- **SearchService**: Unified search across data sources
- **NewsService**: News aggregation and relevance scoring

### Domain Layer

Rich domain models with behavior:
- **Email, Thread**: Email representation with participant tracking
- **CalendarEvent**: Events with attendees and recurrence
- **Task**: Tasks with dependencies and status tracking
- **Organization, Person**: Contact and relationship management
- **FollowUp, Reminder**: Action item tracking

### Infrastructure Layer

- **Providers**: Interfaces for external systems (Outlook, Gmail, Asana, etc.)
- **Repositories**: Database access abstraction using Repository pattern
- **Configuration**: YAML-based configuration loading
- **Logging**: Structured logging with structlog

## Core Components

### Context Engine

The Context Engine analyzes inputs and determines which information sources to consult:

```
Input (Email/Question/Meeting)
         │
         ▼
  ┌─────────────────┐
  │ Context Engine  │
  │  Rule Matcher   │
  └─────────────────┘
         │
         ▼
  ┌─────────────────┐
  │ Source          │
  │ Recommendations │
  └─────────────────┘
         │
         ▼
  [Asana, Memory, Threads, Documentation, ...]
```

Rules are configured in YAML and matched based on:
- Organization name/domain
- Email sender
- Content keywords
- Input type

### Provider Architecture

Providers communicate with external systems and map data to domain models:

```
┌──────────────────┐     ┌──────────────────┐
│   EmailProvider  │     │ CalendarProvider │
│   (Interface)    │     │   (Interface)    │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
    ┌────┴────┐              ┌────┴────┐
    │         │              │         │
┌───▼───┐ ┌───▼───┐    ┌────▼───┐ ┌───▼────┐
│Outlook│ │ Gmail │    │ Outlook│ │ Google │
│Provider│ │Provider│   │Calendar│ │Calendar│
└───────┘ └───────┘    └────────┘ └────────┘
```

Providers:
- Are registered in the ProviderRegistry
- Are instantiated via dependency injection
- Never contain business logic
- Map external formats to domain models

### Repository Pattern

Repositories abstract database access:

```python
class Repository(ABC, Generic[ModelT, TableT]):
    async def get_by_id(self, id: UUID) -> ModelT | None
    async def add(self, model: ModelT) -> ModelT
    async def update(self, model: ModelT) -> ModelT
    async def delete(self, id: UUID) -> bool
```

Unit of Work coordinates transactions:

```python
async with unit_of_work(db_manager) as uow:
    person = await uow.persons.get_by_id(id)
    person = person.with_update(name="New Name")
    await uow.persons.update(person)
    await uow.commit()
```

### Automation Engine

APScheduler-based job scheduling:

```
┌─────────────────────────────────────────────┐
│              Automation Engine               │
│  ┌─────────┐ ┌─────────┐ ┌─────────────┐   │
│  │EmailSync│ │TaskSync │ │DailyBriefing│   │
│  │ Job     │ │ Job     │ │    Job      │   │
│  └────┬────┘ └────┬────┘ └──────┬──────┘   │
│       │           │             │           │
│       └───────────┼─────────────┘           │
│                   ▼                         │
│            APScheduler                       │
└─────────────────────────────────────────────┘
```

Jobs are configured in YAML with cron schedules.

## Data Flow

### Email Processing

```
Provider → Fetch → Classify → Store → Index
              ↓
         Organization
          Matching
              ↓
         Follow-up
         Detection
```

### Query Processing

```
MCP Tool Call
      ↓
   Service
      ↓
Context Engine → Source Recommendations
      ↓
 Gather Data from Sources
      ↓
   Response
```

## Configuration System

Configuration is loaded from YAML files in priority order:
1. `config.yaml` - Base configuration
2. `config.{environment}.yaml` - Environment overrides
3. `config.local.yaml` - Local overrides (not committed)

Environment variables override file configuration using `COPILOT_` prefix.

## Database Design

SQLite for development, PostgreSQL-ready for production:

```
┌────────────┐   ┌─────────────┐   ┌────────────┐
│  persons   │   │organizations│   │   emails   │
├────────────┤   ├─────────────┤   ├────────────┤
│ id         │   │ id          │   │ id         │
│ first_name │   │ name        │   │ thread_id  │
│ last_name  │   │ domains     │   │ sender     │
│ org_id  ◄──┼───┤ type        │   │ subject    │
│ contact    │   │ context_src │   │ org_id  ◄──┤
└────────────┘   └─────────────┘   └────────────┘
```

## Security Considerations

- OAuth tokens stored encrypted
- Provider credentials managed separately
- No sensitive data in logs
- Configuration secrets via environment variables

## Extensibility Points

1. **New Providers**: Implement provider interface, register in registry
2. **New Context Rules**: Add YAML configuration
3. **New MCP Tools**: Add function to MCP server
4. **New Jobs**: Create job class, register in JOB_TYPES
5. **New Domain Models**: Add to models module with repository
