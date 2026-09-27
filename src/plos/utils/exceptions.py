"""Custom exceptions for PLOS AI."""

from __future__ import annotations

from typing import Optional


class PLOSError(Exception):
    """Base exception for all PLOS AI errors."""

    pass


class ConfigError(PLOSError):
    """Raised when configuration loading or validation fails."""

    pass


class ModelConnectionError(PLOSError):
    """Raised when a model backend cannot be reached or fails."""

    def __init__(
        self,
        backend: str,
        host: Optional[str] = None,
        detail: Optional[str] = None,
    ):
        self.backend = backend
        self.host = host
        self.detail = detail
        message = f"Connection error for backend '{backend}'"
        if host:
            message += f" at {host}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class ModelNotFoundError(PLOSError):
    """Raised when a requested model is not available locally."""

    def __init__(self, model: str, backend: Optional[str] = None):
        self.model = model
        self.backend = backend
        message = f"Model '{model}' not found"
        if backend:
            message += f" on backend '{backend}'"
        super().__init__(message)


class ModelDegradedError(PLOSError):
    """Raised when a model falls back or degrades (e.g. vision unavailable)."""

    def __init__(self, component: str, fallback: Optional[str] = None):
        self.component = component
        self.fallback = fallback
        message = f"Component '{component}' is degraded"
        if fallback:
            message += f"; fallback: {fallback}"
        super().__init__(message)


class OCRError(PLOSError):
    """Raised when OCR processing fails."""

    pass


class DocumentParseError(PLOSError):
    """Raised when a document cannot be parsed."""

    pass


class ScreenshotError(PLOSError):
    """Raised when screenshot or clipboard capture fails."""

    pass


class StorageError(PLOSError):
    """Raised when a storage operation fails."""

    pass


__all__ = [
    "ConfigError",
    "DocumentParseError",
    "ModelConnectionError",
    "ModelDegradedError",
    "ModelNotFoundError",
    "OCRError",
    "PLOSError",
    "ScreenshotError",
    "StorageError",
]
