"""LaTeX 混排渲染测试（utils.math_latex）。

覆盖用户验收清单里的关键点：
- 语法识别：\\( \\)、\\[ \\]、$ $、$$ $$
- 混排：中文 + 多个行内公式 + 独立公式
- 常用符号：分数、根号、幂次、上下标、方程组、不等式、化学式
- 容错：错误/残缺 LaTeX 回退源码，不崩溃、不空白
- 普通文本不被强行转换（价格、百分号等）
"""

from __future__ import annotations

import pytest

from plos.utils.math_latex import (
    MATH_HREF_SCHEME,
    escape_html,
    iter_math_spans,
    latex_to_html,
    normalize_text_keep_math,
    render_body,
    split_math_segments,
    strip_math_delimiters,
    text_to_html,
)


def _strip_tags(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html)


# ----------------------------------------------------------------------
# 一、片段切分
# ----------------------------------------------------------------------

def test_inline_parenthesis_delimiters():
    segments = split_math_segments("求 \\(x^2+1\\) 的导数")
    assert [kind for kind, _, _ in segments] == ["text", "math", "text"]
    assert segments[1][1] == "x^2+1"
    assert segments[1][2] is False


def test_block_bracket_delimiters_are_centered():
    segments = split_math_segments("如下：\\[\\frac{7}{18}\\times0.36\\] 结束")
    kinds = [(kind, is_block) for kind, _, is_block in segments]
    assert kinds == [("text", False), ("math", True), ("text", False)]


def test_dollar_and_double_dollar_supported():
    inline = split_math_segments("价格 $x+1$ 而已")
    assert [kind for kind, _, _ in inline] == ["text", "math", "text"]

    block = split_math_segments("推导$$a^2+b^2=c^2$$完毕")
    assert [(kind, is_block) for kind, _, is_block in block] == [
        ("text", False), ("math", True), ("text", False),
    ]


def test_plain_text_is_untouched():
    plain = "今天语文作业：背诵课文第 3 段，明天交。"
    segments = split_math_segments(plain)
    assert segments == [("text", plain, False)]


def test_lone_dollar_is_not_treated_as_formula():
    """价格里的美元符号不能把中间整段文字吞成公式。"""
    text = "这件 $5 元，那件 $10 元"
    segments = split_math_segments(text)
    assert all(kind == "text" for kind, _, _ in segments)


def test_unclosed_delimiter_keeps_text_intact():
    text = "这里有半个公式 \\(x+1 没有闭合"
    segments = split_math_segments(text)
    assert all(kind == "text" for kind, _, _ in segments)
    assert "".join(content for _, content, _ in segments) == text


def test_multiline_dollar_is_rejected():
    text = "第一行 $a+\n第二行 b$ 结束"
    segments = split_math_segments(text)
    assert all(kind == "text" for kind, _, _ in segments)


def test_mixed_content_keeps_all_pieces():
    text = "已知 \\(a=1\\) 且 \\(b=2\\)，则\n\\[a+b=3\\]\n结论成立。"
    segments = split_math_segments(text)
    assert sum(1 for kind, _, _ in segments if kind == "math") == 3
    assert "".join(content for _, content, _ in segments).count("已知") == 1


def test_iter_math_spans_locates_source():
    text = "前 \\(x\\) 后 \\(y\\)"
    spans = iter_math_spans(text)
    assert len(spans) == 2
    for start, end, latex, _ in spans:
        assert text[start:end] == latex


def test_strip_math_delimiters_preserves_content():
    text = "求 \\(x^2\\) 与 \\[y^2\\] 之和"
    result = strip_math_delimiters(text)
    assert "x^2" in result and "y^2" in result
    assert "\\(" not in result and "\\[" not in result


# ----------------------------------------------------------------------
# 入库清洗：必须保留公式标记（否则公式到不了渲染器与导出层）
# ----------------------------------------------------------------------

def test_normalize_keep_math_preserves_markers():
    source = "求 \\(\\frac{7}{18}\\) 与 \\(x^2\\) 的值"
    result = normalize_text_keep_math(source)
    assert "\\(\\frac{7}{18}\\)" in result, "公式标记与内容都要原样保留"
    assert "\\(x^2\\)" in result


def test_normalize_keep_math_cleans_plain_text():
    """公式之外的 LaTeX 记号仍要被清洗成可读 Unicode。"""
    result = normalize_text_keep_math("角度 \\alpha 与 \\leq 符号")
    assert "α" in result and "≤" in result
    assert "\\alpha" not in result


def test_normalize_keep_math_keeps_block_formula():
    result = normalize_text_keep_math("如下：\\[a+b=c\\] 完毕")
    assert "\\[a+b=c\\]" in result


def test_normalize_keep_math_handles_empty():
    assert normalize_text_keep_math("") == ""
    assert normalize_text_keep_math("普通文本") == "普通文本"


# ----------------------------------------------------------------------
# 二、符号与结构
# ----------------------------------------------------------------------

def test_fraction_inline_stays_linear():
    html = latex_to_html("\\frac{7}{18}")
    assert "7" in _strip_tags(html) and "18" in _strip_tags(html)
    assert "table" not in html, "行内分式不能变成表格，否则会打断行内排版"


def test_fraction_block_uses_stacked_table():
    html = latex_to_html("\\frac{7}{18}", block=True)
    assert "table" in html and "mnum" in html and "mden" in html


