"""Screenshot and clipboard capture utilities using PyQt6.

Provides region screenshot overlay and clipboard image retrieval.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import QApplication, QWidget

from ...utils.logger import get_logger

logger = get_logger("multimodal.screenshot")


class ScreenshotOverlay(QWidget):
    """Full-screen overlay for selecting a screenshot region."""

    def __init__(self, on_captured: Callable[[Path], None]):
        super().__init__()
        self.on_captured = on_captured
        self.start_pos: Optional[QPoint] = None
        self.end_pos: Optional[QPoint] = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setWindowState(Qt.WindowState.WindowFullScreen)

        # Esc cancels
        QShortcut(QKeySequence("Esc"), self, activated=self.close)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Semi-transparent dark overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))
        if self.start_pos and self.end_pos:
            rect = QRect(self.start_pos, self.end_pos).normalized()
            painter.setPen(QPen(QColor(0, 150, 255), 2, Qt.PenStyle.SolidLine))
            painter.drawRect(rect)
            painter.fillRect(rect, QColor(255, 255, 255, 30))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.pos()
            self.end_pos = event.pos()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self.start_pos:
            self.end_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.start_pos and self.end_pos:
            self.end_pos = event.pos()
            self.capture_region()
            self.close()

    def capture_region(self) -> None:
        rect = QRect(self.start_pos, self.end_pos).normalized()
        if rect.width() < 10 or rect.height() < 10:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        if pixmap.isNull():
            return
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            save_path = Path(tmp.name)
        pixmap.save(str(save_path), "PNG")
        logger.info("Screenshot saved to %s", save_path)
        self.on_captured(save_path)


class ScreenshotCapture:
    """Entry point for screenshot and clipboard capture."""

    def __init__(self, on_captured: Optional[Callable[[Path], None]] = None):
        self.on_captured = on_captured

    def capture_region(self, callback: Optional[Callable[[Path], None]] = None) -> None:
        """Show region selection overlay."""
        cb = callback or self.on_captured
        if cb is None:
            raise ValueError("callback is required")
        overlay = ScreenshotOverlay(on_captured=cb)
        overlay.show()

    @staticmethod
    def get_clipboard_image() -> Optional[Path]:
        """Get image from clipboard, save to temp file, return path."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return None
        mime = clipboard.mimeData()
        if mime is None or not mime.hasImage():
            return None
        pixmap = clipboard.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            save_path = Path(tmp.name)
        pixmap.save(str(save_path), "PNG")
        logger.info("Clipboard image saved to %s", save_path)
        return save_path

    @staticmethod
    def has_clipboard_image() -> bool:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        mime = clipboard.mimeData()
        return mime is not None and mime.hasImage()
