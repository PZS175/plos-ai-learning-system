"""Logging utilities for PLOS AI."""

from __future__ import annotations

import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from ..core.constants import DEFAULT_LOG_BACKUP_COUNT, DEFAULT_LOG_LEVEL, DEFAULT_LOG_MAX_FILE_SIZE_MB
from .paths import get_logs_dir


DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# 日志保留天数：超过 30 天的日志文件自动删除
LOG_RETENTION_DAYS = 30


def cleanup_old_logs(log_dir: Optional[str] = None, retention_days: int = LOG_RETENTION_DAYS) -> int:
    """删除超过 ``retention_days`` 天的旧日志文件，返回删除的文件数。

    仅清理 ``plos.log*`` 命名的文件，避免误删其他文件。
    任何异常都被吞掉，保证日志清理不影响主流程。
    """
    removed = 0
    try:
        logs_path = Path(get_logs_dir(log_dir))
        if not logs_path.exists():
            return 0
        cutoff = time.time() - retention_days * 86400
        for f in logs_path.glob("plos.log*"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                continue
    except Exception:
        pass
    return removed


def setup_logger(
    level: Optional[str] = None,
    log_dir: Optional[str] = None,
    max_file_size_mb: int = DEFAULT_LOG_MAX_FILE_SIZE_MB,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    log_to_console: bool = True,
) -> logging.Logger:
    """Configure the root logger for the application.

    Args:
        level: Logging level name (DEBUG, INFO, WARNING, ERROR). Defaults to
            ``DEFAULT_LOG_LEVEL``.
        log_dir: Optional override directory for the log file.
        max_file_size_mb: Maximum size of a single log file in megabytes.
        backup_count: Number of rotated log files to keep.
        log_to_console: Whether to also emit logs to ``stderr``.

    Returns:
        The configured root logger.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, (level or DEFAULT_LOG_LEVEL).upper(), logging.INFO))

    # 启动时清理 30 天前的旧日志，防止磁盘无限膨胀
    cleanup_old_logs(log_dir)

    if not root.handlers:
        formatter = logging.Formatter(DEFAULT_FORMAT)

        # File handler
        try:
            logs_path = get_logs_dir(log_dir)
            logs_path.mkdir(parents=True, exist_ok=True)
            log_file = logs_path / "plos.log"
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=max_file_size_mb * 1024 * 1024,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except Exception as e:
            logging.warning("Could not create file logger: %s", e)

        # Console handler
        if log_to_console:
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setFormatter(formatter)
            root.addHandler(console_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger with the given name."""
    return logging.getLogger(name)


__all__ = ["get_logger", "setup_logger", "cleanup_old_logs", "LOG_RETENTION_DAYS"]
