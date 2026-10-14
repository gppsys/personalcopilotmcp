# MCP Guide

## Overview

Executive Copilot exposes its functionality through the Model Context Protocol (MCP), allowing AI assistants like Claude to interact with the executive's digital workspace.

## Running the MCP Server

```bash
# Run with stdio transport (default)
uv run copilot mcp

# Or directly
uv run copilot-mcp
```

## Connecting to Claude Desktop

Add to Claude Desktop's configuration (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "executive-copilot": {
      "command": "uv",
      "args": ["--directory", "/path/to/executive-copilot", "run", "copilot-mcp"],
      "env": {
        "COPILOT_LOGGING__LEVEL": "INFO"
      }
    }
  }
}
```

## Available Tools

### Email Tools

#### get_inbox
Get recent emails from inbox.

```
Arguments:
- limit (int, default: 20): Maximum emails to return
- unread_only (bool, default: false): Only return unread emails

Returns:
- count: Number of emails
- emails: List of email summaries
```

#### search_emails
Search emails with a query.

```
Arguments:
- query (str): Search query
- from_address (str, optional): Filter by sender
- limit (int, default: 20): Maximum results

Returns:
- query: Search query used
- count: Number of results
- emails: List of matching emails
```

#### draft_reply
Draft a reply to an email.

```
Arguments:
- email_id (str): ID of email to reply to
- body (str): Reply body text
- reply_all (bool, default: false): Reply to all recipients

Returns:
- Draft information (placeholder in Sprint 1)
```

#### send_email
Send a new email.

```
Arguments:
- to (list[str]): Recipient email addresses
- subject (str): Email subject
- body (str): Email body
- cc (list[str], optional): CC recipients

Returns:
- Send status (placeholder in Sprint 1)
```

### Calendar Tools

#### today_schedule
Get today's calendar schedule.

```
Arguments: None

Returns:
- date: Today's date
- summary: Schedule summary
- events: List of today's events
```

#### upcoming_events
Get upcoming calendar events.

```
Arguments:
- hours (int, default: 24): Hours to look ahead

Returns:
- hours_ahead: Time window
- count: Number of events
- events: List of upcoming events
```

### Task Tools

#### my_tasks
Get tasks assigned to me.

```
Arguments:
- include_completed (bool, default: false): Include completed tasks

Returns:
- count: Number of tasks
- tasks: List of assigned tasks
```

#### overdue_tasks
Get all overdue tasks.

```
Arguments: None

Returns:
- count: Number of overdue tasks
- tasks: List of overdue tasks
```

### Summary Tools

#### daily_summary
Get a combined daily summary.

```
Arguments: None

Returns:
- date: Current date
- email: Email summary (unread count, requiring response)
- calendar: Calendar summary
- tasks: Task summary
- action_items: Items requiring attention
```

#### organization_context
Get context about an organization.

```
Arguments:
- organization_name (str): Name of the organization

Returns:
- found: Whether organization was found
- name, type, domains, context_sources, etc.
```

### Search Tools

#### search_everything
Search across all data sources.

```
Arguments:
- query (str): Search query
- limit (int, default: 20): Maximum results

Returns:
- query: Search query
- total_count: Total matching items
- by_type: Count by entity type
- results: Search results
```

### Memory Tools

#### recall_memory
Search the executive's knowledge base.

```
Arguments:
- query (str): What to search for
- limit (int, default: 10): Maximum results

Returns:
- query: Search query
- count: Number of results
- memories: List of relevant memories
```

#### get_follow_ups
Get open follow-up items.

```
Arguments: None

Returns:
- count: Number of follow-ups
- follow_ups: List of pending items
```

## Example Conversations

### Checking Schedule

**User:** What's on my calendar today?

**Assistant uses:** `today_schedule()`

**Response:** You have 5 meetings today:
- 9:00 AM - Team Standup (15 min)
- 10:00 AM - Client Call with Acme (1 hr)
- 12:00 PM - Lunch with John
- 2:00 PM - Sprint Planning (2 hrs)
- 4:30 PM - 1:1 with Sarah (30 min)

Total meeting time: 4.75 hours

### Finding Emails

**User:** Show me recent emails from Acme

**Assistant uses:** `search_emails(query="", from_address="*@acme.com")`

**Response:** Found 12 emails from Acme in the last week:
1. "Q4 Contract Review" from john@acme.com (2 days ago)
2. "API Integration Questions" from dev@acme.com (3 days ago)
...

### Daily Briefing

**User:** Give me my daily briefing

**Assistant uses:** `daily_summary()`

**Response:** Good morning! Here's your briefing for June 25:

**Email:**
- 23 unread emails
- 3 emails requiring response

**Calendar:**
- 5 meetings today (4.75 hours)
- First meeting: Team Standup at 9:00 AM

**Tasks:**
- 2 tasks overdue
- 4 tasks due today

**Action Items:**
- Respond to John's contract email
- Complete Q4 budget review (overdue)

### Context Lookup

**User:** What do we know about Acme Corporation?

**Assistant uses:** `organization_context(organization_name="Acme")`

**Response:** Acme Corporation is a client with the following details:
- Domains: acme.com
- Importance: 9/10
- Context sources: Asana, Documentation, Memory
- Tags: enterprise, priority

## Adding Custom Tools

```python
# In src/executive_copilot/mcp/server.py

@mcp.tool()
async def custom_tool(
    param: str,
) -> dict[str, Any]:
    """Description for AI assistants.

    Args:
        param: Description of parameter

    Returns:
        Description of return value
    """
    result = await container.service.operation(param)
    return {"result": result}
```

## Error Handling

Tools return error information when operations fail:

```json
{
  "error": true,
  "message": "Failed to fetch emails",
  "details": {
    "provider": "outlook",
    "error_type": "RateLimitError"
  }
}
```

## Transport Options

### stdio (default)
Standard input/output, used by Claude Desktop.

### Streamable HTTP
For clients that speak streamable HTTP (e.g. Claude Code). Serves `/mcp`,
localhost only, opt-in via the `http` compose profile. It has no
authentication by default; a workspace key protects it when configured.

```bash
docker compose --profile http up -d
claude mcp add --transport http executive-copilot http://127.0.0.1:8091/mcp

# Or without Docker
uv run copilot mcp --transport http --port 8091
```
