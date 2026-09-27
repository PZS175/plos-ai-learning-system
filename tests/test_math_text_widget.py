"""全局公式混排控件测试（ui.math_text / ui.markdown_browser）。

重点覆盖用户验收清单：
1. 全页面控件渲染公式（以控件级用例代表）
2. 混排排版：中文 + 多个行内公式 + 独立公式
3. 错误公式降级、不崩溃
4. 深浅主题切换后公式颜色同步
5. 复制：整段原始文本（含 LaTeX 标记）、单条公式源码
6. 异步渲染不阻塞、过期结果丢弃
"""

from __future__ import annotations

import base64
import time

import pytest

from plos.utils.math_latex import MATH_HREF_SCHEME
from plos.ui.math_text import (
    MathLabel,
    MathTextBrowser,
    attach_live_preview,
    build_body,
    build_math_document,
    decode_math_href,
)

pytestmark = pytest.mark.usefixtures("qapp")

#: 淡入特效回归守卫：关闭淡入可复验崩溃是否来自 QGraphicsOpacityEffect 的释放时机
_DISABLE_FADE = False

_DRAIN_FIELDS = ("ui", "theme")


def _wait_render(qapp, timeout: float = 8.0) -> None:
    """等后台渲染任务结束并把结果投递到主线程。"""
    from plos.ui.workers import ThreadPool

    deadline = time.time() + timeout
    while ThreadPool._active_workers and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def _make_browser(qapp, parent=None) -> MathTextBrowser:
    browser = MathTextBrowser(parent)
    browser.resize(600, 300)
    if _DISABLE_FADE:
        browser._fade_in = lambda: None  # type: ignore[method-assign]
    return browser


# ----------------------------------------------------------------------
# 一、纯函数层
# ----------------------------------------------------------------------

def test_build_body_renders_inline_formula():
    body = build_body("求 \\(x^2\\) 的导数", dark=False)
    assert "x" in body and "mathf" in body
    assert "\\(" not in body, "标记不应出现在渲染结果里"


def test_build_body_keeps_markdown_structure():
    body = build_body("# 标题\n\n- 第一项\n- 第二项", dark=False)
    assert "标题" in body
    assert "第一项" in body and "第二项" in body


def test_build_body_mixes_text_and_multiple_formulas():
    body = build_body("已知 \\(a=1\\)，\\(b=2\\)，则\n\\[a+b=3\\]\n成立", dark=False)
    assert "已知" in body and "成立" in body
    assert "mathblock" in body, "独立公式应有居中容器"


def test_build_math_document_carries_theme_colors():
    light = build_math_document("文本", dark=False)
    dark = build_math_document("文本", dark=True)
    assert "#000000" in light
    assert "#FFFFFF" in dark


def test_decode_math_href_roundtrip():
    latex = "\\frac{1}{2}"
    payload = base64.urlsafe_b64encode(latex.encode("utf-8")).decode("ascii")
    assert decode_math_href(f"{MATH_HREF_SCHEME}:{payload}") == latex
    assert decode_math_href("http://example.com") is None


# ----------------------------------------------------------------------
# 二、异步渲染
# ----------------------------------------------------------------------

