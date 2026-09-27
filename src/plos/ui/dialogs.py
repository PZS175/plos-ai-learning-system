"""跨面板共享的通用对话框。

从错题本与知识库面板中抽取的重复实现，统一维护避免行为漂移。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .math_text import MathTextBrowser, build_body
from .ui_utils import is_dark_theme


def _math_fragment(text: str) -> str:
    """把用户文本内嵌进手工拼装的 HTML 时，先把其中的公式渲染好（不解析 Markdown）。"""
    return build_body(text or "", is_dark_theme(), markdown=False, with_style=False)


class AnnotationDialog(QDialog):
    """添加/编辑批注对话框。"""

    def __init__(self, selected_text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("添加批注")
        self.resize(420, 220)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("选中片段："))
        preview = QTextEdit()
        preview.setPlainText(selected_text)
        preview.setReadOnly(True)
        preview.setMaximumHeight(80)
        layout.addWidget(preview)

        layout.addWidget(QLabel("批注内容："))
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("输入你的批注笔记...")
        layout.addWidget(self.note_edit)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def note(self) -> str:
        return self.note_edit.toPlainText().strip()


class MindMapViewerDialog(QDialog):
    """简单的思维导图查看对话框。"""

    def __init__(self, title: str, graph: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"思维导图 - {title}")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        browser = MathTextBrowser()

        lines = [f"<h2>{title}</h2>"]

        def _render(node: dict, depth: int = 0) -> None:
            text = node.get("text", "")
            if text:
                tag = "h3" if depth == 0 else "div"
                indent = "&nbsp;" * (depth * 4)
                lines.append(f"<{tag}>{indent}• {_math_fragment(text)}</{tag}>")
            for child in node.get("children", []):
                _render(child, depth + 1)

        root = graph.get("root", {})
        _render(root)
        browser.setHtml("".join(lines))
        layout.addWidget(browser)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
