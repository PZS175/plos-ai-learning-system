"""Markdown + LaTeX 混排渲染组件（兼容层）。

历史上这里是「QTextBrowser + matplotlib 渲染公式图片」的实现，存在两个硬伤：
matplotlib 被排除在打包之外（打包后公式会退化成源码），且渲染在主线程同步执行会卡界面。

现在统一由 :mod:`plos.ui.math_text` 提供实现：
- 公式解析/渲染全部在后台线程池执行，UI 不阻塞
- 不依赖 matplotlib，打包后可用
- 公式颜色跟随深浅主题，悬浮提示 + 点击复制源码
- 对外保留原 API，并把 ``browser`` 暴露为自身，兼容既有的
  ``display.browser.xxx`` 调用（滚动条、右键菜单、选区等）
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QWidget

from ..utils.logger import get_logger
from .math_text import MathTextBrowser

logger = get_logger("ui.markdown_browser")


class MarkdownBrowser(MathTextBrowser):
    """支持 Markdown 与 LaTeX 混排的富文本展示控件。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, markdown=True, font_px=13)

    @property
    def browser(self) -> "MarkdownBrowser":
        """兼容旧接口：原先这里是内部 QTextBrowser，现在控件本身就是浏览器。

        这样 ``panel.display.browser.setOpenExternalLinks(...)``、
        ``.browser.viewport()``、``.browser.textCursor()`` 等既有调用无需改动。
        """
        return self


__all__ = ["MarkdownBrowser"]
