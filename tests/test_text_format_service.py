"""一键格式化文本服务测试。

要求（用户验收相关）：
- 清理 OCR 噪声但**不能改坏正文**（中文之间空格、重复标点、零宽字符）
- 数学符号归一（<= / >= / != / ** 等）
- 自动给明显是数学但没带标记的片段补 \\( \\)，且不误伤日期、编号、普通英文
- 已带标记的公式原样保留、不被二次包裹
"""

from __future__ import annotations

import pytest

from plos.services.text_format_service import (
    TextFormatService,
    get_text_format_service,
)


@pytest.fixture()
def service() -> TextFormatService:
    return TextFormatService()


# ----------------------------------------------------------------------
# 一、OCR 噪声清理
# ----------------------------------------------------------------------

def test_removes_invisible_characters(service):
    assert service.clean_ocr_noise("题\u200b目\ufeff内容") == "题目内容"


def test_collapses_space_between_chinese(service):
    assert service.clean_ocr_noise("已 知 函 数") == "已知函数"


def test_keeps_space_between_chinese_and_latin(service):
    """中英之间应保留空格，不能一起吞掉。"""
    assert service.clean_ocr_noise("求 f(x) 的值") == "求 f(x) 的值"


def test_deduplicates_punctuation(service):
    assert service.clean_ocr_noise("为什么？？真的吗！！") == "为什么？真的吗！"


def test_collapses_blank_lines(service):
    assert service.clean_ocr_noise("第一行\n\n\n\n第二行") == "第一行\n\n第二行"


def test_converts_ideographic_space(service):
    assert service.clean_ocr_noise("已知\u3000x=1") == "已知 x=1"


def test_keeps_math_text_intact(service):
    """清理不能动公式标记与算式。"""
    source = "已知 \\(x^2+1\\) 求导，得 2x"
    assert service.clean_ocr_noise(source) == source


# ----------------------------------------------------------------------
# 二、数学符号修复
# ----------------------------------------------------------------------

def test_normalizes_comparison_operators(service):
    fixed = service.fix_math_symbols("条件 \\(x<=5\\) 与 \\(y>=2\\)")
    assert "\\leq" in fixed and "\\geq" in fixed
    assert "<=" not in fixed and ">=" not in fixed


def test_normalizes_not_equal(service):
    assert "\\neq" in service.fix_math_symbols("\\(a!=b\\)")


def test_normalizes_power_operator(service):
    assert "^" in service.fix_math_symbols("\\(x**2\\)")


def test_fixes_fullwidth_braces_after_command(service):
    fixed = service.fix_math_symbols("\\(\\frac（1）（2）\\)")
    assert "\\frac{1}{2}" in fixed.replace(" ", "")


def test_keeps_outside_text_untouched(service):
    source = "题干：以下说法正确的是（  ）"
    assert service.fix_math_symbols(source) == source


def test_block_formula_stays_block(service):
    fixed = service.fix_math_symbols("\\[a<=b\\]")
    assert fixed.startswith("\\[") and fixed.endswith("\\]")


# ----------------------------------------------------------------------
# 三、标记规整
# ----------------------------------------------------------------------

def test_odd_dollar_is_removed(service):
    result = service.normalize_latex_markers("价格 $5 元的商品")
    assert result.count("$") % 2 == 0
    assert "价格" in result and "元的商品" in result


def test_even_dollar_untouched(service):
    source = "公式 $x+1$ 与 $y-2$ 都成立"
    assert service.normalize_latex_markers(source) == source


# ----------------------------------------------------------------------
# 四、自动套用公式标记
# ----------------------------------------------------------------------

def test_wraps_simple_equation(service):
    result = service.wrap_math_expressions("解得 y=2x+1 成立")
    assert "\\(y=2x+1\\)" in result


def test_wraps_power_expression(service):
    result = service.wrap_math_expressions("化简 x^2+3x 即可")
    assert "\\(x^2+3x\\)" in result


def test_wraps_bare_latex_command(service):
    result = service.wrap_math_expressions("结果是 \\frac{1}{2} 米")
    assert "\\(\\frac{1}{2}\\)" in result


def test_keeps_already_marked_formula(service):
    source = "已知 \\(a=1\\) 与 \\[b=2\\]"
    result = service.wrap_math_expressions(source)
    assert result.count("\\(") == 1
    assert "\\[b=2\\]" in result


def test_does_not_wrap_dates(service):
    """日期不能被当成公式（否则会渲染成数学排版）。"""
    source = "生成日期：2026-09-11"
    assert service.wrap_math_expressions(source) == source


def test_does_not_wrap_plain_chinese(service):
    source = "请把上面的错题重新做一遍，注意审题。"
    assert service.wrap_math_expressions(source) == source


def test_does_not_wrap_option_letters(service):
    """选择题选项 A. 甲 B. 乙 不能被包成公式。"""
    source = "A. 增函数 B. 减函数 C. 极值点 D. 以上都不对"
    assert service.wrap_math_expressions(source) == source


def test_does_not_wrap_plain_words(service):
    source = "这道题考查导数与函数单调性"
    assert service.wrap_math_expressions(source) == source


def test_does_not_wrap_question_numbers(service):
    source = "1．第一题 2．第二题"
    assert service.wrap_math_expressions(source) == source


# ----------------------------------------------------------------------
# 五、一键入口
# ----------------------------------------------------------------------

def test_format_text_combines_all_steps(service):
    messy = "已 知 方 程  x**2<=4 ，求 解？？"
    result = service.format_text(messy)
    assert "已知方程" in result
    assert "？？" not in result
    assert "\\leq" in result, "比较符应被归一"
    assert "\\(" in result, "算式应被自动标记"


def test_format_text_is_idempotent(service):
    """重复格式化不应持续变形。"""
    source = "已知 \\(x^2\\leq 4\\)，求 x 的范围。"
    once = service.format_text(source)
    twice = service.format_text(once)
    assert once == twice


def test_format_text_keeps_plain_text_stable(service):
    source = "这是一段普通的中文说明文字，没有任何公式。"
    assert service.format_text(source) == source


def test_format_text_handles_empty(service):
    assert service.format_text("") == ""
    assert service.clean_ocr_noise("") == ""
    assert service.wrap_math_expressions("") == ""


def test_format_text_never_raises_on_weird_input(service):
    for weird in ("$$$$", "\\\\", "\\frac{", "\u3000\u3000", "\n\n\n", "$"):
        assert isinstance(service.format_text(weird), str)


def test_singleton_accessor_returns_same_instance():
    assert get_text_format_service() is get_text_format_service()
