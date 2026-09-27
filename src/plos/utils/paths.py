"""Path helpers for PLOS AI.

默认所有数据目录位于项目根目录下（data/、config/、logs/），
便于离线使用、备份与沙箱运行，同时符合项目目录约定。
可通过配置覆盖路径。

如果程序目录本身不可写（例如装到了权限受限的目录），会自动退回
当前用户的本地应用数据目录，保证程序仍能正常启动。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..core.constants import (
    APP_NAME,
    DEFAULT_ATTACHMENTS_DIR_NAME,
    DEFAULT_CHROMA_DIR_NAME,
    DEFAULT_CONFIG_DIR_NAME,
    DEFAULT_CONFIG_FILE_NAME,
    DEFAULT_DATA_DIR_NAME,
    DEFAULT_DB_FILE_NAME,
    DEFAULT_IMAGES_DIR_NAME,
    DEFAULT_LOGS_DIR_NAME,
)


def _project_root() -> Path:
    """返回项目根目录：包含 src/、docs/、data/ 的目录。"""
    # src/plos/utils/paths.py -> parents[3] = 项目根目录
    return Path(__file__).resolve().parents[3]


def _is_writable(directory: Path) -> bool:
    """实际写一个探针文件来判断目录可写，比只看权限位更可靠。"""
    probe = directory / ".plos_write_probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _fallback_root() -> Path:
    """程序目录不可写时的兜底位置：当前用户的本地应用数据目录。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_NAME


# 缓存解析结果；key 记录来源目录，便于测试替换 _project_root 后重新解析
_ROOT_CACHE: dict = {}


def _resolve_root() -> Path:
    """数据/配置/日志的根目录。

    优先用程序自身所在目录（便携模式：整个文件夹拷走就带走全部数据）；
    若该目录不可写（装在 Program Files、或安装目录权限受限），
    自动退回用户本地应用数据目录，避免程序因为写不了库而无法启动。
    """
    root = _project_root()
    if _ROOT_CACHE.get("source") == root and _ROOT_CACHE.get("root") is not None:
        return _ROOT_CACHE["root"]

    resolved = root
    if not _is_writable(root):
        resolved = _fallback_root()
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        # 延迟导入：logger 依赖本模块，模块级导入会形成循环
        try:
            from .logger import get_logger

            get_logger("utils.paths").warning(
                "程序目录不可写，数据改存到：%s", resolved
            )
        except Exception:
            pass

    _ROOT_CACHE["source"] = root
    _ROOT_CACHE["root"] = resolved
    return resolved


def _resolve_base_dir(override: Optional[str] = None) -> Path:
    """Return the base data directory for the application."""
    if override:
        return Path(override).expanduser().resolve()

    return _resolve_root() / DEFAULT_DATA_DIR_NAME


def get_data_dir(override: Optional[str] = None) -> Path:
    """Return the application data directory."""
    return _resolve_base_dir(override)


def get_config_path(override: Optional[str] = None) -> Path:
    """Return the path to the user configuration file."""
    if override:
        return Path(override).expanduser().resolve()
    return _resolve_root() / DEFAULT_CONFIG_DIR_NAME / DEFAULT_CONFIG_FILE_NAME


def get_db_path(override: Optional[str] = None) -> Path:
    """Return the path to the SQLite database."""
    if override:
        return Path(override).expanduser().resolve()
    return get_data_dir() / DEFAULT_DB_FILE_NAME


def get_chroma_dir(override: Optional[str] = None) -> Path:
    """Return the ChromaDB persistence directory."""
    if override:
        return Path(override).expanduser().resolve()
    return get_data_dir() / DEFAULT_CHROMA_DIR_NAME


def get_logs_dir(override: Optional[str] = None) -> Path:
    """Return the logs directory."""
    if override:
        return Path(override).expanduser().resolve()
    return _resolve_root() / DEFAULT_LOGS_DIR_NAME


def get_images_dir(override: Optional[str] = None) -> Path:
    """Return the images storage directory."""
    if override:
        return Path(override).expanduser().resolve()
    return get_data_dir() / DEFAULT_IMAGES_DIR_NAME


def get_attachments_dir(override: Optional[str] = None) -> Path:
    """Return the attachments storage directory."""
    if override:
        return Path(override).expanduser().resolve()
    return get_data_dir() / DEFAULT_ATTACHMENTS_DIR_NAME


__all__ = [
    "get_attachments_dir",
    "get_chroma_dir",
    "get_config_path",
    "get_data_dir",
    "get_db_path",
    "get_images_dir",
    "get_logs_dir",
]
