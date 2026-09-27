"""云端 OpenAI 兼容接口客户端。

支持文本对话、多模态视觉、文本嵌入，作为本地 Ollama 的云端替代方案。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from ..core.enums import InferenceBackend, Role
from ..core.models import ChatMessage, EmbeddingResult, InferenceResponse
from ..utils.exceptions import ModelConnectionError
from ..utils.logger import get_logger
from .base import BaseEmbeddingClient, BaseLLMClient, BaseVLClient

logger = get_logger("ai.cloud_api")


DEFAULT_CLOUD_TEXT_MODEL = "qwen-turbo"
DEFAULT_CLOUD_VISION_MODEL = "qwen-vl-plus"
DEFAULT_CLOUD_EMBEDDING_MODEL = "text-embedding-v3"


def _encode_image(image_path: str) -> str:
    """将图片文件编码为 base64 字符串。"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _mime_type(path: str) -> str:
    """根据扩展名返回 MIME 类型。"""
    ext = Path(path).suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return mapping.get(ext, "image/jpeg")


class CloudAPIClient(BaseLLMClient, BaseVLClient, BaseEmbeddingClient):
    """封装云端 OpenAI 兼容 API，支持文本/VL/嵌入。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout: int = 120,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: Any = None
        self._last_error: str = ""

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except Exception as e:
                raise ModelConnectionError(
                    InferenceBackend.CLOUD_API.value,
                    detail="缺少 openai SDK，请安装：pip install openai",
                ) from e
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    def is_available(self) -> bool:
        """检查 API Key 是否配置。"""
        if not self.api_key or self.api_key.strip() in ("", "your-api-key"):
            self._last_error = "API Key 未配置"
            return False
        return True

    def list_models(self) -> List[str]:
        """云端模式下返回推荐模型列表。"""
        return [
            DEFAULT_CLOUD_TEXT_MODEL,
            DEFAULT_CLOUD_VISION_MODEL,
            DEFAULT_CLOUD_EMBEDDING_MODEL,
        ]

    def _msg_to_openai(self, msg: ChatMessage) -> Dict[str, Any]:
        """将 ChatMessage 转为 OpenAI 格式。"""
        return {"role": msg.role.value, "content": msg.content}

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
        model = model or DEFAULT_CLOUD_TEXT_MODEL

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[self._msg_to_openai(m) for m in messages],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=False,
            )
        except Exception as e:
            logger.error("Cloud API chat failed: %s", e)
            raise ModelConnectionError(
                InferenceBackend.CLOUD_API.value,
                detail=f"云端对话请求失败：{e}",
            ) from e

        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        return InferenceResponse(
            content=content,
            model=model,
            usage=usage,
            finish_reason=choice.finish_reason,
            raw=response.model_dump(),
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
        model = model or DEFAULT_CLOUD_TEXT_MODEL
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[self._msg_to_openai(m) for m in messages],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in response:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta
        except Exception as e:
            logger.error("Cloud API stream failed: %s", e)
            raise ModelConnectionError(
                InferenceBackend.CLOUD_API.value,
                detail=f"云端流式请求失败：{e}",
            ) from e

    def chat_with_image(
        self,
        messages: List[ChatMessage],
        images: List[str],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> InferenceResponse:
        if not messages:
            raise ValueError("messages cannot be empty")
        model = model or DEFAULT_CLOUD_VISION_MODEL

        # 构造 OpenAI 多模态 content：text + image_url(base64)
        content: List[Dict[str, Any]] = []
        if messages and messages[0].role == Role.USER:
            content.append({"type": "text", "text": messages[0].content})
        else:
            content.append({"type": "text", "text": ""})

        for img_path in images:
            try:
                b64 = _encode_image(img_path)
                mime = _mime_type(img_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            except Exception as e:
                logger.warning("Failed to encode image %s: %s", img_path, e)

        payload_messages = [{"role": Role.USER.value, "content": content}]

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=payload_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
            )
        except Exception as e:
            logger.error("Cloud API vision failed: %s", e)
            raise ModelConnectionError(
                InferenceBackend.CLOUD_API.value,
                detail=f"云端视觉请求失败：{e}",
            ) from e

        choice = response.choices[0]
        return InferenceResponse(
            content=choice.message.content or "",
            model=model,
            usage=response.usage.model_dump() if response.usage else {},
            finish_reason=choice.finish_reason,
            raw=response.model_dump(),
        )

    def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
    ) -> List[EmbeddingResult]:
        model = model or DEFAULT_CLOUD_EMBEDDING_MODEL
        try:
            results: List[EmbeddingResult] = []
            for text in texts:
                response = self.client.embeddings.create(
                    model=model,
                    input=text,
                )
                embedding = response.data[0].embedding if response.data else []
                results.append(
                    EmbeddingResult(
                        embedding=embedding,
                        model=model,
                    )
                )
            return results
        except Exception as e:
            logger.error("Cloud API embedding failed: %s", e)
            raise ModelConnectionError(
                InferenceBackend.CLOUD_API.value,
                detail=f"云端嵌入请求失败：{e}",
            ) from e

    def embed_query(self, text: str, model: Optional[str] = None) -> EmbeddingResult:
        return self.embed([text], model=model)[0]
