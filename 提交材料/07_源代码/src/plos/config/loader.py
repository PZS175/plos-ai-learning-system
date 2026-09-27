"""TOML configuration loader/saver with deep merge."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w
except Exception:  # pragma: no cover
    tomli_w = None  # type: ignore

from ..utils.exceptions import ConfigError
from ..utils.logger import get_logger
from ..utils.paths import get_config_path
from .defaults import DEFAULT_CONFIG
from .schema import AppConfig

logger = get_logger("config.loader")


class Config:
    """Lightweight wrapper around the validated application configuration."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data
        self._validated = AppConfig(**data)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data

    @property
    def validated(self) -> AppConfig:
        return self._validated

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` into ``base``.

    返回全新的深拷贝字典，避免合并结果中的嵌套 dict 与 DEFAULT_CONFIG
    共享引用、运行期修改污染进程内默认配置。
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from TOML, merging with defaults.

    Args:
        config_path: Optional explicit path to the config file. If omitted,
            ``get_config_path()`` is used.

    Returns:
        A dictionary containing the merged configuration.
    """
    path = config_path or get_config_path()
    # 深拷贝默认配置，防止运行期修改回写到模块级 DEFAULT_CONFIG
    config = copy.deepcopy(DEFAULT_CONFIG)

    if path.exists():
        try:
            with open(path, "rb") as f:
                user_config = tomllib.load(f)
            config = _deep_merge(config, user_config)
            logger.info("Loaded configuration from %s", path)
        except Exception as e:
            logger.error("Failed to load config from %s: %s", path, e)
            raise ConfigError(f"Failed to load config from {path}: {e}") from e
    else:
        logger.info("No user config found at %s, using defaults", path)

    try:
        AppConfig(**config)
    except Exception as e:
        logger.warning("Configuration validation failed: %s", e)

    return config


def save_config(config: Dict[str, Any], config_path: Optional[Path] = None) -> None:
    """Save configuration dictionary to TOML.

    Args:
        config: Configuration dictionary to persist.
        config_path: Optional explicit path. Defaults to ``get_config_path()``.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if tomli_w is not None:
            with open(path, "wb") as f:
                tomli_w.dump(config, f)
        else:
            text = _dump_toml(config)
            path.write_text(text, encoding="utf-8")
        logger.info("Saved configuration to %s", path)
    except Exception as e:
        logger.error("Failed to save config to %s: %s", path, e)
        raise ConfigError(f"Failed to save config to {path}: {e}") from e


def _format_toml_value(value: Any) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, list):
        items = ", ".join(_format_toml_value(v) for v in value)
        return f"[{items}]"
    return f'"{value}"'


def _dump_toml(data: Dict[str, Any]) -> str:
    """Simple TOML writer supporting nested dicts only (sufficient for config)."""
    lines: List[str] = []

    def _write_table(table: Dict[str, Any], table_name: str = "") -> None:
        # First pass: scalar values at this level
        scalars: Dict[str, Any] = {}
        nested: Dict[str, Dict[str, Any]] = {}
        for key, value in table.items():
            if isinstance(value, dict):
                nested[key] = value
            else:
                scalars[key] = value

        if table_name and (scalars or not nested):
            lines.append(f"[{table_name}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_format_toml_value(value)}")
        if scalars:
            lines.append("")

        for key, value in nested.items():
            full_name = f"{table_name}.{key}" if table_name else key
            _write_table(value, full_name)

    _write_table(data)
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["Config", "load_config", "save_config"]
