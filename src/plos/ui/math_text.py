"""全局富文本 + LaTeX 混排渲染组件。

分层：
- :func:`build_math_document` —— 纯函数，文本(可含 Markdown) + LaTeX → HTML
- :class:`MathRenderTask` —— 后台线程执行的渲染任务（绝不阻塞 UI 主线程）
- :class:`MathTextBrowser` —— 只读展示控件（异步渲染 / 悬浮复制源码 / 整段原始复制 /
  主题联动 / 淡入），对外 API 与旧的 MarkdownBrowser 兼容
- :class:`MathLabel` —— 需要公式混排的 QLabel
- :func:`attach_live_preview` —— 给输入框挂一个实时预览区

性能取舍
--------
公式渲染本身是纯文本转换（毫秒级），真正的耗时在控件重新布局；即便如此，
所有渲染仍统一走后台线程池，并只在超过阈值时才显示“渲染中”指示器，
避免为了显示 1ms 的加载动画反而引入闪烁。
"""

from __future__ import annotations

import base64
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSize,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QStyledItemDelegate,
    QTextBrowser,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..utils.logger import get_logger
from ..utils.math_latex import (
    MATH_HREF_SCHEME,
    contains_math,
    escape_html,
    math_style,
    render_body,
)
from .ui_utils import get_theme_manager, is_dark_theme, set_clipboard_text

logger = get_logger("ui.math_text")

#: 渲染任务超过该时长才显示加载指示器（避免 1ms 的渲染也闪一下）
_LOADING_DELAY_MS = 180
#: 淡入时长（受全局动画策略限制，低配/闲置自动跳过）
_FADE_MS = 140


class MathRenderTask(QObject):
    """在后台线程把文本渲染为 HTML，结果通过信号回传。

    使用 :class:`plos.ui.workers.CallableWorker` 投递，保证不占用 UI 线程。

    注意：主题（深浅色）必须由调用方在**主线程**解析后传入——后台线程里
    读配置或取 QApplication.palette() 都属于跨线程访问 Qt，会直接崩溃。
    """

    def __init__(self, text: str, markdown: bool = True, font_px: int = 13, dark: bool = False):
        super().__init__()
        self.text = text
        self.markdown = markdown
        self.font_px = font_px
        self.dark = dark

    def run(self) -> str:
        return build_math_document(
            self.text, dark=self.dark, font_px=self.font_px, markdown=self.markdown
        )


# ----------------------------------------------------------------------
# 纯函数：文本 → HTML
# ----------------------------------------------------------------------

def _markdown_to_html(text: str) -> str:
    """用 QTextDocument 把 Markdown 转成 HTML body 片段。"""
    from PyQt6.QtGui import QTextDocument

    document = QTextDocument()
    document.setMarkdown(text)
    html = document.toHtml()
    start = html.find("<body")
    if start >= 0:
        start = html.find(">", start) + 1
        end = html.find("</body>", start)
        if end > start:
            return html[start:end]
    return html


def _math_placeholders(text: str):
    """把公式片段换成占位符，返回 (带占位符的文本, [(占位符, 公式HTML)])。"""
    from ..utils.math_latex import split_math_segments

    pieces = []
    slots = []
    for index, (kind, content, is_block) in enumerate(split_math_segments(text)):
        if kind == "text":
            pieces.append(content)
            continue
        key = f"@@PLOSMATH{index}@@"
        html = _formula_html(content, is_block)
        pieces.append(key)
        slots.append((key, html))
    return "".join(pieces), slots


def _formula_html(latex: str, is_block: bool) -> str:
    """公式 → 带锚点的 HTML；锚点携带 base64 源码，供悬浮提示与点击复制。"""
    from ..utils.math_latex import latex_to_html

    inner = latex_to_html(latex, block=is_block)
    payload = base64.urlsafe_b64encode(latex.encode("utf-8")).decode("ascii")
    if is_block:
        inner = f"<div class='mathblock'>{inner}</div>"
    return f"<a class='mathf' href='{MATH_HREF_SCHEME}:{payload}'>{inner}</a>"


