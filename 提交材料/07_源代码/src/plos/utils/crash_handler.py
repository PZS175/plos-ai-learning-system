"""Global crash/exception handlers for PLOS AI."""

from __future__ import annotations

import logging
import sys
import threading
import traceback
from typing import Any, Callable, Optional

logger = logging.getLogger("utils.crash_handler")

_original_excepthook: Callable[[Any, Any, Any], Any] = sys.excepthook
_original_threading_excepthook: Optional[Callable[..., Any]] = None


def _format_exception(exc_type: Any, exc_value: Any, exc_traceback: Any) -> str:
    """Format an exception as a string."""
    return "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))


def global_excepthook(exc_type: Any, exc_value: Any, exc_traceback: Any) -> None:
    """Custom sys.excepthook that logs uncaught exceptions."""
    if exc_type is None:
        return
    message = _format_exception(exc_type, exc_value, exc_traceback)
    logger.critical("Uncaught exception:\n%s", message)
    _original_excepthook(exc_type, exc_value, exc_traceback)


def threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """Custom threading.excepthook that logs uncaught thread exceptions."""
    exc_type = args.exc_type
    exc_value = args.exc_value
    exc_traceback = args.exc_traceback
    if exc_type is None:
        return
    message = _format_exception(exc_type, exc_value, exc_traceback)
    logger.critical("Uncaught exception in thread %s:\n%s", args.thread, message)
    if _original_threading_excepthook is not None:
        _original_threading_excepthook(args)


def install_crash_handler() -> None:
    """Install global exception hooks for the application."""
    global _original_threading_excepthook
    sys.excepthook = global_excepthook
    if hasattr(threading, "excepthook"):
        _original_threading_excepthook = threading.excepthook
        threading.excepthook = threading_excepthook
    logger.info("Global crash handler installed")


def uninstall_crash_handler() -> None:
    """Restore the original exception hooks."""
    sys.excepthook = _original_excepthook
    if _original_threading_excepthook is not None and hasattr(threading, "excepthook"):
        threading.excepthook = _original_threading_excepthook
    logger.info("Global crash handler uninstalled")


__all__ = ["install_crash_handler", "uninstall_crash_handler"]
