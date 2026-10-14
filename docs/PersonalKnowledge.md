# Personal knowledge management

The workspace's **Conocimiento** view and the `knowledge_*` MCP tools share a
review-first service. GitHub stores published Markdown; MySQL indexes documents
and relationships and retains operational proposals/conflicts. Existing vault
and executive tools continue to work. The feature ships disabled.

## Enable locally

1. Keep `OBSIDIAN_VAULT_PATH` pointing to the authorized working vault. Back up
   the vault before an initial import. The knowledge index covers allowed
   Markdown throughout that vault, not only the old meeting-search folders.
2. Copy the `knowledge` section from `config/knowledge.example.yaml` into
   `config/config.local.yaml`, set the private repository and intended branch.
   Leave `sync_enabled: false` during setup.
3. Use an existing MySQL instance or start the optional Compose profile. Set
   independent random `KNOWLEDGE_MYSQL_PASSWORD` and
   `KNOWLEDGE_MYSQL_ROOT_PASSWORD` in `.env` before starting it. No database port
   is published by the profile.
4. Set the following secrets in `.env` (never in YAML or Git):

   ```dotenv
   COPILOT_WORKSPACE_ACCESS_TOKEN=<at-least-32-random-characters>
   COPILOT_KNOWLEDGE_DATABASE_URL=mysql+asyncmy://copilot_knowledge:<url-encoded-password>@knowledge-mysql:3306/copilot_knowledge?charset=utf8mb4
   COPILOT_KNOWLEDGE_GITHUB_TOKEN=<fine-grained-token-for-one-private-repository>
   ```

   The MySQL account needs DDL for initial table/index creation and DML on its
   knowledge database. A GitHub token needs Metadata read and Contents read/write
   on exactly the notes repository. Alternatively, save it in Cuentas as a
   GitHub credential using the dedicated account configured in `knowledge.account`.
   The separate documentation-mirror account can remain read-only.
5. Start/rebuild, because asyncmy is a new dependency:

   ```sh
   docker compose --profile knowledge up -d --build
   ```

   For an external MySQL instance, use `docker compose up -d --build` instead.
   The DSN must be reachable from inside the container; `localhost` points to
   that container, not the Mac host.
6. Set `knowledge.enabled: true`, recreate the container after changing `.env`,
   and log in at `/login`. The access key protects the existing workspace too.
   Existing HTTP API consumers must send `Authorization: Bearer <key>`.
7. Open **Conocimiento**. Review local notes and exclusions. Every proposed
   write shows the local diff and the diff that will be published. Confirm
   notes individually; unreviewed vault changes are never automatically uploaded.
8. Set `knowledge.sync_enabled: true`. Use **Sincronizar** for the first run.
   Then enable the existing `knowledge_sync` job in `config/automation.yaml`
   (`*/5 * * * *`) and restart the service to reload the schedule. There is no
   new scheduler. Do not run the CLI automation command as a second sync worker.

A public or archived repository blocks synchronization and publication. The
first confirmed note initializes an empty private repository. Files are never
staged with a blanket Git add. Hidden paths, symbolic links, non-Markdown files,
credential directories, configured exclusions, and known credential patterns
are rejected. Exclusion rules should be reviewed for the actual vault: secret
pattern detection cannot recognize every secret format.

Exclusions are `fnmatch` patterns compared case-insensitively and after Unicode
normalization (macOS paths), so `Privado/*` also covers `privado/x.md`; a leading
`**/` also matches at the vault root. Notes that look like they contain
credentials are never indexed, returned or synchronized, local or remote. Notes
that cannot be used (credential-like, invalid YAML, oversized, not UTF-8,
duplicate `id`, or a path that differs from another only by case) are listed as
**No disponible** in the sync monitor; every other note keeps working.

Synchronization stops instead of guessing when the configured repository or
branch changed since the last sync, when the remote branch is empty after notes
were synchronized, or when the vault directory is empty (usually a missing bind
mount). Unchanged remote files are not downloaded again: the blob sha recorded at
the last sync is compared first.

## Document format

Existing unknown YAML keys, comments, body content, BOM and newline style are
preserved. New notes missing an ID get default frontmatter. Prefer stable IDs
for notes that will be renamed. Examples:

```markdown
---
id: reflection-responsibility-2026-10-13
title: Responsabilidad personal
type: [reflection, hypothesis]
created: 2026-10-13
updated: 2026-10-13
tags: [filosofia]
source: conversation
confidence: 0.7
status: active
related: ["[[Autodominio]]"]
relations:
  - target: "[[Reflexión anterior]]"
    type: evolves_from
    origin: user
---
# Responsabilidad personal
Contenido escrito o confirmado por el usuario.
```