def build_body(text: str, dark: bool, markdown: bool = True, with_style: bool = True) -> str:
    """渲染为可嵌入的 body 片段（用于对话气泡等已是 HTML 的场景）。"""
    colors = _colors_for(dark)
    if not text:
        return ""
    if not markdown:
        return render_body(text, colors["fg"], colors["math"])
    try:
        marked, slots = _math_placeholders(text)
        body = _markdown_to_html(marked)
        for key, html in slots:
            body = body.replace(key, html)
        if not _plain(body).strip() and text.strip():
            body = render_body(text, colors["fg"], colors["math"])
    except Exception as e:
        logger.warning("Markdown 渲染失败，回退纯文本: %s", e)
        body = render_body(text, colors["fg"], colors["math"])
    if with_style:
        return f"<style>{math_style(colors['fg'], colors['math'])}</style>{body}"
    return body


def build_math_document(text: str, dark: bool, font_px: int = 13, markdown: bool = True) -> str:
    """渲染为完整 HTML 文档（QTextBrowser.setHtml 用）。"""
    colors = _colors_for(dark)
    body = build_body(text, dark, markdown=markdown, with_style=False)
    return (
        "<html><head><style>"
        f"{math_style(colors['fg'], colors['math'], font_px)}"
        "</style></head><body>"
        f"{body}"
        "</body></html>"
    )


def _colors_for(dark: bool) -> dict:
    if dark:
        return {"fg": "#E9ECF4", "math": "#FFFFFF"}
    return {"fg": "#1A2030", "math": "#000000"}


def _plain(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html or "")


def decode_math_href(href: str) -> Optional[str]:
    """从锚点 href 还原 LaTeX 源码。"""
    prefix = f"{MATH_HREF_SCHEME}:"
    if not href or not href.startswith(prefix):
        return None
    try:
        payload = href[len(prefix):]
        return base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
    except Exception:
        return None


# ----------------------------------------------------------------------
# 只读展示控件
# ----------------------------------------------------------------------

