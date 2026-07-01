# Quick Start Guide

Get Executive Copilot running in 5 minutes.

## 1. Install

```bash
# Clone and install
git clone https://github.com/gppsys/personalcopilot.git
cd personalcopilot
uv sync
```

## 2. Configure Your Accounts

```bash
# Create your organizations config
cp config/organizations.example.yaml config/organizations.yaml
```

Edit `config/organizations.yaml` with your email accounts:

```yaml
organizations:
  - name: "My Company"
    organization_type: "employer"
    primary_domain: "mycompany.com"
    provider_accounts:
      - provider_type: "email"
        provider_name: "gmail"
        account_id: "me@mycompany.com"
      - provider_type: "calendar"
        provider_name: "google"
        account_id: "me@mycompany.com"
```

## 3. Authorize Email/Calendar Access

```bash
# Start admin panel
uv run copilot admin

# Open http://localhost:8080 in your browser
# Click "Authorize" for each account
```

## 4. Initialize Database

```bash
uv run copilot db init
```

## 5. Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "executive-copilot": {
      "command": "uv",
      "args": ["--directory", "/full/path/to/personalcopilot", "run", "copilot-mcp"]
    }
  }
}
```

Restart Claude Desktop. You should see the tools available.

---

## 6. Run Background Automation (Recommended)

For automatic follow-up detection and scheduled jobs, run:

```bash
uv run copilot serve
```

This starts:
- Admin panel at http://localhost:8080
- Background automation jobs (configured in `config/automation.yaml`)

**Default scheduled jobs:**
| Job | Schedule | Description |
|-----|----------|-------------|
| Follow-up detection | 9am & 2pm | Scans sent emails for pending replies |
| Transcript scan | 10am & 4pm | Scans meeting transcripts for action items |
| Email sync | Every 5 min | Syncs emails from providers |
| Calendar sync | Every 15 min | Syncs calendar events |
| Task sync | Every 30 min | Syncs tasks from Asana |

### Running Options

**Option A: With automation (recommended)**
```bash
uv run copilot serve
# Runs admin panel + automation engine together
# Claude Desktop connects to MCP separately when you use it
```

**Option B: Components separately**
```bash
# MCP server only (spawned by Claude Desktop automatically)
uv run copilot mcp

# Admin panel only (no background jobs)
uv run copilot admin

# Manual sync (one-time)
uv run copilot sync-replies
```

---

## Using the Copilot

### Ask Claude things like:

- "What's on my calendar today?"
- "Show me unread emails"
- "What do I need to follow up on?"
- "Prepare me for my 2pm meeting"
- "What tasks are overdue?"

### Manual Sync (if not using `copilot serve`)

If you're not running `copilot serve`, you can manually sync pending replies:

```bash
# Scan last 7 days, flag after 2 days without reply
uv run copilot sync-replies

# Custom settings
uv run copilot sync-replies --days-back 14 --min-wait 3
```

This creates follow-up reminders automatically. When you ask Claude "what do I need to do?", these will be included.

> **Note:** If you're running `copilot serve`, this happens automatically at 9am and 2pm.

---

## Common Tasks

### View Configuration

```bash
uv run copilot config
```

### Run Admin Panel (with automation)

```bash
uv run copilot serve
# Open http://localhost:8080
# Background jobs run automatically
```

### Run Admin Panel (without automation)

```bash
uv run copilot admin
# Open http://localhost:8080
```

### Check Logs

```bash
uv run copilot mcp --log-level DEBUG
```

---

## Troubleshooting

### "Not authorized" errors

Re-authorize via the admin panel:

```bash
uv run copilot admin
```

### MCP not connecting

1. Check the path in `claude_desktop_config.json` is absolute
2. Restart Claude Desktop
3. Check logs in Claude Desktop (Help → View Logs)

### Database errors

Reset the database:

```bash
uv run copilot db drop
uv run copilot db init
```

---

## Next Steps

- Add more organizations in `config/organizations.yaml`
- Configure context rules in `config/context_rules.yaml`
- Set up automation jobs in `config/automation.yaml`
- See full documentation in `/docs`
