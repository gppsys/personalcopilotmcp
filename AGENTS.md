# AGENTS.md

Guidance for coding agents (Codex and others) working in this repository.

## Project Overview

Executive Copilot gives AI assistants access to an executive's digital workspace: e-mail, calendars, tasks, Slack, and an Obsidian vault of meeting notes. Three faces, one codebase:

1. **MCP server** (~80 tools) for MCP clients (Claude Desktop, Claude Code, Cursor, VS Code…).
2. **Web workspace** at http://localhost:8090 — agenda, tasks, e-mail→calendar, Obsidian browse/edit/sync, relationship map, accounts, status console, and a chat with confirm-before-write action cards. The older settings panel lives at `/admin`.
3. **Automation** — APScheduler jobs running in the same process as the workspace.

## Running (Docker first)

The repo is bind-mounted into the container (`.:/app`), so code, `config/`, credentials and SQLite files are shared with the host. Code changes only need a restart, not a rebuild (rebuild when dependencies change).

```bash
printf 'OBSIDIAN_VAULT_PATH=/abs/path/to/vault\nTZ=America/Mexico_City\n' > .env   # gitignored
docker compose up -d --build                  # workspace + automation on :8090
docker compose restart copilot                # pick up code changes
docker compose --profile http up -d           # + MCP over streamable HTTP on :8091 (no auth, localhost only)
docker logs -f copilot-admin

# Run a job now
docker exec copilot-admin uv run copilot automation run obsidian_meeting_sync

# Stdio MCP clients run the server inside the container
docker exec -i copilot-admin /opt/venv/bin/python -m executive_copilot.mcp
```

- OAuth must be started from `http://localhost:8090` so the redirect URI (`/auth/google/callback`) matches what is registered with the provider.
- `copilot automation run` starts a second scheduler for its lifetime, so other jobs may also fire in that process; for one-off runs prefer the service or the `/api/ws/...` endpoint.

## Dev Commands

```bash
uv sync --dev
uv run pytest -q                     # unit tests (fast, offline)
uv run ruff check src tests          # lint (--fix to autofix)
uv run mypy src                      # type check
uv run copilot config                # effective config (secrets excluded)
```

## Architecture

```
src/executive_copilot/
├── admin/        # FastAPI app: workspace (static/workspace.html, Alpine + Tailwind CDN),
│                 #   workspace_api.py (/api/ws/*), OAuth (oauth.py), encrypted credentials
├── automation/   # engine.py (APScheduler), jobs.py (JOB_TYPES), run_log.py (persistent run history)
├── mcp/          # server.py (tools), activity.py (MCP call log in activity.db)
├── services/     # business logic: workspace, accounts, status, obsidian_notes, vault_index,
│                 #   email_forward, email_calendar, chat, knowledge_graph, ...
├── providers/    # email (gmail, outlook), calendar (google, outlook), task (asana),
│                 #   communication (slack); document/news/search are interfaces only
├── models/ database/ context/ memory/ config/ tui/
```

Principles: providers only fetch and map to domain models (no business logic, never expose external types); services orchestrate; domain models are frozen (`model.with_update()`); repositories for DB access; rules and settings in YAML, secrets never in YAML.

### Key subsystems