class MathTextBrowser(QTextBrowser):
    """支持 Markdown + LaTeX 混排的只读展示控件。

    对外方法刻意与旧的 MarkdownBrowser 保持一致，便于就地替换。
    """

    raw_copied = pyqtSignal(str)   # 复制了原始文本（外层可据此弹 Toast）
    formula_copied = pyqtSignal(str)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        markdown: bool = True,
        font_px: int = 13,
        async_render: bool = True,
    ):
        super().__init__(parent)
        self._use_markdown = markdown
        self._font_px = font_px
        self._async_render = async_render
        self._raw_text = ""
        self._token = 0
        self._hover_href: Optional[str] = None
        self._fade_anim: Optional[QPropertyAnimation] = None
        self._fade_effect: Optional[QGraphicsOpacityEffect] = None
        self._loading_timer: Optional[QTimer] = None

        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.anchorClicked.connect(self._on_anchor_clicked)
        self.viewport().setMouseTracking(True)
        self._apply_default_stylesheet()

        self._loading_label = QLabel("渲染中…", self.viewport())
        self._loading_label.setVisible(False)
        self._loading_label.setStyleSheet(
            "background-color: rgba(120,120,120,0.16); border-radius: 8px;"
            " padding: 2px 8px; font-size: 11px;"
        )

        manager = get_theme_manager()
        if manager is not None:
            try:
                manager.theme_changed.connect(self._on_theme_changed)
            except Exception:
                pass

    # ---------------- 主题 ----------------

    def _apply_default_stylesheet(self) -> None:
        """把公式 CSS 设为文档默认样式，保证 append 的片段也能正确渲染。"""
        colors = _colors_for(is_dark_theme())
        try:
            self.document().setDefaultStyleSheet(
                math_style(colors["fg"], colors["math"], self._font_px)
            )
        except Exception:
            pass

    def _on_theme_changed(self, _theme: str = "") -> None:
        """主题切换后重新取色渲染，公式颜色同步变化。"""
        self._apply_default_stylesheet()
        if self._raw_text:
            self.set_rich_text(self._raw_text, markdown=self._use_markdown)

    # ---------------- 对外 API ----------------

    def set_rich_text(self, text: str, markdown: Optional[bool] = None) -> None:
        """设置混排文本（Markdown 可选）。"""
        self._raw_text = text or ""
        if markdown is not None:
            self._use_markdown = markdown
        if not self._raw_text.strip():
            self.clear()
            return
        self._token += 1
        token = self._token
        # 主题在主线程解析：后台线程不能碰 QApplication / 配置文件
        dark = is_dark_theme()
        self._start_loading_timer()
        if not self._async_render:
            self._on_rendered(token, MathRenderTask(
                self._raw_text, self._use_markdown, self._font_px, dark
            ).run())
            return
        from .workers import CallableWorker, ThreadPool

        task = MathRenderTask(self._raw_text, self._use_markdown, self._font_px, dark)
        worker = CallableWorker(task.run)
        worker.signals.result.connect(lambda html, t=token: self._on_rendered(t, html))
        worker.signals.error.connect(lambda msg, t=token: self._on_render_failed(t, msg))
        ThreadPool.start_worker(worker)

    def set_markdown(self, text: str) -> None:
        """兼容旧 API：Markdown + LaTeX。"""
        self.set_rich_text(text, markdown=True)

    def set_html(self, html: str) -> None:
        """直接设置 HTML。

        若 HTML 里含公式标记，则改走 Markdown 通路（Qt 的 Markdown 解析器会
        保留内联 HTML），公式同样能被渲染；不含公式时保持原有零开销路径。
        """
        if contains_math(html):
            self.set_rich_text(html, markdown=True)
            return
        self._raw_text = ""
        self._stop_loading()
        try:
            super().setHtml(html)
        except Exception as e:
            logger.warning("setHtml 失败: %s", e)

    def append_html(self, html: str) -> None:
        """追加 HTML 片段（对话流式气泡等），含公式时同样会渲染。"""
        try:
            if contains_math(html):
                self.append(build_body(html, is_dark_theme(), markdown=True, with_style=False))
                return
            self.append(html)
        except Exception as e:
            logger.warning("append_html 失败: %s", e)

    def append_markdown(self, text: str) -> None:
        """追加混排文本：退化为「拼接后整体重渲染」，保证公式与 Markdown 一致。"""
        self.set_rich_text(f"{self._raw_text}\n{text}" if self._raw_text else text)

    def set_plain_text(self, text: str) -> None:
        """设置纯文本（不做公式解析）。"""
        self._raw_text = text or ""
        self._stop_loading()
        super().setPlainText(self._raw_text)

    def render_markdown(self, text: str) -> str:
        """渲染为 HTML 字符串，不修改显示内容（供外层拼装气泡）。"""
        return build_math_document(text, is_dark_theme(), self._font_px, markdown=True)

    def render_markdown_body(self, text: str) -> str:
        """渲染为 body 片段（供嵌入自定义容器，如对话气泡）。"""
        return build_body(text, is_dark_theme(), markdown=True)

    def to_plain_text(self) -> str:
        return self.toPlainText()

    def raw_text(self) -> str:
        """当前显示的原始文本（含 LaTeX 标记）。"""
        return self._raw_text

    def set_placeholder_text(self, text: str) -> None:
        self.setPlaceholderText(text)

    def clear(self) -> None:
        self._raw_text = ""
        self._stop_loading()
        super().clear()

    # ---------------- 复制 ----------------

    def copy_raw(self) -> None:
        """复制整段原始文本（带 LaTeX 标记）。"""
        text = self._raw_text or self.toPlainText()
        if not text:
            return
        set_clipboard_text(text)
        self.raw_copied.emit(text)

    def copy(self) -> None:
        """有选区时复制选中的渲染文本；无选区时复制整段原始文本。"""
        try:
            if self.textCursor().hasSelection():
                super().copy()
                return
        except Exception:
            pass
        self.copy_raw()

    # ---------------- 渲染回调 ----------------

    def _on_rendered(self, token: int, html: str) -> None:
        # 回调是经 lambda 连接到工作线程的，不会随控件销毁自动断开：
        # 控件（例如刚打开就关闭的弹窗）已释放时这里会抛异常，槽里抛异常
        # 会让 PyQt 直接 abort 进程，因此整段兜底。
        try:
            if token != self._token:
                return  # 已有更新的渲染请求，丢弃过期结果
            self._stop_loading()
            try:
                super().setHtml(html or "")
            except Exception as e:
                logger.warning("公式渲染结果应用失败，回退纯文本: %s", e)
                super().setPlainText(self._raw_text)
            self._fade_in()
        except Exception as e:
            logger.debug("渲染结果已丢弃（控件可能已销毁）: %s", e)

    def _on_render_failed(self, token: int, message: str) -> None:
        try:
            if token != self._token:
                return
            self._stop_loading()
            logger.warning("后台渲染失败，回退纯文本: %s", message)
            try:
                super().setPlainText(self._raw_text)
            except Exception:
                pass
        except Exception as e:
            logger.debug("渲染失败回调已丢弃（控件可能已销毁）: %s", e)

    # ---------------- 加载指示器 / 淡入 ----------------

    def _start_loading_timer(self) -> None:
        """只有渲染确实慢时才显示指示器，避免快速渲染闪烁。"""
        if self._loading_timer is None:
            self._loading_timer = QTimer(self)
            self._loading_timer.setSingleShot(True)
            self._loading_timer.timeout.connect(self._show_loading)
        self._loading_timer.start(_LOADING_DELAY_MS)

    def _stop_loading(self) -> None:
        if self._loading_timer is not None:
            self._loading_timer.stop()
        self._loading_label.setVisible(False)

    def _show_loading(self) -> None:
        try:
            self._loading_label.adjustSize()
            self._loading_label.move(
                max(0, self.viewport().width() - self._loading_label.width() - 8), 6
            )
            self._loading_label.raise_()
            self._loading_label.setVisible(True)
        except Exception:
            pass

    def _fade_in(self) -> None:
        """渲染完成后淡入。

        QGraphicsOpacityEffect 的销毁时机必须谨慎：若在动画 finished 回调里
        直接 setGraphicsEffect(None)，会在动画自身收尾期间删除它仍持有的特效，
        造成 use-after-free（实测会让整个进程 access violation）。因此这里
        延迟到下一轮事件循环、并且用上下文对象绑定生命周期。
        """
        try:
            from .interactions import policy

            duration = policy().duration(_FADE_MS)
            if duration <= 0:
                self._dispose_fade_effect()
                return
            if self._fade_effect is None:
                self._fade_effect = QGraphicsOpacityEffect(self)
                self.setGraphicsEffect(self._fade_effect)
            effect = self._fade_effect
            if self._fade_anim is not None:
                self._fade_anim.stop()
            effect.setOpacity(0.4)
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(duration)
            animation.setStartValue(0.4)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            animation.finished.connect(self._schedule_fade_cleanup)
            self._fade_anim = animation
            animation.start()
        except Exception:
            self._dispose_fade_effect()

    def _schedule_fade_cleanup(self) -> None:
        """下一轮事件循环再摘掉特效（绑定 self，控件销毁则自动作废）。"""
        try:
            QTimer.singleShot(0, self, self._dispose_fade_effect)
        except TypeError:  # 兼容不支持 context 重载的环境
            self._dispose_fade_effect()

    def _dispose_fade_effect(self) -> None:
        try:
            if self._fade_effect is not None:
                effect = self._fade_effect
                self._fade_effect = None
                effect.setOpacity(1.0)
                self.setGraphicsEffect(None)
        except Exception:
            pass
        finally:
            self._fade_anim = None

    # ---------------- 悬浮提示 / 点击复制 ----------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        try:
            cursor = self.cursorForPosition(event.position().toPoint())
            href = cursor.charFormat().anchorHref()
        except Exception:
            href = ""
        if href and href.startswith(f"{MATH_HREF_SCHEME}:"):
            if href != self._hover_href:
                self._hover_href = href
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    "点击复制 LaTeX 源码",
                    self,
                )
        elif self._hover_href is not None:
            self._hover_href = None
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_href = None
        QToolTip.hideText()
        super().leaveEvent(event)

    def _on_anchor_clicked(self, url) -> None:
        latex = decode_math_href(url.toString())
        if latex is None:
            return
        set_clipboard_text(latex)
        self.formula_copied.emit(latex)


