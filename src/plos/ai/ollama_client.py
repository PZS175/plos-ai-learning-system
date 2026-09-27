"""Ollama client implementation supporting text, vision, and embeddings.

混合实现：使用官方 `ollama` Python SDK 列出模型，使用 httpx 直接调用
Ollama REST 接口进行 chat/embeddings，以获得更可靠的超时控制和日志可见性。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

import httpx
from ollama import Client as OllamaNativeClient

from ..core.constants import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_TIMEOUT
from ..core.enums import InferenceBackend, ModelType, Role
from ..core.models import ChatMessage, EmbeddingResult, InferenceResponse
from ..utils.exceptions import ModelConnectionError, ModelNotFoundError
from ..utils.logger import get_logger
from .base import BaseEmbeddingClient, BaseLLMClient, BaseVLClient

logger = get_logger("ai.ollama")


def _encode_image(image_path: str) -> str:
    """Encode an image file to base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _message_to_ollama(msg: ChatMessage) -> Dict[str, Any]:
    """Convert a ChatMessage to Ollama SDK message format."""
    content = msg.content
    images: Optional[List[str]] = None
    if msg.images:
        images = []
        for img in msg.images:
            if Path(img).exists():
                images.append(_encode_image(img))
            elif img.startswith("data:image"):
                images.append(img.split(",", 1)[1] if "," in img else img)
            else:
                images.append(img)
    if images:
        return {"role": msg.role.value, "content": content, "images": images}
    return {"role": msg.role.value, "content": content}