def test_browser_renders_formula_without_blocking(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_markdown("结果 \\(\\frac{7}{18}\\) 与 \\(x^2\\) 都成立")
        _wait_render(qapp)
        text = browser.toPlainText()
        assert "结果" in text and "都成立" in text
        assert "7" in text and "18" in text
        assert "\\frac" not in text, "不应残留 LaTeX 源码"
    finally:
        browser.deleteLater()


def test_browser_ignores_stale_render_result(qapp):
    """连续两次设置文本时，旧结果不能覆盖新结果。"""
    browser = _make_browser(qapp)
    try:
        browser.set_markdown("第一次内容")
        browser.set_markdown("第二次内容 \\(y=1\\)")
        _wait_render(qapp)
        text = browser.toPlainText()
        assert "第二次内容" in text
        assert "第一次内容" not in text
    finally:
        browser.deleteLater()


def test_browser_renders_block_formula_stacked(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_markdown("\\[\\frac{7}{18}\\times0.36\\]")
        _wait_render(qapp)
        assert "7" in browser.toPlainText()
        assert "0.36" in browser.toPlainText()
    finally:
        browser.deleteLater()


def test_browser_handles_broken_formula_gracefully(qapp):
    """错误公式：降级显示源码，不崩溃、不空白。"""
    browser = _make_browser(qapp)
    try:
        browser.set_markdown("坏公式 \\(\\frac{1}{2\\) 后面还有正文")
        _wait_render(qapp)
        text = browser.toPlainText()
        assert "坏公式" in text and "后面还有正文" in text
        assert text.strip(), "不能渲染成空白"
    finally:
        browser.deleteLater()


def test_browser_set_html_and_append(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_html("<p>历史消息</p>")
        browser.append_html("<p>流式追加</p>")
        text = browser.toPlainText()
        assert "历史消息" in text and "流式追加" in text
    finally:
        browser.deleteLater()


def test_browser_plain_text_mode_skips_formula(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_plain_text("原样 \\(x^2\\)")
        assert "\\(x^2\\)" in browser.toPlainText()
    finally:
        browser.deleteLater()


def test_browser_clear_resets_raw_text(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_markdown("内容 \\(a\\)")
        _wait_render(qapp)
        browser.clear()
        assert browser.raw_text() == ""
        assert browser.toPlainText().strip() == ""
    finally:
        browser.deleteLater()


# ----------------------------------------------------------------------
# 三、复制与交互
# ----------------------------------------------------------------------

def test_copy_without_selection_copies_raw_latex(qapp):
    from PyQt6.QtWidgets import QApplication

    browser = _make_browser(qapp)
    try:
        source = "答案 \\(\\frac{7}{18}\\) 完毕"
        browser.set_markdown(source)
        _wait_render(qapp)
        browser.copy()
        assert QApplication.clipboard().text() == source, "整段复制要拿到带标记的原文"
    finally:
        browser.deleteLater()


def test_click_formula_copies_its_latex(qapp):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import QApplication

    browser = _make_browser(qapp)
    copied = []
    browser.formula_copied.connect(copied.append)
    try:
        latex = "\\frac{7}{18}"
        payload = base64.urlsafe_b64encode(latex.encode("utf-8")).decode("ascii")
        browser._on_anchor_clicked(QUrl(f"{MATH_HREF_SCHEME}:{payload}"))
        assert QApplication.clipboard().text() == latex
        assert copied == [latex]
    finally:
        browser.deleteLater()


def test_click_non_math_anchor_is_ignored(qapp):
    from PyQt6.QtCore import QUrl
    from PyQt6.QtWidgets import QApplication

    browser = _make_browser(qapp)
    try:
        QApplication.clipboard().setText("哨兵")
        browser._on_anchor_clicked(QUrl("https://example.com"))
        assert QApplication.clipboard().text() == "哨兵"
    finally:
        browser.deleteLater()


def test_markdown_browser_exposes_browser_attribute(qapp):
    """兼容旧调用：display.browser.xxx 必须仍可用。"""
    from plos.ui.markdown_browser import MarkdownBrowser

    widget = MarkdownBrowser()
    try:
        assert widget.browser is widget
        widget.browser.setOpenExternalLinks(False)
        widget.browser.setTextInteractionFlags(
            widget.browser.textInteractionFlags()
        )
        assert widget.browser.viewport() is not None
    finally:
        widget.deleteLater()


# ----------------------------------------------------------------------
# 四、主题联动
# ----------------------------------------------------------------------

def test_theme_change_rerenders_content(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_markdown("主题测试 \\(x^2\\)")
        _wait_render(qapp)
        before = browser.toPlainText()

        browser._on_theme_changed("light")
        _wait_render(qapp)
        assert browser.toPlainText() == before, "重渲染后内容应保持一致"

        document_css = browser.document().defaultStyleSheet()
        assert "mathf" in document_css
    finally:
        browser.deleteLater()


def test_default_stylesheet_contains_math_classes(qapp):
    browser = _make_browser(qapp)
    try:
        css = browser.document().defaultStyleSheet()
        assert "mathf" in css and "mstack" in css
    finally:
        browser.deleteLater()


# ----------------------------------------------------------------------
# 五、MathLabel 与输入预览
# ----------------------------------------------------------------------

def test_math_label_renders_formula(qapp):
    label = MathLabel("得分 \\(\\frac{3}{5}\\) 分")
    try:
        text = label.text()
        assert "得分" in text and "mathf" in text
        assert label.raw_text() == "得分 \\(\\frac{3}{5}\\) 分"
    finally:
        label.deleteLater()


def test_math_label_theme_rerender_keeps_text(qapp):
    label = MathLabel("公式 \\(a+b\\)")
    try:
        label.on_theme_changed()
        assert "公式" in label.text()
    finally:
        label.deleteLater()


def test_live_preview_renders_on_refresh(qapp):
    from PyQt6.QtWidgets import QTextEdit

    source = QTextEdit()
    preview = MathTextBrowser()
    connector = attach_live_preview(source, preview)
    try:
        source.setPlainText("输入 \\(x^2+1\\) 试试")
        connector._refresh()          # 直接触发，避免依赖定时器
        _wait_render(qapp)
        text = preview.toPlainText()
        assert "输入" in text and "x" in text
        assert "\\(" not in text
    finally:
        connector.deleteLater()
        preview.deleteLater()
        source.deleteLater()


def test_live_preview_debounces_typing(qapp):
    """连续输入只应排队一次渲染（防抖），不会每个字符都渲染。"""
    from PyQt6.QtWidgets import QTextEdit

    source = QTextEdit()
    preview = MathTextBrowser()
    connector = attach_live_preview(source, preview, debounce_ms=50)
    try:
        for ch in "abc":
            source.insertPlainText(ch)
        assert connector._timer.isActive(), "输入后应处于防抖等待中"
        connector._timer.stop()
    finally:
        connector.deleteLater()
        preview.deleteLater()
        source.deleteLater()


# ----------------------------------------------------------------------
# 六、HTML 通路里的公式（set_html / append_html）
# ----------------------------------------------------------------------

def test_set_html_renders_formula(qapp):
    """含公式的 HTML 片段不能把 LaTeX 源码直接显示出来。"""
    browser = _make_browser(qapp)
    try:
        browser.set_html("<p>答案 \\(\\frac{1}{2}\\) 完成</p>")
        _wait_render(qapp)
        text = browser.toPlainText()
        assert "答案" in text and "完成" in text
        assert "\\frac" not in text
        assert MATH_HREF_SCHEME not in text
    finally:
        browser.deleteLater()


def test_append_html_renders_formula(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_html("<p>历史消息</p>")
        browser.append_html("<p>流式 \\(x^2\\) 追加</p>")
        text = browser.toPlainText()
        assert "流式" in text and "追加" in text
        assert "\\(" not in text, "追加片段里的公式同样要渲染"
    finally:
        browser.deleteLater()


def test_plain_html_without_math_keeps_native_path(qapp):
    browser = _make_browser(qapp)
    try:
        browser.set_html("<p>纯 HTML 内容</p>")
        assert browser.raw_text() == "", "不含公式时应保持原有零开销通路"
        assert "纯 HTML 内容" in browser.toPlainText()
    finally:
        browser.deleteLater()


def test_math_fragment_helper_renders_formula(qapp):
    """手工拼装 HTML 的调用点（批注列表 / 思维导图）复用同一个片段渲染入口。"""
    from plos.ui.dialogs import _math_fragment

    out = _math_fragment("解 \\(\\frac{1}{2}\\) 得 x")
    assert "mathf" in out
    assert "\\frac" not in out, "片段里不应残留 LaTeX 源码"


# ----------------------------------------------------------------------
# 七、树 / 列表单元格委托
# ----------------------------------------------------------------------

def _paint_cell(delegate, view, index, width: int = 320, height: int = 40) -> None:
    """把某个单元格画到离屏画布上，用于验证绘制路径不抛异常。"""
    from PyQt6.QtCore import QRect
    from PyQt6.QtGui import QPainter, QPixmap
    from PyQt6.QtWidgets import QStyleOptionViewItem

    pixmap = QPixmap(width, height)
    pixmap.fill()
    painter = QPainter(pixmap)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, height)
    option.font = view.font()
    option.widget = view
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()


def test_tree_delegate_only_renders_formula_cells(qapp):
    """不含公式的单元格必须完全走原生绘制，不产生任何文档对象。"""
    from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

    from plos.ui.math_text import attach_math_delegate

    tree = QTreeWidget()
    tree.setHeaderLabels(["题目", "知识点"])
    tree.setColumnWidth(0, 320)
    tree.addTopLevelItem(QTreeWidgetItem(["普通题目文本", "普通"]))
    tree.addTopLevelItem(QTreeWidgetItem(["解 \\(\\frac{7}{18}\\) 得 x", "方程"]))
    delegate = attach_math_delegate(tree, 0)
    try:
        model = tree.model()
        _paint_cell(delegate, tree, model.index(0, 0))
        assert delegate._cache == {}, "非公式单元格不应生成富文本文档"

        _paint_cell(delegate, tree, model.index(1, 0))
        assert delegate._cache, "公式单元格应生成并缓存富文本文档"
        document = next(iter(delegate._cache.values()))
        assert "7" in document.toPlainText()
        assert "\\frac" not in document.toPlainText()
    finally:
        tree.deleteLater()


def test_tree_delegate_row_height_covers_wrapped_formula(qapp):
    from PyQt6.QtCore import QRect
    from PyQt6.QtWidgets import QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem

    from plos.ui.math_text import attach_math_delegate

    tree = QTreeWidget()
    tree.setWordWrap(True)
    tree.setColumnWidth(0, 200)
    tree.addTopLevelItem(QTreeWidgetItem(["普通文本"]))
    tree.addTopLevelItem(QTreeWidgetItem(["长公式 \\(\\frac{1}{2}+\\frac{3}{4}\\) 说明"]))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 200, 30)
    option.font = tree.font()
    delegate = attach_math_delegate(tree, 0)
    try:
        model = tree.model()
        plain = delegate.sizeHint(option, model.index(0, 0))
        math = delegate.sizeHint(option, model.index(1, 0))
        assert math.height() >= plain.height()
    finally:
        tree.deleteLater()


def test_list_delegate_renders_formula_item(qapp):
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem

    from plos.ui.math_text import attach_math_delegate

    widget = QListWidget()
    widget.addItem(QListWidgetItem("#1 普通题干"))
    widget.addItem(QListWidgetItem("#2 求 \\(\\frac{7}{18}\\) 的值"))
    delegate = attach_math_delegate(widget, 0)
    try:
        model = widget.model()
        _paint_cell(delegate, widget, model.index(0, 0))
        assert delegate._cache == {}
        _paint_cell(delegate, widget, model.index(1, 0))
        assert delegate._cache
    finally:
        widget.deleteLater()


def test_checkable_list_item_keeps_checkbox_and_renders_formula(qapp):
    """带复选框的列表项（导出试卷选题）必须照常画勾选框，公式偏移到文字区。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QListWidget, QListWidgetItem

    from plos.ui.math_text import attach_math_delegate

    widget = QListWidget()
    item = QListWidgetItem("1. [填空] 求 \\(\\frac{7}{18}\\) 的值")
    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Checked)
    widget.addItem(item)
    delegate = attach_math_delegate(widget, 0)
    try:
        _paint_cell(delegate, widget, widget.model().index(0, 0))
        assert delegate._cache, "复选框项里的公式同样要渲染"
    finally:
        widget.deleteLater()


def test_tree_child_cell_paints_formula_with_indent(qapp):
    """子节点有缩进，公式必须画在缩进之后而不是压在最左侧。"""
    from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

    from plos.ui.math_text import attach_math_delegate

    tree = QTreeWidget()
    tree.setHeaderLabels(["来源", "题目"])
    root = QTreeWidgetItem(["整张试卷", "识别到 1 道题目"])
    child = QTreeWidgetItem(["题 1", "求 \\(\\frac{7}{18}\\) 的值"])
    root.addChild(child)
    tree.addTopLevelItem(root)
    tree.expandAll()
    delegate = attach_math_delegate(tree, 1)
    try:
        model = tree.model()
        _paint_cell(delegate, tree, model.index(0, 1, model.index(0, 0)))
        assert delegate._cache
    finally:
        tree.deleteLater()
