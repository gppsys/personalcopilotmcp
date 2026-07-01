# Executive Copilot

An MCP (Model Context Protocol) server that provides AI assistants with access to an executive's digital workspace. Aggregates data from email, calendar, tasks, and documents to enable intelligent assistance.

## Features

- **MCP Interface**: Works with Claude Desktop, ChatGPT, Cursor, and any MCP-compatible client
- **Multi-Provider Architecture**: Pluggable providers for Outlook, Gmail, Google Calendar, Asana, and more
- **Context Engine**: Intelligently determines which information sources to consult
- **Memory Layer**: Persistent knowledge base for organizations, people, and decisions
- **Automation Engine**: Scheduled jobs for synchronization and briefings
- **Follow-up Detection**: Automatically tracks sent emails awaiting replies
- **Transcript Scanning**: Extracts action items from meeting transcripts (Google Meet)

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)

### Installation

```bash
# Clone the repository
git clone https://github.com/gppsys/personalcopilot.git
cd personalcopilot

# Install dependencies
uv sync

# Copy and configure your settings
cp config/organizations.example.yaml config/organizations.yaml
# Edit config/organizations.yaml with your accounts

# Initialize database
uv run copilot db init

# Run MCP server
uv run copilot mcp
```

### Connect to Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "executive-copilot": {
      "command": "uv",
      "args": ["--directory", "/path/to/personalcopilot", "run", "copilot-mcp"]
    }
  }
}
```

## Architecture

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

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `get_inbox` | Get recent inbox emails |
| `search_emails` | Search emails with query |
| `get_follow_ups` | Get pending follow-ups and reminders |
| `today_schedule` | Get today's calendar events |
| `upcoming_events` | Get upcoming events |
| `my_tasks` | Get assigned tasks |
| `overdue_tasks` | Get overdue tasks |
| `prepare_for_meeting` | Get context for upcoming meeting |
| `daily_summary` | Combined daily briefing |
| `organization_context` | Get organization information |
| `search_everything` | Unified search across sources |
| `recall_memory` | Search knowledge base |

## Running the Copilot

### Option A: With Background Automation (Recommended)

Run the server with automatic follow-up detection and scheduled jobs:

```bash
# Start admin panel + automation engine
uv run copilot serve

# In Claude Desktop config, connect to MCP server separately
```

This runs:
- Admin panel at http://localhost:8080
- Background automation (follow-up detection at 9am & 2pm, email sync, etc.)

### Option B: Components Separately

```bash
# MCP server only (for Claude Desktop)
uv run copilot mcp

# Admin panel only (no automation)
uv run copilot admin

# Manual sync of pending replies
uv run copilot sync-replies
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `copilot serve` | Run admin panel with background automation |
| `copilot mcp` | Run the MCP server (for Claude Desktop) |
| `copilot db init` | Initialize database tables |
| `copilot admin` | Run the admin web panel only |
| `copilot sync-replies` | Manually scan sent emails for pending replies |
| `copilot config` | Show current configuration |
| `copilot tui` | Run terminal configuration UI |

## Configuration

Configuration is managed through YAML files in the `config/` directory:

- `config.yaml` - Base configuration
- `organizations.yaml` - Organization definitions (create from `.example.yaml`)
- `context_rules.yaml` - Context engine rules
- `automation.yaml` - Scheduled job configuration

See [Configuration Guide](docs/Configuration.md) for details.

## Development

```bash
# Install dev dependencies
uv sync --dev

# Run tests
uv run pytest

# Run linter
uv run ruff check .

# Type checking
uv run mypy src
```

See [Development Guide](docs/DevelopmentGuide.md) for detailed instructions.

## Documentation

- [Architecture](docs/Architecture.md)
- [Development Guide](docs/DevelopmentGuide.md)
- [Configuration](docs/Configuration.md)
- [Provider Guide](docs/ProviderGuide.md)
- [MCP Guide](docs/MCPGuide.md)
- [Roadmap](docs/Roadmap.md)

## License

MIT