class MathLabel(QLabel):
    """支持公式混排的 QLabel（用于原本就是富文本标签的展示位）。"""

    def __init__(
        self,
        text: str = "",
        parent: Optional[QWidget] = None,
        *,
        markdown: bool = False,
    ):
        super().__init__(parent)
        self._markdown = markdown
        self._raw_text = ""
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.set_rich_text(text)

    def set_rich_text(self, text: str) -> None:
        """设置带公式的文本；渲染失败时回退为转义纯文本。"""
        self._raw_text = text or ""
        try:
            body = build_body(self._raw_text, is_dark_theme(), markdown=self._markdown)
            super().setText(body or escape_html(self._raw_text))
        except Exception as e:
            logger.warning("公式标签渲染失败，回退纯文本: %s", e)
            super().setText(escape_html(self._raw_text))

    def set_markdown(self, text: str) -> None:
        self._markdown = True
        self.set_rich_text(text)

    def raw_text(self) -> str:
        return self._raw_text

    def setPlainText(self, text: str) -> None:  # noqa: N802 - 兼容 QLabel 语义
        self.set_rich_text(text)

    def on_theme_changed(self) -> None:
        """主题切换后重新取色（由面板的 on_theme_changed 调用）。"""
        if self._raw_text:
            self.set_rich_text(self._raw_text)


