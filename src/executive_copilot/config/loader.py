"""Configuration loading from YAML files."""

from pathlib import Path
from typing import Any

import yaml

from executive_copilot.config.models import AppConfig
from executive_copilot.exceptions import ConfigurationError
from executive_copilot.logging import get_logger

logger = get_logger(__name__)

UI_SETTINGS_FILE = "ui_settings.yaml"
SECURITY_FILE = "security.yaml"


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
            # *.example.yaml files are templates to copy, never live config:
            # merging them leaks placeholder accounts into the running app.
            if config_file.name.endswith(".example.yaml"):
                continue
            # config.local.yaml and ui_settings.yaml are merged last, below.
            if config_file.name in ("config.local.yaml", UI_SETTINGS_FILE):
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

        # Settings saved from the web workspace win over hand-edited files.
        ui_file = self.config_dir / UI_SETTINGS_FILE
        if ui_file.exists():
            merged_config = self._deep_merge(merged_config, self._load_yaml_file(ui_file))
            logger.debug("Merged UI settings", file=str(ui_file))

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


def load_ui_settings(config_dir: Path | str) -> dict[str, Any]:
    """Settings saved from the web workspace (config/ui_settings.yaml)."""
    path = Path(config_dir) / UI_SETTINGS_FILE
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def save_ui_settings(config_dir: Path | str, patch: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``patch`` into config/ui_settings.yaml and return the result.

    A ``None`` value removes the key, so the hand-edited files apply again.
    """

    def merge(base: dict[str, Any], upd: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, value in upd.items():
            if value is None:
                out.pop(key, None)
            elif isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge(out[key], value)
            else:
                out[key] = value
        return out

    data = merge(load_ui_settings(config_dir), patch)
    path = Path(config_dir) / UI_SETTINGS_FILE
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        f.write("# Saved from the web workspace. Overrides the other config files.\n")
        yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
    tmp.replace(path)
    return data


def load_security_settings(config_dir: Path | str) -> dict[str, Any]:
    """The ``security:`` section edited from the Seguridad view (config/security.yaml)."""
    path = Path(config_dir) / SECURITY_FILE
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f)
    section = data.get("security") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def save_security_settings(config_dir: Path | str, section: dict[str, Any]) -> dict[str, Any]:
    """Replace the ``security:`` section of config/security.yaml (merged like any config file)."""
    path = Path(config_dir) / SECURITY_FILE
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        f.write("# Security watchlist. Edited from the Seguridad view of the web workspace.\n")
        yaml.safe_dump({"security": section}, f, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
    return section


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
    config = ConfigLoader(config_dir).load()
    # Every writer must agree on one lock file, whatever its working directory.
    if not config.knowledge.state_dir.is_absolute():
        root = Path(config_dir).resolve().parent
        config.knowledge.state_dir = root / config.knowledge.state_dir
    return config
