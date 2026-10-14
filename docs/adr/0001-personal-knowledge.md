# ADR-0001: Review-first personal knowledge over Obsidian and private GitHub

Status: Accepted design; opt-in implementation
Date: 2026-10-13

## Context

Executive Copilot already has a local Obsidian vault, SQLite FTS5 search,
FastMCP tools, a FastAPI/Alpine workspace, GitHub read access and APScheduler.
The existing application database is SQLite, despite the initial requirement
assuming MySQL. Replacing the application database would widen the migration
and risk unrelated executive workflows.

## Decision

Keep the existing databases and tool contracts. Add an isolated async MySQL
knowledge repository, using SQLAlchemy Core and asyncmy. SQLite is supported
only by offline tests. Add no vector database, second scheduler or frontend
framework. The existing embedding path is not invoked by knowledge services.

Markdown in private GitHub is the source of published knowledge. The local
vault is a working copy. MySQL document and edge tables are rebuildable
projections; the separate operational table preserves proposals, approvals,
conflict versions, baselines and audit hashes. Rebuilding derived tables must
never clear operational state. Back up operational state: unpublished approved
work cannot be reconstructed from GitHub.

A write first creates a durable proposal, including local and publication
diffs. Confirmation checks the exact base revision, persists approval, and
atomically replaces the local file. Only approved content matching the current
working copy may be published. Changes from Obsidian and legacy writers remain
unreviewed until explicitly approved. Deletion proposals retain original text.

GitHub reads use a pinned commit tree. Writes build blobs/tree/commit and update
the branch with `force=false`, with the observed head as parent. Divergence
fails publication rather than overwriting. Empty repositories require one
reviewed note through the Contents API before grouped Git Data commits can be
created. A lost response is reconciled by comparing remote content on retry.

Three-way comparison of baseline, local and remote detects conflicts. Both
versions remain durable; the affected path is suspended. Resolving a conflict
creates another proposal and confirmation checks that the remote version has
not changed. Other paths can continue to synchronize.

Wikilinks and metadata declare directed edges. Tags derive associations at
query time. AI relations remain inferred unless explicitly marked confirmed
in a reviewed document. Existing executive graph heuristics are not imported
as asserted knowledge. Reflection evolution is expressed through `evolves_from`
and new dated notes; there is no automatic psychological interpretation.

## Alternatives

| Option | Benefit | Cost / reason not selected |
|---|---|---|
| Extend only SQLite FTS5 | Fewest dependencies | Does not satisfy the approved MySQL requirement |
| Migrate the entire MCP to MySQL | Single database engine | Unnecessary migration risk for existing workflows |
| Isolated MySQL knowledge index | Meets requirement and preserves old contracts | One optional database to operate |
| Git CLI checkout with automatic merges | Familiar Git workflow | Credential subprocess handling and working-tree races |
| Existing REST client plus optimistic Git Data writes | No Git binary or new Git library | Explicit reconciliation and empty-repository bootstrap |

## Consequences

Workspace authentication becomes mandatory when knowledge is enabled. An
optional workspace key also protects legacy UI/API endpoints. Provider OAuth
is not considered user authentication. New MCP knowledge tools require an
access key, whose values and document arguments are omitted from activity logs.
Existing stdio tool contracts are preserved. The HTTP entry point authenticates
all tools with the workspace Bearer key when knowledge or that key is enabled.
TLS and public identity/access deployment remain separate concerns.

All participating writers share a nonblocking process lock in `state_dir`.
A single-host bind-mounted deployment is supported. Obsidian is an external
writer and does not honor that lock: avoid editing the same note during sync.
Atomic rename prevents partial files; comparison detects observed divergence,
but arbitrary external editor races cannot be made transactional with MySQL.
Multi-host deployments require a distributed lease before enabling multiple
sync workers.

Index updates hash content, update changed/renamed documents and remove stale
edges in one transaction. New schemas are additive. FULLTEXT failures are
reported; substring/title/tag matching supplements MySQL token length and
stopword behavior and is labeled textual. Large collection search and graph
pagination may need further optimization; graph responses are bounded.