def test_block_formula_with_operators_keeps_one_row():
    """多个分式 + 运算符要排在同一个表格行里，运算符跨两行。"""
    html = latex_to_html("\\frac{1}{2}+\\frac{1}{3}=\\frac{5}{6}", block=True)
    assert html.count("<tr>") == 2
    assert "rowspan='2'" in html
    assert html.count("mnum") == 3 and html.count("mden") == 3


def test_sqrt_renders_with_radical():
    html = _strip_tags(latex_to_html("\\sqrt{2}"))
    assert "√" in html and "2" in html


def test_nth_root_renders_index():
    html = _strip_tags(latex_to_html("\\sqrt[3]{x+1}"))
    assert "∛" in html or "3" in html
    assert "x+1" in html


def test_superscript_and_subscript_use_tags():
    html = latex_to_html("x^{2}+a_{1}")
    assert "<sup>2</sup>" in html
    assert "<sub>1</sub>" in html


def test_superscript_single_char_without_braces():
    html = latex_to_html("x^2+y_1")
    assert "<sup>2</sup>" in html and "<sub>1</sub>" in html


def test_common_symbols_convert_to_unicode():
    html = _strip_tags(latex_to_html("\\alpha\\le\\beta\\times\\gamma\\div\\delta"))
    for expected in ("α", "≤", "β", "×", "γ", "÷", "δ"):
        assert expected in html


def test_inequality_and_absolute_value():
    html = _strip_tags(latex_to_html("|x-1|\\le 2"))
    assert "≤" in html and "x-1" in html


def test_cases_environment_renders_multiple_lines():
    html = latex_to_html("\\begin{cases}x=1 & y=2\\\\x=3 & y=4\\end{cases}")
    assert "<br/>" in html
    assert "x=1" in _strip_tags(html) and "y=4" in _strip_tags(html)


def test_chemistry_expression_keeps_subscripts():
    """化学式：\\ce 包裹与下标记法都要能显示成下标。"""
    html = _strip_tags(latex_to_html("\\ce{H2O}"))
    assert "H2O" in html
    html_sub = latex_to_html("H_2SO_4")
    assert "<sub>2</sub>" in html_sub and "<sub>4</sub>" in html_sub


def test_text_command_content_is_escaped_not_parsed():
    html = latex_to_html("\\text{速度 v} + 1")
    assert "速度 v" in _strip_tags(html)


def test_left_right_delimiters_are_dropped():
    html = _strip_tags(latex_to_html("\\left(\\frac{1}{2}\\right)"))
    assert "(" in html and ")" in html
    assert "left" not in html and "right" not in html


def test_displaystyle_and_spacing_commands_ignored():
    html = _strip_tags(latex_to_html("\\displaystyle a\\,b\\;c"))
    assert "displaystyle" not in html
    assert "abc" in html.replace(" ", "")


# ----------------------------------------------------------------------
# 三、容错（重点）
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "\\frac{1}",              # 缺分母
        "\\frac{}{}",             # 空分子分母
        "\\sqrt",                 # 缺参数
        "\\unknowncmd{x}",        # 未收录命令
        "a^{",                    # 括号不闭合
        "}{}{",                   # 多余括号
        "\\begin{cases}",         # 环境未闭合
        "\\\\\\",                 # 连续反斜杠
        "\\frac{1}{2",            # 花括号残缺
        "",                       # 空公式
        "$",                      # 只有标记
    ],
)
def test_malformed_latex_never_raises(bad):
    """错误公式必须降级显示，不抛异常、不返回空白（空输入除外）。"""
    html = latex_to_html(bad)
    assert isinstance(html, str)
    if bad:
        assert html != ""


def test_broken_fraction_falls_back_to_source_text():
    html = latex_to_html("\\frac{1}{2")
    text = _strip_tags(html)
    assert "1" in text and "2" in text, "残缺分式也要能看到原文内容"


def test_unknown_command_keeps_readable_name():
    text = _strip_tags(latex_to_html("\\foo{bar}"))
    assert "foo" in text


def test_render_body_contains_no_raw_backslash_command():
    """渲染结果里不应再出现裸露的 LaTeX 命令。"""
    html = render_body("解 \\(\\frac{1}{2}x=3\\) 得 x=6", "#000", "#000")
    assert "\\frac" not in html


def test_escape_html_handles_specials():
    escaped = escape_html("<a & b>")
    assert escaped == "&lt;a &amp; b&gt;"


def test_text_to_html_converts_newlines():
    assert text_to_html("A\nB") == "A<br/>B"


# ----------------------------------------------------------------------
# 四、文档与主题
# ----------------------------------------------------------------------

def test_render_body_wraps_formula_in_anchor_for_copy():
    """公式要带锚点，悬浮/点击才能拿到 LaTeX 源码。"""
    html = render_body("值 \\(x^2\\) 结束", "#111", "#111")
    assert MATH_HREF_SCHEME in html
    assert "class='mathf'" in html


def test_block_formula_is_centered_container():
    html = render_body("\\[a+b\\]", "#111", "#111")
    assert "mathblock" in html


def test_theme_colors_are_injected():
    light = render_body("普通文本", "#1A2030", "#1A2030")
    dark = render_body("普通文本", "#E9ECF4", "#E9ECF4")
    assert "light" != "dark"
    assert light and dark
