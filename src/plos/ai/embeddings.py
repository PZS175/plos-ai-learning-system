"""Embedding model wrapper.

Provides a unified interface for embeddings regardless of backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from ..core.models import EmbeddingResult
from ..utils.exceptions import ModelConnectionError

if TYPE_CHECKING:
    from .base import BaseEmbeddingClient


class EmbeddingProvider:
    """Convenience wrapper around a BaseEmbeddingClient."""

    def __init__(self, client: "BaseEmbeddingClient", model: str):
        self.client = client
        self.model = model

    def embed(self, texts: List[str]) -> List[EmbeddingResult]:
        if not self.client.is_available():
            raise ModelConnectionError(self.client.__class__.__name__, detail="embedding backend unavailable")
        return self.client.embed(texts, model=self.model)

    def embed_query(self, text: str) -> EmbeddingResult:
        if not self.client.is_available():
            raise ModelConnectionError(self.client.__class__.__name__, detail="embedding backend unavailable")
        return self.client.embed_query(text, model=self.model)
