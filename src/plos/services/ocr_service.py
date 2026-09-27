"""OCR / 图像理解服务（纯 VL 实现）。

不再依赖 PaddleOCR/EasyOCR，所有图片文字提取与看图解题都通过本地
Qwen2.5-VL 多模态模型完成。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from ..ai import ModelManager
from ..core.enums import OCRSource, Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.exceptions import ModelConnectionError, ModelDegradedError
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.ocr_service")


_OCR_PROMPT = (
    "提取图片内所有可见文字，只返回识别出来的原文，不要总结、不要解释、不要额外输出。"
)

_BATCH_SPLIT_PROMPT = (
    "下面是一张试卷图片的 OCR 识别结果，请将其切分为若干道独立题目。"
    "每道题输出格式为：\n【题目】...\n【答案】...（如有）\n【解析】...（如有）\n"
    "如果某部分不是题目则忽略。只输出题目块，不要总结。"
)


class OCRService:
    """基于 VL 模型的图片 OCR 与解题服务。"""

    def __init__(
        self,
        model_manager: ModelManager,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
    ):
        self.model_manager = model_manager
        self.db = db or get_db()
        self.user_service = user_service
        self._last_error: Optional[str] = None

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def is_backend_available(self) -> bool:
        """Ollama/llama.cpp 后端是否可连接。"""
        try:
            client = getattr(self.model_manager, "_llm_client", None)
            return client is not None and client.is_available()
        except Exception as e:
            logger.warning("Backend availability check failed: %s", e)
            return False

    def is_vision_available(self) -> bool:
        """VL 模型是否可用。"""
        try:
            return self.model_manager.is_vision_available()
        except Exception as e:
            logger.warning("Vision availability check failed: %s", e)
            return False

    def is_image_service_available(self) -> bool:
        """图片功能整体可用（后端可连 + VL 模型存在）。"""
        return self.is_backend_available() and self.is_vision_available()

    def release_vision_model(self) -> bool:
        """卸载 VL 视觉模型，释放显存 / 内存（下次识别时自动重新加载）。

        低配机器可在不做图片识别时释放资源。
        """
        try:
            return bool(self.model_manager.release_vision_model())
        except Exception as e:
            logger.warning("释放视觉模型失败: %s", e)
            return False

    def get_status_message(self) -> str:
        """返回图片功能当前状态的友好提示。"""
        if not self.is_backend_available():
            return "Ollama服务未启动，图片功能不可用"
        if not self.is_vision_available():
            return "缺少对应VL模型，图片OCR/识图不可用"
        return "图片功能可用"

    @staticmethod
    def _downscaled_path(image_path: Path) -> Path:
        """降采样副本路径：系统临时目录 + 内容哈希命名，原图不受影响。"""
        import hashlib
        import tempfile

        digest = hashlib.md5(str(image_path).encode("utf-8")).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"plos_ocr_{digest}{image_path.suffix.lower() or '.jpg'}"

    def _call_vl(
        self,
        image_path: Path,
        prompt: str,
        max_tokens: Optional[int] = None,
    ) -> str:
        """调用 VL 模型，返回文本结果。"""
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"图片不存在：{image_path}")

        # 超大图（手机原照 4000px+）先等比缩到 2048px：base64 编码与
        # VL prefill 时间大幅下降，识别准确率在 OCR 场景几乎无损
        try:
            from PIL import Image

            with Image.open(image_path) as im:
                if max(im.size) > 2048:
                    scale = 2048 / max(im.size)
                    resized = im.resize(
                        (int(im.width * scale), int(im.height * scale)),
                        Image.Resampling.LANCZOS,
                    )
                    # 写入临时副本供本次识别，绝不覆盖用户原图
                    resized.convert("RGB").save(
                        self._downscaled_path(image_path), quality=90
                    )
                    image_path = self._downscaled_path(image_path)
                    logger.info("Downscaled oversized image to %s", resized.size)
        except Exception as e:
            logger.warning("Image downscale skipped: %s", e)

        if not self.is_backend_available():
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail="Ollama服务未启动，图片功能不可用",
            )
        if not self.is_vision_available():
            raise ModelDegradedError(
                "vision model",
                fallback="图片OCR/识图不可用",
            )

        messages = [ChatMessage(role=Role.USER, content=prompt)]
        try:
            response = self.model_manager.chat_with_image(
                messages=messages,
                images=[str(image_path)],
                allow_degradation=False,
                max_tokens=max_tokens,
            )
            if response is None:
                raise ModelConnectionError(
                    self.model_manager.backend_type.value,
                    detail="图片识别返回为空",
                )
            return response.content.strip()
        except (ModelConnectionError, ModelDegradedError):
            raise
        except Exception as e:
            logger.error("VL inference failed: %s", e)
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail=f"图片识别失败：{e}",
            ) from e

    def extract_text(self, image_path: Path) -> str:
        """提取图片中的所有可见文字。"""
        return self._call_vl(image_path, _OCR_PROMPT, max_tokens=4096)

    def solve_image_question(
        self,
        image_path: Path,
        user_prompt: Optional[str] = None,
    ) -> str:
        """对图片题目进行解析和解答。"""
        prompt = user_prompt or (
            "请仔细观察图片中的题目，给出完整解析过程："
            "先说明题目考查的知识点，再给出详细步骤，最后总结答案。"
        )
        return self._call_vl(image_path, prompt, max_tokens=4096)

    def recognize_image(
        self,
        image_path: Path,
        source: OCRSource = OCRSource.FILE,
    ) -> str:
        """兼容旧接口：默认调用 VL 提取图片文字。"""
        return self.extract_text(image_path)

    def save_ocr_record(
        self,
        source_type: str,
        source_path: str,
        recognized_text: str,
        imported_error_ids: List[int],
        user_id: Optional[int] = None,
    ) -> int:
        """保存 OCR 识别记录，返回记录 ID。"""
        uid = self._user_id(user_id)
        record_id = self.db.insert(
            "INSERT INTO ocr_records (user_id, source_type, source_path, recognized_text, imported_error_ids) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, source_type, source_path, recognized_text, ",".join(map(str, imported_error_ids))),
        )
        return record_id

    def list_ocr_records(
        self,
        limit: int = 100,
        user_id: Optional[int] = None,
    ) -> List[dict]:
        """列出当前用户的 OCR 识别记录。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT * FROM ocr_records WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (uid, limit),
        )

    def batch_recognize(
        self,
        image_paths: List[Path],
        split_questions: bool = True,
        user_id: Optional[int] = None,
    ) -> List[dict]:
        """批量识别多张图片，可选自动切分为多道题目。

        返回每个识别结果字典，包含 image_path, raw_text, questions。
        questions 为切分后的题目列表（当 split_questions=True 时）。
        """
        results: List[dict] = []
        for image_path in image_paths:
            try:
                raw_text = self.extract_text(image_path)
            except Exception as e:
                logger.warning("Batch OCR failed for %s: %s", image_path, e)
                results.append({
                    "image_path": str(image_path),
                    "raw_text": "",
                    "questions": [],
                    "error": str(e),
                })
                continue

            questions: List[dict] = []
            if split_questions and raw_text.strip():
                try:
                    questions = self._split_questions(raw_text)
                except Exception as e:
                    logger.warning("Question split failed for %s: %s", image_path, e)
                    questions = [{"question": raw_text, "answer": "", "analysis": ""}]
            else:
                questions = [{"question": raw_text, "answer": "", "analysis": ""}]

            # 识别出的数学表达式自动补上 \( \) 标记并清理 OCR 噪声，
            # 后续展示与入库都交给全局公式渲染系统
            questions = [self._format_question(q) for q in questions]

            results.append({
                "image_path": str(image_path),
                "raw_text": raw_text,
                "questions": questions,
            })

            self.save_ocr_record(
                source_type="batch",
                source_path=str(image_path),
                recognized_text=raw_text,
                imported_error_ids=[],
                user_id=user_id,
            )
        return results

    @staticmethod
    def _format_question(question: dict) -> dict:
        """给 OCR 识别结果套上规范 LaTeX 标记（供全局公式渲染与入库）。

        只规整文本，不改变题目结构；任何异常都原样返回，绝不影响导入流程。
        """
        try:
            from .text_format_service import get_text_format_service

            formatter = get_text_format_service()
            formatted = dict(question)
            for field in ("question", "answer", "analysis"):
                value = formatted.get(field)
                if isinstance(value, str) and value.strip():
                    formatted[field] = formatter.format_text(value)
            return formatted
        except Exception as e:
            logger.warning("格式化 OCR 题目失败，保持原样: %s", e)
            return question

    def _split_questions(self, raw_text: str) -> List[dict]:
        """调用 LLM 将试卷 OCR 文本切分为多道题目。"""
        if not self.model_manager.is_text_available():
            return [{"question": raw_text, "answer": "", "analysis": ""}]

        prompt = f"{_BATCH_SPLIT_PROMPT}\n\n{raw_text}"
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        try:
            response = self.model_manager.chat(messages=messages, max_tokens=4096)
            if response is None:
                logger.warning("LLM question splitting returned empty response")
                return [{"question": raw_text, "answer": "", "analysis": ""}]
            return self._parse_question_blocks(response.content)
        except Exception as e:
            logger.warning("LLM question splitting failed: %s", e)
            return [{"question": raw_text, "answer": "", "analysis": ""}]

    @staticmethod
    def _parse_question_blocks(text: str) -> List[dict]:
        """从模型输出中解析题目块。"""
        blocks: List[dict] = []
        pattern = re.compile(
            r"【题目】\s*(.*?)\s*(?:【答案】\s*(.*?)\s*)?(?:【解析】\s*(.*?)\s*)?(?=【题目】|$)",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            question = match.group(1).strip()
            answer = (match.group(2) or "").strip()
            analysis = (match.group(3) or "").strip()
            if question:
                blocks.append({"question": question, "answer": answer, "analysis": analysis})
        if not blocks and text.strip():
            blocks.append({"question": text.strip(), "answer": "", "analysis": ""})
        return blocks
