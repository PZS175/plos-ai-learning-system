"""Model manager: backend switching, availability checks, graceful degradation.

This is the single entry point from upper layers. It hides whether Ollama or
llama.cpp is being used and provides friendly fallbacks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.constants import DEFAULT_OLLAMA_HOST
from ..core.enums import InferenceBackend
from ..core.models import (
    ChatMessage,
    EmbeddingResult,
    HardwareInfo,
    InferenceResponse,
)
from ..utils.exceptions import ModelConnectionError, ModelDegradedError
from ..utils.logger import get_logger
from .base import BaseEmbeddingClient, BaseLLMClient, BaseVLClient
from .cloud_api_client import CloudAPIClient
from .llamacpp_client import LlamaCppClient
from .ollama_client import OllamaClient

logger = get_logger("ai.model_manager")


class ModelManager:
    """Central manager for AI models and backends."""

    def __init__(self, config: Dict[str, Any], hardware: Optional[HardwareInfo] = None):
        self.config = config
        self.hardware = hardware
        self.backend_type = InferenceBackend(config.get("backend", {}).get("type", "ollama"))

        # Build backend clients
        self._llm_client: Optional[BaseLLMClient] = None
        self._vl_client: Optional[BaseVLClient] = None
        self._embedding_client: Optional[BaseEmbeddingClient] = None
        self._build_clients()

        self._vl_degraded = False  # If VL unavailable, mark and fall back to text-only

    def _close_clients(self) -> None:
        """切换后端/重建客户端前释放旧客户端持有的 HTTP 连接与模型句柄，
        避免反复切换造成句柄/内存泄漏。"""
        old_clients = {self._llm_client, self._vl_client, self._embedding_client}
        for client in old_clients:
            if client is None:
                continue
            # 客户端自身若提供 close/shutdown 则优先调用
            for method_name in ("close", "shutdown"):
                fn = getattr(client, method_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception as e:
                        logger.warning("Close old client failed: %s", e)
                    break
            # 防御：显式关闭内部 httpx 连接（OllamaClient/SDK 客户端）
            for attr_name in ("_http_client", "_client"):
                inner = getattr(client, attr_name, None)
                if inner is client:
                    continue
                close_fn = getattr(inner, "close", None)
                if callable(close_fn):
                    try:
                        close_fn()
                    except Exception:
                        pass

    def _build_clients(self) -> None:
        # 重建前先释放旧客户端（首次构建时三个引用均为 None，为无操作）
        self._close_clients()
        if self.backend_type == InferenceBackend.OLLAMA:
            host = self.config.get("backend", {}).get("ollama_host", DEFAULT_OLLAMA_HOST)
            timeout = self.config.get("backend", {}).get("ollama_timeout", 120)
            client = OllamaClient(host=host, timeout=timeout)
            self._llm_client = client
            self._vl_client = client
            self._embedding_client = client
        elif self.backend_type == InferenceBackend.LLAMA_CPP:
            model_dir = self.config.get("backend", {}).get("llamacpp_model_dir", "")
            hw_cfg = self.config.get("hardware", {})
            ctx = self.config.get("models", {}).get("context_length", 4096)
            n_threads = hw_cfg.get("num_threads", 0)
            n_gpu_layers = hw_cfg.get("gpu_layers", 0)
            client = LlamaCppClient(
                model_dir=model_dir,
                n_ctx=ctx,
                n_threads=n_threads,
                n_gpu_layers=n_gpu_layers,
            )
            self._llm_client = client
            self._vl_client = client
            self._embedding_client = client
        elif self.backend_type == InferenceBackend.CLOUD_API:
            cloud_cfg = self.config.get("cloud", {})
            client = CloudAPIClient(
                api_key=cloud_cfg.get("api_key", ""),
                base_url=cloud_cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                timeout=cloud_cfg.get("timeout", 120),
            )
            self._llm_client = client
            self._vl_client = client
            self._embedding_client = client
        else:
            raise ValueError(f"Unsupported backend: {self.backend_type}")

    def switch_backend(self, backend: InferenceBackend) -> None:
        """Switch backend and rebuild clients."""
        self.backend_type = backend
        # 防御：config 可能缺少 backend 段（如使用默认/空配置初始化后切换后端），
        # 直接下标访问会抛 KeyError 导致切换崩溃，这里用 setdefault 兜底。
        self.config.setdefault("backend", {})["type"] = backend.value
        self._build_clients()
        logger.info("Switched backend to %s", backend.value)

    def apply_config(self, config: Dict[str, Any]) -> None:
        """Apply a new configuration (e.g. after settings changed)."""
        self.config = config
        backend = InferenceBackend(config.get("backend", {}).get("type", "ollama"))
        if backend != self.backend_type:
            self.switch_backend(backend)
        else:
            self._build_clients()

    @staticmethod
    def _model_exists(model_name: str | None, available_models: List[str]) -> bool:
        """宽松匹配模型名，允许 :latest / :<tag> 等后缀差异和常见别名。"""
        if not model_name:
            return True
        base_name = model_name.split(":", 1)[0]
        # 常见别名映射：配置中写 qwen2.5-vl，Ollama 列表中可能是 qwen2.5vl
        aliases = {
            "qwen2.5-vl": "qwen2.5vl",
            "qwen2.5vl": "qwen2.5-vl",
        }
        alias = aliases.get(base_name)

        for available in available_models:
            if available == model_name or available.startswith(base_name + ":"):
                return True
            if alias and (available == alias or available.startswith(alias + ":")):
                return True
        return False

    def is_text_available(self) -> bool:
        if self._llm_client is None or not self._llm_client.is_available():
            return False
        # 云端模式下只要 API Key 有效即认为文本可用
        if self.backend_type == InferenceBackend.CLOUD_API:
            return True
        model = self.config.get("models", {}).get("text_model")
        return self._model_exists(model, self.list_text_models())

    def is_vision_available(self) -> bool:
        if self._vl_client is None or not self._vl_client.is_available():
            return False
        if self.backend_type == InferenceBackend.CLOUD_API:
            return True
        model = self.config.get("models", {}).get("vision_model")
        return self._model_exists(model, self.list_vision_models())

    def is_embedding_available(self) -> bool:
        if self._embedding_client is None or not self._embedding_client.is_available():
            return False
        if self.backend_type == InferenceBackend.CLOUD_API:
            return True
        model = self.config.get("models", {}).get("embedding_model")
        return self._model_exists(model, self.list_embedding_models())

    # ------------------------------------------------------------------
    # 模型内存 / 显存释放
    # ------------------------------------------------------------------

    def vision_model_name(self) -> str:
        """当前配置的视觉（VL）模型名。"""
        return str(self.config.get("models", {}).get("vision_model") or "")

    def text_model_name(self) -> str:
        """当前配置的文本模型名。"""
        return str(self.config.get("models", {}).get("text_model") or "")

    def unload_model(self, model_name: str) -> bool:
        """请求后端立即卸载指定模型，释放显存 / 内存。

        本地 Ollama 通过 ``keep_alive=0`` 触发卸载，模型下次使用时按需重新加载；
        低配机器可借此在不用图片识别时腾出内存。云端 API 无需卸载，直接返回 False。
        """
        if not model_name:
            return False
        if self.backend_type == InferenceBackend.CLOUD_API:
            logger.info("云端 API 模式无需卸载本地模型")
            return False
        client = self._vl_client or self._llm_client
        http_client = getattr(client, "http_client", None)
        if http_client is None:
            logger.warning("当前后端不支持模型卸载")
            return False
        try:
            response = http_client.post(
                "/api/generate", json={"model": model_name, "keep_alive": 0}
            )
            response.raise_for_status()
            logger.info("已请求卸载模型: %s（下次使用时自动重新加载）", model_name)
            return True
        except Exception as e:
            logger.warning("卸载模型 %s 失败: %s", model_name, e)
            return False

    def release_vision_model(self) -> bool:
        """卸载视觉模型（OCR / 看图解题用）。"""
        return self.unload_model(self.vision_model_name())

    def release_text_model(self) -> bool:
        """卸载文本模型（对话 / RAG 用）。"""
        return self.unload_model(self.text_model_name())

    def health_check(self) -> Dict[str, Any]:
        """模型健康自检：服务、模型、GPU 加速状态及影响说明。"""
        result: Dict[str, Any] = {
            "backend_type": self.backend_type.value,
            "service_ok": True,
            "text_model_ok": False,
            "vision_model_ok": False,
            "gpu_accelerated": False,
            "issues": [],
        }

        # 服务状态
        try:
            service_ok = self._llm_client is not None and self._llm_client.is_available()
        except Exception:
            service_ok = False
        result["service_ok"] = service_ok

        if not service_ok:
            if self.backend_type == InferenceBackend.CLOUD_API:
                result["issues"].append("云端 API 连接异常：AI 对话、OCR 识别、AI 打标等功能将不可用。")
            else:
                result["issues"].append("Ollama 服务未启动：所有 AI 功能（对话、OCR、打标、变式题）将无法使用。")
            return result

        # 模型状态
        result["text_model_ok"] = self.is_text_available()
        result["vision_model_ok"] = self.is_vision_available()

        if not result["text_model_ok"]:
            if self.backend_type == InferenceBackend.CLOUD_API:
                result["issues"].append("云端文本模型不可用：AI 对话、AI 打标、变式题生成将失败。")
            else:
                result["issues"].append(
                    f"本地文本模型未找到（当前配置：{self.config.get('models', {}).get('text_model')}）："
                    "AI 对话、自动打标、变式题等功能不可用。"
                )

        if not result["vision_model_ok"]:
            result["issues"].append(
                "视觉/VL 模型不可用：图片 OCR 识别、拍题功能将降级为纯文本处理或不可用。"
            )

        # GPU 加速状态（仅本地模式）
        if self.backend_type != InferenceBackend.CLOUD_API:
            hw_cfg = self.config.get("hardware", {})
            use_gpu = hw_cfg.get("use_gpu", True)
            n_gpu_layers = hw_cfg.get("gpu_layers", 0)
            has_gpu = self.hardware is not None and self.hardware.gpu_vram_gb > 0
            result["gpu_accelerated"] = has_gpu and use_gpu and n_gpu_layers > 0
            if has_gpu and not result["gpu_accelerated"]:
                result["issues"].append("检测到独立显卡但未启用 GPU 加速：本地模型推理速度较慢。")

        return result

    def list_text_models(self) -> List[str]:
        if self._llm_client:
            return self._llm_client.list_models()
        return []

    def list_vision_models(self) -> List[str]:
        if self._vl_client:
            return self._vl_client.list_models()
        return []

    def list_embedding_models(self) -> List[str]:
        if self._embedding_client:
            return self._embedding_client.list_models()
        return []

    # ---- Chat ----

    def chat(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> InferenceResponse:
        if self._llm_client is None or not self._llm_client.is_available():
            raise ModelConnectionError(self.backend_type.value, detail="text model unavailable")
        if model is None:
            model = self.config.get("models", {}).get("text_model")
        cfg_models = self.config.get("models", {})
        params = {
            "temperature": cfg_models.get("temperature", 0.7),
            "top_p": cfg_models.get("top_p", 0.9),
            "max_tokens": cfg_models.get("max_tokens", 2048),
            "num_ctx": cfg_models.get("context_length", 8192),
        }
        params.update(kwargs)
        logger.info("Chat request: model=%s, messages=%d", model, len(messages))
        return self._llm_client.chat(messages, model=model, **params)

    def chat_stream(self, messages: List[ChatMessage], model: Optional[str] = None, **kwargs: Any):
        """流式对话：逐段产出文本增量。

        仅 Ollama 后端支持真流式；其他后端回退为整段单次产出。
        """
        if self._llm_client is None or not self._llm_client.is_available():
            raise ModelConnectionError(self.backend_type.value, detail="text model unavailable")
        if model is None:
            model = self.config.get("models", {}).get("text_model")
        cfg_models = self.config.get("models", {})
        params = {
            "temperature": cfg_models.get("temperature", 0.7),
            "top_p": cfg_models.get("top_p", 0.9),
            "max_tokens": cfg_models.get("max_tokens", 2048),
            "num_ctx": cfg_models.get("context_length", 8192),
        }
        params.update(kwargs)

        stream_fn = getattr(self._llm_client, "chat_stream_sync", None)
        if callable(stream_fn):
            yield from stream_fn(messages, model=model, **params)
        else:
            response = self._llm_client.chat(messages, model=model, **params)
            if response is not None and response.content:
                yield response.content

    # ---- Vision ----

    def chat_with_image(
        self,
        messages: List[ChatMessage],
        images: List[str],
        model: Optional[str] = None,
        allow_degradation: bool = True,
        **kwargs: Any,
    ) -> InferenceResponse:
        """Send image+text request to VL model.

        If VL model is unavailable and allow_degradation is True, raise
        ModelDegradedError so the caller can fall back to text-only/OCR mode.
        """
        if self._vl_client is None or not self._vl_client.is_available():
            self._vl_degraded = True
            if allow_degradation:
                raise ModelDegradedError("vision model", fallback="OCR/text-only")
            raise ModelConnectionError(self.backend_type.value, detail="vision model unavailable")

        if model is None:
            model = self.config.get("models", {}).get("vision_model")
        cfg_models = self.config.get("models", {})
        params = {
            "temperature": cfg_models.get("temperature", 0.7),
            "max_tokens": cfg_models.get("max_tokens", 2048),
            "num_ctx": cfg_models.get("context_length", 8192),
        }
        params.update(kwargs)
        logger.info("VL request: model=%s, images=%d", model, len(images))
        try:
            return self._vl_client.chat_with_image(messages, images, model=model, **params)
        except Exception as e:
            self._vl_degraded = True
            logger.warning("VL request failed: %s", e)
            if allow_degradation:
                raise ModelDegradedError("vision model", fallback="OCR/text-only") from e
            raise

    def vision_degraded(self) -> bool:
        """Return True if the vision model is currently unavailable/degraded."""
        return self._vl_degraded or not self.is_vision_available()

    # ---- Embeddings ----

    def embed(self, texts: List[str], model: Optional[str] = None) -> List[EmbeddingResult]:
        if self._embedding_client is None or not self._embedding_client.is_available():
            raise ModelConnectionError(self.backend_type.value, detail="embedding model unavailable")
        if model is None:
            model = self.config.get("models", {}).get("embedding_model")
        return self._embedding_client.embed(texts, model=model)

    def embed_query(self, text: str, model: Optional[str] = None) -> EmbeddingResult:
        if self._embedding_client is None or not self._embedding_client.is_available():
            raise ModelConnectionError(self.backend_type.value, detail="embedding model unavailable")
        if model is None:
            model = self.config.get("models", {}).get("embedding_model")
        return self._embedding_client.embed_query(text, model=model)

    # ---- Recommendations ----

    def get_recommended_model_settings(self) -> Dict[str, Any]:
        """Recommend models and settings based on detected hardware."""
        from ..config.hardware import get_recommended_settings
        if self.hardware:
            return get_recommended_settings(self.hardware)
        return {
            "text_model": self.config.get("models", {}).get("text_model"),
            "vision_model": self.config.get("models", {}).get("vision_model"),
            "use_gpu": self.config.get("hardware", {}).get("use_gpu", True),
            "low_spec_mode": self.config.get("hardware", {}).get("low_spec_mode", "off"),
        }
