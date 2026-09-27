"""llama.cpp adapter for local GGUF model inference.

This is a placeholder adapter using the `llama-cpp-python` library.
The library is an optional dependency because it requires platform-specific
compilation. If the import fails, the class reports unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from ..core.enums import InferenceBackend
from ..core.models import ChatMessage, EmbeddingResult, InferenceResponse
from ..utils.exceptions import ModelConnectionError, ModelNotFoundError
from ..utils.logger import get_logger
from .base import BaseEmbeddingClient, BaseLLMClient, BaseVLClient

logger = get_logger("ai.llamacpp")


class LlamaCppClient(BaseLLMClient, BaseVLClient, BaseEmbeddingClient):
    """llama.cpp backend adapter.

    Expects GGUF files in the configured model directory. Each model name maps to a
    `.gguf` file in that directory.
    """

    def __init__(self, model_dir: str = "", n_ctx: int = 4096, n_threads: int = 0, n_gpu_layers: int = 0):
        self.model_dir = Path(model_dir) if model_dir else None
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.n_gpu_layers = n_gpu_layers
        self._llama_cls = None
        self._models: Dict[str, Any] = {}
        self._available = False
        self._init_library()

    def _init_library(self) -> None:
        try:
            from llama_cpp import Llama
            self._llama_cls = Llama
            self._available = True
        except ImportError:
            logger.warning("llama-cpp-python is not installed. llama.cpp backend unavailable.")
            self._available = False

    def _model_path(self, model_name: str) -> Optional[Path]:
        """Resolve model name to a .gguf file path."""
        if not self.model_dir:
            return None
        candidates = [
            self.model_dir / model_name,
            self.model_dir / f"{model_name}.gguf",
        ]
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    def _load_model(self, model_name: str) -> Any:
        """Lazy-load a Llama model."""
        if model_name in self._models:
            return self._models[model_name]

        path = self._model_path(model_name)
        if path is None:
            raise ModelNotFoundError(model_name, backend=InferenceBackend.LLAMA_CPP.value)

        if self._llama_cls is None:
            raise ModelConnectionError("llama.cpp", detail="llama-cpp-python not installed")

        n_threads = self.n_threads if self.n_threads > 0 else None
        llm = self._llama_cls(
            model_path=str(path),
            n_ctx=self.n_ctx,
            n_threads=n_threads,
            n_gpu_layers=self.n_gpu_layers,
            verbose=False,
        )
        self._models[model_name] = llm
        logger.info("Loaded llama.cpp model: %s", path)
        return llm

    def is_available(self) -> bool:
        return self._available and self.model_dir is not None and self.model_dir.exists()

    def list_models(self) -> List[str]:
        if not self.model_dir or not self.model_dir.exists():
            return []
        return [p.stem for p in self.model_dir.glob("*.gguf")]

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
        if model is None:
            raise ValueError("model name is required")
        llm = self._load_model(model)
        try:
            response = llm.create_chat_completion(
                messages=[m.to_dict() for m in messages],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=False,
            )
        except Exception as e:
            raise ModelConnectionError("llama.cpp", detail=str(e)) from e

        choice = response["choices"][0]
        message = choice.get("message", {})
        content = message.get("content", "")
        usage = response.get("usage", {})
        return InferenceResponse(
            content=content,
            model=model,
            usage=usage,
            finish_reason=choice.get("finish_reason"),
            raw=response,
        )

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        if model is None:
            raise ValueError("model name is required")
        llm = self._load_model(model)
        try:
            stream = llm.create_chat_completion(
                messages=[m.to_dict() for m in messages],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=True,
            )
        except Exception as e:
            raise ModelConnectionError("llama.cpp", detail=str(e)) from e

        for chunk in stream:
            delta = chunk["choices"][0].get("delta", {})
            yield delta.get("content", "")

    def chat_with_image(
        self,
        messages: List[ChatMessage],
        images: List[str],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> InferenceResponse:
        # llama.cpp with vision requires a specific multimodal GGUF and a different
        # loader (e.g. llava or qwen2-vl variants). We expose a basic path.
        logger.warning("llama.cpp vision support is experimental; ensure you use a multimodal GGUF")
        return self.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def embed(
        self, texts: List[str], model: Optional[str] = None
    ) -> List[EmbeddingResult]:
        if model is None:
            raise ValueError("embedding model name is required")
        llm = self._load_model(model)
        results: List[EmbeddingResult] = []
        for text in texts:
            try:
                embedding = llm.embed(text)
                results.append(EmbeddingResult(embedding=embedding, model=model))
            except Exception as e:
                raise ModelConnectionError("llama.cpp", detail=str(e)) from e
        return results

    def embed_query(self, text: str, model: Optional[str] = None) -> EmbeddingResult:
        results = self.embed([text], model=model)
        return results[0]