- **Accounts & credentials** (`admin/credentials.py`, `services/accounts.py`): Fernet-encrypted files in `config/.credentials/`. OAuth families (google, microsoft) share client and refresh token across sibling records (mail/calendar/docs); a refresh writes the new token to all siblings. Google scopes include `drive.readonly` to read Gemini notes Docs; adding a scope requires reconnecting the account. Listings report only whether a secret exists, never its value.
- **Gemini → Obsidian meeting notes** (`services/obsidian_notes.py`, job `obsidian_meeting_sync`): sources are Gemini Docs attached to Google Calendar events (Drive export, transcript stripped; transcript-only Docs are summarized from the transcript; Docs where Gemini produced no summary are skipped) with Gemini e-mails as fallback. One note per meeting series with `## YYYY-MM-DD` sections newest-first. Idempotent per (note, date); the job catches up from the newest date already in the vault. Sections with an empty summary are never written, and the summary prompt forbids inventing content.
- **Vault search** (`services/vault_index.py`): SQLite FTS5 + embeddings (hybrid RRF) in `vault_index.db`, refreshed incrementally. MCP tools follow search-then-read (`vault_overview`, `search_vault`, `read_vault_note` with section, `get_person_context`) to keep token use low. `obsidian.index_exclude` keeps sensitive notes out; secrets are also redacted at index time.
- **GitHub repo docs** (`providers/code/github.py`, `services/github_docs.py`, job `github_docs_sync`, config `github_docs:`): mirrors first-party markdown of the org's repos to `github_docs/` (gitignored; vendored/plugin/CHANGELOG docs skipped) and indexes it read-only under `GitHub/<org>/<repo>/…`. Each repo gets a `_repositorio.md` card (metadata + AI summary: what it does, stack, integrations, state); `vault_overview(folder="GitHub")` lists the cards. Incremental by `pushed_at` and blob sha; bump `SUMMARY_VERSION` to regenerate cards. Search collapses identical sections found in both the vault and a repo (`also_in`). Token: fine-grained, read-only Contents, saved in Cuentas.
- **Status console** (`services/status.py`, `/api/ws/status`, `/api/ws/status/checks`, view `#status`): local snapshot (accounts, jobs and run history, data freshness, MCP traffic, events) refreshed every 30 s; live API checks cached for 60 s.
- **Job run log** (`automation/run_log.py`): every `Job.run()` is persisted to `activity.db` (`job_runs`). Jobs not implemented yet set `placeholder = True`.

## Adding Features

- **MCP tool**: `@mcp.tool()` in `mcp/server.py`; keep outputs compact.
- **Workspace feature**: route in `admin/workspace_api.py` (config is re-read per request), logic in `services/`, view in `admin/static/workspace.html` (add it to `nav`, `go()` and `refresh()`). It must work in light and dark themes and at phone width.
- **Provider**: interface in `providers/{type}/interface.py`, implementation, register in `providers/base/registry.py`, account under `provider_accounts` in `config/organizations.yaml`.
- **Automation job**: class in `automation/jobs.py`, add to `JOB_TYPES`, schedule in `config/automation.yaml`. Report `FAILED` (or errors in `details`) when nothing could be read, never a silent "0 items".
- **Calendar writes** stay review-first: propose, then confirm.

## Configuration

- Load order: `config/config.yaml`, then `automation.yaml`, `context_rules.yaml`, `email_forward_rules.yaml`, `organizations.yaml`, and finally `config.local.yaml` and `ui_settings.yaml`.
- Personal files are gitignored and have `*.example.yaml` templates: `config.local.yaml`, `organizations.yaml`, `context_rules.yaml`, `email_forward_rules.yaml`. `ui_settings.yaml` is written by the workspace.
- `.env`: `OBSIDIAN_VAULT_PATH` (mounted at the same absolute path in the container) and `TZ`. Environment overrides use the `COPILOT_` prefix (e.g. `COPILOT_DATABASE__HOST`).
- Runtime files at the repo root (gitignored): `executive_copilot.db`, `activity.db` (MCP activity + job runs), `vault_index.db`, `briefing_cache.json`, `dashboard_done_items.json`.

## Testing Patterns

- Unit tests are pure and offline: `tmp_path` for vault/DB files, `SimpleNamespace` for config.
- HTTP: patch the module's `httpx.AsyncClient` with one using `httpx.MockTransport` (see `tests/unit/test_gmail_provider.py`).
- Providers created inside services: monkeypatch the class in its module (see `FakeCalendar` in `tests/unit/test_obsidian_notes.py`).
- Async tests use `@pytest.mark.asyncio`.

## Code Style

- async/await for all I/O; type hints everywhere; Ruff and mypy clean.
- Match the surrounding code: comment density, naming, Spanish for user-facing UI text, English for code and logs.
- Keep personal data, credentials and vault content out of commits.
