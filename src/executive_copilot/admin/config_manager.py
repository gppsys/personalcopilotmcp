"""Configuration file manager for the admin panel."""

from pathlib import Path
from typing import Any

import yaml

from executive_copilot.logging import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """Manages reading and writing configuration files."""

    def __init__(self, config_dir: Path) -> None:
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> dict[str, Any]:
        """Load main configuration."""
        config_file = self.config_dir / "config.yaml"
        local_file = self.config_dir / "config.local.yaml"

        config: dict[str, Any] = {}

        if config_file.exists():
            config = self._load_yaml(config_file)

        if local_file.exists():
            local_config = self._load_yaml(local_file)
            config = self._deep_merge(config, local_config)

        return config

    def update_config(self, updates: dict[str, Any]) -> None:
        """Update configuration (writes to config.local.yaml)."""
        local_file = self.config_dir / "config.local.yaml"

        existing: dict[str, Any] = {}
        if local_file.exists():
            existing = self._load_yaml(local_file)

        merged = self._deep_merge(existing, updates)
        self._save_yaml(local_file, merged)
        logger.info("Updated configuration", file=str(local_file))

    def load_organizations(self) -> list[dict[str, Any]]:
        """Load organizations configuration."""
        config_file = self.config_dir / "organizations.yaml"
        if not config_file.exists():
            return []
        data = self._load_yaml(config_file)
        result: list[dict[str, Any]] = data.get("organizations", [])
        return result

    def save_organizations(self, organizations: list[dict[str, Any]]) -> None:
        """Save organizations configuration."""
        config_file = self.config_dir / "organizations.yaml"
        self._save_yaml(config_file, {"organizations": organizations})
        logger.info("Saved organizations", count=len(organizations))

    def load_context_rules(self) -> list[dict[str, Any]]:
        """Load context rules configuration."""
        config_file = self.config_dir / "context_rules.yaml"
        if not config_file.exists():
            return []
        data = self._load_yaml(config_file)
        result: list[dict[str, Any]] = data.get("context_rules", [])
        return result

    def save_context_rules(self, rules: list[dict[str, Any]]) -> None:
        """Save context rules configuration."""
        config_file = self.config_dir / "context_rules.yaml"
        self._save_yaml(config_file, {"context_rules": rules})
        logger.info("Saved context rules", count=len(rules))

    def load_automation_jobs(self) -> list[dict[str, Any]]:
        """Load automation jobs configuration."""
        config_file = self.config_dir / "automation.yaml"
        if not config_file.exists():
            return []
        data = self._load_yaml(config_file)
        result: list[dict[str, Any]] = data.get("automation_jobs", [])
        return result

    def save_automation_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Save automation jobs configuration."""
        config_file = self.config_dir / "automation.yaml"
        self._save_yaml(config_file, {"automation_jobs": jobs})
        logger.info("Saved automation jobs", count=len(jobs))

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        """Load a YAML file."""
        try:
            with open(path) as f:
                content = yaml.safe_load(f)
                return content if isinstance(content, dict) else {}
        except Exception as e:
            logger.error("Failed to load YAML", path=str(path), error=str(e))
            return {}

    def _save_yaml(self, path: Path, data: dict[str, Any]) -> None:
        """Save data to a YAML file."""
        try:
            with open(path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception as e:
            logger.error("Failed to save YAML", path=str(path), error=str(e))
            raise

    def _deep_merge(
        self,
        base: dict[str, Any],
        override: dict[str, Any],
    ) -> dict[str, Any]:
        """Deep merge two dictionaries."""
        result = base.copy()
        for key, value in override.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
