"""AI inference layer.

Provides a unified interface for text generation, vision-language understanding,
and embeddings, abstracting away the underlying backend (Ollama / llama.cpp).
"""

from .base import BaseEmbeddingClient, BaseLLMClient, BaseVLClient
from .ollama_client import OllamaClient
from .model_manager import ModelManager

__all__ = [
    "BaseLLMClient",
    "BaseVLClient",
    "BaseEmbeddingClient",
    "OllamaClient",
    "ModelManager",
]