# ----------------------------------------------------------------------
# 输入框实时预览
# ----------------------------------------------------------------------

def attach_live_preview(
    source: QTextEdit,
    preview: MathTextBrowser,
    *,
    debounce_ms: int = 260,
) -> QObject:
    """给输入框挂实时预览：停止输入后再渲染，避免每敲一个字都重排。

    返回一个连接器对象，需由调用方持有引用（否则会被回收）。
    """

    class _Connector(QObject):
        def __init__(self) -> None:
            super().__init__(source)
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(debounce_ms)
            self._timer.timeout.connect(self._refresh)
            source.textChanged.connect(self._timer.start)

        def _refresh(self) -> None:
            try:
                preview.set_rich_text(source.toPlainText(), markdown=True)
            except Exception as e:
                logger.warning("预览渲染失败: %s", e)

    return _Connector()


def make_format_button(sources, parent: Optional[QWidget] = None, *, label: str = "一键格式化"):
    """创建「一键格式化」按钮：清理 OCR 噪声 + 修复数学符号 + 补公式标记。

    sources 可以是单个 QTextEdit，也可以是列表（如错题本的题目/答案/解析），
    格式化结果会就地写回，供全局公式渲染系统使用。
    """
    from PyQt6.QtWidgets import QPushButton

    targets = list(sources) if isinstance(sources, (list, tuple)) else [sources]
    button = QPushButton(label, parent)
    button.setToolTip(
        "清理 OCR 乱码、修复数学符号、自动为算式补上 \\( \\) 公式标记"
    )

    def _format() -> None:
        try:
            from ..services.text_format_service import get_text_format_service

            formatter = get_text_format_service()
            for widget in targets:
                if widget is None:
                    continue
                original = widget.toPlainText()
                if not original.strip():
                    continue
                cleaned = formatter.format_text(original)
                if cleaned != original:
                    widget.setPlainText(cleaned)
        except Exception as e:
            logger.warning("一键格式化失败: %s", e)

    button.clicked.connect(_format)
    return button


def build_preview_panel(source: QTextEdit, parent: Optional[QWidget] = None):
    """构造「输入框 + 预览区」容器，返回 (容器, 预览控件, 连接器)。"""
    holder = QWidget(parent)
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addWidget(source)
    caption = QLabel("公式预览")
    caption.setObjectName("card_subtitle")
    layout.addWidget(caption)
    preview = MathTextBrowser(holder)
    preview.setMinimumHeight(80)
    preview.setMaximumHeight(160)
    preview.setPlaceholderText("输入 \\(公式\\) 或 \\[公式\\] 后可在此预览")
    layout.addWidget(preview)
    return holder, preview, attach_live_preview(source, preview)


