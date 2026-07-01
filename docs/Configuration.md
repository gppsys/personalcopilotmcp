# Configuration Guide

## Overview

Executive Copilot uses a layered configuration system:

1. **Base configuration** (`config/config.yaml`)
2. **Environment-specific** (`config/config.{environment}.yaml`)
3. **Local overrides** (`config/config.local.yaml`)
4. **Environment variables** (prefix: `COPILOT_`)

Later sources override earlier ones.

## Configuration Files

### config.yaml (Base)

```yaml
# Environment
environment: development
debug: true

# Database
database:
  driver: "sqlite+aiosqlite"
  name: "executive_copilot.db"
  pool_size: 5
  echo: false

# Logging
logging:
  level: "INFO"
  json_format: false
  add_timestamps: true

# MCP Server
mcp:
  name: "executive-copilot"
  version: "0.1.0"
  transport: "stdio"

# Server-side AI
# MCP clients choose their own assistant model. This block is only for
# Executive Copilot background intelligence such as summaries and extraction.
ai:
  provider: "openai"
  default_model: "gpt-5.4-mini"
  premium_model: "gpt-5.5"
  fallback_model: "gpt-5.4-nano"
  embedding_model: "text-embedding-3-small"
  reasoning_effort: "medium"
  text_verbosity: "low"
  max_output_tokens: 4096
  temperature: 0.2
  store: false
  credential_provider_type: "ai"
  credential_provider_name: "openai"
```

Configure the OpenAI API key through the Credentials UI using provider type `ai`,
provider name `openai`, and the `api_key` field.

### organizations.yaml

```yaml
organizations:
  - name: "Acme Corporation"
    short_name: "Acme"
    organization_type: "client"  # employer, client, vendor, partner, school, personal
    domains:
      - "acme.com"
      - "acme.io"
    context_sources:
      - "asana"
      - "documentation"
      - "memory"
      - "previous_threads"
    default_project_id: "12345"
    importance: 9  # 1-10
    tags:
      - "enterprise"
      - "priority"
```

### context_rules.yaml

```yaml
context_rules:
  - name: "rule_name"
    description: "When this rule applies"
    priority: 100  # Higher = evaluated first
    enabled: true
    conditions:
      # Available conditions:
      organization: "Acme"          # Organization name
      domain: "acme.com"            # Sender domain
      input_type: "email"           # email, question, meeting, task
      content_contains: "keyword"   # Content includes text
      content_regex: "pattern.*"    # Content matches regex
      keywords: ["api", "bug"]      # Any keyword present
      topics: ["technical"]         # Any topic matched
    sources:
      - "asana"
      - "documentation"
      - "memory"
      - "previous_threads"
      - "calendar"
      - "tasks"
```

### automation.yaml

```yaml
automation_jobs:
  - job_name: "email_sync"
    job_type: "email_sync"
    enabled: true
    schedule: "*/5 * * * *"  # Cron syntax
    settings:
      max_emails_per_sync: 100
      sync_sent: true

  - job_name: "daily_briefing"
    job_type: "daily_briefing"
    enabled: true
    schedule: "0 7 * * *"  # 7 AM daily
    settings:
      include_weather: true
      include_news: true
```

### providers.yaml

```yaml
providers:
  - provider_type: "email"
    provider_name: "outlook"
    enabled: true
    account_id: "work"
    oauth_config_name: "microsoft_work"
    settings:
      sync_sent: true
      max_results: 100

  - provider_type: "calendar"
    provider_name: "google"
    enabled: true
    account_id: "personal"
    oauth_config_name: "google_personal"
```

### oauth.yaml

```yaml
oauth:
  microsoft_work:
    provider_name: "microsoft"
    client_id: "${MS_WORK_CLIENT_ID}"
    client_secret: "${MS_WORK_CLIENT_SECRET}"
    redirect_uri: "http://localhost:8080/callback"
    scopes:
      - "Mail.Read"
      - "Mail.Send"
      - "Calendars.ReadWrite"
      - "Tasks.ReadWrite"

  google_personal:
    provider_name: "google"
    client_id: "${GOOGLE_CLIENT_ID}"
    client_secret: "${GOOGLE_CLIENT_SECRET}"
    redirect_uri: "http://localhost:8080/callback"
    scopes:
      - "https://www.googleapis.com/auth/gmail.readonly"
      - "https://www.googleapis.com/auth/calendar"
```

