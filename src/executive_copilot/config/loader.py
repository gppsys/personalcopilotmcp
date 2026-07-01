"""Configuration loading from YAML files."""

from pathlib import Path
from typing import Any

import yaml

from executive_copilot.config.models import AppConfig
from executive_copilot.exceptions import ConfigurationError
from executive_copilot.logging import get_logger

logger = get_logger(__name__)


class ConfigLoader:
    """Load and merge configuration from multiple YAML files."""

    def __init__(self, config_dir: Path | str) -> None:
        self.config_dir = Path(config_dir)

    def load(self) -> AppConfig:
        """Load configuration from all YAML files in config directory.

        Files are loaded in alphabetical order and merged.
        Later files override earlier ones for the same keys.
        """
        if not self.config_dir.exists():
            logger.warning(
                "Config directory does not exist, using defaults",
                config_dir=str(self.config_dir),
            )
            return AppConfig()

        merged_config: dict[str, Any] = {}

        # Load base config first
        base_file = self.config_dir / "config.yaml"
        if base_file.exists():
            merged_config = self._load_yaml_file(base_file)
            logger.debug("Loaded base config", file=str(base_file))

        # Load additional config files in order
        for config_file in sorted(self.config_dir.glob("*.yaml")):
            if config_file.name == "config.yaml":
                continue
            file_config = self._load_yaml_file(config_file)
            merged_config = self._deep_merge(merged_config, file_config)
            logger.debug("Merged config file", file=str(config_file))

        # Load environment-specific overrides
        env = merged_config.get("environment", "development")
        env_file = self.config_dir / f"config.{env}.yaml"
        if env_file.exists():
            env_config = self._load_yaml_file(env_file)
            merged_config = self._deep_merge(merged_config, env_config)
            logger.debug("Merged environment config", file=str(env_file), environment=env)

        # Load local overrides (not committed to git)
        local_file = self.config_dir / "config.local.yaml"
        if local_file.exists():
            local_config = self._load_yaml_file(local_file)
            merged_config = self._deep_merge(merged_config, local_config)
            logger.debug("Merged local config", file=str(local_file))

        merged_config["config_dir"] = self.config_dir

        try:
            return AppConfig(**merged_config)
        except Exception as e:
            raise ConfigurationError(
                f"Failed to parse configuration: {e}",
                cause=e,
            ) from e

    def _load_yaml_file(self, path: Path) -> dict[str, Any]:
        """Load a single YAML file."""
        try:
            with path.open() as f:
                content = yaml.safe_load(f)
                return content if isinstance(content, dict) else {}
        except yaml.YAMLError as e:
            raise ConfigurationError(
                f"Failed to parse YAML file: {path}",
                config_key=str(path),
                cause=e,
            ) from e
        except OSError as e:
            raise ConfigurationError(
                f"Failed to read config file: {path}",
                config_key=str(path),
                cause=e,
            ) from e

    def _deep_merge(
        self,
        base: dict[str, Any],
        override: dict[str, Any],
    ) -> dict[str, Any]:
        """Deep merge two dictionaries.

        Override values replace base values. For lists, override
        replaces entirely (no merge). For dicts, merge recursively.
        """
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


def load_config(config_dir: Path | str | None = None) -> AppConfig:
    """Load application configuration.

    Args:
        config_dir: Path to configuration directory.
                   Defaults to 'config' in current directory.

    Returns:
        Loaded and validated AppConfig instance.
    """
    if config_dir is None:
        config_dir = Path("config")
    loader = ConfigLoader(config_dir)
    return loader.load()
