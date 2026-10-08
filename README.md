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
- **Obsidian Meeting Notes**: Writes Gemini meeting notes into your vault as dated sections per meeting series, and lets the assistant search them
- **E-mail to Calendar**: Finds e-mails that need a calendar entry (invites, appointments, school events, reservations) and adds the ones you approve to your default calendar

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

# Optional: local overrides and extra rules (all gitignored)
cp config/config.local.example.yaml config/config.local.yaml
cp config/context_rules.example.yaml config/context_rules.yaml
cp config/email_forward_rules.example.yaml config/email_forward_rules.yaml

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
| `sync_meeting_notes_to_obsidian` | Write recent Gemini meeting notes into the Obsidian vault (dry run available) |
| `vault_overview` / `search_vault` / `read_vault_note` / `get_person_context` | Search-then-read access to the Obsidian vault through a local index (SQLite FTS5 + OpenAI embeddings, fused by rank): searches return short snippets, reads can target one section. Credential-looking values are redacted before indexing; `obsidian.index_exclude` keeps whole notes out. Evaluate on your own questions with `scripts/eval_vault_search.py` |
| `edit_vault_note` | Surgical note edits (tick a checkbox, append to or replace within a section) with a backup and a diff |
| `review_email_calendar_requests` | Propose calendar entries found in e-mail (nothing is created) |
| `add_email_to_calendar` / `dismiss_email_calendar_request` | Accept or dismiss a proposal |
| `create_calendar_event` | Create an event; defaults to `calendar_defaults` |

## Running the Copilot

### Docker (recommended)

```bash
# 1. Local settings for compose (gitignored)
cat > .env <<'ENV'
OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
TZ=America/Mexico_City
ENV

# 2. Admin panel + automation on http://127.0.0.1:8090
docker compose up -d --build
```

Open http://localhost:8090 for the workspace: today's agenda, Asana tasks and
follow-ups, e-mail-to-calendar proposals, the Obsidian meeting notes (browse,
search, edit, sync from Gemini) and a chat that can read all of it. Changes the
chat suggests (new task, new event) appear as cards you confirm. Settings and
account authorization live at http://localhost:8090/admin. The chat uses the
server AI key configured under Settings → Credentials.

The **Cuentas** section lists every Google/Microsoft account and token-based
service (Asana, Slack, OpenAI) with a live health check, connect/reconnect,
disconnect, and the redirect URIs to register. Secrets are never sent to the
browser. It also sets the default calendar and the calendar that receives
events found in Outlook mail; those choices are saved to
`config/ui_settings.yaml` (gitignored), which overrides the other config files.

The **Mapa de relaciones** section draws how people, organizations, meeting
series, Asana projects/tasks, upcoming events and delegations connect, built
from Obsidian notes, the e-mail interaction log, Asana and the calendar.

The repo is bind-mounted, so config, credentials and SQLite files stay on the
host and code changes only need `docker compose restart`. The vault is mounted
at the same absolute path it has on the host. `TZ` matters: cron schedules in
`config/automation.yaml` run in the container's local time.

Point Claude Desktop at the running container (stdio):

```json
{
  "mcpServers": {
    "executive-copilot": {
      "command": "docker",
      "args": ["exec", "-i", "copilot-admin", "/opt/venv/bin/python", "-m", "executive_copilot.mcp"]
    }
  }
}
```

Clients that speak streamable HTTP (e.g. Claude Code) can use the opt-in HTTP
service instead. It has no authentication and listens on localhost only:

```bash
docker compose --profile http up -d
claude mcp add --transport http executive-copilot http://127.0.0.1:8091/mcp
```

### Option A: With Background Automation

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