Supported categories: `fact`, `reflection`, `inference`, `hypothesis`, `decision`,
`event`, `concept`. They can be combined. Other `type` values already used in the
vault are kept as plain metadata and do not count as categories. Relation types: `related_to`, `supports`,
`contradicts`, `evolves_from`, `references`, `influenced_by`, `part_of`.
An AI suggestion uses `origin: ai`; it remains inferred unless a reviewed note
explicitly sets `confirmed: true`. Create a new dated reflection for changed
opinions and link the previous one; retain the original date and source.

## MCP and HTTP

All new MCP tools take `access_token`. Tool payloads and tokens are omitted from
MCP activity records. Clients must store the key securely. MCP HTTP also requires the Bearer key for every tool when knowledge or a
workspace key is enabled. It remains localhost-only; public deployments still
need TLS and an appropriate identity/access deployment. Stdio contracts remain
unchanged.

- `knowledge_search`: textual search with folder, category and tag filters.
- `knowledge_get`: raw Markdown, metadata, revision hash and relations; optional
  complete commit SHA reads a historical version.
- `knowledge_create`, `knowledge_update`, `knowledge_delete`: proposals only.
  Updates/deletions require the hash from the most recent read.
- `knowledge_confirm`: apply an exact proposal after explicit user approval.
- `knowledge_related`, `knowledge_graph`: directed links, backlinks, tags and
  unresolved targets. Graphs default to 100 nodes and cap at 300.
- `knowledge_history`: latest thirty commits affecting the path.
- `knowledge_sync`, `knowledge_sync_status`: real pending/conflict/commit state.
- `knowledge_conflict`, `knowledge_resolve`: inspect preserved versions and
  propose a resolution. Confirmation checks both local and remote revisions.

HTTP lives under `/api/ws/knowledge`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/dashboard`, `/search`, `/document`, `/related`, `/graph`, `/history` | Read/index/explore |
| POST | `/proposals` | Propose `{operation,path,content,expected_hash}` |
| GET | `/proposals/{id}` | Review the stored diffs |
| POST | `/proposals/{id}/confirm` | Confirm the exact proposal |
| GET / POST | `/sync` | Status / manual synchronization |
| POST | `/reindex` | Force-rebuild derived tables from the working vault |
| GET | `/conflicts/{id}` | Preserved base/local/remote |
| POST | `/conflicts/{id}/resolve` | Propose merged content or deletion |

Authentication uses an HttpOnly SameSite=Lax cookie or Bearer header. Origin
checks reject cross-origin browser writes. API errors use 401 for authorization,
409 for invalid/stale operations, and 503 for unavailable infrastructure.

## Recovery and reconstruction

- A failed sync preserves proposals and reports an error. Retry **Sincronizar**;
  remote equality reconciles a committed change whose response was lost.
- A conflict suspends the affected file. Open it in the monitor, compare all
  versions, propose a resolution and confirm it. Synchronize again to publish.
  Confirmation fails if the remote changed after the resolution was proposed; a
  new divergence on the same file opens a new conflict and supersedes earlier
  approvals for it.
- A confirmation interrupted after approval (crash, failed write) is completed by
  confirming the same proposal again.
- Rebuild from GitHub with an empty authorized working vault and a fresh MySQL
  knowledge database, then synchronize: allowed remote Markdown is downloaded,
  parsed and indexed. The repository must be private and credentials configured.
  With an existing working vault, preserve it and unresolved operational state;
  sync compares versions rather than replacing local edits.
- `/reindex` resets derived tables only. Back up `knowledge_state` in MySQL before
  database maintenance; it includes unpublished approved content and conflicts.
  The filesystem `knowledge_state/` directory contains the shared lock, not a
  substitute backup of that operational table.
- Disable the job and `sync_enabled` to stop publication. Disable `knowledge.enabled`
  to stop new knowledge operations. Keep the workspace key while private UI
  access is needed. Existing databases and published Git history are retained.

## Validation

```sh
uv run pytest tests/unit -q
uv run ruff check src tests
uv run mypy src/executive_copilot/services/knowledge src/executive_copilot/mcp/knowledge.py
```

The opt-in integration test requires an isolated disposable MySQL database:
set `KNOWLEDGE_TEST_MYSQL_URL` and run
`uv run pytest tests/integration/test_knowledge_mysql.py -q`. It replaces only
knowledge index contents in that test database; never target a live database.

See [ADR-0001](adr/0001-personal-knowledge.md) for decisions and deployment limits.
