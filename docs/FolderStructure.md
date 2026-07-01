# Folder Structure

```
executive-copilot/
├── src/
│   └── executive_copilot/
│       ├── __init__.py              # Package initialization, version
│       ├── cli.py                   # Command-line interface
│       │
│       ├── automation/              # Scheduled jobs and automation
│       │   ├── __init__.py
│       │   ├── engine.py            # APScheduler automation engine
│       │   └── jobs.py              # Job definitions (sync, briefing, etc.)
│       │
│       ├── config/                  # Configuration management
│       │   ├── __init__.py
│       │   ├── loader.py            # YAML config loading and merging
│       │   └── models.py            # Pydantic configuration models
│       │
│       ├── context/                 # Context Engine
│       │   ├── __init__.py
│       │   ├── engine.py            # Context determination logic
│       │   ├── models.py            # Context request/result models
│       │   └── rules.py             # Rule definitions and matching
│       │
│       ├── database/                # Database layer
│       │   ├── __init__.py
│       │   ├── connection.py        # Database connection management
│       │   ├── tables.py            # SQLModel table definitions
│       │   ├── unit_of_work.py      # Unit of Work pattern
│       │   └── repositories/        # Repository implementations
│       │       ├── __init__.py
│       │       ├── base.py          # Base repository class
│       │       ├── email.py         # Email/Thread repositories
│       │       ├── organization.py  # Organization repository
│       │       └── person.py        # Person repository
│       │
│       ├── exceptions/              # Exception hierarchy
│       │   ├── __init__.py
│       │   └── base.py              # All exception classes
│       │
│       ├── logging/                 # Structured logging
│       │   ├── __init__.py
│       │   └── config.py            # Logging configuration
│       │
│       ├── mcp/                     # MCP Server
│       │   ├── __init__.py
│       │   └── server.py            # FastMCP server and tool definitions
│       │
│       ├── memory/                  # Memory Layer
│       │   ├── __init__.py
│       │   ├── store.py             # Memory store implementation
│       │   └── types.py             # Memory entry types
│       │
│       ├── models/                  # Domain Models
│       │   ├── __init__.py          # Model exports
│       │   ├── base.py              # Base model classes
│       │   ├── attachment.py        # Attachment model
│       │   ├── calendar.py          # CalendarEvent, Attendee, etc.
│       │   ├── communication.py     # FollowUp, Reminder, ConversationSummary
│       │   ├── document.py          # Document model
│       │   ├── email.py             # Email, Thread, EmailAddress
│       │   ├── meeting.py           # Meeting model
│       │   ├── news.py              # NewsArticle model
│       │   ├── organization.py      # Organization, Customer
│       │   ├── person.py            # Person model
│       │   └── task.py              # Task model
│       │
│       ├── providers/               # Provider Interfaces
│       │   ├── __init__.py
│       │   ├── auth/                # Authentication providers
│       │   │   ├── __init__.py
│       │   │   └── interface.py     # AuthenticationProvider interface
│       │   ├── base/                # Base provider classes
│       │   │   ├── __init__.py
│       │   │   ├── provider.py      # Base Provider class
│       │   │   └── registry.py      # Provider registry
│       │   ├── calendar/            # Calendar providers
│       │   │   ├── __init__.py
│       │   │   └── interface.py     # CalendarProvider interface
│       │   ├── document/            # Document providers
│       │   │   ├── __init__.py
│       │   │   └── interface.py     # DocumentProvider interface
│       │   ├── email/               # Email providers
│       │   │   ├── __init__.py
│       │   │   └── interface.py     # EmailProvider interface
│       │   ├── news/                # News providers
│       │   │   ├── __init__.py
│       │   │   └── interface.py     # NewsProvider interface
│       │   ├── search/              # Search providers
│       │   │   ├── __init__.py
│       │   │   └── interface.py     # SearchProvider interface
│       │   └── task/                # Task providers
│       │       ├── __init__.py
│       │       └── interface.py     # TaskProvider interface
│       │
│       └── services/                # Service Layer
│           ├── __init__.py
│           ├── base.py              # Base service class
│           ├── calendar.py          # CalendarService
│           ├── container.py         # Dependency injection container
│           ├── context.py           # ContextService
│           ├── email.py             # EmailService
│           ├── memory.py            # MemoryService
│           ├── news.py              # NewsService
│           ├── search.py            # SearchService
│           └── task.py              # TaskService
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # Pytest fixtures
│   ├── unit/                        # Unit tests
│   │   ├── __init__.py
│   │   ├── test_context_engine.py
│   │   ├── test_models.py
│   │   └── test_repositories.py
│   ├── integration/                 # Integration tests
│   │   └── __init__.py
│   └── fixtures/                    # Test data
│
├── config/                          # Configuration files
│   ├── config.yaml                  # Base configuration
│   ├── organizations.yaml           # Organization definitions
│   ├── context_rules.yaml           # Context engine rules
│   ├── automation.yaml              # Automation job schedules
│   └── config.local.yaml            # Local overrides (gitignored)
│
├── docs/                            # Documentation
│   ├── Architecture.md
│   ├── Configuration.md
│   ├── DevelopmentGuide.md
│   ├── FolderStructure.md
│   ├── MCPGuide.md
│   ├── ProviderGuide.md
│   └── Roadmap.md
│
├── scripts/                         # Utility scripts
│
├── .env.example                     # Example environment variables
├── .gitignore
├── pyproject.toml                   # Project configuration
├── README.md
└── CLAUDE.md                        # Claude Code guidance
```

## Layer Responsibilities

### `/models`
Domain models representing business entities. Immutable, rich with behavior. No database or external system dependencies.

### `/providers`
Interfaces for external systems. Each subdirectory contains an interface file defining the contract and eventually implementation files.

### `/services`
Business logic orchestration. Services use providers and repositories, never access databases or external systems directly.

### `/database`
Database abstractions. Contains connection management, table definitions (SQLModel), and repository implementations.

### `/context`
Context Engine for determining which information sources to consult. Configuration-driven rules.

### `/memory`
Persistent knowledge storage. Executive's facts, decisions, and summaries.

### `/automation`
Scheduled job execution using APScheduler.

### `/mcp`
MCP server exposing tools to AI assistants.

### `/config`
Configuration loading and validation.

### `/logging`
Structured logging setup.

### `/exceptions`
Unified exception hierarchy for the application.
