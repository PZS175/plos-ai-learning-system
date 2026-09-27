"""多模态输入路由。

统一入口处理图片、截图、剪贴板图片、PDF、文档等输入，
根据输入类型分发到 OCR、拍照搜题或文档入库流程。
"""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path
from typing import Optional

from ..ai.multimodal import DocumentParser
from ..core.enums import OCRSource
from ..utils.exceptions import DocumentParseError
from ..utils.logger import get_logger
from .ocr_service import OCRService
from .rag_service import RAGService

logger = get_logger("services.input_router")


class InputType(Enum):
    """支持的输入类型。"""

    IMAGE_OCR = auto()       # 普通图片，走 OCR
    SCREENSHOT = auto()      # 截图，走 OCR
    CLIPBOARD_IMAGE = auto() # 剪贴板图片，走 OCR
    PHOTO_SOLVE = auto()     # 拍照搜题，走 VL
    DOCUMENT = auto()        # PDF/DOCX/TXT，走文档解析入库或 OCR
    UNKNOWN = auto()


class InputRouter:
    """统一多模态输入路由。"""

    def __init__(
        self,
        ocr_service: OCRService,
        rag_service: Optional[RAGService] = None,
    ):
        self.ocr_service = ocr_service
        self.rag_service = rag_service
        self.parser = DocumentParser()

    def classify(self, file_path: Path, intent: str = "ocr") -> InputType:
        """根据文件类型和用户意图判断输入类型。"""
        ext = file_path.suffix.lower()
        if intent == "solve":
            return InputType.PHOTO_SOLVE
        if ext in DocumentParser.SUPPORTED_PDF | DocumentParser.SUPPORTED_DOCX | DocumentParser.SUPPORTED_TEXT:
            return InputType.DOCUMENT
        if ext in DocumentParser.SUPPORTED_IMAGE:
            if intent == "ocr":
                return InputType.IMAGE_OCR
            return InputType.PHOTO_SOLVE
        return InputType.UNKNOWN

    def handle_ocr(self, file_path: Path, source: OCRSource = OCRSource.FILE) -> str:
        """处理 OCR 请求：使用 VL 提取图片文字。"""
        return self.ocr_service.extract_text(file_path)

    def handle_photo_solve(self, file_path: Path) -> str:
        """处理拍照搜题请求：使用 VL 解析题目。"""
        return self.ocr_service.solve_image_question(file_path)

    def handle_document(self, file_path: Path, add_to_knowledge: bool = True) -> dict:
        """处理文档：解析文本，可选择入库。"""
        if not DocumentParser.is_supported(file_path):
            raise DocumentParseError(f"不支持的文件类型：{file_path.suffix}")

        text, images, page_count = self.parser.parse(file_path)
        result = {
            "file_path": str(file_path),
            "page_count": page_count,
            "text_preview": text[:1000],
            "image_count": len(images),
            "document_id": None,
        }

        if add_to_knowledge and self.rag_service is not None:
            document_id = self.rag_service.add_document(file_path)
            result["document_id"] = document_id

        return result