### news_sources.yaml

```yaml
news_sources:
  - name: "TechCrunch"
    source_type: "rss"
    url: "https://techcrunch.com/feed/"
    enabled: true
    category: "technology"
    refresh_minutes: 60
    filters:
      exclude_keywords: ["sponsored"]

  - name: "Bloomberg Markets"
    source_type: "rss"
    url: "https://feeds.bloomberg.com/markets/news.rss"
    enabled: true
    category: "finance"
    refresh_minutes: 30
```

## Environment Variables

All configuration can be overridden via environment variables:

```bash
# Pattern: COPILOT_<SECTION>__<KEY>
export COPILOT_DATABASE__DRIVER="postgresql+asyncpg"
export COPILOT_DATABASE__HOST="localhost"
export COPILOT_DATABASE__PORT="5432"
export COPILOT_DATABASE__NAME="copilot"
export COPILOT_DATABASE__USER="copilot"
export COPILOT_DATABASE__PASSWORD="secret"

export COPILOT_LOGGING__LEVEL="DEBUG"
export COPILOT_LOGGING__JSON_FORMAT="true"

export COPILOT_ENVIRONMENT="production"
export COPILOT_DEBUG="false"
```

## Environment-Specific Configuration

### config.development.yaml

```yaml
debug: true
database:
  echo: true
logging:
  level: "DEBUG"
```

### config.production.yaml

```yaml
debug: false
database:
  driver: "postgresql+asyncpg"
  pool_size: 20
  max_overflow: 10
logging:
  level: "INFO"
  json_format: true
```

## Local Configuration

Create `config/config.local.yaml` for personal overrides (not committed):

```yaml
# OAuth credentials
oauth:
  microsoft_work:
    client_id: "your-actual-client-id"
    client_secret: "your-actual-secret"

# Personal organization overrides
organizations:
  - name: "My Company"
    domains:
      - "mycompany.com"
```

## Available Context Sources

| Source | Description |
|--------|-------------|
| `asana` | Asana tasks and projects |
| `outlook` | Outlook emails |
| `gmail` | Gmail emails |
| `google_calendar` | Google Calendar |
| `outlook_calendar` | Outlook Calendar |
| `google_drive` | Google Drive documents |
| `onedrive` | OneDrive documents |
| `slack` | Slack messages |
| `github` | GitHub issues and PRs |
| `memory` | Executive's knowledge base |
| `organizations` | Organization info |
| `people` | Contact information |
| `conversations` | Conversation summaries |
| `decisions` | Recorded decisions |
| `follow_ups` | Follow-up items |
| `documentation` | Documentation search |
| `previous_threads` | Email thread history |
| `calendar` | Calendar events |
| `tasks` | Task list |
| `news` | News articles |
| `web_search` | Web search |
| `website` | Organization websites |

## Job Types

| Type | Description |
|------|-------------|
| `email_sync` | Synchronize emails from providers |
| `calendar_sync` | Synchronize calendar events |
| `task_sync` | Synchronize tasks |
| `news_sync` | Fetch news articles |
| `daily_briefing` | Generate daily briefing |
| `follow_up_detection` | Detect follow-up needs |

## Cron Schedule Syntax

```
# ┌───────────── minute (0 - 59)
# │ ┌───────────── hour (0 - 23)
# │ │ ┌───────────── day of month (1 - 31)
# │ │ │ ┌───────────── month (1 - 12)
# │ │ │ │ ┌───────────── day of week (0 - 6) (Sunday to Saturday)
# │ │ │ │ │
# * * * * *

# Examples:
"*/5 * * * *"     # Every 5 minutes
"0 * * * *"       # Every hour
"0 7 * * *"       # Daily at 7 AM
"0 9,14 * * *"    # At 9 AM and 2 PM
"0 0 * * 0"       # Weekly on Sunday
"0 0 1 * *"       # Monthly on the 1st
```

## Validation

Configuration is validated at startup using Pydantic:

```python
from executive_copilot.config import load_config

# Raises ConfigurationError if invalid
config = load_config()
```

## Viewing Configuration

```bash
# Show current configuration (excluding secrets)
uv run copilot config
```
