"""Configuration management for Executive Copilot.

All configuration is loaded from YAML files and environment variables.
No business rules should be hardcoded.
"""

from executive_copilot.config.loader import ConfigLoader, load_config
from executive_copilot.config.models import (
    AIConfig,
    AIProvider,
    AppConfig,
    AutomationConfig,
    CalendarDefaultsConfig,
    ContextRuleConfig,
    DatabaseConfig,
    EmailCalendarConfig,
    LoggingConfig,
    MCPConfig,
    ModelRoutingConfig,
    NewsSourceConfig,
    OAuthConfig,
    ObsidianConfig,
    OrganizationConfig,
    ProviderAccountMapping,
    ProviderConfig,
)

__all__ = [
    "AIConfig",
    "AIProvider",
    "AppConfig",
    "AutomationConfig",
    "CalendarDefaultsConfig",
    "ConfigLoader",
    "ContextRuleConfig",
    "DatabaseConfig",
    "EmailCalendarConfig",
    "LoggingConfig",
    "MCPConfig",
    "ModelRoutingConfig",
    "NewsSourceConfig",
    "OAuthConfig",
    "ObsidianConfig",
    "OrganizationConfig",
    "ProviderAccountMapping",
    "ProviderConfig",
    "load_config",
]
