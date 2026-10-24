"""Configuration models using Pydantic Settings."""

import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Application environment."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class AIProvider(StrEnum):
    """Supported AI model providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE_OPENAI = "azure_openai"
    GOOGLE = "google"
    LOCAL = "local"


class ReasoningEffort(StrEnum):
    """OpenAI Responses API reasoning effort levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TextVerbosity(StrEnum):
    """OpenAI Responses API text output verbosity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DatabaseConfig(BaseModel):
    """Database configuration."""

    driver: str = Field(default="sqlite+aiosqlite", description="Database driver")
    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, description="Database port")
    name: str = Field(default="executive_copilot.db", description="Database name")
    user: str | None = Field(default=None, description="Database user")
    password: SecretStr | None = Field(default=None, description="Database password")
    pool_size: int = Field(default=5, ge=1, description="Connection pool size")
    max_overflow: int = Field(default=10, ge=0, description="Max overflow connections")
    echo: bool = Field(default=False, description="Echo SQL statements")

    @property
    def url(self) -> str:
        """Build database URL."""
        if self.driver.startswith("sqlite"):
            return f"{self.driver}:///{self.name}"
        password = self.password.get_secret_value() if self.password else ""
        return f"{self.driver}://{self.user}:{password}@{self.host}:{self.port}/{self.name}"


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="INFO", description="Log level")
    json_format: bool = Field(default=False, description="Use JSON format")
    add_timestamps: bool = Field(default=True, description="Add timestamps")


class OAuthConfig(BaseModel):
    """OAuth provider configuration."""

    provider_name: str = Field(description="Provider name")
    client_id: SecretStr = Field(description="OAuth client ID")
    client_secret: SecretStr = Field(description="OAuth client secret")
    redirect_uri: str = Field(description="OAuth redirect URI")
    scopes: list[str] = Field(default_factory=list, description="OAuth scopes")
    auth_url: str | None = Field(default=None, description="Authorization URL")
    token_url: str | None = Field(default=None, description="Token URL")


class ProviderConfig(BaseModel):
    """Provider instance configuration."""

    provider_type: str = Field(description="Type of provider (email, calendar, etc.)")
    provider_name: str = Field(description="Provider implementation name")
    enabled: bool = Field(default=True, description="Whether provider is enabled")
    account_id: str | None = Field(default=None, description="Account identifier")
    oauth_config_name: str | None = Field(default=None, description="Reference to OAuth config")
    settings: dict[str, Any] = Field(default_factory=dict, description="Provider-specific settings")


class ProviderAccountMapping(BaseModel):
    """Maps a provider type to a specific account for an organization."""

    provider_type: str = Field(description="Type of provider (email, calendar, task)")
    provider_name: str = Field(description="Provider implementation (outlook, gmail, google)")
    account_id: str = Field(description="Account identifier (email address or workspace)")


class OrganizationConfig(BaseModel):
    """Organization configuration for context matching.

    Each organization can specify which provider accounts to use.
    For example, a primary workspace might use user@primary.com for email
    while a secondary workspace uses user@secondary.com.
    """

    name: str = Field(description="Organization name")
    short_name: str | None = Field(default=None, description="Short name")
    organization_type: str = Field(description="Type (employer, client, etc.)")
    domains: list[str] = Field(default_factory=list, description="Email domains")
    context_sources: list[str] = Field(
        default_factory=list,
        description="Information sources to consult",
    )
    provider_accounts: list[ProviderAccountMapping] = Field(
        default_factory=list,
        description="Provider accounts to use for this organization",
    )
    default_project_id: str | None = Field(default=None, description="Default project ID")
    importance: int = Field(default=5, ge=1, le=10, description="Importance level")
    tags: list[str] = Field(default_factory=list, description="Tags")

    def get_account_for_provider(
        self, provider_type: str, provider_name: str | None = None
    ) -> str | None:
        """Get the account ID for a specific provider type.

        Args:
            provider_type: Type of provider (email, calendar, etc.)
            provider_name: Optional specific provider (outlook, gmail)

        Returns:
            Account ID if found, None otherwise
        """
        for mapping in self.provider_accounts:
            if mapping.provider_type == provider_type:
                if provider_name is None or mapping.provider_name == provider_name:
                    return mapping.account_id
        return None


class ContextRuleConfig(BaseModel):
    """Context engine rule configuration."""

    name: str = Field(description="Rule name")
    description: str | None = Field(default=None, description="Rule description")
    priority: int = Field(default=0, description="Rule priority (higher = first)")
    conditions: dict[str, Any] = Field(description="Conditions to match")
    sources: list[str] = Field(description="Sources to consult when matched")
    enabled: bool = Field(default=True, description="Whether rule is enabled")


class NewsSourceConfig(BaseModel):
    """News source configuration."""

    name: str = Field(description="Source name")
    source_type: str = Field(description="Source type (rss, api, etc.)")
    url: str = Field(description="Source URL")
    enabled: bool = Field(default=True, description="Whether source is enabled")
    category: str | None = Field(default=None, description="News category")
    refresh_minutes: int = Field(default=60, description="Refresh interval")
    filters: dict[str, Any] = Field(default_factory=dict, description="Content filters")


class AutomationConfig(BaseModel):
    """Automation job configuration."""

    job_name: str = Field(description="Job name")
    job_type: str = Field(description="Type of job")
    enabled: bool = Field(default=True, description="Whether job is enabled")
    schedule: str = Field(description="Cron schedule expression")
    settings: dict[str, Any] = Field(default_factory=dict, description="Job-specific settings")


class ModelRoutingConfig(BaseModel):
    """AI model routing configuration."""

    default_model: str = Field(default="gpt-5.4-mini", description="Default model")
    routing_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Rules for model selection",
    )
    fallback_model: str | None = Field(default="gpt-5.4-nano", description="Fallback model")
    max_tokens: int = Field(default=4096, description="Default max tokens")
    temperature: float = Field(default=0.7, ge=0, le=2, description="Default temperature")


class AIConfig(BaseModel):
    """Server-side AI configuration.

    This config is for background intelligence performed by Executive Copilot
    itself. MCP clients still choose their own assistant model.

    Supports multiple providers: OpenAI, Anthropic, Azure OpenAI, Google, and local models.
    """

    provider: AIProvider = Field(default=AIProvider.OPENAI, description="AI provider")
    default_model: str = Field(default="gpt-4o-mini", description="Default model")
    premium_model: str = Field(default="gpt-4o", description="High-capability model")
    fallback_model: str | None = Field(default=None, description="Fallback model")
    embedding_model: str = Field(default="text-embedding-3-small", description="Embedding model")
    max_output_tokens: int = Field(default=4096, ge=1, description="Default output token limit")
    temperature: float = Field(default=0.7, ge=0, le=2, description="Default temperature")
    timeout_seconds: float = Field(default=60.0, gt=0, description="Request timeout")

    # OpenAI Responses API options
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.MEDIUM, description="Reasoning effort for OpenAI Responses API"
    )
    text_verbosity: TextVerbosity = Field(
        default=TextVerbosity.MEDIUM, description="Text output verbosity"
    )
    store: bool = Field(default=False, description="Whether to store responses (OpenAI)")

    # Azure-specific settings
    azure_endpoint: str | None = Field(default=None, description="Azure OpenAI endpoint URL")
    azure_deployment: str | None = Field(default=None, description="Azure deployment name")
    azure_api_version: str = Field(default="2024-02-15-preview", description="Azure API version")

    # Local model settings (Ollama, LM Studio, etc.)
    local_base_url: str | None = Field(default=None, description="Local model API base URL")

    # Credential reference
    credential_provider_type: str = Field(
        default="ai",
        description="Credential store provider type",
    )
    credential_provider_name: str | None = Field(
        default=None,
        description="Credential store provider name (defaults to provider value)",
    )

    @property
    def effective_credential_name(self) -> str:
        """Get the effective credential provider name."""
        return self.credential_provider_name or self.provider.value


class MCPConfig(BaseModel):
    """MCP server configuration."""

    host: str = Field(default="localhost", description="Server host")
    port: int = Field(default=8000, description="Server port")
    transport: str = Field(default="stdio", description="Transport type (stdio, sse)")
    name: str = Field(default="executive-copilot", description="Server name")
    version: str = Field(default="0.1.0", description="Server version")


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


class ObsidianConfig(BaseModel):
    """Obsidian vault integration (meeting notes from Gemini transcripts).

    In Docker the vault is bind-mounted at the same absolute path as on the
    host, and ``OBSIDIAN_VAULT_PATH`` (from the repo's ``.env``) is the single
    place that path is configured. A ``vault_path`` in YAML overrides it.
    """

    enabled: bool = Field(default=True, description="Enable Obsidian meeting-notes sync")
    vault_path: Path | None = Field(
        default_factory=lambda: _env_path("OBSIDIAN_VAULT_PATH"),
        description="Absolute path to the Obsidian vault",
    )
    meetings_folder: str = Field(
        default="Paycode/Reuniones", description="Vault-relative folder for meeting notes"
    )
    people_folder: str | None = Field(
        default="Paycode/Personas", description="Vault-relative folder with one note per person"
    )
    calendar_account: str | None = Field(
        default=None,
        description="Google account whose meetings are synced (default: calendar_defaults.account_id)",
    )
    organization: str | None = Field(
        default=None,
        description=(
            "Organization the meetings belong to (frontmatter tag and property). "
            "Default: the organization whose provider_accounts/domains include calendar_account"
        ),
    )
    note_tags: list[str] = Field(
        default_factory=lambda: ["reunion"],
        description="Tags every meeting note carries, besides the organization's",
    )
    series_map: dict[str, str] = Field(
        default_factory=dict,
        description="Regex on the meeting title -> note name (without .md)",
    )
    skip_titles: list[str] = Field(
        default_factory=list, description="Regexes of meeting titles to ignore"
    )
    update_index: bool = Field(default=True, description="Add new notes to README.md's index")
    transcripts_folder: str | None = Field(
        default="Transcripciones",
        description=(
            "Subfolder of meetings_folder holding one note per meeting transcript, "
            "linked from the dated section (null: do not save transcripts)"
        ),
    )
    index_transcripts: bool = Field(
        default=False,
        description="Also make transcripts searchable by the assistant (long notes; off keeps searches cheap)",
    )
    index_folders: list[str] = Field(
        default_factory=list,
        description="Vault folders searchable by the assistant (default: top folder of meetings_folder)",
    )
    index_exclude: list[str] = Field(
        default_factory=list,
        description="Glob patterns (vault-relative) never indexed, e.g. 'Paycode/SAFE/secrets/*'",
    )
    index_path: str = Field(default="vault_index.db", description="SQLite file holding the search index")
    backup_dir: str = Field(
        default="vault_backups", description="Where a copy of each note is saved before the assistant edits it"
    )


class KnowledgeConfig(BaseModel):
    """Opt-in personal knowledge layer; secrets are read from the environment."""

    enabled: bool = False
    repository: str = ""
    branch: str = "main"
    account: str = "knowledge"
    state_dir: Path = Path("knowledge_state")
    sync_enabled: bool = False
    exclude: list[str] = Field(default_factory=list)
    max_file_bytes: int = Field(default=1_000_000, ge=1, le=10_000_000)


class GitHubDocsConfig(BaseModel):
    """Markdown docs of GitHub repositories, mirrored locally and searchable with the vault.

    The token is stored in Cuentas (kind "github"); a fine-grained token with
    read-only Contents and Metadata on the organization is enough.
    """

    enabled: bool = Field(default=False, description="Mirror and index repository docs")
    orgs: list[str] = Field(default_factory=list, description="Organizations whose repositories are read")
    account: str = Field(default="default", description="Credential account holding the GitHub token")
    include_repos: list[str] = Field(default_factory=list, description="Only these repositories (empty = all)")
    exclude_repos: list[str] = Field(default_factory=list, description="Repositories to skip (e.g. forks, copies)")
    include_archived: bool = Field(default=False, description="Also read archived repositories")
    exclude_paths: list[str] = Field(
        default_factory=list, description="Extra glob patterns (repo-relative) never mirrored"
    )
    max_file_kb: int = Field(default=300, description="Skip larger markdown files")
    cache_dir: str = Field(default="github_docs", description="Local mirror of the docs (gitignored)")
    summarize: bool = Field(default=True, description="Write an AI summary of what each repository does")
    aliases: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Other names a repository goes by in meetings/projects, e.g. tokenize_and_pay: [Tokeniza y Cobra]",
    )


class SecurityWatchItem(BaseModel):
    """One product of the stack to watch (runtime, server, database, framework)."""

    product: str = Field(description="endoflife.date slug, e.g. php, nodejs, postgresql, mariadb, apache")
    name: str | None = Field(default=None, description="Display name (default: from the known products table)")
    cpe: str | None = Field(default=None, description="CPE 2.3 prefix for NVD, e.g. cpe:2.3:a:php:php")
    cycles: int | None = Field(default=None, ge=1, le=6, description="Release lines to watch (default: security.cycles)")
    lts_only: bool | None = Field(default=None, description="Only LTS release lines (default: per product)")
    where: list[str] = Field(default_factory=list, description="Where it runs (free text, shown in the UI)")


class SecurityIgnore(BaseModel):
    """An advisory accepted or not applicable."""

    id: str = Field(description="CVE/GHSA id")
    reason: str | None = Field(default=None)
    until: str | None = Field(default=None, description="Stop ignoring after this date (YYYY-MM-DD)")


class SecurityConfig(BaseModel):
    """Daily review of security advisories for the stack (repos + watched products).

    Sources are public and keyless: OSV.dev for npm/Packagist packages found in the
    repositories' lock files, NVD for watched products, endoflife.date for release
    lines reaching end of life. Everything the model sees is already structured
    data; the model never decides severities.
    """

    enabled: bool = Field(default=False, description="Run the daily scan and show the Seguridad view")
    cycles: int = Field(default=2, ge=1, le=6, description="Release lines watched per product (newest first)")
    lookback_days: int = Field(default=120, ge=7, le=120, description="How far back NVD advisories are read")
    eol_warning_days: int = Field(default=180, ge=0, description="Warn this many days before a release line ends")
    scan_repos: bool = Field(default=True, description="Read lock files of the GitHub repositories (github_docs)")
    max_lockfile_kb: int = Field(default=3000, description="Skip larger lock files")
    use_ai: bool = Field(default=True, description="One short AI note prioritizing the new findings of the day")
    state_file: str = Field(default="security_state.json", description="Cached inventory and last AI note")
    watchlist: list[SecurityWatchItem] = Field(default_factory=list)
    ignore: list[SecurityIgnore] = Field(default_factory=list)


class CalendarDefaultsConfig(BaseModel):
    """Where new events go when the caller does not name a calendar."""

    provider: str = Field(default="google", description="Calendar provider (google, outlook)")
    account_id: str | None = Field(
        default=None,
        description="Default calendar account (e.g. the work Google account)",
    )
    calendar_id: str = Field(default="primary", description="Calendar ID within the account")
    timezone: str = Field(default="America/Mexico_City", description="IANA timezone for new events")
    default_duration_minutes: int = Field(default=60, ge=5, description="Duration when no end given")
    personal_events_private: bool = Field(
        default=True,
        description="Mark events created from personal e-mail as private on the work calendar",
    )


class EmailCalendarConfig(BaseModel):
    """Detection of e-mails that should become calendar entries."""

    source_accounts: list[str] = Field(
        default_factory=list,
        description="E-mail accounts to review (default: every Outlook account)",
    )
    days_back: int = Field(default=7, ge=1, le=60, description="Default look-back window")
    max_emails: int = Field(default=60, ge=1, le=200, description="Max e-mails triaged per account")
    target_provider: str | None = Field(
        default=None, description="Calendar provider for events found in e-mail (default: calendar_defaults)"
    )
    target_account_id: str | None = Field(default=None, description="Calendar account for those events")
    target_calendar_id: str | None = Field(default=None, description="Calendar ID for those events")
    min_confidence: float = Field(
        default=0.7, ge=0, le=1, description="Hide proposals below this extraction confidence"
    )
    ignore_senders: list[str] = Field(
        default_factory=list,
        description="Sender addresses/domains never considered (marketing, etc.)",
    )


class AppConfig(BaseSettings):
    """Root application configuration.

    Combines all configuration sections and supports loading
    from environment variables and config files.
    """

    model_config = SettingsConfigDict(
        env_prefix="COPILOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Environment
    environment: Environment = Field(default=Environment.DEVELOPMENT)
    debug: bool = Field(default=False)
    config_dir: Path = Field(default=Path("config"))

    # User configuration
    user_email: str | None = Field(default=None, description="User's email address")

    # Core configuration
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    model_routing: ModelRoutingConfig = Field(default_factory=ModelRoutingConfig)

    # OAuth configurations (keyed by name)
    oauth: dict[str, OAuthConfig] = Field(default_factory=dict)

    # Provider configurations
    providers: list[ProviderConfig] = Field(default_factory=list)

    # Organization configurations
    organizations: list[OrganizationConfig] = Field(default_factory=list)

    # Context rules
    context_rules: list[ContextRuleConfig] = Field(default_factory=list)

    # News sources
    news_sources: list[NewsSourceConfig] = Field(default_factory=list)

    # Automation jobs
    automation_jobs: list[AutomationConfig] = Field(default_factory=list)

    # Workspace integrations
    obsidian: ObsidianConfig = Field(default_factory=ObsidianConfig)
    github_docs: GitHubDocsConfig = Field(default_factory=GitHubDocsConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    calendar_defaults: CalendarDefaultsConfig = Field(default_factory=CalendarDefaultsConfig)
    email_calendar: EmailCalendarConfig = Field(default_factory=EmailCalendarConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_model_routing(cls, data: Any) -> Any:
        """Use legacy model_routing values when ai is not configured."""
        if not isinstance(data, dict) or "ai" in data:
            return data

        model_routing_value = data.get("model_routing")
        if isinstance(model_routing_value, BaseModel):
            model_routing = model_routing_value.model_dump()
        elif isinstance(model_routing_value, dict):
            model_routing = model_routing_value
        else:
            return data

        ai: dict[str, Any] = {}
        if "default_model" in model_routing:
            ai["default_model"] = model_routing["default_model"]
        if "fallback_model" in model_routing:
            ai["fallback_model"] = model_routing["fallback_model"]
        if "max_tokens" in model_routing:
            ai["max_output_tokens"] = model_routing["max_tokens"]
        if "temperature" in model_routing:
            ai["temperature"] = model_routing["temperature"]

        if ai:
            data = data.copy()
            data["ai"] = ai
        return data

    def get_provider_config(self, provider_type: str, provider_name: str) -> ProviderConfig | None:
        """Get configuration for a specific provider."""
        for provider in self.providers:
            if provider.provider_type == provider_type and provider.provider_name == provider_name:
                return provider
        return None

    def get_organization_by_domain(self, domain: str) -> OrganizationConfig | None:
        """Find organization by email domain."""
        domain_lower = domain.lower()
        for org in self.organizations:
            if any(d.lower() == domain_lower for d in org.domains):
                return org
        return None

    def get_enabled_providers(self, provider_type: str | None = None) -> list[ProviderConfig]:
        """Get all enabled providers, optionally filtered by type."""
        return [
            p
            for p in self.providers
            if p.enabled and (provider_type is None or p.provider_type == provider_type)
        ]