# ----------------------------------------------------------------------
# 树 / 列表单元格的公式渲染
# ----------------------------------------------------------------------

class MathItemDelegate(QStyledItemDelegate):
    """让 QTreeWidget / QListWidget 的单元格支持富文本 + 公式混排。

    QTreeWidgetItem 本身不支持富文本，所以这里用一个委托：命中公式的单元格
    改用 QTextDocument 绘制；不含公式的单元格完全走 Qt 原生绘制，
    不引入任何额外开销。文档按「文本 + 列宽 + 主题」缓存，滚动时不重复排版。
    """

    def __init__(self, parent: Optional[QObject] = None, *, font_px: int = 12):
        super().__init__(parent)
        self._font_px = font_px
        self._cache: dict = {}
        self._cache_limit = 240

    def _document(self, text: str, width: int, base_font) -> QTextDocument:
        dark = is_dark_theme()
        key = (hash(text), width, dark)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        colors = _colors_for(dark)
        document = QTextDocument()
        document.setDefaultFont(base_font)
        document.setDefaultStyleSheet(
            math_style(colors["fg"], colors["math"], self._font_px)
        )
        document.setHtml(
            build_body(text, dark, markdown=False, with_style=False)
        )
        document.setTextWidth(width)
        if len(self._cache) >= self._cache_limit:
            self._cache.clear()
        self._cache[key] = document
        return document

    def _layout_width(self, option, index) -> int:
        """公式文档的排版宽度。

        控件开启自动换行时按真实列宽折行（行高随之增大）；未开启换行时给一个
        足够大的宽度，让公式单行绘制、由控件裁剪 —— 与原控件行为保持一致。
        """
        view = self.parent()
        try:
            if not view.wordWrap():
                return 100000
        except Exception:
            pass
        hint = index.data(Qt.ItemDataRole.UserRole + 100)
        if hint:
            try:
                return max(40, int(hint))
            except (TypeError, ValueError):
                pass
        try:
            width = int(view.columnWidth(index.column()))
        except Exception:
            width = 0
        if width > 0:
            return width
        return max(40, int(option.rect.width()) or 480)

    def paint(self, painter, option, index) -> None:  # noqa: N802
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not contains_math(text):
            super().paint(painter, option, index)
            return
        try:
            from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""  # 文字交给 QTextDocument 画，避免与原生绘制重叠
            style = option.widget.style() if option.widget else QApplication.style()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)

            # 文字区域由样式算出来，才能避开复选框与树形缩进
            text_rect = style.subElementRect(
                QStyle.SubElement.SE_ItemViewItemText, opt, option.widget
            )
            if not text_rect.isValid():
                text_rect = option.rect
            if text_rect.width() <= 0 or text_rect.height() <= 0:
                return
            document = self._document(text, self._layout_width(option, index), option.font)
            painter.save()
            painter.setClipRect(option.rect)
            painter.translate(text_rect.left(), text_rect.top() + 2)
            document.drawContents(painter)
            painter.restore()
        except Exception as e:
            logger.warning("公式单元格渲染失败，回退原生绘制: %s", e)
            super().paint(painter, option, index)

    def sizeHint(self, option, index) -> "QSize":  # noqa: N802
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not contains_math(text):
            return super().sizeHint(option, index)
        try:
            width = self._layout_width(option, index)
            document = self._document(text, width, option.font)
            hint = super().sizeHint(option, index)
            height = int(document.size().height()) + 6
            return QSize(hint.width(), max(hint.height(), height))
        except Exception:
            return super().sizeHint(option, index)


def attach_math_delegate(view, column: int = 0, *, font_px: int = 12) -> MathItemDelegate:
    """给树/列表的某一列挂上公式渲染委托，返回委托对象（需由调用方持有引用）。"""
    delegate = MathItemDelegate(view, font_px=font_px)
    view.setItemDelegateForColumn(column, delegate)
    return delegate


__all__ = [
    "MathItemDelegate",
    "MathLabel",
    "MathRenderTask",
    "MathTextBrowser",
    "attach_live_preview",
    "attach_math_delegate",
    "build_body",
    "build_math_document",
    "build_preview_panel",
    "decode_math_href",
    "make_format_button",
]