class OllamaClient(BaseLLMClient, BaseVLClient, BaseEmbeddingClient):
    """Unified Ollama client for text, vision, and embeddings."""

    def __init__(
        self,
        host: str = DEFAULT_OLLAMA_HOST,
        timeout: int = DEFAULT_OLLAMA_TIMEOUT,
    ):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._client: Optional[OllamaNativeClient] = None
        self._http_client: Optional[httpx.Client] = None
        self._available_models: List[str] = []
        self._last_error: str = ""

    @property
    def client(self) -> OllamaNativeClient:
        if self._client is None:
            self._client = OllamaNativeClient(host=self.host, timeout=self.timeout)
        return self._client

    @property
    def http_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(
                base_url=self.host,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._http_client

    def _refresh_models(self) -> None:
        try:
            response = self.client.list()
            models = response.get("models", [])
            names = []
            for m in models:
                # Ollama versions differ: some use "name", some use "model"
                name = m.get("name") or m.get("model")
                if name:
                    names.append(name)
            self._available_models = names
        except Exception as e:
            logger.warning("Failed to list Ollama models: %s", e)
            self._available_models = []

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            self.client.list()
            return True
        except Exception as e:
            self._last_error = str(e)
            return False

    def list_models(self) -> List[str]:
        self._refresh_models()
        return self._available_models

    def _resolve_model(self, model: str) -> str:
        """将配置中的模型名解析为 Ollama 中实际存在的名称。

        若完全匹配则直接返回；否则按 base_name（冒号前部分）在可用模型中
        查找第一个匹配项，以兼容配置名与实际安装名的 tag/别名差异。
        """
        self._refresh_models()
        if not self._available_models:
            return model
        if model in self._available_models:
            return model

        base_name = model.split(":", 1)[0]
        # 常见别名：配置 qwen2.5-vl 对应 Ollama qwen2.5vl
        aliases = {"qwen2.5-vl": "qwen2.5vl", "qwen2.5vl": "qwen2.5-vl"}
        alias = aliases.get(base_name)

        for available in self._available_models:
            available_base = available.split(":", 1)[0]
            if available_base == base_name or (alias and available_base == alias):
                return available
        return model

    def _check_model(self, model: str, model_type: ModelType) -> None:
        """Ensure model exists on Ollama, raise if not."""
        resolved = self._resolve_model(model)
        if resolved == model and model not in self._available_models:
            base_name = model.split(":", 1)[0]
            if not any(base_name in m for m in self._available_models):
                raise ModelNotFoundError(model, backend=InferenceBackend.OLLAMA.value)

    def _prepare_messages(
        self,
        messages: List[ChatMessage],
        images: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        payload_messages = [_message_to_ollama(m) for m in messages]
        if images and payload_messages:
            encoded_images = []
            for img in images:
                if Path(img).exists():
                    encoded_images.append(_encode_image(img))
                elif img.startswith("data:image"):
                    encoded_images.append(img.split(",", 1)[1] if "," in img else img)
                else:
                    encoded_images.append(img)

            last_user = None
            for i in range(len(payload_messages) - 1, -1, -1):
                if payload_messages[i].get("role") == Role.USER.value:
                    last_user = i
                    break
            if last_user is not None:
                payload_messages[last_user]["images"] = encoded_images
            else:
                payload_messages.append({
                    "role": Role.USER.value,
                    "content": "",
                    "images": encoded_images,
                })
        return payload_messages

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
        if not messages:
            raise ValueError("messages cannot be empty")
        if model is None:
            raise ValueError("model name is required")
        self._check_model(model, ModelType.TEXT)
        model = self._resolve_model(model)

        payload = {
            "model": model,
            "messages": self._prepare_messages(messages),
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
            # 模型常驻内存：避免 Ollama 空闲 5 分钟卸载后的重复加载（10-40s）
            "keep_alive": kwargs.get("keep_alive", "30m"),
            "stream": False,
        }
        if kwargs.get("num_ctx"):
            payload["options"]["num_ctx"] = kwargs["num_ctx"]
        elif kwargs.get("context_length"):
            payload["options"]["num_ctx"] = kwargs["context_length"]

        logger.info("Ollama chat request: model=%s, messages=%d", model, len(messages))
        try:
            response = self.http_client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise ModelConnectionError(
                "ollama", self.host, detail=f"请求超时（{self.timeout}s），模型可能正在加载或响应较慢"
            ) from e
        except Exception as e:
            raise ModelConnectionError("ollama", self.host, detail=str(e)) from e

        content = data.get("message", {}).get("content", "")
        usage = {
            "prompt_tokens": data.get("prompt_eval_count", 0),
            "completion_tokens": data.get("eval_count", 0),
        }
        return InferenceResponse(
            content=content,
            model=model,
            usage=usage,
            finish_reason=data.get("done_reason"),
            raw=data,
        )

    def chat_stream_sync(
        self,
        messages: List[ChatMessage],
        model: str,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> "Iterator[str]":
        """同步流式对话：逐段产出文本增量（打字机效果）。"""
        payload = {
            "model": self._resolve_model(model),
            "messages": self._prepare_messages(messages),
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
            "keep_alive": kwargs.get("keep_alive", "30m"),
            "stream": True,
        }
        if kwargs.get("num_ctx"):
            payload["options"]["num_ctx"] = kwargs["num_ctx"]

        try:
            with self.http_client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
        except httpx.TimeoutException as e:
            raise ModelConnectionError(
                "ollama", self.host, detail=f"请求超时（{self.timeout}s）"
            ) from e
        except Exception as e:
            raise ModelConnectionError("ollama", self.host, detail=str(e)) from e

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
        self._check_model(model, ModelType.TEXT)

        payload = {
            "model": model,
            "messages": self._prepare_messages(messages),
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
            "stream": True,
        }
        if kwargs.get("num_ctx"):
            payload["options"]["num_ctx"] = kwargs["num_ctx"]

        # 同步流式实现，避免引入 async httpx 复杂度
        with httpx.Client(base_url=self.host, timeout=httpx.Timeout(self.timeout, connect=10.0)) as client:
            with client.stream("POST", "/api/chat", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    yield chunk.get("message", {}).get("content", "")

    def chat_with_image(
        self,
        messages: List[ChatMessage],
        images: List[str],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> InferenceResponse:
        if model is None:
            raise ValueError("VL model name is required")
        if not messages:
            raise ValueError("messages cannot be empty")
        self._check_model(model, ModelType.VISION)
        model = self._resolve_model(model)

        payload = {
            "model": model,
            "messages": self._prepare_messages(messages, images=images),
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "keep_alive": kwargs.get("keep_alive", "30m"),
            "stream": False,
        }
        if kwargs.get("num_ctx"):
            payload["options"]["num_ctx"] = kwargs["num_ctx"]

        logger.info("Ollama VL request: model=%s, images=%d", model, len(images))
        try:
            response = self.http_client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise ModelConnectionError("ollama", self.host, detail=str(e)) from e

        content = data.get("message", {}).get("content", "")
        return InferenceResponse(
            content=content,
            model=model,
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
            finish_reason=data.get("done_reason"),
            raw=data,
        )

    def embed(
        self, texts: List[str], model: Optional[str] = None
    ) -> List[EmbeddingResult]:
        if model is None:
            raise ValueError("embedding model name is required")
        self._check_model(model, ModelType.EMBEDDING)
        model = self._resolve_model(model)
        results: List[EmbeddingResult] = []
        for text in texts:
            try:
                response = self.http_client.post(
                    "/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                response.raise_for_status()
                data = response.json()
                embedding = data.get("embedding", [])
                results.append(EmbeddingResult(embedding=embedding, model=model))
            except Exception as e:
                logger.error("Failed to embed text: %s", e)
                raise ModelConnectionError("ollama", self.host, detail=str(e)) from e
        return results

    def embed_query(self, text: str, model: Optional[str] = None) -> EmbeddingResult:
        results = self.embed([text], model=model)
        return results[0]
