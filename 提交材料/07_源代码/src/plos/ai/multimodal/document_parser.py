"""Document parser supporting PDF, DOCX, TXT, and images.

Extracts text and optionally renders pages/images.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz  # PyMuPDF
from docx import Document as DocxDocument

from ...utils.exceptions import DocumentParseError
from ...utils.logger import get_logger

logger = get_logger("multimodal.document_parser")


def _apply_overlap(paragraphs: List[str], overlap: int) -> Tuple[List[str], int]:
    """为下一块保留末尾若干段落作为重叠内容。"""
    overlap_paras: List[str] = []
    length = 0
    for para in reversed(paragraphs):
        if length + len(para) + 2 > overlap and overlap_paras:
            break
        overlap_paras.insert(0, para)
        length += len(para) + 2
    return overlap_paras, length


class DocumentParser:
    """Parser for PDF, DOCX, TXT, and image files."""

    SUPPORTED_TEXT = {".txt", ".md", ".markdown", ".text"}
    SUPPORTED_PDF = {".pdf"}
    SUPPORTED_DOCX = {".docx"}
    SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

    def parse(self, file_path: Path) -> Tuple[str, List[Path], int]:
        """Parse a document and return (text, page_images, page_count).

        Args:
            file_path: Path to document.

        Returns:
            Tuple of full text, list of page image paths, page count.
        """
        ext = file_path.suffix.lower()
        if ext in self.SUPPORTED_PDF:
            return self._parse_pdf(file_path)
        elif ext in self.SUPPORTED_DOCX:
            return self._parse_docx(file_path)
        elif ext in self.SUPPORTED_TEXT:
            return self._parse_text(file_path)
        elif ext in self.SUPPORTED_IMAGE:
            return self._parse_image(file_path)
        else:
            raise DocumentParseError(f"Unsupported file type: {ext}")

    def _parse_pdf(self, file_path: Path) -> Tuple[str, List[Path], int]:
        try:
            doc = fitz.open(str(file_path))
        except Exception as e:
            raise DocumentParseError(f"Failed to open PDF: {e}") from e

        texts: List[str] = []
        images: List[Path] = []
        for i, page in enumerate(doc):
            texts.append(f"\n--- Page {i + 1} ---\n")
            texts.append(page.get_text())
            pix = page.get_pixmap(dpi=200)
            img_path = Path(tempfile.gettempdir()) / f"plos_pdf_{file_path.stem}_{i}.png"
            pix.save(str(img_path))
            images.append(img_path)
        doc.close()
        logger.info("Parsed PDF %s: %d pages", file_path, len(images))
        return "\n".join(texts), images, len(images)

    def _parse_docx(self, file_path: Path) -> Tuple[str, List[Path], int]:
        try:
            doc = DocxDocument(str(file_path))
        except Exception as e:
            raise DocumentParseError(f"Failed to open DOCX: {e}") from e
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)
        # DOCX -> single-page text for now
        return text, [], 1

    def _parse_text(self, file_path: Path) -> Tuple[str, List[Path], int]:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            raise DocumentParseError(f"Failed to read text file: {e}") from e
        return text, [], 1

    def _parse_image(self, file_path: Path) -> Tuple[str, List[Path], int]:
        return "", [file_path], 1

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        """Split text into overlapping chunks."""
        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            if end >= len(text):
                break
            start = end - overlap
        return chunks

    def chunk_pdf_by_toc(
        self,
        file_path: Path,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> List[Dict[str, Any]]:
        """基于 PDF 目录（TOC）的教材智能分块。

        优先按章节标题切分，不切断段落；超长章节再按段落二次细分。
        无目录的 PDF 自动降级为普通字符切分。
        每个分块携带章节名称、层级和起始页码，便于 RAG 精准定位。
        """
        ext = file_path.suffix.lower()
        if ext not in self.SUPPORTED_PDF:
            raise DocumentParseError(f"仅支持 PDF 文件：{ext}")

        try:
            doc = fitz.open(str(file_path))
        except Exception as e:
            raise DocumentParseError(f"Failed to open PDF: {e}") from e

        toc = doc.get_toc()
        if not toc:
            doc.close()
            # 无目录时降级为普通切分
            text, _, _ = self._parse_pdf(file_path)
            return [
                {
                    "content": chunk,
                    "chapter": "",
                    "level": 0,
                    "page_num": 0,
                }
                for chunk in self.chunk_text(text, chunk_size, overlap)
            ]

        chunks: List[Dict[str, Any]] = []
        for i, entry in enumerate(toc):
            if len(entry) < 3:
                logger.warning("Skipping malformed TOC entry: %s", entry)
                continue
            try:
                level, title, start_page = entry[0], entry[1], int(entry[2])
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse TOC entry %s: %s", entry, e)
                continue
            try:
                end_page = int(toc[i + 1][2]) if (i + 1 < len(toc) and len(toc[i + 1]) >= 3) else len(doc)
            except (ValueError, TypeError):
                end_page = len(doc)
            # fitz 页码从 0 开始，TOC 页码通常从 1 开始
            start_idx = max(0, start_page - 1)
            # 边界：本章结束于下一章起始页的前一页；相邻条目同页时至少保留本章起始页
            end_idx = max(start_idx + 1, end_page - 1)
            end_idx = min(end_idx, len(doc))

            chapter_texts: List[str] = []
            for page_idx in range(start_idx, min(end_idx, len(doc))):
                page = doc[page_idx]
                chapter_texts.append(page.get_text())
            chapter_text = "\n".join(chapter_texts).strip()
            if not chapter_text:
                continue

            if len(chapter_text) <= chunk_size:
                chunks.append(
                    {
                        "content": chapter_text,
                        "chapter": title,
                        "level": level,
                        "page_num": start_page,
                    }
                )
            else:
                sub_chunks = self._split_by_paragraphs(
                    chapter_text, chunk_size, overlap
                )
                for sub_idx, sub_text in enumerate(sub_chunks):
                    chunks.append(
                        {
                            "content": sub_text,
                            "chapter": title,
                            "level": level,
                            "page_num": start_page,
                        }
                    )

        doc.close()
        logger.info(
            "PDF chunked by TOC: %s -> %d chunks", file_path.name, len(chunks)
        )
        return chunks if chunks else []

    @staticmethod
    def _split_by_paragraphs(
        text: str, chunk_size: int, overlap: int
    ) -> List[str]:
        """按段落边界切分文本，尽量避免切断段落。"""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        chunks: List[str] = []
        current = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para)
            # 单段超长时按句子再切分
            if para_len > chunk_size:
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_len = _apply_overlap(current, overlap)
                chunks.extend(
                    DocumentParser._split_oversized(para, chunk_size, overlap)
                )
                continue

            if current_len + para_len + 2 > chunk_size and current:
                chunks.append("\n\n".join(current))
                current, current_len = _apply_overlap(current, overlap)

            current.append(para)
            current_len += para_len + 2

        if current:
            chunks.append("\n\n".join(current))

        return chunks

    @staticmethod
    def _split_oversized(text: str, chunk_size: int, overlap: int) -> List[str]:
        """对超长段落按句子边界切分。"""
        sentences = re.split(r"(?<=[。\.\?\!])\s+", text)
        chunks: List[str] = []
        current = []
        current_len = 0

        for sent in sentences:
            sent_len = len(sent)
            if current_len + sent_len + 1 > chunk_size and current:
                chunks.append(" ".join(current))
                current, current_len = _apply_overlap(current, overlap)
            current.append(sent)
            current_len += sent_len + 1

        if current:
            chunks.append(" ".join(current))
        return chunks

    @staticmethod
    def is_supported(file_path: Path) -> bool:
        ext = file_path.suffix.lower()
        return ext in (
            DocumentParser.SUPPORTED_TEXT
            | DocumentParser.SUPPORTED_PDF
            | DocumentParser.SUPPORTED_DOCX
            | DocumentParser.SUPPORTED_IMAGE
        )
