"""Abstract base classes for AI inference backends.

All backends must implement these interfaces so upper layers don't need
to know which backend is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, List, Optional

from ..core.models import ChatMessage, EmbeddingResult, InferenceResponse


class BaseLLMClient(ABC):
    """Abstract interface for text-only LLM inference."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is reachable and the model is loaded."""

    @abstractmethod
    def list_models(self) -> List[str]:
        """List available text model names."""

    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        stream: bool = False,
        **kwargs: Any,
    ) -> InferenceResponse:
        """Send a chat completion request (non-streaming)."""

    @abstractmethod
    def chat_stream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens as they are generated."""


class BaseVLClient(ABC):
    """Abstract interface for vision-language model inference."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if VL model is available."""

    @abstractmethod
    def chat_with_image(
        self,
        messages: List[ChatMessage],
        images: List[str],  # file paths or base64
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> InferenceResponse:
        """Send a chat request with images to the VL model."""


class BaseEmbeddingClient(ABC):
    """Abstract interface for text embedding models."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if embedding model is available."""

    @abstractmethod
    def embed(self, texts: List[str], model: Optional[str] = None) -> List[EmbeddingResult]:
        """Generate embeddings for a list of texts."""

    @abstractmethod
    def embed_query(self, text: str, model: Optional[str] = None) -> EmbeddingResult:
        """Generate embedding for a single query text."""
